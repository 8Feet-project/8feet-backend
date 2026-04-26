"""
站内消息相关路由 (挂载于 /api/v1/messages/)
"""
from django.urls import path

from analytics.api.messages_api import (
    message_list, mark_read, mark_all_read
)

urlpatterns = [
    path('', message_list, name='message-list'),
    path('<int:message_id>/read', mark_read, name='message-read'),
    path('read-all', mark_all_read, name='message-read-all'),
    path('read-all/', mark_all_read, name='message-read-all-slash'),
]
