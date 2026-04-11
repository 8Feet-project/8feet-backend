"""
管理端用户管理路由 (挂载于 /api/v1/admin/users/)
"""
from django.urls import path
from users.api.user_api import (
    list_users, create_user, get_user_detail, 
    update_user_permissions, reset_user_password, toggle_user
)

urlpatterns = [
    path('', list_users, name='admin-user-list'),
    path('', create_user, name='admin-user-create'), # POST method handled by view
    path('<int:user_id>', get_user_detail, name='admin-user-detail'),
    path('<int:user_id>', update_user_permissions, name='admin-user-update'), # PATCH method
    path('<int:user_id>/reset-password', reset_user_password, name='admin-user-reset-password'),
    path('toggle', toggle_user, name='admin-user-toggle'), # Non-REST compatible but keeping for now or mapping
]
