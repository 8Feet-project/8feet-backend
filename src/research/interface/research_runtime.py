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
from types import SimpleNamespace
from typing import Any

from django.db import close_old_connections, transaction
from django.utils import timezone
from dotenv import load_dotenv
from langchain_core.messages import ToolMessage

try:
    from efeet import (
        SandboxPaths,
        STOPPED_MESSAGE,
        create_chat_model,
        create_thread,
        extract_presented_report_event,
        extract_presented_reports,
        resolve_effective_output,
    )
except Exception as exc:
    _EFEET_IMPORT_ERROR = exc
    STOPPED_MESSAGE = "__STOPPED__"

    class SandboxPaths:
        def __init__(self, *args, **kwargs):
            raise ModuleNotFoundError(f"efeet runtime is unavailable: {_EFEET_IMPORT_ERROR}")

    def _missing_efeet(*args, **kwargs):
        raise ModuleNotFoundError(f"efeet runtime is unavailable: {_EFEET_IMPORT_ERROR}")

    create_chat_model = _missing_efeet
    create_thread = _missing_efeet
    resolve_effective_output = _missing_efeet

    def extract_presented_report_event(event):
        if not isinstance(event, dict) or str(event.get("type") or "") != "report_presented":
            return None
        content = str(event.get("content") or "")
        if not content.strip():
            return None
        citations = event.get("citations")
        if not isinstance(citations, list):
            citations = []
        return SimpleNamespace(
            path=str(event.get("path") or ""),
            full_path=str(event.get("full_path") or event.get("path") or ""),
            brief_path=str(event.get("brief_path") or ""),
            content=content,
            brief_content=str(event.get("brief_content") or ""),
            citations=[item for item in citations if isinstance(item, dict)],
        )

    def extract_presented_reports(_state_snapshot):
        return []
else:
    _EFEET_IMPORT_ERROR = None
from llm_manager.interface.llm_interface import (
    get_provider_runtime_config,
    log_model_usage,
    resolve_user_model_config,
)
from llm_manager.models.llm_config import LLMConfig
from reports.models.citation import Citation
from reports.interface.report_interface import cite_keys_from_markdown, normalize_report_markdown
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
from research.realtime import publish_task_update
from users.interface.persona_interface import get_user_persona_markdown
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
    SESSION_STATUS_IDLE,
    SESSION_STATUS_RUNNING,
    STATUS_ANALYZING,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SEARCHING,
    STATUS_WAITING_USER,
    TaskStepLog,
)

DEFAULT_MAX_TURNS = 100
MAX_WORKERS = 4
STEP_APPROVAL_TOOL_NAMES = {"request_step_approval", "ask_clarification"}
STEP_APPROVAL_ACCEPTED = "接受"
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
    return _build_research_system_message(include_step_approval=True)


def build_research_system_message_without_step_approval() -> str:
    """Research prompt variant for non-interactive background threads."""
    return _build_research_system_message(include_step_approval=False)


def _build_research_system_message(*, include_step_approval: bool) -> str:
    if include_step_approval:
        workflow_summary = "先拆解调研维度，将并行检索和验证工作通过 task 工具分配给 deep-search 或 researcher 子代理，最后汇总结果产出报告。"
        workflow_contract = search_then_research_workflow()
        subagent_report_rule = "不要让子代理产出最终报告文件。"
    else:
        workflow_summary = "交叉验证等后台线程固定自动推进；先拆解调研维度，再直接使用检索和读取工具完成证据收集、分析和报告定稿。"
        workflow_contract = (
            "后台独立调研工作流建议:\n"
            "- 不要调用步骤审批，也不要委托子代理；当前线程独立完成检索、证据读取、分析和报告定稿。\n"
            "- web_search 用于发现候选网址、信息面、关键维度和明显争议点；不要把搜索摘要当作引用证据。\n"
            "- 对高价值候选来源使用 web_fetch 或结构化工具获取可引用证据。\n"
            "- 证据不足或部分工具失败时，在风险与不确定性中说明局限并完成报告。\n"
        )
        subagent_report_rule = ""
    report_contract = report_format_requirements()
    citation_contract = citation_discipline_requirements()
    approval_contract = f"\n{step_approval_requirements()}" if include_step_approval else ""
    return (
        "你是 8Feet 商业对象智能调研分析助手。"
        "你的目标是围绕公司、股票、商品三类对象开展深入、可追溯的商业调研。"
        f"{workflow_summary}"
        f"{approval_contract}"
        f"\n{workflow_contract}"
        f"\n{report_contract}"
        f"{citation_contract}"
        "所有结论都必须以已检索到的事实为基础，避免无依据推断。"
        "如果来源不足或部分工具失败，不要反复换关键词重试，请在风险与不确定性中说明。"
        "最终详细报告和简版报告文件只能由 Lead Agent 定稿，并在全部写入后调用 present_report。"
        f"{subagent_report_rule}"
    )


