from __future__ import annotations

from typing import Any

from django.utils import timezone

from research.interface import research_runtime
from research.interface.thread_codec import json_safe
from research.models import ResearchTask, TaskStepLog

from .types import CROSS_VALIDATION_STEP_NAME


def _record_cross_event(task_id: int, run_id: str, actor: str, event: dict[str, Any]) -> None:
    step_name, step_status, detail = research_runtime._event_to_step(event, 1)
    detail = dict(detail or {})
    detail.update(
        {
            "cross_validation_run_id": run_id,
            "actor": actor,
            "event": json_safe(event),
        }
    )
    _record_cross_step(
        task_id,
        run_id,
        f"[cross:{actor}] {step_name}",
        step_status,
        detail,
    )


def _record_cross_step(
    task_id: int,
    run_id: str,
    step_name: str,
    step_status: str,
    detail: dict[str, Any],
) -> None:
    TaskStepLog.objects.create(
        task_id=task_id,
        step_name=step_name[:256],
        step_status=step_status,
        detail={
            "cross_validation_run_id": run_id,
            **json_safe(detail),
        },
    )


def _mark_cross_failed(task: ResearchTask, run_id: str, error: str) -> None:
    _update_cross_log(task, run_id, status="failed", step_status="FAILED", error=error)
    _update_cross_progress(task, run_id, "failed", error=error)
    TaskStepLog.objects.create(
        task=task,
        step_name="多模型交叉验证失败",
        step_status="FAILED",
        detail={
            "cross_validation_run_id": run_id,
            "error": error,
        },
    )


def _update_cross_log(
    task: ResearchTask,
    run_id: str,
    *,
    status: str,
    step_status: str | None = None,
    **extra: Any,
) -> None:
    log = (
        TaskStepLog.objects
        .filter(
            task=task,
            step_name=CROSS_VALIDATION_STEP_NAME,
            detail__cross_validation_run_id=run_id,
        )
        .order_by("-id")
        .first()
    )
    if log is None:
        return
    detail = dict(log.detail or {})
    detail.update({"status": status, **json_safe(extra)})
    log.detail = detail
    if step_status:
        log.step_status = step_status
        log.save(update_fields=["detail", "step_status"])
    else:
        log.save(update_fields=["detail"])


def _update_cross_progress(
    task: ResearchTask,
    run_id: str,
    status: str,
    **extra: Any,
) -> None:
    progress = dict(task.progress or {})
    progress["cross_validation"] = {
        "run_id": run_id,
        "status": status,
        "updated_at": timezone.now().isoformat(),
        **json_safe(extra),
    }
    ResearchTask.objects.filter(pk=task.id).update(progress=progress, updated_at=timezone.now())


def _latest_cross_log(task: ResearchTask) -> TaskStepLog | None:
    return (
        TaskStepLog.objects
        .filter(task=task, step_name=CROSS_VALIDATION_STEP_NAME)
        .order_by("-created_at", "-id")
        .first()
    )


def _conversation_status(task: ResearchTask) -> str:
    try:
        conversation = getattr(task, "conversation", None)
    except Exception:
        return ""
    return str(getattr(conversation, "status", "") or "")
