"""
efeet / LangChain thread 快照编解码工具。
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict


def json_safe(value: object) -> Any:
    """将任意对象尽量转换为 JSON 可序列化结构。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


def content_to_text(content: object) -> str:
    """将消息内容压平成便于展示的字符串。"""
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return json.dumps(json_safe(content), ensure_ascii=False, indent=2)


def serialize_history(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """序列化 LangChain 消息列表。"""
    return list(messages_to_dict(messages))


def deserialize_history(payload: list[dict[str, Any]] | None) -> list[BaseMessage]:
    """反序列化 LangChain 消息列表。"""
    if not payload:
        return []
    return list(messages_from_dict(payload))


def serialize_state(state: dict[str, Any] | None) -> dict[str, Any]:
    """序列化可恢复的 thread.state，排除消息历史。"""
    if not isinstance(state, dict):
        return {}
    return {
        str(key): json_safe(value)
        for key, value in state.items()
        if key != "messages"
    }
