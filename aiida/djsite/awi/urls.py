from django.conf.urls import patterns, url

from awi import views

urlpatterns = patterns('',
	url(r'^$', views.index, name='home'), # home page
	url(r'^computers/rename/(?P<computer_id>\d+)', views.computers_rename, name='computers_rename'), # rename a computer (ajax)
	url(r'^computers/detail/(?P<computer_id>\d+)/(?P<ajax>\w+)', views.computers_detail, name='computers_detail_ajax'), # details for a computer called in ajax
	url(r'^computers/detail/(?P<computer_id>\d+)', views.computers_detail, name='computers_detail'), # details for a computer
	url(r'^computers/list/(?P<ordering>[a-z0-9\-_]+)', views.computers_list), # listing of computers
	url(r'^computers/list', views.computers_list, name='computers_list'), # listing of computers
	url(r'^computers', views.computers, name='computers'), # computers module
	url(r'^codes', views.codes, name='codes'), # page for management of codes
)