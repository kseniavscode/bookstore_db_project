from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('catalog/', views.catalog_view, name='catalog'),
    path('admin-panel/', views.admin_panel_view, name='admin_panel'),
    path('audit-log/', views.audit_log_view, name='audit_log'),
]