from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter
from typing import Any
from uuid import uuid4

from django.db import close_old_connections

try:
    from efeet import (
        SandboxPaths,
        create_thread,
        resolve_effective_output,
    )
    from efeet.sandbox.copy import copy_thread_sandbox_into_model_dir
except Exception as exc:
    _EFEET_IMPORT_ERROR = exc

    class SandboxPaths:
        def __init__(self, *args, **kwargs):
            raise ModuleNotFoundError(f"efeet runtime is unavailable: {_EFEET_IMPORT_ERROR}")

    def _missing_efeet(*args, **kwargs):
        raise ModuleNotFoundError(f"efeet runtime is unavailable: {_EFEET_IMPORT_ERROR}")

    create_thread = _missing_efeet
    resolve_effective_output = _missing_efeet
    copy_thread_sandbox_into_model_dir = _missing_efeet
else:
    _EFEET_IMPORT_ERROR = None
from research.interface import research_runtime
from research.interface.thread_codec import json_safe, serialize_history, serialize_state
from research.models import (
    ResearchTask,
    SESSION_STATUS_RUNNING,
    STATUS_ANALYZING,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SEARCHING,
    TaskStepLog,
)

from .artifacts import (
    _ensure_report_payloads,
    _is_llm_failure_output,
    _report_paths_from_payloads,
    _strip_tool_call_markup,
)
from .model_specs import (
    _coerce_model_id_list,
    _create_model,
    resolve_cross_model_specs,
    resolve_integrator_model_spec,
)
from .prompts import (
    build_cross_validation_base_prompt,
    build_cross_integrator_prompt,
    build_cross_integrator_system_message,
)
from .result_records import _persist_cross_success
from .run_logs import (
    _conversation_status,
    _mark_cross_failed,
    _record_cross_event,
    _record_cross_event_for_task,
    _record_cross_step,
    _update_cross_log,
    _update_cross_progress,
)
from .task_records import (
    _create_cross_model_child_tasks,
    _mark_model_child_failed,
    _mark_model_child_running,
    _persist_model_child_success,
)
from .types import (
    CROSS_VALIDATION_DEFAULT_WORKERS,
    CROSS_VALIDATION_STEP_NAME,
    CrossModelSpec,
)


_CROSS_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="research-cross-validation",
)


def enqueue_cross_validation_run(
    task_id: int,
    *,
    requested_model_ids: Any = None,
    integrator_model_id: Any = None,
    prompt: str | None = None,
    run_metadata: dict[str, Any] | None = None,
    allow_env_models: bool = False,
    allow_incomplete_parent: bool = False,
) -> tuple[bool, str | None, str | None]:
    """Start a multi-model cross-validation run for an existing research task."""
    task = (
        ResearchTask.objects
        .select_related("user", "llm_config", "conversation")
        .filter(pk=task_id)
        .first()
    )
    if task is None:
        return (False, "任务不存在", None)
    if task.parent_task_id is not None:
        return (False, "多模型交叉验证只能在主任务上启动", None)
    if task.status in {STATUS_CANCELLED, STATUS_FAILED}:
        return (False, "任务状态不允许启动多模型交叉验证", None)
    if task.status != STATUS_COMPLETED and not allow_incomplete_parent:
        return (False, "主任务完成后才能启动多模型交叉验证", None)
    if _conversation_status(task) == SESSION_STATUS_RUNNING:
        return (False, "主任务会话仍在运行，请等待调研完成后再启动多模型交叉验证", None)
    if TaskStepLog.objects.filter(
        task=task,
        step_name=CROSS_VALIDATION_STEP_NAME,
        step_status="RUNNING",
    ).exists():
        return (False, "多模型交叉验证正在运行，请稍后再试", None)

    try:
        specs = resolve_cross_model_specs(
            task,
            requested_model_ids,
            allow_env_models=allow_env_models,
        )
        integrator_spec = resolve_integrator_model_spec(
            task,
            integrator_model_id,
            specs,
            allow_env_models=allow_env_models,
        )
    except ValueError as exc:
        return (False, str(exc), None)

    if len(specs) < 2:
        return (False, "多模型交叉验证至少需要 2 个模型", None)

    run_id = str(uuid4())
    _force_cross_validation_auto_advance(task)
    effective_prompt = (prompt or "").strip() or build_cross_validation_base_prompt(task)
    metadata = dict(run_metadata or {})
    metadata.update(
        {
            "cross_validation_run_id": run_id,
            "requested_model_ids": _coerce_model_id_list(requested_model_ids),
            "integrator_model_id": str(integrator_model_id or ""),
        }
    )

    TaskStepLog.objects.create(
        task=task,
        step_name=CROSS_VALIDATION_STEP_NAME,
        step_status="RUNNING",
        detail={
            "cross_validation_run_id": run_id,
            "status": "queued",
            "model_count": len(specs),
            "models": [spec.public_payload() for spec in specs],
            "integrator_model": integrator_spec.public_payload(),
            "run_metadata": json_safe(metadata),
        },
    )
    _update_cross_progress(task, run_id, "queued", model_count=len(specs))

    try:
        _CROSS_EXECUTOR.submit(
            _run_cross_validation,
            task.id,
            run_id,
            effective_prompt,
            specs,
            integrator_spec,
            metadata,
        )
    except Exception as exc:
        _mark_cross_failed(task, run_id, f"提交后台交叉验证失败: {exc}")
        return (False, f"提交后台交叉验证失败: {exc}", run_id)

    return (True, None, run_id)


