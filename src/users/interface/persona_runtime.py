"""
用户人设 AI 会话运行时。
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from django.db import transaction

from efeet import Agent, SandboxPaths, Thread, create_chat_model, extract_presented_reports
from efeet.agents.middlewares import build_default_middlewares
from efeet.tools.builtins import present_report_tool, web_fetch_tool, web_search_tool, write_file_tool
from llm_manager.interface.llm_interface import get_provider_runtime_config, log_model_usage, resolve_user_model_config
from research.interface.thread_codec import deserialize_history, serialize_history, serialize_state
from users.interface.persona_interface import get_user_persona_markdown, save_user_persona_report, serialize_user_persona
from users.models.persona import PERSONA_STATUS_COMPLETED, PERSONA_STATUS_DRAFT, PERSONA_STATUS_FAILED, UserPersonaConversation


PERSONA_TOOLS = [web_search_tool, web_fetch_tool, write_file_tool, present_report_tool]
PERSONA_FULL_REPORT_PATH = "/mnt/user-data/outputs/user_persona.md"
PERSONA_BRIEF_REPORT_PATH = "/mnt/user-data/outputs/user_persona_brief.md"


def build_persona_system_message(previous_persona: str = "") -> str:
    previous_block = ""
    if previous_persona.strip():
        previous_block = (
            "\n上一次的用户人设如下。请提醒用户你可以在此基础上修改、补充或推翻其中内容，"
            "不要把旧人设当成不可更改事实:\n"
            "```markdown\n"
            f"{previous_persona.strip()}\n"
            "```\n"
        )
    return (
        "你是 8Feet 的用户人设设定助手。你的任务是通过自然、低打字成本的对话，"
        "逐步引导用户说明其个性化调研需求，最终产出一份中文 Markdown 人设分析报告。\n"
        "你必须主动引导，而不是一次性要求用户填写长表单；优先给出 2-4 个可选项，"
        "允许用户直接选择、修改或用简短文字补充。\n"
        "需要尽量覆盖但不机械追问的信息包括: 人设背景（职业、知识水平、行业经验）、"
        "核心诉求（使用调研平台的核心需求）、调研风格（偏好的信息类型和判断方式）、"
        "调研深度、报告风格、调研侧重点、风险偏好、时间敏感度、常用对象类型。\n"
        "你可以随机应变，根据信息缺口决定下一问；如果用户已经表达清楚，就不要继续打扰。\n"
        "当信息足够时，必须先用 write_file 写入详细版 Markdown 到 "
        f"`{PERSONA_FULL_REPORT_PATH}`，并写入简版 Markdown 到 `{PERSONA_BRIEF_REPORT_PATH}`，"
        "然后必须调用 present_report(full_report_path=..., brief_report_path=...) 完成保存。\n"
        "详细版 Markdown 应包含: # 用户调研人设分析、## 背景画像、## 核心诉求、## 调研偏好、"
        "## 报告偏好、## 调研侧重点、## 给 8Feet 调研 AI 的上下文建议。"
        "不要输出 JSON，不要把过程日志写进报告。"
        "你允许使用的工具有且仅有 web_search、web_fetch、write_file、present_report；"
        "通常不需要联网，除非用户要求参考公开信息或需要验证职业/行业背景。"
        f"{previous_block}"
    )


def initial_persona_prompt() -> str:
    return (
        "请开始帮助我设定 8Feet 用户人设。先用简短方式说明你会如何引导我，"
        "然后提出第一个问题，并尽量给出可直接选择的选项。"
    )


def start_persona_conversation(user, model_id: str | int | None = None) -> tuple[bool, str | None, dict | None]:
    ok, message, config, params = resolve_user_model_config(user, model_id=model_id)
    if not ok:
        return (False, message, None)
    previous_persona = get_user_persona_markdown(user)
    conversation = UserPersonaConversation.objects.create(
        user=user,
        thread_id=str(uuid4()),
        model_id=str(config.id),
        system_message=build_persona_system_message(previous_persona),
        status=PERSONA_STATUS_DRAFT,
    )
    success, error, payload = run_persona_turn(conversation, initial_persona_prompt(), llm_params=params)
    if not success:
        return (False, error, None)
    return (True, None, payload)


def continue_persona_conversation(user, thread_id: str, message: str) -> tuple[bool, str | None, dict | None]:
    conversation = UserPersonaConversation.objects.filter(user=user, thread_id=thread_id).first()
    if conversation is None:
        return (False, "人设设定会话不存在", None)
    if not str(message or "").strip():
        return (False, "message 不能为空", None)
    return run_persona_turn(conversation, message)


def run_persona_turn(
    conversation: UserPersonaConversation,
    message: str,
    *,
    llm_params: dict | None = None,
) -> tuple[bool, str | None, dict | None]:
    try:
        user = conversation.user
        config = _resolve_persona_config(user, conversation.model_id, llm_params)
        model = _build_model(config, llm_params)
        sandbox_paths = SandboxPaths()
        thread = Thread(
            agent=Agent(
                model=model,
                tools=PERSONA_TOOLS,
                middlewares=build_default_middlewares(paths=sandbox_paths),
                system_message=conversation.system_message,
            ),
            thread_id=conversation.thread_id,
            system_message=conversation.system_message,
        )
        thread.history = deserialize_history(conversation.history_messages)
        thread.state = dict(conversation.state_snapshot or {})
        thread.max_turns = 30

        previous_presented_count = len(extract_presented_reports(thread.state))
        chunks = list(thread.stream(message))
        assistant_message = "".join(chunks).strip() or "我已记录。"
        state_snapshot = serialize_state(thread.state)
        presented_reports = extract_presented_reports(state_snapshot)
        new_reports = presented_reports[previous_presented_count:]
        latest_report = new_reports[-1] if new_reports else None

        with transaction.atomic():
            conversation.history_messages = serialize_history(thread.history)
            conversation.state_snapshot = state_snapshot
            conversation.latest_user_message = str(message or "").strip()
            conversation.latest_assistant_message = assistant_message
            conversation.last_error = ""
            conversation.run_count += 1
            conversation.status = PERSONA_STATUS_COMPLETED if latest_report else PERSONA_STATUS_DRAFT
            if latest_report is not None:
                conversation.presented_report_path = latest_report.path
                conversation.presented_report_markdown = latest_report.content
                save_user_persona_report(
                    user=user,
                    content_markdown=latest_report.content,
                    source_thread_id=conversation.thread_id,
                    model_id=conversation.model_id,
                )
            conversation.save()

        log_model_usage(
            user,
            int(conversation.model_id) if str(conversation.model_id).isdigit() else None,
            f"persona-{conversation.thread_id}-{conversation.run_count}",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
            usage_type="GENERAL",
        )
        return (True, None, serialize_persona_conversation(conversation))
    except Exception as exc:
        conversation.status = PERSONA_STATUS_FAILED
        conversation.last_error = str(exc)
        conversation.save(update_fields=["status", "last_error", "updated_at"])
        return (False, str(exc), None)


def _resolve_persona_config(user, model_id: str, llm_params: dict | None = None):
    ok, message, config, params = resolve_user_model_config(user, model_id=model_id)
    if not ok:
        raise ValueError(message)
    if llm_params is not None:
        llm_params.clear()
        llm_params.update(params)
    return config


def _build_model(config, params: dict | None = None):
    ok, message, runtime_config = get_provider_runtime_config(config, params)
    if not ok:
        raise ValueError(message)
    return create_chat_model(
        model=runtime_config["model"],
        api_key=runtime_config["api_key"],
        base_url=runtime_config["base_url"],
        provider=runtime_config.get("provider"),
        debug_provider_http=runtime_config["debug_provider_http"],
        streaming=bool(runtime_config.get("streaming", True)),
    )


def serialize_persona_conversation(conversation: UserPersonaConversation) -> dict[str, Any]:
    return {
        "thread_id": conversation.thread_id,
        "model_id": conversation.model_id,
        "status": conversation.status.lower(),
        "latest_user_message": conversation.latest_user_message,
        "latest_assistant_message": conversation.latest_assistant_message,
        "last_error": conversation.last_error,
        "presented_report_markdown": conversation.presented_report_markdown,
        "persona": serialize_user_persona(conversation.user),
        "messages": _conversation_messages(conversation.history_messages),
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
    }


def _conversation_messages(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for payload in history or []:
        if not isinstance(payload, dict):
            continue
        message_type = str(payload.get("type") or "")
        if message_type not in {"human", "ai"}:
            continue
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        content = str(data.get("content") or "").strip()
        if not content:
            continue
        rows.append({
            "role": "user" if message_type == "human" else "assistant",
            "content": content,
        })
    return rows
