"""
users URL 配置
挂载点: /api/users/
"""
from django.urls import path
from users.api.auth import login, refresh_token, get_profile
from users.api.user_api import create_user, update_user, toggle_user, list_users

urlpatterns = [
    # 认证接口 (公开 / 仅认证)
    path('login', login, name='users-login'),
    path('token-refresh', refresh_token, name='users-token-refresh'),
    path('profile', get_profile, name='users-profile'),
    # 用户管理接口 (管理员权限)
    path('create', create_user, name='users-create'),
    path('update', update_user, name='users-update'),
    path('toggle', toggle_user, name='users-toggle'),
    path('list', list_users, name='users-list'),
]
