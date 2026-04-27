from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
import json
import os
from pathlib import PurePosixPath
from time import perf_counter
from typing import Any
from uuid import uuid4

from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from efeet import (
    SandboxPaths,
    create_chat_model,
    create_thread,
    extract_presented_reports,
    resolve_effective_output,
)
from efeet.sandbox.copy import copy_thread_sandbox_into_model_dir
from efeet.sandbox.paths import OUTPUTS_VIRTUAL_PATH
from llm_manager.interface.llm_interface import (
    get_provider_runtime_config,
    log_model_usage,
    resolve_user_model_config,
    user_can_use_model,
)
from llm_manager.models.llm_config import LLMConfig
from research.interface import research_runtime
from research.interface.thread_codec import json_safe, serialize_history, serialize_state
from research.models import (
    AnalysisResult,
    ResearchTask,
    STATUS_CANCELLED,
    STATUS_FAILED,
    TaskStepLog,
)

CROSS_VALIDATION_STEP_NAME = "多模型交叉验证"
CROSS_VALIDATION_DEFAULT_WORKERS = 4
_CROSS_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="research-cross-validation",
)


@dataclass(frozen=True, slots=True)
class CrossModelSpec:
    model_key: str
    model_name: str
    provider: str
    runtime_config: dict[str, Any]
    llm_config_id: int | None = None
    order: int = 0

    def public_payload(self) -> dict[str, Any]:
        return {
            "model_key": self.model_key,
            "model_id": str(self.llm_config_id) if self.llm_config_id else self.model_key,
            "model_name": self.model_name,
            "provider": self.provider,
            "llm_config_id": self.llm_config_id,
            "order": self.order,
        }


