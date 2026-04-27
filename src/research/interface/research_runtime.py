"""
research 模块的 efeet 运行时集成。
负责构建 thread、异步执行任务、持久化会话和进度。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
from time import perf_counter
from typing import Any

from django.db import close_old_connections, transaction
from django.utils import timezone
from dotenv import load_dotenv

from efeet import (
    SandboxPaths,
    STOPPED_MESSAGE,
    create_chat_model,
    create_thread,
    extract_presented_report_event,
    extract_presented_reports,
    resolve_effective_output,
)
from llm_manager.interface.llm_interface import (
    get_provider_runtime_config,
    log_model_usage,
    resolve_user_model_config,
)
from llm_manager.models.llm_config import LLMConfig
from reports.models.citation import Citation
from research.interface.prompt_contracts import (
    citation_discipline_requirements,
    object_type_research_requirements,
    report_format_requirements,
    search_then_research_workflow,
)
from reports.models.report import Report
from research.interface.thread_codec import (
    content_to_text,
    deserialize_history,
    json_safe,
    serialize_history,
    serialize_state,
)
from research.models import (
    MESSAGE_ROLE_AI,
    MESSAGE_ROLE_HUMAN,
    MESSAGE_ROLE_TOOL,
    AnalysisResult,
    ResearchConversation,
    ResearchConversationMessage,
    ResearchTask,
    ScrapedContent,
    SESSION_STATUS_CANCELLED,
    SESSION_STATUS_COMPLETED,
    SESSION_STATUS_FAILED,
    SESSION_STATUS_RUNNING,
    STATUS_ANALYZING,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SEARCHING,
    TaskStepLog,
)

DEFAULT_MAX_TURNS = 10
MAX_WORKERS = 4
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / '.env')
_EXECUTOR = ThreadPoolExecutor(
    max_workers=MAX_WORKERS,
    thread_name_prefix="research-agent",
)

SUMMARY_SECTION_RE = re.compile(
    r"(?ms)^#{1,3}\s*摘要\s*$\n(?P<body>.*?)(?=^#{1,3}\s+\S|\Z)"
)


class TaskCancelledError(RuntimeError):
    """任务被用户取消。"""


def build_research_system_message() -> str:
    """统一的 research agent system prompt。"""
    workflow_contract = search_then_research_workflow()
    report_contract = report_format_requirements()
    citation_contract = citation_discipline_requirements()
    return (
        "你是 8Feet 商业对象智能调研分析助手。"
        "你的目标是围绕公司、股票、商品三类对象开展深入、可追溯的商业调研。"
        "由你根据任务复杂度判断调研深度、检索范围和是否需要子代理协作。"
        f"\n{workflow_contract}"
        f"\n{report_contract}"
        f"{citation_contract}"
        "所有结论都必须以已检索到的事实为基础，避免无依据推断。"
        "如果来源不足或部分工具失败，不要反复换关键词重试，请在风险与不确定性中说明。"
        "最终报告文件只能由 Lead Agent 定稿，并在写入后调用 present_report。"
        "不要让子代理产出最终报告文件。"
    )


def _normalize_research_depth(search_params: dict[str, Any] | None) -> str:
    params = search_params or {}
    depth = str(params.get("research_depth") or params.get("depth") or "standard").strip().lower()
    if depth in {"quick", "fast", "lite"}:
        return "quick"
    if depth in {"deep", "advanced", "full"}:
        return "deep"
    return "standard"


def _subagents_disabled(params: dict[str, Any]) -> bool:
    enable_subagents = params.get("enable_subagents")
    return enable_subagents is False or str(enable_subagents).strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }


def _build_execution_constraints(search_params: dict[str, Any] | None) -> str:
    params = search_params or {}
    depth = _normalize_research_depth(params)
    mode_name = {
        "quick": "快速调研",
        "deep": "深度调研",
    }.get(depth, "标准调研")
    subagent_rule = (
        "由 Lead Agent 根据任务复杂度自行判断；内容较多、来源跨度大或需要多角度验证时，可调用 deep-search 或 researcher。"
        if not _subagents_disabled(params)
        else "当前参数禁用了子代理，不要调用 task 工具。"
    )
    return (
        "执行建议:\n"
        f"- 当前模式: {mode_name}。\n"
        "- web_search 用于发现候选网址、信息面和检索方向；不要把搜索摘要当作引用证据。\n"
        "- web_fetch 用于读取候选网页并形成可引用证据；优先抓取与专项框架和关键争议直接相关的来源。\n"
        f"- task 子代理: {subagent_rule}\n"
        "- bash 仅在需要处理本地文件、沙箱资料或命令行数据时使用；普通网页调研优先使用检索、抓取和结构化业务数据工具。\n"
        "- 不要按来源数量机械停止；当证据覆盖对象专项框架、关键争议点和主要不确定性后，再收束生成最终 Markdown 报告。\n"
        "- 如果搜索失败、网页不可访问或证据不足，不要反复扩大关键词范围，请在“风险与不确定性”中说明。\n"
        "- 如果运行即将结束或工具不可用，直接基于已有证据输出阶段性最终报告。\n"
    )


def build_initial_prompt(task: ResearchTask) -> str:
    """把任务字段转换为首轮研究 prompt。"""
    search_params = json.dumps(
        json_safe(task.search_params or {}),
        ensure_ascii=False,
        indent=2,
    )
    object_requirements = object_type_research_requirements(task.object_type)
    workflow_contract = search_then_research_workflow()
    report_contract = report_format_requirements("/mnt/user-data/outputs/research_report.md")
    citation_contract = citation_discipline_requirements()
    return (
        "请围绕以下商业对象开展一次商业调研，并输出结构化 Markdown 报告。\n\n"
        f"- 调研标题: {task.title}\n"
        f"- 调研对象: {task.object_name}\n"
        f"- 对象类型: {task.object_type}\n"
        f"\n{object_requirements}\n"
        "- 任务要求:\n"
        "  1. 先明确调研思路，再按 DeepSearch 工作流和执行约束选择必要工具或子代理补充证据。\n"
        "  2. 必须优先覆盖上方对象类型专项调研框架，再补充通用商业分析维度。\n"
        "  3. 严格遵守下方引用约束，不能把无引用内容写成确定事实。\n"
        "  4. 严格遵守下方最终报告格式与交付要求。\n"
        "  5. 不要为了追求完整性无限检索；证据不足时说明不确定性并完成报告。\n\n"
        f"{workflow_contract}\n"
        f"{citation_contract}\n"
        f"{report_contract}\n"
        f"{_build_execution_constraints(task.search_params)}\n"
        f"补充检索参数:\n```json\n{search_params}\n```"
    )


def build_followup_prompt(message: str) -> str:
    """基于已有会话继续追问。"""
    return (
        "请基于当前已经积累的调研上下文继续回答下面的问题。"
        "如有必要，可以继续调用工具补充验证，再给出答案。\n\n"
        f"用户追问:\n{message.strip()}"
    )


def enqueue_task_run(
    task_id: int,
    *,
    prompt: str,
    create_report: bool,
    queued_step_name: str,
    run_metadata: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    """提交一个异步 research run。"""
    task = (
        ResearchTask.objects
        .select_related('conversation')
        .filter(pk=task_id)
        .first()
    )
    if not task:
        return (False, "任务不存在")

    conversation = getattr(task, 'conversation', None)
    if conversation is None:
        return (False, "任务尚未初始化会话")
    if task.status == STATUS_CANCELLED or conversation.status == SESSION_STATUS_CANCELLED:
        return (False, "任务已取消，无法继续执行")
    if conversation.status == SESSION_STATUS_RUNNING:
        return (False, "任务正在运行，请稍后再试")

    run_number = int(conversation.run_count or 0) + 1
    now = timezone.now()
    progress = _merge_progress(
        task.progress,
        {
            "stage": STATUS_SEARCHING if create_report else STATUS_ANALYZING,
            "event_count": 0,
            "run_number": run_number,
            "last_event_type": "queued",
        },
    )
    if create_report:
        progress["searching"] = max(int(progress["searching"]), 5)
        progress["analyzing"] = max(int(progress["analyzing"]), 0)
        progress["report"] = max(int(progress["report"]), 0)
        task.status = STATUS_SEARCHING
    else:
        progress["analyzing"] = max(int(progress["analyzing"]), 5)
        task.status = STATUS_ANALYZING
    task.progress = progress
    task.save(update_fields=['status', 'progress', 'updated_at'])

    conversation.status = SESSION_STATUS_RUNNING
    conversation.latest_user_message = prompt.strip()
    conversation.last_error = ''
    conversation.last_started_at = now
    conversation.run_count = run_number
    conversation.save(
        update_fields=[
            'status',
            'latest_user_message',
            'last_error',
            'last_started_at',
            'run_count',
            'updated_at',
        ]
    )

    TaskStepLog.objects.create(
        task=task,
        step_name=queued_step_name,
        step_status="RUNNING",
        detail={
            "message": prompt.strip(),
            "run_number": run_number,
            "create_report": create_report,
            "run_metadata": run_metadata or {},
        },
    )

    try:
        _EXECUTOR.submit(
            _run_task,
            task.id,
            prompt,
            create_report,
            run_number,
            run_metadata or {},
        )
    except Exception as exc:
        conversation.status = SESSION_STATUS_FAILED
        conversation.last_error = str(exc)
        conversation.last_finished_at = timezone.now()
        conversation.save(
            update_fields=[
                'status',
                'last_error',
                'last_finished_at',
                'updated_at',
            ]
        )
        task.status = STATUS_FAILED
        task.progress = _merge_progress(
            task.progress,
            {"stage": STATUS_FAILED},
        )
        task.save(update_fields=['status', 'progress', 'updated_at'])
        return (False, f"提交后台任务失败: {exc}")

    return (True, None)


def _run_task(
    task_id: int,
    prompt: str,
    create_report: bool,
    run_number: int,
    run_metadata: dict[str, Any] | None = None,
) -> None:
    close_old_connections()
    started_at = perf_counter()
    task = None
    conversation = None
    thread = None

    try:
        task = (
            ResearchTask.objects
            .select_related('user', 'llm_config', 'conversation')
            .get(pk=task_id)
        )
        conversation = task.conversation
        if task.status == STATUS_CANCELLED or conversation.status == SESSION_STATUS_CANCELLED:
            raise TaskCancelledError("任务已取消")

        model, effective_config = _build_task_model(task)
        thread = create_thread(
            model,
            system_message=conversation.system_message or build_research_system_message(),
            sandbox_paths=_resolve_sandbox_paths(task.search_params),
            thread_id=conversation.thread_id,
        )
        thread.history = deserialize_history(conversation.history_messages)
        thread.state = dict(conversation.state_snapshot or {})
        thread.max_turns = _resolve_max_turns(task.search_params)

        previous_history_count = len(conversation.history_messages or [])
        previous_presented_report_count = len(
            extract_presented_reports(conversation.state_snapshot or {})
        )
        previous_report_row_count = Report.objects.filter(task=task).count()
        output_chunks: list[str] = []

        def on_event(event: dict[str, Any]) -> None:
            _record_event(task_id, run_number, event)

        for chunk in thread.stream(prompt, on_event=on_event):
            output_chunks.append(chunk)

        final_output = "".join(output_chunks).strip()
        if not final_output:
            final_output = "研究已完成，但当前轮没有返回可展示文本。"

        latency_ms = round((perf_counter() - started_at) * 1000, 2)
        _persist_success(
            task=task,
            conversation=conversation,
            thread=thread,
            prompt=prompt,
            final_output=final_output,
            create_report=create_report,
            run_number=run_number,
            previous_history_count=previous_history_count,
            previous_presented_report_count=previous_presented_report_count,
            previous_report_row_count=previous_report_row_count,
            llm_config=effective_config,
            latency_ms=latency_ms,
            run_metadata=run_metadata or {},
        )
    except TaskCancelledError as exc:
        _persist_cancelled(task, conversation, str(exc))
    except Exception as exc:
        _persist_failure(task, conversation, thread, prompt, str(exc), run_number)
    finally:
        close_old_connections()


def _build_task_model(task: ResearchTask):
    """根据任务配置构建 efeet 模型。"""
    config = _resolve_task_llm_config(task)
    if config is not None:
        ok, error_message, runtime_config = get_provider_runtime_config(
            config,
            getattr(task, "_llm_params", None),
        )
        if not ok:
            raise ValueError(error_message)
        return (
            create_chat_model(
                model=runtime_config["model"],
                api_key=runtime_config["api_key"],
                base_url=runtime_config["base_url"],
                debug_provider_http=runtime_config["debug_provider_http"],
            ),
            config,
        )

    model_name = _env_first("EFEET_MODEL_NAME", "MODEL_NAME")
    api_key = _env_first("EFEET_MODEL_API_KEY", "MODEL_API_KEY")
    base_url = _env_first("EFEET_MODEL_BASE_URL", "MODEL_BASE_URL")
    if not model_name or not api_key or not base_url:
        raise ValueError(
            "未找到可用模型配置，请先在 llm_config 中配置，"
            "或设置 EFEET_MODEL_* / MODEL_* 环境变量"
        )
    return (
        create_chat_model(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
        ),
        None,
    )


def _resolve_task_llm_config(task: ResearchTask) -> LLMConfig | None:
    model_id = str(task.llm_config_id) if task.llm_config_id else None
    ok, message, config, params = resolve_user_model_config(
        task.user,
        model_id=model_id,
        object_type=task.object_type,
    )
    if not ok:
        raise ValueError(message)
    task._llm_params = params
    return config


def _resolve_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("${") and text.endswith("}"):
        return os.getenv(text[2:-1].strip(), "").strip()
    if text.lower().startswith("env:"):
        return os.getenv(text[4:].strip(), "").strip()
    return text


def _resolve_env_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        return ""
    return os.getenv(name, "").strip()


def _env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _resolve_max_turns(search_params: dict[str, Any] | None) -> int:
    params = search_params or {}
    default_by_depth = {
        "quick": 6,
        "deep": 18,
    }.get(_normalize_research_depth(params), DEFAULT_MAX_TURNS)
    try:
        value = int(params.get("max_turns", default_by_depth))
    except (TypeError, ValueError):
        value = default_by_depth
    return min(max(value, 6), 40)


def _resolve_sandbox_paths(search_params: dict[str, Any] | None) -> SandboxPaths | None:
    params = search_params or {}
    base_dir = str(
        params.get("sandbox_base_dir")
        or _env_first("EFEET_SANDBOX_BASE_DIR", "RESEARCH_SANDBOX_BASE_DIR")
        or ""
    ).strip()
    if not base_dir:
        return None
    return SandboxPaths(base_dir)


def _record_event(task_id: int, run_number: int, event: dict[str, Any]) -> None:
    task = ResearchTask.objects.filter(pk=task_id).first()
    if task is None:
        raise TaskCancelledError("任务已不存在")
    if task.status == STATUS_CANCELLED:
        raise TaskCancelledError("任务已被取消")

    _mark_matching_tool_call_completed(task_id, run_number, event)
    step_name, step_status, detail = _event_to_step(event, run_number)
    TaskStepLog.objects.create(
        task=task,
        step_name=step_name,
        step_status=step_status,
        detail=detail,
    )

    _persist_presented_report_event(task, event)

    task_status, progress = _progress_from_event(task.progress, event)
    ResearchTask.objects.filter(pk=task_id).update(
        status=task_status,
        progress=progress,
        updated_at=timezone.now(),
    )


def _mark_matching_tool_call_completed(task_id: int, run_number: int, event: dict[str, Any]) -> None:
    event_type = str(event.get("type", "") or "")
    if event_type not in {"tool_result", "subagent_tool_result"}:
        return

    tool_name = str(event.get("name", "") or "")
    if not tool_name:
        return

    actor = str(event.get("subagent_id") or "").strip()
    prefix = f"[{actor}] " if actor else ""
    expected_step_name = f"{prefix}调用工具: {tool_name}"
    call_event_type = "subagent_tool_call" if event_type == "subagent_tool_result" else "tool_call"
    tool_call_id = str(event.get("id", "") or "").strip()

    candidates = (
        TaskStepLog.objects
        .filter(
            task_id=task_id,
            step_name=expected_step_name,
            step_status="RUNNING",
            detail__run_number=run_number,
            detail__event_type=call_event_type,
        )
        .order_by("-id")
    )
    if tool_call_id:
        candidates = candidates.filter(detail__id=tool_call_id)

    call_log = candidates.first()
    if call_log is None:
        return

    detail = dict(call_log.detail or {})
    detail["completed_by_event_type"] = event_type
    if tool_call_id:
        detail["completed_by_tool_call_id"] = tool_call_id
    call_log.detail = detail
    call_log.step_status = "COMPLETED"
    call_log.save(update_fields=["step_status", "detail"])


def _event_to_step(
    event: dict[str, Any],
    run_number: int,
) -> tuple[str, str, dict[str, Any]]:
    event_type = str(event.get("type", "") or "")
    actor = str(event.get("subagent_id") or "").strip()
    prefix = f"[{actor}] " if actor else ""

    if event_type in {"pre_tool_text", "subagent_pre_tool_text"}:
        return (
            f"{prefix}规划下一步",
            "RUNNING",
            {
                "message": content_to_text(event.get("content")),
                "run_number": run_number,
                "event_type": event_type,
            },
        )
    if event_type in {"tool_call", "subagent_tool_call"}:
        return (
            f"{prefix}调用工具: {event.get('name', '')}",
            "RUNNING",
            {
                "args": json_safe(event.get("args", {})),
                "id": str(event.get("id", "") or ""),
                "run_number": run_number,
                "event_type": event_type,
            },
        )
    if event_type in {"tool_result", "subagent_tool_result"}:
        return (
            f"{prefix}工具返回: {event.get('name', '')}",
            "COMPLETED",
            {
                "content": json_safe(event.get("content")),
                "citation_keys": json_safe(event.get("citation_keys", [])),
                "id": str(event.get("id", "") or ""),
                "run_number": run_number,
                "event_type": event_type,
            },
        )
    report_event = extract_presented_report_event(event)
    if report_event is not None:
        return (
            f"{prefix}产出报告",
            "COMPLETED",
            {
                "path": report_event.path,
                "content_length": len(report_event.content),
                "generated_reference_count": report_event.generated_reference_count,
                "citation_keys": list(report_event.citation_keys),
                "run_number": run_number,
                "event_type": event_type,
            },
        )
    if event_type == "files_presented":
        return (
            f"{prefix}展示文件",
            "COMPLETED",
            {
                "paths": json_safe(event.get("paths", [])),
                "run_number": run_number,
                "event_type": event_type,
            },
        )
    if event_type in {"message", "subagent_message"}:
        return (
            f"{prefix}生成回答",
            "RUNNING",
            {
                "message": content_to_text(event.get("content")),
                "run_number": run_number,
                "event_type": event_type,
            },
        )
    if event_type == "subagent_start":
        return (
            f"{prefix}启动子代理",
            "RUNNING",
            {
                "description": content_to_text(event.get("description")),
                "run_number": run_number,
                "event_type": event_type,
            },
        )
    if event_type == "subagent_complete":
        return (
            f"{prefix}子代理完成",
            "COMPLETED",
            {
                "message": content_to_text(event.get("message")),
                "run_number": run_number,
                "event_type": event_type,
            },
        )
    return (
        f"{prefix}{event_type or 'agent_event'}",
        "RUNNING",
        {
            "payload": json_safe(event),
            "run_number": run_number,
            "event_type": event_type,
        },
    )


def _merge_progress(progress: dict[str, Any] | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = {
        "searching": 0,
        "analyzing": 0,
        "report": 0,
        "stage": STATUS_PENDING,
        "event_count": 0,
    }
    if isinstance(progress, dict):
        merged.update(progress)
    if isinstance(extra, dict):
        merged.update(extra)
    merged["searching"] = int(merged.get("searching", 0) or 0)
    merged["analyzing"] = int(merged.get("analyzing", 0) or 0)
    merged["report"] = int(merged.get("report", 0) or 0)
    merged["event_count"] = int(merged.get("event_count", 0) or 0)
    return merged


def _progress_from_event(
    progress: dict[str, Any] | None,
    event: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    merged = _merge_progress(progress)
    merged["event_count"] += 1
    merged["last_event_type"] = str(event.get("type", "") or "")
    if event.get("name"):
        merged["last_tool"] = str(event.get("name"))

    event_type = merged["last_event_type"]
    if event_type in {
        "tool_call",
        "tool_result",
        "subagent_tool_call",
        "subagent_tool_result",
        "subagent_start",
    }:
        merged["stage"] = STATUS_SEARCHING
        merged["searching"] = min(90, max(merged["searching"], 10 + merged["event_count"] * 5))
        return (STATUS_SEARCHING, merged)
    if extract_presented_report_event(event) is not None:
        merged["stage"] = STATUS_ANALYZING
        merged["searching"] = max(merged["searching"], 90)
        merged["analyzing"] = max(merged["analyzing"], 85)
        merged["report"] = min(95, max(merged["report"], 60 + merged["event_count"] * 4))
        return (STATUS_ANALYZING, merged)
    if event_type == "files_presented":
        merged["stage"] = STATUS_ANALYZING
        merged["searching"] = max(merged["searching"], 80)
        merged["analyzing"] = max(merged["analyzing"], 50)
        return (STATUS_ANALYZING, merged)

    merged["stage"] = STATUS_ANALYZING
    merged["searching"] = max(merged["searching"], 70 if merged["event_count"] > 2 else merged["searching"])
    merged["analyzing"] = min(95, max(merged["analyzing"], 15 + merged["event_count"] * 6))
    return (STATUS_ANALYZING, merged)


def _persist_success(
    *,
    task: ResearchTask,
    conversation: ResearchConversation,
    thread,
    prompt: str,
    final_output: str,
    create_report: bool,
    run_number: int,
    previous_history_count: int,
    previous_presented_report_count: int,
    previous_report_row_count: int,
    llm_config: LLMConfig | None,
    latency_ms: float,
    run_metadata: dict[str, Any] | None = None,
) -> None:
    serialized_history = serialize_history(thread.history)
    state_snapshot = serialize_state(thread.state)
    effective_output = resolve_effective_output(
        final_output,
        state=state_snapshot,
        serialized_history=serialized_history,
        create_report=create_report,
    )
    effective_output = _recover_report_output_from_tool_logs(
        task=task,
        run_number=run_number,
        effective_output=effective_output,
        create_report=create_report,
    )
    citations = _extract_citations(state_snapshot)
    presented_reports = extract_presented_reports(state_snapshot)
    has_new_presented_report_content = any(
        report.content.strip()
        for report in presented_reports[previous_presented_report_count:]
    )

    with transaction.atomic():
        conversation.history_messages = serialized_history
        conversation.state_snapshot = state_snapshot
        conversation.latest_user_message = prompt.strip()
        conversation.latest_assistant_message = effective_output
        conversation.last_error = ''
        conversation.status = SESSION_STATUS_COMPLETED
        conversation.last_finished_at = timezone.now()
        conversation.save(
            update_fields=[
                'history_messages',
                'state_snapshot',
                'latest_user_message',
                'latest_assistant_message',
                'last_error',
                'status',
                'last_finished_at',
                'updated_at',
            ]
        )

        _sync_message_rows(
            conversation,
            serialized_history,
            previous_history_count,
            run_number,
        )
        _sync_scraped_contents(task, citations)
        _create_analysis_result(
            task=task,
            llm_config=llm_config,
            prompt=prompt,
            final_output=effective_output,
            state_snapshot=state_snapshot,
            run_number=run_number,
            latency_ms=latency_ms,
        )
        has_persisted_report_rows = Report.objects.filter(task=task).count() > previous_report_row_count
        if create_report and not has_new_presented_report_content and not has_persisted_report_rows:
            _create_report(task, effective_output, citations)

        task.status = STATUS_COMPLETED
        task.progress = _merge_progress(
            task.progress,
            {
                "searching": 100,
                "analyzing": 100,
                "report": 100 if create_report else int((task.progress or {}).get("report", 0) or 0),
                "stage": STATUS_COMPLETED,
                "last_event_type": "completed",
            },
        )
        task.save(update_fields=['status', 'progress', 'updated_at'])

        TaskStepLog.objects.create(
            task=task,
            step_name="调研完成",
            step_status="COMPLETED",
            detail={
                "message": effective_output[:1000],
                "run_number": run_number,
                "create_report": create_report,
            },
        )

        log_model_usage(
            task.user,
            llm_config.id if llm_config else None,
            f"research-{task.id}-{run_number}",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=int(latency_ms or 0),
            usage_type="GENERAL" if create_report else "FOLLOWUP",
        )

        _sync_report_followup_answer(run_metadata, effective_output)


def _recover_report_output_from_tool_logs(
    *,
    task: ResearchTask,
    run_number: int,
    effective_output: str,
    create_report: bool,
) -> str:
    """Use a pending write_file markdown payload when max-turns stops before present_report."""
    if not create_report or (effective_output or "").strip() != STOPPED_MESSAGE:
        return effective_output

    rows = (
        TaskStepLog.objects
        .filter(
            task=task,
            step_name="调用工具: write_file",
            detail__run_number=run_number,
        )
        .order_by("-id")
    )
    for row in rows:
        detail = row.detail if isinstance(row.detail, dict) else {}
        args = detail.get("args")
        if not isinstance(args, dict):
            continue
        path = str(args.get("path", "") or "").lower()
        content = args.get("content")
        if (
            isinstance(content, str)
            and content.strip()
            and path.endswith((".md", ".markdown", ".txt"))
        ):
            return content.strip()
    return effective_output


def _persist_failure(
    task: ResearchTask | None,
    conversation: ResearchConversation | None,
    thread,
    prompt: str,
    error_message: str,
    run_number: int,
) -> None:
    if task is None or conversation is None:
        return

    update_fields = [
        'last_error',
        'status',
        'last_finished_at',
        'updated_at',
    ]
    conversation.last_error = error_message
    conversation.status = SESSION_STATUS_FAILED
    conversation.last_finished_at = timezone.now()

    if thread is not None:
        conversation.history_messages = serialize_history(thread.history)
        conversation.state_snapshot = serialize_state(thread.state)
        conversation.latest_user_message = prompt.strip()
        update_fields.extend(['history_messages', 'state_snapshot', 'latest_user_message'])

    conversation.save(update_fields=list(dict.fromkeys(update_fields)))

    task.status = STATUS_FAILED
    task.progress = _merge_progress(
        task.progress,
        {
            "stage": STATUS_FAILED,
            "last_event_type": "failed",
        },
    )
    task.save(update_fields=['status', 'progress', 'updated_at'])

    TaskStepLog.objects.create(
        task=task,
        step_name="调研失败",
        step_status="FAILED",
        detail={
            "error": error_message,
            "run_number": run_number,
        },
    )


def _sync_report_followup_answer(
    run_metadata: dict[str, Any] | None,
    answer: str,
) -> None:
    followup_id = (run_metadata or {}).get("report_followup_id")
    if not followup_id:
        return
    from reports.models.citation import ReportFollowup

    ReportFollowup.objects.filter(pk=followup_id).update(answer=answer)


def _persist_cancelled(
    task: ResearchTask | None,
    conversation: ResearchConversation | None,
    reason: str,
) -> None:
    if task is None or conversation is None:
        return

    conversation.status = SESSION_STATUS_CANCELLED
    conversation.last_error = reason
    conversation.last_finished_at = timezone.now()
    conversation.save(
        update_fields=[
            'status',
            'last_error',
            'last_finished_at',
            'updated_at',
        ]
    )

    task.status = STATUS_CANCELLED
    task.progress = _merge_progress(
        task.progress,
        {
            "stage": STATUS_CANCELLED,
            "last_event_type": "cancelled",
        },
    )
    task.save(update_fields=['status', 'progress', 'updated_at'])

    TaskStepLog.objects.create(
        task=task,
        step_name="任务已取消",
        step_status="COMPLETED",
        detail={"message": reason},
    )


def _sync_message_rows(
    conversation: ResearchConversation,
    serialized_history: list[dict[str, Any]],
    previous_history_count: int,
    run_number: int,
) -> None:
    if previous_history_count > len(serialized_history):
        ResearchConversationMessage.objects.filter(
            conversation=conversation
        ).delete()
        previous_history_count = 0

    rows: list[ResearchConversationMessage] = []
    for index, payload in enumerate(
        serialized_history[previous_history_count:],
        start=previous_history_count + 1,
    ):
        role, message_type, content = _message_row_parts(payload)
        rows.append(
            ResearchConversationMessage(
                conversation=conversation,
                run_number=run_number,
                message_index=index,
                role=role,
                message_type=message_type,
                content=content,
                payload=payload,
            )
        )
    if rows:
        ResearchConversationMessage.objects.bulk_create(rows)


def _message_row_parts(payload: dict[str, Any]) -> tuple[str, str, str]:
    message_type = str(payload.get("type", "") or "")
    data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    content = content_to_text(data.get("content"))
    if message_type == 'human':
        return (MESSAGE_ROLE_HUMAN, message_type, content)
    if message_type == 'tool':
        return (MESSAGE_ROLE_TOOL, message_type, content)
    return (MESSAGE_ROLE_AI, message_type or 'ai', content)


def _extract_citations(state_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    citations = state_snapshot.get("citations", [])
    if not isinstance(citations, list):
        return []
    return [item for item in citations if isinstance(item, dict)]


def _persist_presented_report_event(task: ResearchTask, event: dict[str, Any]) -> Report | None:
    presented_report = extract_presented_report_event(event)
    if presented_report is None or not presented_report.content.strip():
        return None
    return _create_report(
        task,
        presented_report.content,
        [dict(item) for item in presented_report.citations],
    )


def _sync_scraped_contents(task: ResearchTask, citations: list[dict[str, Any]]) -> None:
    seen_urls: set[str] = set()
    for index, item in enumerate(citations, start=1):
        url = str(item.get("url", "") or "").strip()
        title = str(item.get("title", "") or "").strip() or f"来源 {index}"
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        defaults = {
            "source_title": title[:512],
            "source_type": _infer_source_type(item),
            "content_text": content_to_text(
                item.get("summary")
                or item.get("note")
                or item.get("howpublished")
                or item
            ),
            "relevance_score": max(0.0, 1.0 - (index - 1) * 0.05),
        }
        existing = ScrapedContent.objects.filter(task=task, source_url=url).first()
        if existing:
            for key, value in defaults.items():
                setattr(existing, key, value)
            existing.save(
                update_fields=[
                    'source_title',
                    'source_type',
                    'content_text',
                    'relevance_score',
                ]
            )
            continue
        ScrapedContent.objects.create(
            task=task,
            source_url=url,
            **defaults,
        )


def _infer_source_type(item: dict[str, Any]) -> str:
    endpoint = str(item.get("endpoint", "") or "").lower()
    platform = str(item.get("source_platform", "") or "").lower()
    if any(keyword in endpoint for keyword in ("research_report", "report", "financial")):
        return "FINANCIAL"
    if any(keyword in endpoint for keyword in ("news", "notice")):
        return "NEWS"
    if "gov" in platform:
        return "GOV"
    return "WEB"


def _create_analysis_result(
    *,
    task: ResearchTask,
    llm_config: LLMConfig | None,
    prompt: str,
    final_output: str,
    state_snapshot: dict[str, Any],
    run_number: int,
    latency_ms: float,
) -> None:
    AnalysisResult.objects.create(
        task=task,
        llm_config=llm_config,
        analysis_type='SINGLE',
        conclusion=_extract_summary(final_output),
        raw_output={
            "prompt": prompt,
            "final_output": final_output,
            "run_number": run_number,
            "latency_ms": latency_ms,
            "state_snapshot": state_snapshot,
        },
    )


def _create_report(
    task: ResearchTask,
    final_output: str,
    citations: list[dict[str, Any]],
) -> Report:
    latest_report = Report.objects.filter(task=task, is_latest=True).first()
    next_version = 1 if latest_report is None else latest_report.version + 1
    if latest_report is not None:
        latest_report.is_latest = False
        latest_report.save(update_fields=['is_latest'])

    report = Report.objects.create(
        task=task,
        title=task.title,
        summary=_extract_summary(final_output),
        content_markdown=final_output,
        content_brief=_extract_summary(final_output),
        version=next_version,
        is_latest=True,
    )

    citation_rows: list[Citation] = []
    for index, item in enumerate(citations, start=1):
        url = str(item.get("url", "") or "").strip()
        title = str(item.get("title", "") or "").strip() or f"来源 {index}"
        if not url:
            continue
        citation_rows.append(
            Citation(
                report=report,
                index_number=index,
                source_url=url,
                source_title=title[:512],
                cited_text_snippet=content_to_text(
                    item.get("summary")
                    or item.get("note")
                    or item.get("howpublished")
                )[:2000],
            )
        )
    if citation_rows:
        Citation.objects.bulk_create(citation_rows)
    return report


def _extract_summary(markdown_text: str) -> str:
    text = (markdown_text or "").strip()
    if not text:
        return ""

    matched = SUMMARY_SECTION_RE.search(text)
    if matched:
        summary = matched.group("body").strip()
        if summary:
            return summary[:1000]

    normalized_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return " ".join(normalized_lines)[:1000]
