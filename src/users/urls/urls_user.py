"""
用户个人信息路由 (挂载于 /api/v1/users/)
"""
from django.urls import path
from users.api.auth import get_profile, update_profile, change_password

urlpatterns = [
    path('me', get_profile, name='user-me'),
    # Note: PATCH is handled within the same view or a separate one. 
    # Document says PATCH /api/v1/users/me
    # Doc also has /api/v1/users/me/password
    path('me/password', change_password, name='user-change-password'),
]
