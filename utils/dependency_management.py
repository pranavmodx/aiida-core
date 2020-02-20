#!/usr/bin/env python
# -*- coding: utf-8 -*-
###########################################################################
# Copyright (c), The AiiDA team. All rights reserved.                     #
# This file is part of the AiiDA code.                                    #
#                                                                         #
# The code is hosted on GitHub at https://github.com/aiidateam/aiida-core #
# For further information on the license, see the LICENSE.txt file        #
# For further information please visit http://www.aiida.net               #
###########################################################################
"""Utility CLI to update dependency version requirements of the `setup.json`."""

import os
import re
import json
import copy
from collections import OrderedDict
from pkg_resources import Requirement

import click
import yaml
import toml

from validate_consistency import get_setup_json, write_setup_json

FILENAME_SETUP_JSON = 'setup.json'
SCRIPT_PATH = os.path.split(os.path.realpath(__file__))[0]
ROOT_DIR = os.path.join(SCRIPT_PATH, os.pardir)
FILEPATH_SETUP_JSON = os.path.join(ROOT_DIR, FILENAME_SETUP_JSON)
DEFAULT_EXCLUDE_LIST = ['django', 'circus', 'numpy', 'pymatgen', 'ase', 'monty', 'pyyaml']
DOCS_DIR = os.path.join(ROOT_DIR, 'docs')

SETUPTOOLS_CONDA_MAPPINGS = {
    'psycopg2-binary': 'psycopg2',
    'graphviz': 'python-graphviz',
}

CONDA_IGNORE = [
    'pyblake2',
]


def _setuptools_to_conda(req):
    for pattern, replacement in SETUPTOOLS_CONDA_MAPPINGS.items():
        if re.match(pattern, str(req)):
            return Requirement.parse(re.sub(pattern, replacement, str(req)))
    return req  # did not match any of the replacement patterns, just return original


@click.group()
def cli():
    """Utility to update dependency requirements for `aiida-core`.

    Since `aiida-core` fixes the versions of almost all of its dependencies, once in a while these need to be updated.
    This is a manual process, but this CLI attempts to simplify it somewhat. The idea is to remote all explicit version
    restrictions from the `setup.json`, except for those packages where it is known that a upper limit is necessary.
    This is accomplished by the command:

        python dependency_management.py unrestrict

    The command will update the `setup.json` to remove all explicit limits, except for those packages specified by the
    `--exclude` option. After this step, install `aiida-core` through pip with the `[all]` flag to install all optional
    extra requirements as well. Since there are no explicit version requirements anymore, pip should install the latest
    available version for each dependency.

    Once all the tests complete successfully, run the following command:

        pip freeze > requirements.txt

    This will now capture the exact versions of the packages installed in the virtual environment. Since the tests run
    for this setup, we can now set those versions as the new requirements in the `setup.json`. Note that this is why a
    clean virtual environment should be used for this entire procedure. Now execute the command:

        python dependency_management.py update requirements.txt

    This will now update the `setup.json` to reinstate the exact version requirements for all dependencies. Commit the
    changes to `setup.json` and make a pull request.
    """


@cli.command('generate-environment-yml')
def generate_environment_yml():
    """Generate `environment.yml` file for conda."""

    # needed for ordered dict, see https://stackoverflow.com/a/52621703
    yaml.add_representer(
        OrderedDict,
        lambda self, data: yaml.representer.SafeRepresenter.represent_dict(self, data.items()),
        Dumper=yaml.SafeDumper
    )

    # Read the requirements from 'setup.json'
    with open('setup.json') as file:
        setup_cfg = json.load(file)
        install_requirements = [Requirement.parse(r) for r in setup_cfg['install_requires']]

    # python version cannot be overriden from outside environment.yml
    # (even if it is not specified at all in environment.yml)
    # https://github.com/conda/conda/issues/9506
    conda_requires = ['python~=3.7']
    for req in install_requirements:
        if req.name == 'python' or any(re.match(ignore, str(req)) for ignore in CONDA_IGNORE):
            continue
        conda_requires.append(str(_setuptools_to_conda(req)))

    environment = OrderedDict([
        ('name', 'aiida'),
        ('channels', ['conda-forge', 'defaults']),
        ('dependencies', conda_requires),
    ])

    environment_filename = 'environment.yml'
    file_path = os.path.join(ROOT_DIR, environment_filename)
    with open(file_path, 'w') as env_file:
        env_file.write('# Usage: conda env create -n myenvname -f environment.yml\n')
        yaml.safe_dump(
            environment, env_file, explicit_start=True, default_flow_style=False, encoding='utf-8', allow_unicode=True
        )