def build_research_system_message_for_user(user) -> str:
    base_message = build_research_system_message()
    persona = get_user_persona_markdown(user)
    if not persona:
        return base_message
    return (
        f"{base_message}\n\n"
        "用户人设背景（用于个性化调研，不要在报告中原样披露，除非用户明确要求）:\n"
        "```markdown\n"
        f"{persona}\n"
        "```\n"
        "在规划、检索、分析和报告写作时，请参考该人设背景调整调研侧重点、深度、表达风格和风险提示方式。"
    )


def prepend_user_persona_to_subagent_prompt(user, prompt: str) -> str:
    persona = get_user_persona_markdown(user)
    if not persona:
        return prompt
    return (
        "父任务用户人设背景如下；请只把它作为调研偏好和报告口径背景，不要原样披露:\n"
        "```markdown\n"
        f"{persona}\n"
        "```\n\n"
        f"{prompt}"
    )


def step_approval_requirements(auto_advance: bool | None = None) -> str:
    """Instructions for user approval checkpoints before core decisions."""
    lines = [
        "\n关键步骤审批约束:",
        "- 在做关键决策或进入每个核心步骤前，必须先调用 request_step_approval 工具，等待用户返回后再继续。",
        "- 核心步骤至少包括: 调研维度拆解、子代理分工、关键证据口径取舍、最终报告结构/结论定稿。",
        "- request_step_approval 的参数有且只有两个字符串: next_action 用一句短语概括接下来做什么，execution_plan 说明这一步计划如何执行。",
        "- 不要在工具参数里传选项；前端固定展示“接受 / 重新计划 / 拒绝，因为_______”。",
        "- 用户返回“接受”时按原计划执行；返回“重新计划”时先调整方案并再次请求审批；返回“拒绝，因为...”时根据理由修正计划。",
        "- request_step_approval 必须单独调用，不要和其他工具调用混在同一轮。",
        "- 该审批工具和本段审批规则仅适用于 Lead Agent；通过 task 委托子代理时，不要在子代理 prompt 中提及该工具或审批规则。",
    ]
    if auto_advance is True:
        lines.append("- 当前任务开启了自动推进；系统会自动接受这些审批请求。")
    elif auto_advance is False:
        lines.append("- 当前任务关闭了自动推进；必须等待用户人工选择后继续。")
    return "\n".join(lines) + "\n"


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
    lines = [
        "执行建议:",
        "- web_search 用于发现候选网址、信息面和检索方向；不要把搜索摘要当作引用证据。",
        "- web_fetch 用于读取候选网页并形成可引用证据；优先抓取与专项框架和关键争议直接相关的来源。",
    ]
    if not _subagents_disabled(params):
        lines.append(
            "- task 子代理: 将调研拆解为多个子任务，优先通过 task 工具调用 "
            "deep-search 并行发现证据，再由 researcher 分析验证；"
            "Lead Agent 负责规划、调度和最终定稿。"
        )
    return "\n".join(lines) + "\n"


TIME_RANGE_LABELS = {
    "7d": "近 7 天",
    "30d": "近 30 天",
    "90d": "近 90 天",
    "1y": "近 1 年",
}

SOURCE_AUTHORITY_LABELS = {
    "authoritative": "权威",
    "high": "权威",
    "strict": "权威",
    "balanced": "中等",
    "medium": "中等",
    "mid": "中等",
    "moderate": "中等",
    "unrestricted": "无限制",
    "unlimited": "无限制",
    "any": "无限制",
}

