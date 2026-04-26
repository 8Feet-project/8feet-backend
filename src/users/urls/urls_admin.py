"""
管理端用户管理路由 (挂载于 /api/v1/admin/users/)
"""
from django.urls import path
from users.api.user_api import (
    reset_user_password,
    toggle_user,
    user_detail_resource,
    users_collection,
)

urlpatterns = [
    path('', users_collection, name='admin-user-collection'),
    path('<int:user_id>', user_detail_resource, name='admin-user-detail'),
    path('<int:user_id>/reset-password', reset_user_password, name='admin-user-reset-password'),
    path('toggle', toggle_user, name='admin-user-toggle'), # Non-REST compatible but keeping for now or mapping
]
