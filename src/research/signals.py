"""
Signals that fan out research task changes to WebSocket subscribers.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from research.models import ResearchTask, TaskStepLog
from research.realtime import publish_task_update


@receiver(post_save, sender=ResearchTask, dispatch_uid="research.publish_research_task_saved")
def publish_research_task_saved(sender, instance: ResearchTask, **kwargs) -> None:
    transaction.on_commit(
        lambda: publish_task_update(
            instance.id,
            "task_changed",
            {
                "status": instance.status,
                "progress": instance.progress or {},
            },
        )
    )


@receiver(post_save, sender=TaskStepLog, dispatch_uid="research.publish_task_step_log_saved")
def publish_task_step_log_saved(sender, instance: TaskStepLog, created: bool, **kwargs) -> None:
    transaction.on_commit(
        lambda: publish_task_update(
            instance.task_id,
            "step_log_created" if created else "step_log_updated",
            {
                "step_log_id": str(instance.id),
                "step_name": instance.step_name,
                "step_status": instance.step_status,
            },
        )
    )