def _force_cross_validation_auto_advance(task: ResearchTask) -> None:
    params = dict(task.search_params or {})
    params["auto_advance"] = True
    params["enable_cross_validation"] = True
    ResearchTask.objects.filter(pk=task.id).update(search_params=json_safe(params))
    task.search_params = params


def _run_cross_validation(
    task_id: int,
    run_id: str,
    prompt: str,
    specs: list[CrossModelSpec],
    integrator_spec: CrossModelSpec,
    run_metadata: dict[str, Any],
) -> None:
    close_old_connections()
    started_at = perf_counter()
    try:
        task = (
            ResearchTask.objects
            .select_related("user", "llm_config")
            .get(pk=task_id)
        )
        ResearchTask.objects.filter(pk=task.id).update(status=STATUS_SEARCHING)
        task.status = STATUS_SEARCHING
        _update_cross_log(task, run_id, status="running")
        _update_cross_progress(task, run_id, "running", model_count=len(specs))

        sandbox_paths = research_runtime._resolve_sandbox_paths(task.search_params) or SandboxPaths()
        task_payload = _task_prompt_payload(task)
        child_tasks = _create_cross_model_child_tasks(task, run_id, specs)
        worker_count = min(len(specs), _cross_worker_count(task.search_params))
        model_results: list[dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="research-cross-model") as executor:
            futures = [
                executor.submit(
                    _run_single_model_thread,
                    task.id,
                    child_tasks[int(spec.order or 0)].id,
                    run_id,
                    task_payload,
                    spec,
                    sandbox_paths,
                )
                for spec in specs
            ]
            for future in as_completed(futures):
                model_results.append(future.result())

        model_results.sort(key=lambda item: int(item.get("order", 0) or 0))
        successful_results = [item for item in model_results if item.get("status") == "completed"]
        if not successful_results:
            raise RuntimeError("所有模型调研线程均失败，无法进行智能整合")

        integrator_result, effective_integrator_spec = _run_integrator_with_fallbacks(
            task_id=task.id,
            run_id=run_id,
            task_payload=task_payload,
            requested_integrator_spec=integrator_spec,
            model_specs=specs,
            successful_results=successful_results,
            model_results=model_results,
            sandbox_paths=sandbox_paths,
        )
        latency_ms = round((perf_counter() - started_at) * 1000, 2)
        ResearchTask.objects.filter(pk=task.id).update(status=STATUS_ANALYZING)
        task.status = STATUS_ANALYZING
        _persist_cross_success(
            task=task,
            run_id=run_id,
            prompt=prompt,
            model_results=model_results,
            integrator_result=integrator_result,
            integrator_spec=effective_integrator_spec,
            latency_ms=latency_ms,
            run_metadata=run_metadata,
        )
    except Exception as exc:
        task = ResearchTask.objects.filter(pk=task_id).first()
        if task is not None:
            ResearchTask.objects.filter(pk=task.id).update(status=STATUS_FAILED)
            _mark_cross_failed(task, run_id, str(exc))
    finally:
        close_old_connections()


