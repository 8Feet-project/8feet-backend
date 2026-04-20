from django.urls import path

from analytics.api.messages_api import (
    mark_all_read,
    mark_read,
    message_list,
)

urlpatterns = [
    path('', message_list, name='messages-collection'),
    path('read-all', mark_all_read, name='messages-read-all'),
    path('<int:message_id>/read', mark_read, name='messages-read'),
]