@cli.command('generate-rtd-reqs')
def generate_requirements_for_rtd():
    """Generate 'docs/requirements_for_rtd.txt' file."""

    # Read the requirements from 'setup.json'
    with open('setup.json') as file:
        setup_cfg = json.load(file)
        install_requirements = {Requirement.parse(r) for r in setup_cfg['install_requires']}
        for key in ('testing', 'docs', 'rest', 'atomic_tools'):
            install_requirements.update({Requirement.parse(r) for r in setup_cfg['extras_require'][key]})

    basename = 'requirements_for_rtd.txt'

    # pylint: disable=bad-continuation
    with open(os.path.join(DOCS_DIR, basename), 'w') as reqs_file:
        reqs_file.write('\n'.join(sorted(map(str, install_requirements))))


@cli.command('validate-environment-yml', help="Validate 'environment.yml'.")
def validate_environment_yml():
    """Validate consistency of the requirements specification of the package.

    Validates that the specification of requirements/dependencies is consistent across
    the following files:

    - setup.json
    - environment.yml
    """
    # Read the requirements from 'setup.json' and 'environment.yml'.
    with open('setup.json') as file:
        setup_cfg = json.load(file)
        install_requirements = [Requirement.parse(r) for r in setup_cfg['install_requires']]
        python_requires = Requirement.parse('python' + setup_cfg['python_requires'])

    with open('environment.yml') as file:
        conda_dependencies = {Requirement.parse(d) for d in yaml.load(file, Loader=yaml.SafeLoader)['dependencies']}

    # Attempt to find the specification of Python among the 'environment.yml' dependencies.
    for dependency in conda_dependencies:
        if dependency.name == 'python':  # Found the Python dependency specification
            conda_python_dependency = dependency
            conda_dependencies.remove(dependency)
            break
    else:  # Failed to find Python dependency specification
        raise click.ClickException("Did not find specification of Python version in 'environment.yml'.")

    # The Python version specified in 'setup.json' should be listed as trove classifiers.
    for spec in conda_python_dependency.specifier:
        expected_classifier = 'Programming Language :: Python :: ' + spec.version
        if expected_classifier not in setup_cfg['classifiers']:
            raise click.ClickException("Trove classifier '{}' missing from 'setup.json'.".format(expected_classifier))

        # The Python version should be specified as supported in 'setup.json'.
        if not any(spec.version >= other_spec.version for other_spec in python_requires.specifier):
            raise click.ClickException(
                "Required Python version between 'setup.json' and 'environment.yml' not consistent."
            )

        break
    else:
        raise click.ClickException("Missing specifier: '{}'.".format(conda_python_dependency))

    # Check that all requirements specified in the setup.json file are found in the
    # conda environment specification.
    missing_from_env = set()
    for req in install_requirements:
        if any(re.match(ignore, str(req)) for ignore in CONDA_IGNORE):
            continue  # skip explicitly ignored packages

        try:
            conda_dependencies.remove(_setuptools_to_conda(req))
        except KeyError:
            raise click.ClickException("Requirement '{}' not specified in 'environment.yml'.".format(req))

    # The only dependency left should be the one for Python itself, which is not part of
    # the install_requirements for setuptools.
    if len(conda_dependencies) > 0:
        raise click.ClickException(
            "The 'environment.yml' file contains dependencies that are missing "
            "in 'setup.json':\n- {}".format('\n- '.join(conda_dependencies))
        )

    click.secho('Conda dependency specification is consistent.', fg='green')


@cli.command('validate-rtd-reqs', help='Validate requirements_for_rtd.txt.')
def validate_rtd_reqs():
    """Validate consistency of the specification of 'docs/requirements_for_rtd.txt'."""

    # Read the requirements from 'setup.json'
    with open('setup.json') as file:
        setup_cfg = json.load(file)
        install_requirements = {Requirement.parse(r) for r in setup_cfg['install_requires']}
        for key in ('testing', 'docs', 'rest', 'atomic_tools'):
            install_requirements.update({Requirement.parse(r) for r in setup_cfg['extras_require'][key]})

    basename = 'requirements_for_rtd.txt'

    with open(os.path.join(DOCS_DIR, basename)) as rtd_reqs_file:
        reqs = {Requirement.parse(r) for r in rtd_reqs_file}

    assert reqs == install_requirements

    click.secho('RTD requirements specification is consistent.', fg='green')


