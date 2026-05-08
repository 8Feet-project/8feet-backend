"""
WebSocket routing for research.
"""
from django.urls import path

from research.consumers import ResearchTaskConsumer


websocket_urlpatterns = [
    path("ws/research/tasks/<int:task_id>/", ResearchTaskConsumer.as_asgi()),
]
