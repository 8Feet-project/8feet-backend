"""
认证接口路由 (挂载于 /api/v1/auth/)
"""
from django.urls import path
from users.api.auth import (
    register, login, login_by_username, login_by_email, logout, refresh_token,
    send_email_code, verify_email, reset_password_request, reset_password_confirm
)

urlpatterns = [
    path('register', register, name='auth-register'),
    path('register/', register),
    path('login', login, name='auth-login'),
    path('login/', login),
    path('login/username', login_by_username, name='auth-login-username'),
    path('login/username/', login_by_username),
    path('login/email', login_by_email, name='auth-login-email'),
    path('login/email/', login_by_email),
    path('logout', logout, name='auth-logout'),
    path('logout/', logout),
    path('refresh', refresh_token, name='auth-refresh'),
    path('refresh/', refresh_token),
    path('email/send-code', send_email_code, name='auth-send-code'),
    path('email/send-code/', send_email_code),
    path('email/verify', verify_email, name='auth-verify-email'),
    path('email/verify/', verify_email),
    path('password/reset-request', reset_password_request, name='auth-password-reset-request'),
    path('password/reset-request/', reset_password_request),
    path('password/reset-confirm', reset_password_confirm, name='auth-password-reset-confirm'),
    path('password/reset-confirm/', reset_password_confirm),
]