def _run_single_model_thread(
    task_id: int,
    child_task_id: int,
    run_id: str,
    task_payload: dict[str, Any],
    spec: CrossModelSpec,
    sandbox_paths: SandboxPaths,
) -> dict[str, Any]:
    close_old_connections()
    started_at = perf_counter()
    thread_id = str(uuid4())
    order = int(spec.order or task_payload.get("model_order", {}).get(spec.model_key, 0) or 0)
    try:
        child_task = (
            ResearchTask.objects
            .select_related("user")
            .get(pk=child_task_id)
        )
        prompt = research_runtime.build_initial_prompt(child_task)
        _mark_model_child_running(child_task_id, run_id, spec, thread_id, prompt)
        _record_cross_step(
            task_id,
            run_id,
            f"[cross:{spec.model_name}] 启动模型调研线程",
            "RUNNING",
            {
                "thread_id": thread_id,
                "model": spec.public_payload(),
            },
        )
        thread = create_thread(
            _create_model(spec),
            system_message=research_runtime.build_research_system_message_for_user(child_task.user),
            sandbox_paths=sandbox_paths,
            thread_id=thread_id,
            include_task=True,
            include_step_approval=True,
        )
        thread.max_turns = _cross_model_max_turns(task_payload.get("search_params"))
        final_output = _stream_cross_model_thread(
            thread=thread,
            prompt=prompt,
            child_task_id=child_task_id,
            group_task_id=task_id,
            run_id=run_id,
            actor=f"model:{spec.model_name}",
        )

        serialized_history = serialize_history(thread.history)
        state_snapshot = serialize_state(thread.state)
        final_output = resolve_effective_output(
            final_output,
            state=state_snapshot,
            serialized_history=serialized_history,
            create_report=True,
        )
        final_output = _strip_tool_call_markup(final_output)
        if _is_llm_failure_output(final_output):
            raise RuntimeError(final_output)
        presented_reports = _ensure_report_payloads(
            sandbox_paths=sandbox_paths,
            thread_id=thread_id,
            state_snapshot=state_snapshot,
            final_output=final_output,
            fallback_filename="model_research_report.md",
            fallback_brief_filename="model_research_report_brief.md",
        )
        if not _report_paths_from_payloads(presented_reports):
            raise RuntimeError("模型调研线程未产出报告文件")
        latency_ms = round((perf_counter() - started_at) * 1000, 2)
        child_persisted = _persist_model_child_success(
            child_task_id=child_task_id,
            run_id=run_id,
            spec=spec,
            thread_id=thread_id,
            prompt=prompt,
            final_output=final_output,
            serialized_history=serialized_history,
            state_snapshot=state_snapshot,
            presented_reports=presented_reports,
            latency_ms=latency_ms,
        )
        _record_cross_step(
            task_id,
            run_id,
            f"[cross:{spec.model_name}] 模型调研完成",
            "COMPLETED",
            {
                "thread_id": thread_id,
                "child_task_id": child_task_id,
                **child_persisted,
                "model": spec.public_payload(),
                "report_paths": _report_paths_from_payloads(presented_reports),
                "latency_ms": latency_ms,
            },
        )
        return {
            "order": order,
            "status": "completed",
            "thread_id": thread_id,
            "child_task_id": child_task_id,
            **child_persisted,
            "model": spec.public_payload(),
            "final_output": final_output,
            "summary": research_runtime._extract_summary(final_output),
            "state_snapshot": state_snapshot,
            "presented_reports": presented_reports,
            "report_paths": _report_paths_from_payloads(presented_reports),
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        latency_ms = round((perf_counter() - started_at) * 1000, 2)
        error = str(exc)
        _mark_model_child_failed(child_task_id, run_id, spec, thread_id, error, latency_ms)
        _record_cross_step(
            task_id,
            run_id,
            f"[cross:{spec.model_name}] 模型调研失败",
            "FAILED",
            {
                "thread_id": thread_id,
                "child_task_id": child_task_id,
                "model": spec.public_payload(),
                "error": error,
                "latency_ms": latency_ms,
            },
        )
        return {
            "order": order,
            "status": "failed",
            "thread_id": thread_id,
            "child_task_id": child_task_id,
            "model": spec.public_payload(),
            "final_output": "",
            "summary": "",
            "state_snapshot": {},
            "presented_reports": [],
            "report_paths": [],
            "error": error,
            "latency_ms": latency_ms,
        }
    finally:
        close_old_connections()


def _stream_cross_model_thread(
    *,
    thread,
    prompt: str,
    child_task_id: int,
    group_task_id: int,
    run_id: str,
    actor: str,
) -> str:
    current_prompt = prompt
    auto_approval_count = 0
    while True:
        output_chunks: list[str] = []
        pending_approval_event: dict[str, Any] | None = None

        def on_event(event: dict[str, Any]) -> None:
            nonlocal pending_approval_event
            if research_runtime._is_step_approval_event(event):
                pending_approval_event = dict(event)
                return
            _record_cross_event_for_task(
                child_task_id,
                run_id,
                actor,
                event,
                group_task_id=group_task_id,
            )

        for chunk in thread.stream(current_prompt, on_event=on_event):
            output_chunks.append(chunk)

        if pending_approval_event is None:
            return "".join(output_chunks).strip()

        auto_approval_count += 1
        if auto_approval_count > 20:
            raise RuntimeError("自动推进审批次数过多，已停止以避免循环")
        research_runtime._remove_step_approval_tool_message(thread)
        research_runtime._append_auto_approval_tool_message(
            thread,
            pending_approval_event,
            research_runtime.STEP_APPROVAL_ACCEPTED,
        )
        research_runtime._record_auto_step_approval(child_task_id, 1, pending_approval_event)
        current_prompt = research_runtime._approval_response_prompt(
            research_runtime.STEP_APPROVAL_ACCEPTED
        )


def _run_integrator_thread(
    task_id: int,
    run_id: str,
    task_payload: dict[str, Any],
    spec: CrossModelSpec,
    successful_results: list[dict[str, Any]],
    all_results: list[dict[str, Any]],
    sandbox_paths: SandboxPaths,
) -> dict[str, Any]:
    started_at = perf_counter()
    thread_id = str(uuid4())
    copy_manifests: list[dict[str, Any]] = []
    for result in successful_results:
        manifest = copy_thread_sandbox_into_model_dir(
            paths=sandbox_paths,
            source_thread_id=str(result["thread_id"]),
            target_thread_id=thread_id,
            model_name=str(result.get("model", {}).get("model_name") or "model"),
            directory_name=_cross_model_input_directory(result),
            report_paths=list(result.get("report_paths") or []),
        )
        manifest.pop("thread_data", None)
        copy_manifests.append(manifest)

    _record_cross_step(
        task_id,
        run_id,
        "[cross:integrator] 启动智能整合线程",
        "RUNNING",
        {
            "thread_id": thread_id,
            "model": spec.public_payload(),
            "input_count": len(copy_manifests),
            "copy_manifests": json_safe(copy_manifests),
        },
    )

    thread = create_thread(
        _create_model(spec),
        system_message=build_cross_integrator_system_message(),
        sandbox_paths=sandbox_paths,
        thread_id=thread_id,
        include_task=False,
        include_step_approval=False,
    )
    thread.max_turns = _cross_integrator_max_turns(task_payload.get("search_params"))
    output_chunks: list[str] = []

    def on_event(event: dict[str, Any]) -> None:
        _record_cross_event(task_id, run_id, "integrator", event)

    prompt = build_cross_integrator_prompt(task_payload, all_results, copy_manifests)
    for chunk in thread.stream(prompt, on_event=on_event):
        output_chunks.append(chunk)

    serialized_history = serialize_history(thread.history)
    state_snapshot = serialize_state(thread.state)
    final_output = resolve_effective_output(
        "".join(output_chunks).strip(),
        state=state_snapshot,
        serialized_history=serialized_history,
        create_report=True,
    )
    final_output = _strip_tool_call_markup(final_output)
    if _is_llm_failure_output(final_output):
        raise RuntimeError(final_output)
    presented_reports = _ensure_report_payloads(
        sandbox_paths=sandbox_paths,
        thread_id=thread_id,
        state_snapshot=state_snapshot,
        final_output=final_output,
        fallback_filename="cross_validation_report.md",
        fallback_brief_filename="cross_validation_report_brief.md",
    )
    if not _report_paths_from_payloads(presented_reports):
        raise RuntimeError("智能整合线程未产出报告文件")
    latency_ms = round((perf_counter() - started_at) * 1000, 2)
    _record_cross_step(
        task_id,
        run_id,
        "[cross:integrator] 智能整合完成",
        "COMPLETED",
        {
            "thread_id": thread_id,
            "model": spec.public_payload(),
            "report_paths": _report_paths_from_payloads(presented_reports),
            "latency_ms": latency_ms,
        },
    )
    return {
        "status": "completed",
        "thread_id": thread_id,
        "model": spec.public_payload(),
        "copy_manifests": copy_manifests,
        "final_output": final_output,
        "summary": research_runtime._extract_summary(final_output),
        "state_snapshot": state_snapshot,
        "presented_reports": presented_reports,
        "report_paths": _report_paths_from_payloads(presented_reports),
        "latency_ms": latency_ms,
    }


def _run_integrator_with_fallbacks(
    *,
    task_id: int,
    run_id: str,
    task_payload: dict[str, Any],
    requested_integrator_spec: CrossModelSpec,
    model_specs: list[CrossModelSpec],
    successful_results: list[dict[str, Any]],
    model_results: list[dict[str, Any]],
    sandbox_paths: SandboxPaths,
) -> tuple[dict[str, Any], CrossModelSpec]:
    candidates = _integrator_candidates(requested_integrator_spec, model_specs, successful_results)
    errors: list[str] = []
    for candidate in candidates:
        try:
            return (
                _run_integrator_thread(
                    task_id,
                    run_id,
                    task_payload,
                    candidate,
                    successful_results,
                    model_results,
                    sandbox_paths,
                ),
                candidate,
            )
        except Exception as exc:
            error = str(exc)
            errors.append(f"{candidate.model_name}: {error}")
            _record_cross_step(
                task_id,
                run_id,
                f"[cross:integrator] 智能整合失败: {candidate.model_name}",
                "FAILED",
                {
                    "model": candidate.public_payload(),
                    "error": error,
                },
            )
    raise RuntimeError("智能整合线程全部失败: " + " | ".join(errors))


def _integrator_candidates(
    requested_integrator_spec: CrossModelSpec,
    model_specs: list[CrossModelSpec],
    successful_results: list[dict[str, Any]],
) -> list[CrossModelSpec]:
    candidates = [requested_integrator_spec]
    successful_names = {
        str(item.get("model", {}).get("model_name") or "").strip()
        for item in successful_results
        if isinstance(item.get("model"), dict)
    }
    for spec in model_specs:
        if spec.model_name not in successful_names:
            continue
        if any(
            existing.provider == spec.provider and existing.model_name == spec.model_name
            for existing in candidates
        ):
            continue
        candidates.append(spec)
    return candidates


def _cross_model_input_directory(result: dict[str, Any]) -> str:
    model = result.get("model") if isinstance(result.get("model"), dict) else {}
    parts = [
        f"{int(result.get('order') or 0):02d}",
        str(model.get("provider") or "model"),
        str(model.get("llm_config_id") or model.get("model_key") or model.get("model_id") or "runtime"),
        str(result.get("child_task_id") or "task"),
        str(model.get("model_name") or "model"),
    ]
    return "-".join(part for part in parts if part)


def _cross_worker_count(search_params: dict[str, Any] | None) -> int:
    params = search_params or {}
    cross_params = params.get("cross_validation") if isinstance(params.get("cross_validation"), dict) else {}
    raw = cross_params.get("max_workers", params.get("cross_validation_max_workers", CROSS_VALIDATION_DEFAULT_WORKERS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = CROSS_VALIDATION_DEFAULT_WORKERS
    return min(max(value, 1), CROSS_VALIDATION_DEFAULT_WORKERS)


def _cross_model_max_turns(search_params: dict[str, Any] | None) -> int:
    params = search_params or {}
    raw = params.get("cross_model_max_turns")
    if raw is None and isinstance(params.get("cross_validation"), dict):
        raw = params["cross_validation"].get("model_max_turns")
    if raw is None:
        return research_runtime._resolve_max_turns(params)
    return _bounded_turns(raw, default=research_runtime._resolve_max_turns(params))


def _cross_integrator_max_turns(search_params: dict[str, Any] | None) -> int:
    params = search_params or {}
    raw = params.get("cross_integrator_max_turns")
    if raw is None and isinstance(params.get("cross_validation"), dict):
        raw = params["cross_validation"].get("integrator_max_turns")
    return _bounded_turns(raw, default=10)


def _bounded_turns(raw: Any, *, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return min(max(value, 6), 60)


def _task_prompt_payload(task: ResearchTask) -> dict[str, Any]:
    specs = _coerce_model_id_list((task.search_params or {}).get("multi_model_ids"))
    return {
        "title": task.title,
        "object_name": task.object_name,
        "object_type": task.object_type,
        "search_params": task.search_params or {},
        "model_order": {str(item): index for index, item in enumerate(specs, start=1)},
    }
