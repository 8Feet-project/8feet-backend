"""
users URL 配置
挂载点: /api/users/
"""
from django.urls import path
from users.api.auth import login, refresh_token, get_profile

urlpatterns = [
    path('login', login, name='users-login'),
    path('token-refresh', refresh_token, name='users-token-refresh'),
    path('profile', get_profile, name='users-profile'),
]