SOURCE_AUTHORITY_GUIDANCE = {
    "权威": "优先使用官方披露、监管机构、交易所、公司官网、可复现结构化数据等高权威来源。",
    "中等": "优先高可信来源，同时允许主流媒体、行业报告和专业数据库作为交叉验证材料。",
    "无限制": "不限制来源范围，但低可信来源只能作为线索，必须在报告中明确标注不确定性并交叉核验。",
}

SOURCE_TYPE_LABELS = {
    "official": "官方披露",
    "regulator": "监管/交易所",
    "exchange": "监管/交易所",
    "data": "结构化数据",
    "structured": "结构化数据",
    "structured_financial_data": "结构化数据",
    "research": "研报分析",
    "report": "研报分析",
    "news": "新闻舆情",
    "media": "新闻舆情",
}

RESEARCH_FOCUS_LABELS = {
    "overview": "综合调研",
    "finance": "财务经营",
    "competition": "竞争格局",
    "risk": "风险合规",
    "recent": "近期动态",
}


def _coerce_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _label_from_mapping(value: Any, mapping: dict[str, str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return mapping.get(raw.lower(), raw)


def _labels_from_mapping(values: Any, mapping: dict[str, str]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for value in _coerce_list(values):
        label = _label_from_mapping(value, mapping)
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels


def _build_user_source_requirements(search_params: dict[str, Any] | None) -> str:
    params = search_params or {}
    user_requirements = params.get("user_source_requirements")
    if not isinstance(user_requirements, dict):
        user_requirements = {}
    merged = {**params, **user_requirements}

    time_range = _label_from_mapping(merged.get("time_range"), TIME_RANGE_LABELS)
    source_authority = _label_from_mapping(merged.get("source_authority"), SOURCE_AUTHORITY_LABELS)
    source_types = _labels_from_mapping(merged.get("source_types"), SOURCE_TYPE_LABELS)
    research_focus = _labels_from_mapping(merged.get("research_focus"), RESEARCH_FOCUS_LABELS)

    lines = ["用户配置要求:"]
    if time_range:
        lines.append(f"- 时间范围: {time_range}。")
    if source_types:
        lines.append(f"- 信息源类型: {'、'.join(source_types)}。")
    if source_authority:
        guidance = SOURCE_AUTHORITY_GUIDANCE.get(source_authority)
        suffix = f" {guidance}" if guidance else ""
        lines.append(f"- 子项信息来源要求: {source_authority}。{suffix}")
    if research_focus:
        lines.append(f"- 调研重点: {'、'.join(research_focus)}。")
    if len(lines) == 1:
        return ""
    lines.append("- 请把以上内容视为用户对检索和选源的要求；若证据不足，请在报告中说明来源局限。")
    return "\n".join(lines) + "\n"


def build_initial_prompt(task: ResearchTask) -> str:
    """把任务字段转换为首轮研究 prompt。"""
    search_params = json.dumps(
        json_safe(task.search_params or {}),
        ensure_ascii=False,
        indent=2,
    )
    object_requirements = object_type_research_requirements(task.object_type)
    workflow_contract = search_then_research_workflow()
    report_contract = report_format_requirements(
        "/mnt/user-data/outputs/research_report.md",
        "/mnt/user-data/outputs/research_report_brief.md",
    )
    citation_contract = citation_discipline_requirements()
    return (
        "请围绕以下商业对象开展一次商业调研，并输出结构化 Markdown 详细报告与简版报告。\n\n"
        f"- 调研标题: {task.title}\n"
        f"- 调研对象: {task.object_name}\n"
        f"- 对象类型: {task.object_type}\n"
        f"\n{object_requirements}\n"
        "- 任务要求:\n"
        "  1. 先拆解调研维度，按 DeepSearch 工作流将子任务通过 task 工具分配给子代理，再汇总定稿。\n"
        "  2. 必须优先覆盖上方对象类型专项调研框架，再补充通用商业分析维度。\n"
        "  3. 严格遵守下方引用约束，不能把无引用内容写成确定事实。\n"
        "  4. 严格遵守下方最终报告格式与交付要求。\n"
        "  5. 不要为了追求完整性无限检索；证据不足时说明不确定性并完成报告。\n\n"
        f"{workflow_contract}\n"
        f"{step_approval_requirements(_auto_advance_enabled(task.search_params))}\n"
        f"{citation_contract}\n"
        f"{report_contract}\n"
        f"{_build_user_source_requirements(task.search_params)}\n"
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


def _auto_advance_enabled(search_params: dict[str, Any] | None) -> bool:
    params = search_params or {}
    value = params.get("auto_advance")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _approval_response_prompt(response_text: str) -> str:
    return (
        "用户已对上一条关键步骤审批请求作出选择，请把这条选择视为 "
        "request_step_approval 工具返回值，并继续执行。\n\n"
        f"审批返回值:\n{response_text.strip() or STEP_APPROVAL_ACCEPTED}"
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
        thread.state["user_id"] = task.user_id
        thread.max_turns = _resolve_max_turns(task.search_params)

        previous_history_count = len(conversation.history_messages or [])
        previous_presented_report_count = len(
            extract_presented_reports(conversation.state_snapshot or {})
        )
        previous_report_row_count = Report.objects.filter(task=task).count()
        output_chunks: list[str] = []
        current_prompt = prompt
        auto_approval_count = 0
        while True:
            output_chunks = []
            pending_approval_event: dict[str, Any] | None = None

            def on_event(event: dict[str, Any]) -> None:
                nonlocal pending_approval_event
                if _is_step_approval_event(event):
                    pending_approval_event = dict(event)
                    return
                _record_event(task_id, run_number, event)

            for chunk in thread.stream(current_prompt, on_event=on_event):
                output_chunks.append(chunk)

            if pending_approval_event is None:
                break

            if _auto_advance_enabled(task.search_params):
                auto_approval_count += 1
                if auto_approval_count > 20:
                    raise RuntimeError("自动推进审批次数过多，已停止以避免循环")
                _remove_step_approval_tool_message(thread)
                _append_auto_approval_tool_message(thread, pending_approval_event, STEP_APPROVAL_ACCEPTED)
                _record_auto_step_approval(task_id, run_number, pending_approval_event)
                current_prompt = _approval_response_prompt(STEP_APPROVAL_ACCEPTED)
                continue

            _persist_pending_step_approval(
                task=task,
                conversation=conversation,
                thread=thread,
                prompt=current_prompt,
                event=pending_approval_event,
                run_number=run_number,
            )
            return

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
                provider=runtime_config.get("provider"),
                debug_provider_http=runtime_config["debug_provider_http"],
                streaming=bool(runtime_config.get("streaming", True)),
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
    try:
        value = int(params.get("max_turns", DEFAULT_MAX_TURNS))
    except (TypeError, ValueError):
        value = DEFAULT_MAX_TURNS
    return min(max(value, 6), 100)


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


def _is_step_approval_event(event: dict[str, Any]) -> bool:
    return (
        str(event.get("type", "") or "") == "tool_call"
        and str(event.get("name", "") or "") in STEP_APPROVAL_TOOL_NAMES
    )


def _step_approval_args(event: dict[str, Any]) -> dict[str, str]:
    args = event.get("args") if isinstance(event.get("args"), dict) else {}
    next_action = str(args.get("next_action") or args.get("question") or "").strip()
    execution_plan = str(args.get("execution_plan") or args.get("context") or "").strip()
    return {
        "next_action": next_action or "确认下一步计划",
        "execution_plan": execution_plan,
    }


def _step_approval_content(args: dict[str, str]) -> str:
    lines = [args.get("next_action") or "确认下一步计划"]
    execution_plan = str(args.get("execution_plan") or "").strip()
    if execution_plan:
        lines.append(execution_plan)
    return "\n".join(lines)


def _approval_detail_from_event(
    event: dict[str, Any],
    run_number: int,
    *,
    auto_accepted: bool = False,
) -> dict[str, Any]:
    args = _step_approval_args(event)
    return {
        "event_type": "step_approval",
        "tool_name": str(event.get("name") or "request_step_approval"),
        "id": str(event.get("id", "") or ""),
        "args": args,
        "next_action": args["next_action"],
        "execution_plan": args["execution_plan"],
        "message": _step_approval_content(args),
        "run_number": run_number,
        "approval_options": ["accept", "replan", "reject"],
        "auto_accepted": auto_accepted,
        **_event_detail_extra(event),
    }


def _append_auto_approval_tool_message(
    thread,
    event: dict[str, Any],
    response_text: str = STEP_APPROVAL_ACCEPTED,
) -> None:
    tool_call_id = str(event.get("id") or "")
    if not tool_call_id:
        return
    thread.history.append(
        ToolMessage(
            content=response_text,
            tool_call_id=tool_call_id,
            name=str(event.get("name") or "request_step_approval"),
        )
    )
    state_messages = thread.state.get("messages") if isinstance(thread.state, dict) else None
    if isinstance(state_messages, list):
        state_messages.append(thread.history[-1])
    elif isinstance(thread.state, dict):
        thread.state["messages"] = [*thread.history]


def _remove_step_approval_tool_message(thread) -> None:
    if not getattr(thread, "history", None):
        return
    latest = thread.history[-1]
    if isinstance(latest, ToolMessage) and latest.name in STEP_APPROVAL_TOOL_NAMES:
        thread.history = list(thread.history[:-1])
    if isinstance(getattr(thread, "state", None), dict):
        state_messages = thread.state.get("messages")
        if isinstance(state_messages, list) and state_messages:
            latest_state_message = state_messages[-1]
            if isinstance(latest_state_message, ToolMessage) and latest_state_message.name in STEP_APPROVAL_TOOL_NAMES:
                thread.state["messages"] = list(state_messages[:-1])


def _persist_pending_step_approval(
    task: ResearchTask,
    conversation: ResearchConversation,
    thread,
    prompt: str,
    event: dict[str, Any],
    run_number: int,
) -> None:
    _remove_step_approval_tool_message(thread)
    serialized_history = serialize_history(thread.history)
    state_snapshot = serialize_state(thread.state)
    detail = _approval_detail_from_event(event, run_number)

    with transaction.atomic():
        conversation.history_messages = serialized_history
        conversation.state_snapshot = state_snapshot
        conversation.latest_user_message = prompt.strip()
        conversation.latest_assistant_message = _step_approval_content(detail)
        conversation.last_error = ""
        conversation.status = SESSION_STATUS_IDLE
        conversation.last_finished_at = timezone.now()
        conversation.save(
            update_fields=[
                "history_messages",
                "state_snapshot",
                "latest_user_message",
                "latest_assistant_message",
                "last_error",
                "status",
                "last_finished_at",
                "updated_at",
            ]
        )

        task.status = STATUS_WAITING_USER
        task.progress = _merge_progress(
            task.progress,
            {
                "stage": STATUS_WAITING_USER,
                "last_event_type": "step_approval",
            },
        )
        task.save(update_fields=["status", "progress", "updated_at"])

        TaskStepLog.objects.create(
            task=task,
            step_name=detail["next_action"],
            step_status="PAUSED",
            detail=detail,
            is_interactive=True,
        )

    publish_task_update(
        task.id,
        "task_progress_changed",
        {
            "status": STATUS_WAITING_USER,
            "progress": task.progress,
            "reference_count": 0,
        },
    )


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

    reference_count = _persist_event_citations(task, event)
    _persist_presented_report_event(task, event)

    task_status, progress = _progress_from_event(task.progress, event)
    ResearchTask.objects.filter(pk=task_id).update(
        status=task_status,
        progress=progress,
        updated_at=timezone.now(),
    )
    publish_task_update(
        task_id,
        "task_progress_changed",
        {
            "status": task_status,
            "progress": progress,
            "reference_count": reference_count,
        },
    )
    if reference_count:
        publish_task_update(
            task_id,
            "references_changed",
            {
                "reference_count": reference_count,
            },
        )


def _record_auto_step_approval(task_id: int, run_number: int, event: dict[str, Any]) -> None:
    task = ResearchTask.objects.filter(pk=task_id).first()
    if task is None:
        raise TaskCancelledError("任务已不存在")
    detail = _approval_detail_from_event(event, run_number, auto_accepted=True)
    TaskStepLog.objects.create(
        task=task,
        step_name=detail["next_action"],
        step_status="COMPLETED",
        detail=detail,
        is_interactive=True,
        user_response={
            "action": "accept",
            "data": {"response": STEP_APPROVAL_ACCEPTED, "auto_advance": True},
        },
    )
    publish_task_update(
        task.id,
        "step_log_created",
        {
            "step_name": detail["next_action"],
            "step_status": "COMPLETED",
            "auto_accepted": True,
        },
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


def _persist_event_citations(task: ResearchTask, event: dict[str, Any]) -> int:
    new_citations = _event_citations(event)
    if not new_citations:
        return 0

    conversation = ResearchConversation.objects.filter(task_id=task.id).first()
    if conversation is None:
        return 0

    state_snapshot = dict(conversation.state_snapshot or {})
    state_snapshot["citations"] = _merge_citation_snapshots(
        state_snapshot.get("citations"),
        new_citations,
    )
    conversation.state_snapshot = state_snapshot
    conversation.save(update_fields=["state_snapshot", "updated_at"])
    _sync_scraped_contents(task, state_snapshot["citations"])
    return len(state_snapshot["citations"])


def _event_citations(event: dict[str, Any]) -> list[dict[str, Any]]:
    citations = event.get("citations")
    if isinstance(citations, list):
        direct = [
            json_safe(item)
            for item in citations
            if isinstance(item, dict) and _citation_identity(item)
        ]
        if direct:
            return direct
    return _citations_from_tool_content(event)


def _citations_from_tool_content(event: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _tool_content_payload(event.get("content"))
    if not payload:
        return []

    citations = payload.get("citations")
    if isinstance(citations, list):
        direct = [
            json_safe(item)
            for item in citations
            if isinstance(item, dict) and _citation_identity(item)
        ]
        if direct:
            return direct

    guidance = payload.get("citation_guidance")
    entries = guidance.get("entries") if isinstance(guidance, dict) else None
    if not isinstance(entries, list):
        return []

    url = str(payload.get("url") or payload.get("source_url") or payload.get("canonical_url") or "").strip()
    canonical_url = str(payload.get("canonical_url") or "").strip()
    fallback_title = str(payload.get("title") or payload.get("source_title") or "").strip()
    source_platform = str(payload.get("source_platform") or "").strip()
    tool_name = str(event.get("name") or payload.get("tool_name") or "").strip()
    summary = content_to_text(payload.get("content") or payload.get("summary") or "")[:400]

    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cite_key = str(entry.get("cite_key", "") or "").strip().lower()
        if not cite_key:
            continue
        item = {**entry, "cite_key": cite_key}
        item.setdefault("title", fallback_title or cite_key)
        if url:
            item.setdefault("url", url)
        if canonical_url:
            item.setdefault("canonical_url", canonical_url)
        if source_platform:
            item.setdefault("source_platform", source_platform)
        if tool_name:
            item.setdefault("tool_name", tool_name)
        if summary:
            item.setdefault("summary", summary)
        normalized.append(json_safe(item))
    return normalized


def _citation_identity(item: dict[str, Any]) -> str:
    return str(item.get("cite_key") or item.get("url") or "").strip()


def _tool_content_payload(content: Any) -> dict[str, Any] | None:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _merge_citation_snapshots(existing: object, new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for item in [*(existing if isinstance(existing, list) else []), *new]:
        if not isinstance(item, dict):
            continue
        cite_key = str(item.get("cite_key", "") or "").strip().lower()
        url = str(item.get("url", "") or "").strip()
        identity = cite_key or url
        if not identity:
            continue
        normalized_item = {**item}
        if cite_key:
            normalized_item["cite_key"] = cite_key
        if identity not in merged:
            order.append(identity)
            merged[identity] = normalized_item
            continue
        merged[identity] = {**merged[identity], **normalized_item}

    return [merged[key] for key in order]


def _event_detail_extra(event: dict[str, Any]) -> dict[str, Any]:
    """Extract workflow linking metadata from a runtime event."""
    extra: dict[str, Any] = {}
    for key in (
        "subagent_id",
        "parent_tool_call_id",
        "subagent_type",
        "description",
        "tool_call_batch_id",
        "model_response_id",
    ):
        value = event.get(key)
        if value is not None and str(value).strip():
            extra[key] = str(value).strip()
    return extra


def _event_to_step(
    event: dict[str, Any],
    run_number: int,
) -> tuple[str, str, dict[str, Any]]:
    event_type = str(event.get("type", "") or "")
    actor = str(event.get("subagent_id") or "").strip()
    prefix = f"[{actor}] " if actor else ""

    if event_type == "step_approval":
        detail = _approval_detail_from_event(event, run_number)
        return (
            f"{prefix}{detail['next_action']}",
            "PAUSED",
            detail,
        )
    if event_type in {"pre_tool_text", "subagent_pre_tool_text"}:
        return (
            f"{prefix}规划下一步",
            "RUNNING",
            {
                "message": content_to_text(event.get("content")),
                "run_number": run_number,
                "event_type": event_type,
                **_event_detail_extra(event),
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
                **_event_detail_extra(event),
            },
        )
    if event_type in {"tool_result", "subagent_tool_result"}:
        return (
            f"{prefix}工具返回: {event.get('name', '')}",
            "COMPLETED",
            {
                "content": json_safe(event.get("content")),
                "citation_keys": json_safe(event.get("citation_keys", [])),
                "citations": json_safe(event.get("citations", [])),
                "id": str(event.get("id", "") or ""),
                "run_number": run_number,
                "event_type": event_type,
                **_event_detail_extra(event),
            },
        )
    report_event = extract_presented_report_event(event)
    if report_event is not None:
        return (
            f"{prefix}产出报告",
            "COMPLETED",
            {
                "path": report_event.path,
                "brief_path": getattr(report_event, "brief_path", ""),
                "content_length": len(report_event.content),
                "brief_content_length": len(getattr(report_event, "brief_content", "") or ""),
                "generated_reference_count": report_event.generated_reference_count,
                "citation_keys": list(report_event.citation_keys),
                "run_number": run_number,
                "event_type": event_type,
                **_event_detail_extra(event),
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
                **_event_detail_extra(event),
            },
        )
    if event_type in {"message", "subagent_message"}:
        return (
            f"{prefix}生成回答",
            "COMPLETED",
            {
                "message": content_to_text(event.get("content")),
                "run_number": run_number,
                "event_type": event_type,
                **_event_detail_extra(event),
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
                **_event_detail_extra(event),
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
                **_event_detail_extra(event),
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
    if event_type == "step_approval":
        merged["stage"] = STATUS_WAITING_USER
        merged["analyzing"] = min(95, max(merged["analyzing"], 85))
        return (STATUS_WAITING_USER, merged)
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
    if _is_llm_failure_output(effective_output):
        raise RuntimeError(effective_output)
    presented_reports = extract_presented_reports(state_snapshot)
    new_presented_reports = presented_reports[previous_presented_report_count:]
    latest_new_presented_report = _latest_presented_report(new_presented_reports)
    report_output = (
        latest_new_presented_report.content
        if latest_new_presented_report is not None
        else effective_output
    )
    citations = _resolve_report_citations(
        task=task,
        run_number=run_number,
        state_snapshot=state_snapshot,
        report_markdown=report_output,
        presented_citations=(
            [dict(item) for item in latest_new_presented_report.citations]
            if latest_new_presented_report is not None
            else []
        ),
    )
    if citations:
        state_snapshot = {**state_snapshot, "citations": citations}

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
        persisted_report = (
            Report.objects.filter(task=task).order_by("-version", "-id").first()
            if has_persisted_report_rows
            else None
        )
        if create_report and not has_persisted_report_rows:
            if latest_new_presented_report is not None:
                persisted_report = _create_report(
                    task,
                    latest_new_presented_report.content,
                    citations,
                    brief_output=latest_new_presented_report.brief_content,
                )
            else:
                persisted_report = _create_report(task, effective_output, citations)
        if create_report and persisted_report is not None:
            _ensure_report_citations(persisted_report, citations)
            _mark_pending_report_presentation_completed(task, run_number, persisted_report)

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


def _resolve_report_citations(
    *,
    task: ResearchTask,
    run_number: int,
    state_snapshot: dict[str, Any],
    report_markdown: str,
    presented_citations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    citations = _merge_citation_snapshots(
        _extract_citations(state_snapshot),
        [item for item in (presented_citations or []) if isinstance(item, dict)],
    )
    citations = _merge_citation_snapshots(
        citations,
        _recover_citations_from_tool_logs(task=task, run_number=run_number),
    )

    used_keys = cite_keys_from_markdown(report_markdown)
    if not used_keys:
        return citations

    by_key = {
        str(item.get("cite_key") or "").strip().lower(): item
        for item in citations
        if str(item.get("cite_key") or "").strip()
    }
    filtered = [by_key[key] for key in used_keys if key in by_key]
    return filtered or citations


def _recover_citations_from_tool_logs(
    *,
    task: ResearchTask,
    run_number: int,
) -> list[dict[str, Any]]:
    rows = (
        TaskStepLog.objects
        .filter(task=task, detail__run_number=run_number)
        .order_by("id")
    )
    citations: list[dict[str, Any]] = []
    for row in rows:
        detail = row.detail if isinstance(row.detail, dict) else {}
        event_type = str(detail.get("event_type") or "").strip()
        if event_type not in {"tool_result", "subagent_tool_result"}:
            continue
        event = {**detail, "type": event_type}
        event.setdefault("name", _tool_name_from_step_name(row.step_name))
        citations.extend(_event_citations(event))
    return _merge_citation_snapshots([], citations)


def _tool_name_from_step_name(step_name: str) -> str:
    text = str(step_name or "")
    for marker in ("工具返回: ", "调用工具: "):
        if marker in text:
            return text.rsplit(marker, 1)[1].strip()
    return ""


def _mark_pending_report_presentation_completed(
    task: ResearchTask,
    run_number: int,
    report: Report,
) -> None:
    rows = (
        TaskStepLog.objects
        .filter(
            task=task,
            step_status="RUNNING",
            detail__run_number=run_number,
            detail__event_type="tool_call",
        )
        .order_by("id")
    )
    for row in rows:
        if _tool_name_from_step_name(row.step_name) != "present_report":
            continue
        detail = dict(row.detail or {})
        detail["completed_by_event_type"] = "report_persisted"
        detail["completed_by_report_id"] = report.id
        row.detail = detail
        row.step_status = "COMPLETED"
        row.save(update_fields=["step_status", "detail"])


def _is_llm_failure_output(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    failure_markers = (
        "the configured llm provider is temporarily unavailable",
        "the configured llm provider rejected the request",
        "the configured llm provider rate limit was exceeded",
        "llm request failed:",
    )
    return any(marker in normalized for marker in failure_markers)


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


def _latest_presented_report(reports: list[Any]) -> Any | None:
    for report in reversed(reports):
        if str(getattr(report, "content", "") or "").strip():
            return report
    return None


def _persist_presented_report_event(task: ResearchTask, event: dict[str, Any]) -> Report | None:
    presented_report = extract_presented_report_event(event)
    if presented_report is None or not presented_report.content.strip():
        return None
    return _create_report(
        task,
        presented_report.content,
        [dict(item) for item in presented_report.citations],
        brief_output=presented_report.brief_content,
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
            "skip_auto_report": True,
        },
    )


def _create_report(
    task: ResearchTask,
    final_output: str,
    citations: list[dict[str, Any]],
    *,
    brief_output: str | None = None,
) -> Report:
    normalized_output = normalize_report_markdown(final_output)
    normalized_brief = normalize_report_markdown(brief_output)
    latest_report = Report.objects.filter(task=task, is_latest=True).first()
    next_version = 1 if latest_report is None else latest_report.version + 1
    if latest_report is not None:
        latest_report.is_latest = False
        latest_report.save(update_fields=['is_latest'])

    report = Report.objects.create(
        task=task,
        title=task.title,
        summary=_extract_summary(normalized_output),
        content_markdown=normalized_output,
        content_brief=normalized_brief or _extract_summary(normalized_output),
        version=next_version,
        is_latest=True,
    )

    citation_rows = _build_citation_rows(report, citations)
    if citation_rows:
        Citation.objects.bulk_create(citation_rows)
    return report


def _ensure_report_citations(report: Report, citations: list[dict[str, Any]]) -> None:
    if not citations or Citation.objects.filter(report=report).exists():
        return
    citation_rows = _build_citation_rows(report, citations)
    if citation_rows:
        Citation.objects.bulk_create(citation_rows)


def _build_citation_rows(report: Report, citations: list[dict[str, Any]]) -> list[Citation]:
    rows: list[Citation] = []
    for index, item in enumerate(citations, start=1):
        url = str(item.get("url", "") or "").strip()
        title = str(item.get("title", "") or "").strip() or f"来源 {index}"
        reproduction_code = str(item.get("reproduction_code", "") or "").strip()
        rows.append(
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
                reproduction_code=reproduction_code or None,
            )
        )
    return rows


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
