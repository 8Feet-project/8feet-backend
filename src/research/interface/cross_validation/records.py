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

from .artifacts import _report_paths_from_payloads
from .types import CROSS_VALIDATION_STEP_NAME, CrossModelSpec


def get_cross_validation_payload(task: ResearchTask) -> dict[str, Any]:
    latest = (
        AnalysisResult.objects
        .filter(task_id=task.id, analysis_type="CROSS")
        .order_by("-created_at")
        .first()
    )
    latest_run = _latest_cross_log(task)

    payload = _empty_cross_payload(task)
    if latest is not None:
        payload.update(_payload_from_result(task, latest))

    if latest_run is not None:
        detail = latest_run.detail if isinstance(latest_run.detail, dict) else {}
        log_status = str(detail.get("status") or "").strip().lower()
        if latest_run.step_status == "RUNNING":
            payload.update(
                {
                    "status": log_status or "running",
                    "run_id": detail.get("cross_validation_run_id"),
                    "updated_at": latest_run.created_at.isoformat(),
                }
            )
        elif latest_run.step_status == "FAILED" and (
            latest is None or latest_run.created_at > latest.created_at
        ):
            payload.update(
                {
                    "status": "failed",
                    "run_id": detail.get("cross_validation_run_id"),
                    "error": detail.get("error") or "",
                    "updated_at": latest_run.created_at.isoformat(),
                }
            )
    return payload


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
    ResearchConversation.objects.update_or_create(
        task_id=child_task_id,
        defaults={
            "thread_id": thread_id,
            "system_message": research_runtime.build_research_system_message(),
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
            },
        )
        report = research_runtime._create_report(child_task, final_output, citations)
        ResearchConversation.objects.update_or_create(
            task=child_task,
            defaults={
                "thread_id": thread_id,
                "system_message": research_runtime.build_research_system_message(),
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
            "system_message": research_runtime.build_research_system_message(),
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


def _persist_cross_success(
    *,
    task: ResearchTask,
    run_id: str,
    prompt: str,
    model_results: list[dict[str, Any]],
    integrator_result: dict[str, Any],
    integrator_spec: CrossModelSpec,
    latency_ms: float,
    run_metadata: dict[str, Any],
) -> None:
    final_output = str(integrator_result.get("final_output") or "").strip()
    citations = research_runtime._extract_citations(integrator_result.get("state_snapshot") or {})
    with transaction.atomic():
        analysis = AnalysisResult.objects.create(
            task=task,
            llm_config_id=integrator_spec.llm_config_id,
            analysis_type="CROSS",
            conclusion=research_runtime._extract_summary(final_output),
            raw_output={
                "cross_validation_run_id": run_id,
                "prompt": prompt,
                "model_outputs": json_safe(model_results),
                "integrator": json_safe(integrator_result),
                "used_models": [item.get("model") for item in model_results],
                "latency_ms": latency_ms,
                "run_metadata": json_safe(run_metadata),
            },
        )
        if final_output:
            research_runtime._create_report(task, final_output, citations)

        _update_cross_log(
            task,
            run_id,
            status="completed",
            step_status="COMPLETED",
            analysis_result_id=analysis.id,
            integrator_report_paths=integrator_result.get("report_paths", []),
            latency_ms=latency_ms,
        )
        _update_cross_progress(
            task,
            run_id,
            "completed",
            model_count=len(model_results),
            result_id=analysis.id,
        )
        TaskStepLog.objects.create(
            task=task,
            step_name="多模型交叉验证完成",
            step_status="COMPLETED",
            detail={
                "cross_validation_run_id": run_id,
                "analysis_result_id": analysis.id,
                "message": final_output[:1000],
            },
        )

    log_model_usage(
        task.user,
        integrator_spec.llm_config_id,
        f"research-cross-{task.id}-{run_id}",
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=int(latency_ms or 0),
        usage_type="GENERAL",
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


def _empty_cross_payload(task: ResearchTask) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "status": "queued",
        "run_id": None,
        "result_id": None,
        "consensus_points": [],
        "difference_points": [],
        "model_outputs": [],
        "used_models": [],
        "consensus_summary": "",
        "consensus_score": 0,
        "report_path": "",
        "report_content": "",
        "error": "",
        "updated_at": task.updated_at.isoformat(),
    }


def _payload_from_result(task: ResearchTask, result: AnalysisResult) -> dict[str, Any]:
    raw = result.raw_output if isinstance(result.raw_output, dict) else {}
    integrator = raw.get("integrator") if isinstance(raw.get("integrator"), dict) else {}
    final_report = str(integrator.get("final_output") or "")
    model_outputs = [
        _public_model_output(item)
        for item in raw.get("model_outputs", [])
        if isinstance(item, dict)
    ]
    return {
        "task_id": str(task.id),
        "status": "completed",
        "run_id": raw.get("cross_validation_run_id"),
        "result_id": str(result.id),
        "consensus_points": _section_bullets(final_report, ("共识", "一致", "核心结论")),
        "difference_points": _section_bullets(final_report, ("分歧", "差异", "冲突")),
        "model_outputs": model_outputs,
        "used_models": _public_used_models(raw.get("used_models", []), model_outputs),
        "consensus_summary": result.conclusion,
        "consensus_score": _consensus_score(model_outputs),
        "report_path": _last_report_path(integrator),
        "report_content": final_report,
        "error": "",
        "updated_at": result.created_at.isoformat(),
    }


def _public_model_output(item: dict[str, Any]) -> dict[str, Any]:
    model = item.get("model") if isinstance(item.get("model"), dict) else {}
    model_id = str(model.get("model_name") or model.get("model_id") or item.get("thread_id") or "")
    return {
        "model_id": model_id,
        "status": item.get("status"),
        "model": model,
        "thread_id": item.get("thread_id"),
        "child_task_id": item.get("child_task_id"),
        "child_report_id": item.get("child_report_id"),
        "summary": item.get("summary"),
        "report_paths": item.get("report_paths", []),
        "presented_reports": [
            {
                "path": report.get("path"),
                "citation_keys": report.get("citation_keys", []),
                "generated_reference_count": report.get("generated_reference_count", 0),
            }
            for report in item.get("presented_reports", [])
            if isinstance(report, dict)
        ],
        "error": item.get("error", ""),
        "latency_ms": item.get("latency_ms", 0),
    }


def _public_used_models(raw_used_models: Any, model_outputs: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    if isinstance(raw_used_models, list):
        for item in raw_used_models:
            if isinstance(item, dict):
                value = item.get("model_name") or item.get("model_id")
            else:
                value = item
            text = str(value or "").strip()
            if text and text not in names:
                names.append(text)
    for item in model_outputs:
        text = str(item.get("model_id") or "").strip()
        if text and text not in names:
            names.append(text)
    return names


def _section_bullets(markdown_text: str, headings: tuple[str, ...]) -> list[str]:
    lines = (markdown_text or "").splitlines()
    capture = False
    bullets: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            capture = any(keyword in heading for keyword in headings)
            continue
        if not capture:
            continue
        if stripped.startswith(("-", "*")):
            bullets.append(stripped.lstrip("-* ").strip())
        elif re_match := _numbered_bullet(stripped):
            bullets.append(re_match)
        elif stripped.startswith("#"):
            capture = False
        if len(bullets) >= 12:
            break
    return [item for item in bullets if item]


def _numbered_bullet(line: str) -> str:
    parts = line.split(".", 1)
    if len(parts) == 2 and parts[0].isdigit():
        return parts[1].strip()
    return ""


def _consensus_score(model_outputs: list[dict[str, Any]]) -> int:
    total = len(model_outputs)
    if not total:
        return 0
    completed = sum(1 for item in model_outputs if item.get("status") == "completed")
    return round(completed / total * 100)


def _last_report_path(integrator: dict[str, Any]) -> str:
    paths = integrator.get("report_paths")
    if isinstance(paths, list) and paths:
        return str(paths[-1] or "")
    return ""