def enqueue_cross_validation_run(
    task_id: int,
    *,
    requested_model_ids: Any = None,
    integrator_model_id: Any = None,
    prompt: str | None = None,
    run_metadata: dict[str, Any] | None = None,
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
    if task.status in {STATUS_CANCELLED, STATUS_FAILED}:
        return (False, "任务状态不允许启动多模型交叉验证", None)
    if TaskStepLog.objects.filter(
        task=task,
        step_name=CROSS_VALIDATION_STEP_NAME,
        step_status="RUNNING",
    ).exists():
        return (False, "多模型交叉验证正在运行，请稍后再试", None)

    try:
        specs = resolve_cross_model_specs(task, requested_model_ids)
        integrator_spec = resolve_integrator_model_spec(task, integrator_model_id, specs)
    except ValueError as exc:
        return (False, str(exc), None)

    if len(specs) < 2:
        return (False, "多模型交叉验证至少需要 2 个模型", None)

    run_id = str(uuid4())
    effective_prompt = (prompt or "").strip() or research_runtime.build_initial_prompt(task)
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


def resolve_cross_model_specs(task: ResearchTask, requested_model_ids: Any = None) -> list[CrossModelSpec]:
    raw_items = _coerce_model_id_list(requested_model_ids)
    if not raw_items:
        raw_items = _coerce_model_id_list((task.search_params or {}).get("multi_model_ids"))
    if not raw_items:
        cross_params = (task.search_params or {}).get("cross_validation")
        if isinstance(cross_params, dict):
            raw_items = _coerce_model_id_list(cross_params.get("model_ids") or cross_params.get("models"))

    specs: list[CrossModelSpec] = []
    seen: set[str] = set()
    for item in raw_items:
        spec = _resolve_model_spec(task, item)
        dedupe_key = f"{spec.provider}:{spec.model_name}".lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        specs.append(replace(spec, order=len(specs) + 1))
    return specs


def resolve_integrator_model_spec(
    task: ResearchTask,
    integrator_model_id: Any,
    model_specs: list[CrossModelSpec],
) -> CrossModelSpec:
    raw = str(integrator_model_id or "").strip()
    if raw:
        return _resolve_model_spec(task, raw)
    cross_params = (task.search_params or {}).get("cross_validation")
    if isinstance(cross_params, dict):
        raw = str(cross_params.get("integrator_model_id") or cross_params.get("integrator_model") or "").strip()
        if raw:
            return _resolve_model_spec(task, raw)
    if task.llm_config_id:
        return _resolve_model_spec(task, str(task.llm_config_id))
    if model_specs:
        return model_specs[0]
    env_model = _env_first("EFEET_MODEL_NAME", "MODEL_NAME")
    if env_model:
        return _resolve_env_model_spec(env_model)
    raise ValueError("未找到可用于智能整合的模型配置")


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
        _update_cross_log(task, run_id, status="running")
        _update_cross_progress(task, run_id, "running", model_count=len(specs))

        sandbox_paths = research_runtime._resolve_sandbox_paths(task.search_params) or SandboxPaths()
        task_payload = _task_prompt_payload(task)
        model_prompt = build_cross_model_research_prompt(task, prompt)
        worker_count = min(len(specs), _cross_worker_count(task.search_params))
        model_results: list[dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="research-cross-model") as executor:
            futures = [
                executor.submit(
                    _run_single_model_thread,
                    task.id,
                    run_id,
                    task_payload,
                    spec,
                    model_prompt,
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
            _mark_cross_failed(task, run_id, str(exc))
    finally:
        close_old_connections()


def _run_single_model_thread(
    task_id: int,
    run_id: str,
    task_payload: dict[str, Any],
    spec: CrossModelSpec,
    prompt: str,
    sandbox_paths: SandboxPaths,
) -> dict[str, Any]:
    close_old_connections()
    started_at = perf_counter()
    thread_id = str(uuid4())
    order = int(spec.order or task_payload.get("model_order", {}).get(spec.model_key, 0) or 0)
    try:
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
            system_message=research_runtime.build_research_system_message(),
            sandbox_paths=sandbox_paths,
            thread_id=thread_id,
        )
        thread.max_turns = _cross_model_max_turns(task_payload.get("search_params"))
        output_chunks: list[str] = []

        def on_event(event: dict[str, Any]) -> None:
            _record_cross_event(task_id, run_id, f"model:{spec.model_name}", event)

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
            fallback_filename="model_research_report.md",
        )
        if not _report_paths_from_payloads(presented_reports):
            raise RuntimeError("模型调研线程未产出报告文件")
        latency_ms = round((perf_counter() - started_at) * 1000, 2)
        _record_cross_step(
            task_id,
            run_id,
            f"[cross:{spec.model_name}] 模型调研完成",
            "COMPLETED",
            {
                "thread_id": thread_id,
                "model": spec.public_payload(),
                "report_paths": _report_paths_from_payloads(presented_reports),
                "latency_ms": latency_ms,
            },
        )
        return {
            "order": order,
            "status": "completed",
            "thread_id": thread_id,
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
        _record_cross_step(
            task_id,
            run_id,
            f"[cross:{spec.model_name}] 模型调研失败",
            "FAILED",
            {
                "thread_id": thread_id,
                "model": spec.public_payload(),
                "error": error,
                "latency_ms": latency_ms,
            },
        )
        return {
            "order": order,
            "status": "failed",
            "thread_id": thread_id,
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


def build_cross_model_research_prompt(task: ResearchTask, base_prompt: str) -> str:
    return (
        "你现在是多模型交叉验证中的一个独立调研线程。"
        "请不要参考其他模型的输出，也不要等待外部人工反馈。"
        "请独立完成完整调研，写入 Markdown 报告文件，并调用 present_report 展示该报告。\n\n"
        "报告文件路径建议使用:\n"
        "/mnt/user-data/outputs/model_research_report.md\n\n"
        "原始调研任务如下:\n"
        f"{base_prompt.strip()}"
    )


def build_cross_integrator_system_message() -> str:
    return (
        "你是 8Feet 多模型交叉验证智能整合 Lead Agent。"
        "你的任务不是重新做外部检索，而是读取多个模型独立调研线程的报告与证据，"
        "比较它们的一致结论、分歧、证据质量和遗漏点，产出一份更全面、更稳健的中文 Markdown 报告。"
        "所有来自模型报告的事实都要保留原报告中的 citation key；不要编造新的 citation key。"
        "最终必须把整合优化报告写入 /mnt/user-data/outputs/cross_validation_report.md，"
        "然后调用 present_report。"
    )


def build_cross_integrator_prompt(
    task_payload: dict[str, Any],
    model_results: list[dict[str, Any]],
    copy_manifests: list[dict[str, Any]],
) -> str:
    manifest_json = json.dumps(json_safe(copy_manifests), ensure_ascii=False, indent=2)
    results_overview = json.dumps(
        [
            {
                "status": item.get("status"),
                "model": item.get("model"),
                "thread_id": item.get("thread_id"),
                "summary": item.get("summary"),
                "report_paths": item.get("report_paths", []),
                "error": item.get("error", ""),
            }
            for item in model_results
        ],
        ensure_ascii=False,
        indent=2,
    )
    return (
        "请基于以下多模型调研结果完成智能整合优化。\n\n"
        "调研对象:\n"
        f"- 标题: {task_payload.get('title')}\n"
        f"- 对象: {task_payload.get('object_name')}\n"
        f"- 类型: {task_payload.get('object_type')}\n\n"
        "每个模型线程的沙箱已经复制到当前线程 workspace 下。"
        "请优先读取 copied_path 指向的 presented report；必要时再阅读同目录下的 evidence、outputs 或 workspace 文件。\n\n"
        "复制清单:\n"
        f"```json\n{manifest_json}\n```\n\n"
        "模型输出概览:\n"
        f"```json\n{results_overview}\n```\n\n"
        "输出要求:\n"
        "1. 提炼多模型一致支持的核心结论。\n"
        "2. 标出模型间分歧、证据冲突或只有单一模型支持的观点。\n"
        "3. 对证据质量和缺口做判断，必要时说明哪些结论需要人工复核。\n"
        "4. 形成整合优化后的最终调研参考报告。\n"
        "5. 将报告写入 /mnt/user-data/outputs/cross_validation_report.md 并调用 present_report。"
    )


def _resolve_model_spec(task: ResearchTask, raw_id: Any) -> CrossModelSpec:
    text = str(raw_id or "").strip()
    if not text:
        raise ValueError("模型 ID 不能为空")
    if text.isdigit():
        return _resolve_config_model_spec(task, int(text))

    config = (
        LLMConfig.objects
        .filter(Q(model_id=text) | Q(name=text))
        .order_by("id")
        .first()
    )
    if config is not None:
        if not user_can_use_model(task.user, config):
            raise ValueError(f"当前用户无权使用模型: {text}")
        return _resolve_config_model_spec(task, config.id)
    return _resolve_env_model_spec(text)


def _resolve_config_model_spec(task: ResearchTask, config_id: int) -> CrossModelSpec:
    ok, message, config, params = resolve_user_model_config(
        task.user,
        model_id=config_id,
        object_type=task.object_type,
    )
    if not ok or config is None:
        raise ValueError(message or f"模型不可用: {config_id}")
    ok, message, runtime_config = get_provider_runtime_config(config, params)
    if not ok:
        raise ValueError(message or f"模型运行配置不可用: {config.name}")
    return CrossModelSpec(
        model_key=str(config.id),
        model_name=config.name,
        provider=config.provider,
        runtime_config=runtime_config,
        llm_config_id=config.id,
    )


def _resolve_env_model_spec(model_name: str) -> CrossModelSpec:
    api_key = _env_first("EFEET_MODEL_API_KEY", "MODEL_API_KEY")
    base_url = _env_first("EFEET_MODEL_BASE_URL", "MODEL_BASE_URL")
    if not api_key or not base_url:
        raise ValueError(
            f"模型 {model_name} 未在平台配置中找到，且环境变量缺少 MODEL_API_KEY / MODEL_BASE_URL"
        )
    return CrossModelSpec(
        model_key=str(model_name),
        model_name=str(model_name),
        provider=_env_first("EFEET_MODEL_PROVIDER", "MODEL_PROVIDER") or "env",
        runtime_config={
            "model": str(model_name),
            "api_key": api_key,
            "base_url": base_url,
            "debug_provider_http": _env_bool("EFEET_DEBUG_PROVIDER_HTTP", False),
        },
        llm_config_id=None,
    )


def _create_model(spec: CrossModelSpec):
    return create_chat_model(
        model=str(spec.runtime_config["model"]),
        api_key=str(spec.runtime_config["api_key"]),
        base_url=str(spec.runtime_config["base_url"]),
        debug_provider_http=bool(spec.runtime_config.get("debug_provider_http", False)),
    )


def _ensure_report_payloads(
    *,
    sandbox_paths: SandboxPaths,
    thread_id: str,
    state_snapshot: dict[str, Any],
    final_output: str,
    fallback_filename: str,
) -> list[dict[str, Any]]:
    reports = [report.to_payload() for report in extract_presented_reports(state_snapshot)]
    if reports:
        return _sanitize_report_payloads(
            sandbox_paths=sandbox_paths,
            thread_id=thread_id,
            reports=reports,
        )

    text = str(final_output or "").strip()
    if not text or text == research_runtime.STOPPED_MESSAGE or _is_llm_failure_output(text):
        return []

    output_dir = sandbox_paths.outputs_dir(thread_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    fallback_path = output_dir / fallback_filename
    fallback_path.write_text(text, encoding="utf-8")
    return [
        {
            "path": f"{OUTPUTS_VIRTUAL_PATH}/{fallback_filename}",
            "content": text,
            "citation_keys": [],
            "citations": [],
            "generated_reference_count": 0,
            "fallback_generated": True,
        }
    ]


def _sanitize_report_payloads(
    *,
    sandbox_paths: SandboxPaths,
    thread_id: str,
    reports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for report in reports:
        payload = dict(report)
        original = str(payload.get("content") or "")
        cleaned = _strip_tool_call_markup(original)
        if cleaned != original:
            payload["content"] = cleaned
            _rewrite_output_report(
                sandbox_paths=sandbox_paths,
                thread_id=thread_id,
                virtual_path=str(payload.get("path") or ""),
                content=cleaned,
            )
        sanitized.append(payload)
    return sanitized


def _report_paths_from_payloads(reports: list[dict[str, Any]]) -> list[str]:
    return [
        str(item.get("path") or "").strip()
        for item in reports
        if str(item.get("path") or "").strip()
    ]


def _rewrite_output_report(
    *,
    sandbox_paths: SandboxPaths,
    thread_id: str,
    virtual_path: str,
    content: str,
) -> None:
    normalized = PurePosixPath(str(virtual_path or "").strip()).as_posix()
    if not normalized.startswith(f"{OUTPUTS_VIRTUAL_PATH}/"):
        return
    relative = PurePosixPath(normalized[len(OUTPUTS_VIRTUAL_PATH) :].lstrip("/"))
    if any(part == ".." for part in relative.parts):
        return
    target = sandbox_paths.outputs_dir(thread_id).joinpath(*relative.parts)
    try:
        target.write_text(content, encoding="utf-8")
    except OSError:
        return


def _strip_tool_call_markup(text: str) -> str:
    cleaned = str(text or "")
    markers = (
        "<longcat_tool_call>",
        "<tool_call>",
        "<function_call>",
        "<tool_calls>",
    )
    positions = [cleaned.find(marker) for marker in markers if marker in cleaned]
    if not positions:
        return cleaned.strip()
    return cleaned[: min(positions)].rstrip()


def _is_llm_failure_output(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    failure_markers = (
        "the configured llm provider is temporarily unavailable",
        "the configured llm provider rejected the request",
        "llm request failed:",
    )
    return any(marker in normalized for marker in failure_markers)


def _coerce_model_id_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                return _coerce_model_id_list(json.loads(text))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return [item.strip() for item in text.split(",") if item.strip()]
    if isinstance(value, dict):
        return _coerce_model_id_list(value.get("model_ids") or value.get("models"))
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return [str(value).strip()]


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


def _task_prompt_payload(task: ResearchTask) -> dict[str, Any]:
    specs = _coerce_model_id_list((task.search_params or {}).get("multi_model_ids"))
    return {
        "title": task.title,
        "object_name": task.object_name,
        "object_type": task.object_type,
        "search_params": task.search_params or {},
        "model_order": {str(item): index for index, item in enumerate(specs, start=1)},
    }


def _env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}