@cli.command('validate-pyproject-toml', help="Validate 'pyproject.toml'.")
def validate_pyproject_toml():
    """Validate consistency of the specification of 'pyprojec.toml'."""

    # Read the requirements from 'setup.json'
    with open('setup.json') as file:
        setup_cfg = json.load(file)
        install_requirements = [Requirement.parse(r) for r in setup_cfg['install_requires']]

    for requirement in install_requirements:
        if requirement.name == 'reentry':
            reentry_requirement = requirement
            break
    else:
        raise click.ClickException("Failed to find reentry requirement in 'setup.json'.")

    try:
        with open(os.path.join(ROOT_DIR, 'pyproject.toml')) as file:
            pyproject = toml.load(file)
            pyproject_requires = [Requirement.parse(r) for r in pyproject['build-system']['requires']]

        if reentry_requirement not in pyproject_requires:
            raise click.ClickException("Missing requirement '{}' in 'pyproject.toml'.".format(reentry_requirement))

    except FileNotFoundError as error:
        raise click.ClickException("The 'pyproject.toml' file is missing!")

    click.secho('Pyproject.toml dependency specification is consistent.', fg='green')


@cli.command('validate-all', help='Validate consistency of all requirements.')
@click.pass_context
def validate_all(ctx):
    """Validate consistency of all requirement specifications of the package.

    Validates that the specification of requirements/dependencies is consistent across
    the following files:

    - setup.py
    - setup.json
    - environment.yml
    - pyproject.toml
    - docs/requirements_for_rtd.txt
    """

    ctx.invoke(validate_environment_yml)
    ctx.invoke(validate_pyproject_toml)
    ctx.invoke(validate_rtd_reqs)


@cli.command('unrestrict')
@click.option('--exclude', multiple=True, help='List of package names to exclude from updating.')
def unrestrict_requirements(exclude):
    """Remove all explicit dependency version restrictions from `setup.json`.

    Warning, this currently only works for dependency requirements that use the `==` operator. Statements with different
    operators, additional filters after a semicolon, or with extra requirements (using `[]`) are not supported. The
    limits for these statements will have to be updated manually.
    """
    setup = get_setup_json()
    clone = copy.deepcopy(setup)
    clone['install_requires'] = []

    if exclude:
        exclude = list(exclude).extend(DEFAULT_EXCLUDE_LIST)
    else:
        exclude = DEFAULT_EXCLUDE_LIST

    for requirement in setup['install_requires']:
        if requirement in exclude or ';' in requirement or '==' not in requirement:
            clone['install_requires'].append(requirement)
        else:
            package = requirement.split('==')[0]
            clone['install_requires'].append(package)

    for extra, requirements in setup['extras_require'].items():
        clone['extras_require'][extra] = []

        for requirement in requirements:
            if requirement in exclude or ';' in requirement or '==' not in requirement:
                clone['extras_require'][extra].append(requirement)
            else:
                package = requirement.split('==')[0]
                clone['extras_require'][extra].append(package)

    write_setup_json(clone)


@cli.command('update')
@click.argument('requirements', type=click.File(mode='r'))
def update_requirements(requirements):
    """Apply version restrictions from REQUIREMENTS.

    The REQUIREMENTS file should contain the output of `pip freeze`.
    """
    setup = get_setup_json()

    package_versions = []

    for requirement in requirements.readlines():
        try:
            package, version = requirement.strip().split('==')
            package_versions.append((package, version))
        except ValueError:
            continue

    requirements = set()

    for requirement in setup['install_requires']:
        for package, version in package_versions:
            if requirement.lower() == package.lower():
                requirements.add('{}=={}'.format(package.lower(), version))
                break
        else:
            requirements.add(requirement)

    setup['install_requires'] = sorted(requirements)

    for extra, extra_requirements in setup['extras_require'].items():
        requirements = set()

        for requirement in extra_requirements:
            for package, version in package_versions:
                if requirement.lower() == package.lower():
                    requirements.add('{}=={}'.format(package.lower(), version))
                    break
            else:
                requirements.add(requirement)

        setup['extras_require'][extra] = sorted(requirements)

    write_setup_json(setup)


if __name__ == '__main__':
    cli()  # pylint: disable=no-value-for-parameter
