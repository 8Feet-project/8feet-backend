"""
WebSocket consumers for research task live updates.
"""
from __future__ import annotations

from urllib.parse import parse_qs

import jwt
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from research.models import ResearchTask
from research.realtime import research_task_group


class ResearchTaskConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self) -> None:
        self.task_id = self.scope["url_route"]["kwargs"]["task_id"]
        self.group_name = research_task_group(self.task_id)
        self.user = await self._authenticate()

        if self.user is None:
            await self.close(code=4401)
            return

        if not await self._can_view_task(self.user.id, self.task_id):
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json(
            {
                "type": "connection_ack",
                "task_id": str(self.task_id),
                "reason": "connected",
                "payload": {},
                "timestamp": timezone.now().isoformat(),
            }
        )

    async def disconnect(self, close_code: int) -> None:
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content: dict, **kwargs) -> None:
        if content.get("type") == "ping":
            await self.send_json(
                {
                    "type": "pong",
                    "task_id": str(self.task_id),
                    "reason": "pong",
                    "payload": {},
                    "timestamp": timezone.now().isoformat(),
                }
            )

    async def task_update(self, event: dict) -> None:
        await self.send_json(event["message"])

    async def _authenticate(self):
        query_string = self.scope.get("query_string", b"").decode("utf-8")
        token = parse_qs(query_string).get("token", [""])[0].strip()
        if not token or await self._is_token_blacklisted(token):
            return None

        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
            if payload.get("type") != "access_token":
                return None
            user_id = payload["user_id"]
        except (KeyError, jwt.InvalidTokenError):
            return None

        return await self._get_user(user_id)

    @database_sync_to_async
    def _is_token_blacklisted(self, token: str) -> bool:
        return bool(cache.get(f"blacklist_{token}"))

    @database_sync_to_async
    def _get_user(self, user_id: int):
        return get_user_model().objects.filter(pk=user_id, is_active=True).first()

    @database_sync_to_async
    def _can_view_task(self, user_id: int, task_id: int) -> bool:
        user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
        if user is None or not user.has_perm("research.view_research"):
            return False
        return ResearchTask.objects.filter(pk=task_id, user_id=user.id).exists()
