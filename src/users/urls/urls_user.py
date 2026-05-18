"""
用户个人信息路由 (挂载于 /api/v1/users/)
"""
from django.urls import path
from users.api.auth import get_profile, update_profile, change_password
from users.api.persona_api import (
    persona_clear,
    persona_conversation_message,
    persona_conversation_start,
    persona_detail,
    persona_skip,
)

urlpatterns = [
    path('me', get_profile, name='user-me'),
    # Note: PATCH is handled within the same view or a separate one. 
    # Document says PATCH /api/v1/users/me
    # Doc also has /api/v1/users/me/password
    path('me/password', change_password, name='user-change-password'),
    path('me/persona', persona_detail, name='user-persona-detail'),
    path('me/persona/skip', persona_skip, name='user-persona-skip'),
    path('me/persona/clear', persona_clear, name='user-persona-clear'),
    path('me/persona/conversations', persona_conversation_start, name='user-persona-conversation-start'),
    path('me/persona/conversations/<str:thread_id>/messages', persona_conversation_message, name='user-persona-conversation-message'),
]
