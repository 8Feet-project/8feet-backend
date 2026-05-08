"""
Research task realtime notifications.
"""
from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone


logger = logging.getLogger(__name__)


def research_task_group(task_id: int | str) -> str:
    return f"research_task_{task_id}"


def publish_task_update(
    task_id: int | str,
    reason: str,
    payload: dict[str, Any] | None = None,
) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    message = {
        "type": "task_update",
        "task_id": str(task_id),
        "reason": reason,
        "payload": payload or {},
        "timestamp": timezone.now().isoformat(),
    }

    try:
        async_to_sync(channel_layer.group_send)(
            research_task_group(task_id),
            {
                "type": "task_update",
                "message": message,
            },
        )
    except Exception:
        logger.exception("Failed to publish research task realtime update", extra={"task_id": task_id})
