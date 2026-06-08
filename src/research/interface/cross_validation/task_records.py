from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from llm_manager.interface.llm_interface import log_model_usage
from research.interface import research_runtime
from research.interface.thread_codec import json_safe
from research.models import (
    AnalysisResult,
    ResearchConversation,
    ResearchTask,
    SESSION_STATUS_COMPLETED,
    SESSION_STATUS_FAILED,
    SESSION_STATUS_RUNNING,
    STATUS_ANALYZING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SEARCHING,
    TASK_ROLE_CROSS_MODEL,
    TaskStepLog,
)

from .artifacts import _latest_report_payload, _report_paths_from_payloads
from .run_logs import _record_cross_step
from .types import CrossModelSpec


def _create_cross_model_child_tasks(
    parent_task: ResearchTask,
    run_id: str,
    specs: list[CrossModelSpec],
) -> dict[int, ResearchTask]:
    children: dict[int, ResearchTask] = {}
    created_payloads: list[dict[str, Any]] = []
    with transaction.atomic():
        for spec in specs:
            order = int(spec.order or 0)
            child = ResearchTask.objects.create(
                user_id=parent_task.user_id,
                parent_task=parent_task,
                task_role=TASK_ROLE_CROSS_MODEL,
                title=_cross_child_task_title(parent_task, spec),
                object_name=parent_task.object_name,
                object_type=parent_task.object_type,
                llm_config_id=spec.llm_config_id,
                search_params=_cross_child_search_params(parent_task, run_id, spec),
                status=STATUS_SEARCHING,
                progress=_model_child_progress(run_id, "queued", searching=5),
            )
            children[order] = child
            payload = {
                "child_task_id": child.id,
                "model": spec.public_payload(),
                "status": "queued",
            }
            created_payloads.append(payload)
            TaskStepLog.objects.create(
                task=child,
                step_name="交叉验证模型子任务已创建",
                step_status="COMPLETED",
                detail={
                    "cross_validation_run_id": run_id,
                    **json_safe(payload),
                },
            )

    _record_cross_step(
        parent_task.id,
        run_id,
        "创建交叉验证模型子任务",
        "COMPLETED",
        {"child_tasks": created_payloads},
    )
    return children


def _cross_child_task_title(parent_task: ResearchTask, spec: CrossModelSpec) -> str:
    title = f"{parent_task.title} - {spec.model_name} 交叉验证"
    return title[:256]


def _cross_child_search_params(
    parent_task: ResearchTask,
    run_id: str,
    spec: CrossModelSpec,
) -> dict[str, Any]:
    params = dict(parent_task.search_params or {})
    params["auto_advance"] = True
    params["enable_cross_validation"] = True
    params["cross_validation_child"] = {
        "parent_task_id": parent_task.id,
        "cross_validation_run_id": run_id,
        "model": spec.public_payload(),
    }
    return json_safe(params)


def _mark_model_child_running(
    child_task_id: int,
    run_id: str,
    spec: CrossModelSpec,
    thread_id: str,
    prompt: str,
) -> None:
    now = timezone.now()
    child_task = (
        ResearchTask.objects
        .select_related("user")
        .filter(pk=child_task_id)
        .first()
    )
    if child_task is None:
        return
    ResearchConversation.objects.update_or_create(
        task_id=child_task_id,
        defaults={
            "thread_id": thread_id,
            "system_message": research_runtime.build_research_system_message_for_user(child_task.user),
            "status": SESSION_STATUS_RUNNING,
            "latest_user_message": prompt.strip(),
            "last_error": "",
            "run_count": 1,
            "last_started_at": now,
        },
    )
    ResearchTask.objects.filter(pk=child_task_id).update(
        status=STATUS_SEARCHING,
        progress=_model_child_progress(run_id, "running", searching=20, thread_id=thread_id),
        updated_at=now,
    )
    TaskStepLog.objects.create(
        task_id=child_task_id,
        step_name=f"[cross:{spec.model_name}] 模型调研启动",
        step_status="RUNNING",
        detail={
            "cross_validation_run_id": run_id,
            "thread_id": thread_id,
            "model": spec.public_payload(),
        },
    )


def _persist_model_child_success(
    *,
    child_task_id: int,
    run_id: str,
    spec: CrossModelSpec,
    thread_id: str,
    prompt: str,
    final_output: str,
    serialized_history: list[dict[str, Any]],
    state_snapshot: dict[str, Any],
    presented_reports: list[dict[str, Any]],
    latency_ms: float,
) -> dict[str, Any]:
    child_task = (
        ResearchTask.objects
        .select_related("user")
        .filter(pk=child_task_id)
        .first()
    )
    if child_task is None:
        raise RuntimeError(f"交叉验证模型子任务不存在: {child_task_id}")

    report_paths = _report_paths_from_payloads(presented_reports)
    citations = research_runtime._extract_citations(state_snapshot)
    latest_report = _latest_report_payload(presented_reports)
    latest_report_citations = latest_report.get("citations", []) if latest_report else []
    report_citations = (
        [item for item in latest_report_citations if isinstance(item, dict)]
        if isinstance(latest_report_citations, list)
        else []
    )
    brief_output = str(latest_report.get("brief_content") or "").strip() if latest_report else ""
    with transaction.atomic():
        analysis = AnalysisResult.objects.create(
            task=child_task,
            llm_config_id=spec.llm_config_id,
            analysis_type="SINGLE",
            conclusion=research_runtime._extract_summary(final_output),
            raw_output={
                "cross_validation_run_id": run_id,
                "prompt": prompt,
                "final_output": final_output,
                "state_snapshot": state_snapshot,
                "presented_reports": json_safe(presented_reports),
                "report_paths": report_paths,
                "model": spec.public_payload(),
                "latency_ms": latency_ms,
                "skip_auto_report": True,
            },
        )
        report = research_runtime._create_report(
            child_task,
            final_output,
            report_citations or citations,
            brief_output=brief_output,
        )
        ResearchConversation.objects.update_or_create(
            task=child_task,
            defaults={
                "thread_id": thread_id,
                "system_message": research_runtime.build_research_system_message_for_user(child_task.user),
                "status": SESSION_STATUS_COMPLETED,
                "history_messages": serialized_history,
                "state_snapshot": state_snapshot,
                "latest_user_message": prompt.strip(),
                "latest_assistant_message": final_output,
                "last_error": "",
                "run_count": 1,
                "last_finished_at": timezone.now(),
            },
        )
        ResearchTask.objects.filter(pk=child_task_id).update(
            status=STATUS_COMPLETED,
            progress=_model_child_progress(
                run_id,
                "completed",
                searching=100,
                analyzing=100,
                report=100,
                analysis_result_id=analysis.id,
                report_id=report.id,
            ),
            updated_at=timezone.now(),
        )
        TaskStepLog.objects.create(
            task=child_task,
            step_name=f"[cross:{spec.model_name}] 模型调研完成",
            step_status="COMPLETED",
            detail={
                "cross_validation_run_id": run_id,
                "analysis_result_id": analysis.id,
                "report_id": report.id,
                "report_paths": report_paths,
                "latency_ms": latency_ms,
            },
        )

    log_model_usage(
        child_task.user,
        spec.llm_config_id,
        f"research-cross-model-{child_task_id}-{run_id}",
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=int(latency_ms or 0),
        usage_type="GENERAL",
    )
    return {
        "child_analysis_result_id": analysis.id,
        "child_report_id": report.id,
        "child_report_paths": report_paths,
    }


def _mark_model_child_failed(
    child_task_id: int,
    run_id: str,
    spec: CrossModelSpec,
    thread_id: str,
    error: str,
    latency_ms: float,
) -> None:
    now = timezone.now()
    child_task = (
        ResearchTask.objects
        .select_related("user")
        .filter(pk=child_task_id)
        .first()
    )
    if child_task is None:
        return
    ResearchConversation.objects.update_or_create(
        task_id=child_task_id,
        defaults={
            "thread_id": thread_id,
            "system_message": research_runtime.build_research_system_message_for_user(child_task.user),
            "status": SESSION_STATUS_FAILED,
            "last_error": error,
            "run_count": 1,
            "last_finished_at": now,
        },
    )
    ResearchTask.objects.filter(pk=child_task_id).update(
        status=STATUS_FAILED,
        progress=_model_child_progress(
            run_id,
            "failed",
            searching=100,
            analyzing=100,
            report=0,
            thread_id=thread_id,
            error=error,
        ),
        updated_at=now,
    )
    TaskStepLog.objects.create(
        task_id=child_task_id,
        step_name=f"[cross:{spec.model_name}] 模型调研失败",
        step_status="FAILED",
        detail={
            "cross_validation_run_id": run_id,
            "thread_id": thread_id,
            "model": spec.public_payload(),
            "error": error,
            "latency_ms": latency_ms,
        },
    )
    log_model_usage(
        child_task.user,
        spec.llm_config_id,
        f"research-cross-model-{child_task_id}-{run_id}",
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=int(latency_ms or 0),
        usage_type="GENERAL",
    )


def _model_child_progress(
    run_id: str,
    status: str,
    *,
    searching: int = 0,
    analyzing: int = 0,
    report: int = 0,
    **extra: Any,
) -> dict[str, Any]:
    if status == "completed":
        stage = STATUS_COMPLETED
    elif status == "failed":
        stage = STATUS_FAILED
    elif status == "running":
        stage = STATUS_SEARCHING
    elif status not in {"queued", "running", "completed", "failed"}:
        stage = STATUS_ANALYZING
    else:
        stage = STATUS_SEARCHING
    return {
        "searching": searching,
        "analyzing": analyzing,
        "report": report,
        "stage": stage,
        "cross_validation": {
            "run_id": run_id,
            "status": status,
            "updated_at": timezone.now().isoformat(),
            **json_safe(extra),
        },
    }
