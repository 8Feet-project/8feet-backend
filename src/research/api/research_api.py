"""
调研任务 API — 任务发起/查询/取消/步骤日志
映射需求: FR-JSDY-0001 ~ FR-JSDY-0006
"""
import json
import re
from typing import Any

from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

from shared.utils import (
    ErrorCode, failed_api_response, response_wrapper,
    success_api_response, jwt_auth
)
from research.interface.research_interface import (
    cancel_task,
    continue_task_conversation,
    create_research_task,
    get_task_conversation_history,
    get_task_detail,
    get_task_step_logs,
    list_user_tasks,
    respond_to_step,
)
from research.interface.cross_validation_runtime import (
    enqueue_cross_validation_run,
    get_cross_validation_payload,
)
from research.models import ResearchTask, ScrapedContent, TaskStepLog
from research.interface.thread_codec import content_to_text


def _frontend_status(status: str) -> str:
    return (status or "PENDING").lower()


def _frontend_object_type(object_type: str) -> str:
    return {
        "COMPANY": "company",
        "STOCK": "stock",
        "PRODUCT": "commodity",
        "COMMODITY": "commodity",
    }.get(object_type or "", (object_type or "company").lower())


def _task_progress_percent(task: ResearchTask) -> int:
    progress = task.progress or {}
    values = [value for value in progress.values() if isinstance(value, (int, float))]
    if not values:
        return 100 if task.status == "COMPLETED" else 0
    return int(sum(values) / len(values))


_STAGE_TEMPLATE = [
    {"key": "ingest", "label": "任务接收", "weight": 10},
    {"key": "retrieval", "label": "数据检索", "weight": 35},
    {"key": "analysis", "label": "结构化分析", "weight": 35},
    {"key": "report", "label": "报告生成", "weight": 20},
]

_HIDDEN_WORKFLOW_STEP_NAMES = {
    "开始执行调研",
    "调研完成",
}

_REPORT_EVENT_TYPES = {
    "report_presented",
    "files_presented",
}

_REPORT_TOOL_NAMES = {
    "present_report",
    "present_files",
}

_DSML_TOOL_CALLS_MARKER = "<｜DSML｜tool_calls>"
_DSML_INVOKE_RE = re.compile(r'<｜DSML｜invoke\s+name="([^"]+)">')


def _progress_model(task: ResearchTask) -> dict[str, Any]:
    progress = task.progress or {}
    current_status = _frontend_status(task.status)
    current_stage = str(progress.get("stage", task.status) or task.status).lower()

    searching_progress = int(progress.get("searching", 0) or 0)
    analyzing_progress = int(progress.get("analyzing", 0) or 0)
    report_progress = int(progress.get("report", 0) or 0)

    def stage_payload(key: str, label: str, weight: int, status: str, stage_progress: int) -> dict[str, Any]:
        bounded_progress = max(0, min(100, int(stage_progress)))
        return {
            "key": key,
            "label": label,
            "weight": weight,
            "status": status,
            "progress_percent": bounded_progress,
        }

    if current_status == "completed":
        stages = [
            stage_payload("ingest", "任务接收", 10, "completed", 100),
            stage_payload("retrieval", "数据检索", 35, "completed", 100),
            stage_payload("analysis", "结构化分析", 35, "completed", 100),
            stage_payload("report", "报告生成", 20, "completed", 100),
        ]
    elif current_status == "cancelled":
        stages = [
            stage_payload("ingest", "任务接收", 10, "completed", 100),
            stage_payload("retrieval", "数据检索", 35, "failed", max(searching_progress, 70)),
            stage_payload("analysis", "结构化分析", 35, "pending", 0),
            stage_payload("report", "报告生成", 20, "pending", 0),
        ]
    elif current_status == "failed":
        if current_stage == "searching":
            stages = [
                stage_payload("ingest", "任务接收", 10, "completed", 100),
                stage_payload("retrieval", "数据检索", 35, "failed", max(searching_progress, 70)),
                stage_payload("analysis", "结构化分析", 35, "pending", 0),
                stage_payload("report", "报告生成", 20, "pending", 0),
            ]
        else:
            stages = [
                stage_payload("ingest", "任务接收", 10, "completed", 100),
                stage_payload("retrieval", "数据检索", 35, "completed", max(searching_progress, 100)),
                stage_payload("analysis", "结构化分析", 35, "failed", max(analyzing_progress, 70)),
                stage_payload("report", "报告生成", 20, "pending", 0),
            ]
    elif current_status == "waiting_user":
        stages = [
            stage_payload("ingest", "任务接收", 10, "completed", 100),
            stage_payload("retrieval", "数据检索", 35, "completed", max(searching_progress, 100)),
            stage_payload("analysis", "结构化分析", 35, "waiting_user", max(analyzing_progress, 85)),
            stage_payload("report", "报告生成", 20, "pending", 0),
        ]
    elif current_status == "analyzing":
        stages = [
            stage_payload("ingest", "任务接收", 10, "completed", 100),
            stage_payload("retrieval", "数据检索", 35, "completed", max(searching_progress, 100)),
            stage_payload("analysis", "结构化分析", 35, "running", max(analyzing_progress, 60)),
            stage_payload("report", "报告生成", 20, "running" if report_progress > 0 else "pending", max(report_progress, 0)),
        ]
    elif current_status == "searching":
        stages = [
            stage_payload("ingest", "任务接收", 10, "completed", 100),
            stage_payload("retrieval", "数据检索", 35, "running", max(searching_progress, 20)),
            stage_payload("analysis", "结构化分析", 35, "pending", 0),
            stage_payload("report", "报告生成", 20, "pending", 0),
        ]
    else:
        stages = [
            stage_payload("ingest", "任务接收", 10, "running" if current_status == "pending" else "completed", 10 if current_status == "pending" else 100),
            stage_payload("retrieval", "数据检索", 35, "pending", 0),
            stage_payload("analysis", "结构化分析", 35, "pending", 0),
            stage_payload("report", "报告生成", 20, "pending", 0),
        ]

    total_weight = sum(stage["weight"] for stage in stages)
    completed_weight = sum(stage["weight"] * stage["progress_percent"] / 100 for stage in stages)
    percent = 0 if total_weight <= 0 else round((completed_weight / total_weight) * 100)
    current_stage_index = next((index for index, stage in enumerate(stages) if stage["status"] not in {"completed", "skipped"}), len(stages) - 1)

    return {
        "total_weight": total_weight,
        "completed_weight": round(completed_weight, 2),
        "percent": percent,
        "current_stage_index": current_stage_index,
        "stages": stages,
    }


def _serialize_history_task(task: ResearchTask) -> dict:
    report = task.reports.filter(is_latest=True).first()
    return {
        "task_id": str(task.id),
        "object_name": task.object_name,
        "object_type": _frontend_object_type(task.object_type),
        "report_id": str(report.id) if report else None,
        "status": _frontend_status(task.status),
        "created_at": task.created_at.isoformat(),
    }


def _get_user_task(task_id: int, user_id: int):
    return ResearchTask.objects.filter(pk=task_id, user_id=user_id).first()


def _request_data(request: HttpRequest) -> dict:
    if request.content_type and "application/json" in request.content_type:
        try:
            return json.loads(request.body or b"{}")
        except (json.JSONDecodeError, TypeError):
            return {}
    return request.POST


def _workflow_node_kind(event_type: str) -> str:
    normalized = (event_type or "").strip().lower()
    if normalized in _REPORT_EVENT_TYPES:
        return "report_generation"
    if normalized in {"tool_call", "subagent_tool_call"}:
        return "tool_call"
    if normalized in {"tool_result", "subagent_tool_result"}:
        return "tool_return"
    if normalized in {"message", "subagent_message"}:
        return "llm_message"
    if normalized in {"subagent_start", "subagent_complete"}:
        return "subagent"
    if normalized in {"pre_tool_text", "subagent_pre_tool_text"}:
        return "planning"
    return "business"


def _workflow_execution_id(detail: dict[str, Any]) -> str | None:
    for key in ("id", "completed_by_tool_call_id"):
        value = str(detail.get(key, "") or "").strip()
        if value:
            return value
    return None


def _workflow_payload(detail: dict[str, Any]) -> dict[str, Any]:
    event_type = str(detail.get("event_type", "") or "").strip().lower()
    payload: dict[str, Any] = {"event_type": event_type} if event_type else {}
    if event_type in {"tool_call", "subagent_tool_call"}:
        payload["input"] = detail.get("args", {})
    elif event_type in {"tool_result", "subagent_tool_result"}:
        payload["output"] = detail.get("content")
    elif event_type in {"pre_tool_text", "subagent_pre_tool_text", "message", "subagent_message"}:
        payload["text"] = content_to_text(detail.get("message", ""))
    elif event_type == "report_presented":
        payload.update(
            {
                "path": detail.get("path", ""),
                "brief_path": detail.get("brief_path", ""),
                "content_length": detail.get("content_length"),
                "brief_content_length": detail.get("brief_content_length"),
                "generated_reference_count": detail.get("generated_reference_count"),
                "citation_keys": detail.get("citation_keys", []),
            }
        )
    elif event_type == "files_presented":
        payload["paths"] = detail.get("paths", [])
    return payload


def _pair_workflow_nodes(nodes: list[dict[str, Any]]) -> None:
    call_kinds = {"tool_call"}
    return_kinds = {"tool_return"}
    calls_by_execution: dict[str, list[dict[str, Any]]] = {}
    returns_by_execution: dict[str, list[dict[str, Any]]] = {}

    for index, node in enumerate(nodes):
        node["_order_index"] = index
        execution_id = node.get("execution_id")
        if not execution_id:
            continue
        node_kind = node.get("node_kind")
        if node_kind in call_kinds:
            calls_by_execution.setdefault(execution_id, []).append(node)
        elif node_kind in return_kinds:
            returns_by_execution.setdefault(execution_id, []).append(node)

    for execution_id in set(calls_by_execution) | set(returns_by_execution):
        call_nodes = sorted(calls_by_execution.get(execution_id, []), key=lambda item: item["_order_index"])
        return_nodes = sorted(returns_by_execution.get(execution_id, []), key=lambda item: item["_order_index"])
        for call_node, return_node in zip(call_nodes, return_nodes):
            call_node["paired_node_id"] = return_node["node_id"]
            return_node["paired_node_id"] = call_node["node_id"]

    for node in nodes:
        node.pop("_order_index", None)


def _tool_name_from_node(node: dict[str, Any]) -> str:
    name = str(node.get("node_name") or "").strip()
    for marker in ("调用工具:", "工具返回:"):
        if marker in name:
            return name.split(marker, 1)[1].strip() or "未知工具"
    return name or "未知工具"


def _strip_dsml_tool_markup(text: Any) -> str:
    raw = content_to_text(text).strip()
    if not raw:
        return ""
    if _DSML_TOOL_CALLS_MARKER in raw:
        raw = raw.split(_DSML_TOOL_CALLS_MARKER, 1)[0]
    return " ".join(raw.split()).strip()


def _dsml_tool_names(text: Any) -> list[str]:
    raw = content_to_text(text)
    if _DSML_TOOL_CALLS_MARKER not in raw:
        return []
    return [match.group(1).strip() for match in _DSML_INVOKE_RE.finditer(raw) if match.group(1).strip()]


def _dsml_report_tool_names(text: Any) -> list[str]:
    return [name for name in _dsml_tool_names(text) if name in _REPORT_TOOL_NAMES]


def _is_report_tool_name(tool_name: str, input_payload: Any = None, output_payload: Any = None) -> bool:
    normalized = (tool_name or "").strip()
    return normalized in _REPORT_TOOL_NAMES


def _report_url(task_id: Any, report_id: Any | None = None) -> str:
    task_part = str(task_id)
    report_part = str(report_id or "").strip()
    return (
        f"/report?task_id={task_part}&report_id={report_part}"
        if report_part
        else f"/report?task_id={task_part}"
    )


def _report_payload_for_task(task: ResearchTask | None) -> dict[str, Any]:
    if task is None:
        return {"report_id": "", "report_title": "", "report_url": ""}
    report = task.reports.filter(is_latest=True).first()
    payload = {
        "report_id": str(report.id) if report else "",
        "report_title": report.title if report else "",
        "report_url": _report_url(task.id, report.id if report else None),
    }
    if report:
        payload["report_created_at"] = report.created_at.isoformat()
    return payload


def _with_report_payload(payload: dict[str, Any], report_payload: dict[str, Any]) -> dict[str, Any]:
    merged = {**payload}
    for key in ("report_id", "report_title", "report_url", "report_created_at"):
        value = report_payload.get(key)
        if value not in (None, ""):
            merged[key] = value
    return merged


def _message_node_display(detail: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    text = content_to_text(detail.get("message", ""))
    clean_text = _strip_dsml_tool_markup(text)
    tool_names = _dsml_tool_names(text)
    report_tool_names = _dsml_report_tool_names(text)
    if tool_names:
        if any(name in {"present_report", "present_files"} for name in tool_names):
            title = "展示调研报告"
            fallback = "正在展示调研报告。"
        else:
            title = "准备工具调用"
            fallback = "正在准备工具调用。"
        summary = clean_text or fallback
        return title, summary, {
            "text": summary,
            "tool_names": tool_names,
            "report_tool_names": report_tool_names,
            "hide_tool_payload": bool(report_tool_names),
        }

    summary = clean_text or "本轮回复已生成。"
    return "总结调研结果", summary, {"text": summary}


def _is_dsml_tool_message(node: dict[str, Any]) -> bool:
    if node.get("node_kind") != "llm_message":
        return False
    payload = node.get("payload") if isinstance(node.get("payload"), dict) else {}
    tool_names = payload.get("tool_names")
    return isinstance(tool_names, list) and bool(tool_names)


def _is_report_summary_message(node: dict[str, Any], report_payload: dict[str, Any]) -> bool:
    if node.get("node_kind") != "llm_message" or not report_payload.get("report_url"):
        return False
    text = str(((node.get("payload") or {}).get("text") if isinstance(node.get("payload"), dict) else "") or node.get("summary") or "").strip()
    if not text:
        return False
    return "报告" in text and any(marker in text for marker in ("已生成", "已展示", "查看", "完成", "提交"))


def _workflow_tool_from_dsml_message(node: dict[str, Any], tool_name: str, report_payload: dict[str, Any]) -> dict[str, Any]:
    hide_payload = _is_report_tool_name(tool_name)
    display_name, status_text = _tool_display(tool_name, node.get("node_status") or "completed", {}, {})
    return {
        "tool_name": tool_name,
        "display_name": display_name,
        "execution_id": node.get("execution_id"),
        "status": node.get("node_status") or "completed",
        "status_text": status_text,
        "input": None,
        "output": None,
        "hide_payload": hide_payload,
        "report_url": report_payload.get("report_url") if hide_payload else "",
        "started_at": node.get("updated_at"),
        "finished_at": node.get("updated_at"),
        "source_node_ids": [str(node["node_id"])] if node.get("node_id") is not None else [],
    }


def _enrich_report_links(nodes: list[dict[str, Any]], report_payload: dict[str, Any]) -> None:
    for node in nodes:
        payload = node.get("payload")
        if not isinstance(payload, dict):
            payload = {}
            node["payload"] = payload

        if node.get("node_kind") == "report_generation" or _is_report_summary_message(node, report_payload):
            node["payload"] = _with_report_payload(payload, report_payload)
            node["payload"]["hide_tool_payload"] = node.get("node_kind") == "report_generation"

        tools = node["payload"].get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                if _is_report_tool_name(str(tool.get("tool_name", "")), tool.get("input"), tool.get("output")):
                    tool["hide_payload"] = True
                    if report_payload.get("report_url"):
                        tool["report_url"] = report_payload["report_url"]


def _workflow_node_from_log(log: dict[str, Any], index: int) -> dict[str, Any]:
    detail = log.get("detail") if isinstance(log.get("detail"), dict) else {}
    event_type = str(detail.get("event_type", "") or "")
    node_kind = _workflow_node_kind(event_type)
    node_id = str(log.get("id") or index + 1)
    step_name = log.get("step_name") or log.get("name") or f"步骤 {index + 1}"
    if not event_type and str(step_name).strip() == "调研完成" and content_to_text(detail.get("message", "")).strip():
        node_kind = "llm_message"
    status = (log.get("step_status") or log.get("status") or "completed").lower()
    if status == "paused":
        status = "waiting_user"
    if node_kind == "planning" and status == "running":
        status = "completed"
    payload = _workflow_payload(detail)
    node_name = step_name
    summary = None
    description = str(log.get("detail") or "")
    if node_kind == "llm_message":
        node_name, summary, message_payload = _message_node_display(detail)
        payload.update(message_payload)
        description = summary
    if node_kind == "report_generation":
        node_name = "生成调研报告"
        if event_type == "report_presented":
            reference_count = payload.get("generated_reference_count")
            summary = (
                f"报告文件已生成，包含 {reference_count} 条参考信息。"
                if reference_count not in (None, "")
                else "报告文件已生成。"
            )
        else:
            summary = "报告相关文件已生成。"
        description = summary
    return {
        "node_id": node_id,
        "node_name": node_name,
        "node_status": status,
        "description": description,
        "summary": summary,
        "payload": payload,
        "node_kind": node_kind,
        "event_type": event_type or None,
        "execution_id": _workflow_execution_id(detail),
        "paired_node_id": None,
        "can_intervene": bool(log.get("is_interactive")),
        "metrics": [],
        "updated_at": log.get("created_at"),
    }


def _workflow_tool_payload(call_node: dict[str, Any] | None, return_node: dict[str, Any] | None) -> dict[str, Any]:
    tool_name = _tool_name_from_node(call_node or return_node or {})
    status = (return_node or call_node or {}).get("node_status") or "running"
    input_payload = ((call_node or {}).get("payload") or {}).get("input")
    output_payload = ((return_node or {}).get("payload") or {}).get("output")
    display_name, status_text = _tool_display(tool_name, status, input_payload, output_payload)
    is_report_tool = _is_report_tool_name(tool_name, input_payload, output_payload)
    payload = {
        "tool_name": tool_name,
        "display_name": display_name,
        "execution_id": (call_node or return_node or {}).get("execution_id"),
        "status": status,
        "status_text": status_text,
        "input": input_payload,
        "output": output_payload,
        "hide_payload": is_report_tool,
        "report_url": "/report" if is_report_tool else "",
        "started_at": (call_node or {}).get("updated_at"),
        "finished_at": (return_node or {}).get("updated_at"),
    }
    source_ids = [
        str(node.get("node_id"))
        for node in (call_node, return_node)
        if node and node.get("node_id") is not None
    ]
    payload["source_node_ids"] = source_ids
    return payload


def _parse_tool_output(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _tool_display(
    tool_name: str,
    status: str,
    input_payload: Any,
    output_payload: Any,
) -> tuple[str, str]:
    normalized = (tool_name or "").strip()
    args = input_payload if isinstance(input_payload, dict) else {}
    output = _parse_tool_output(output_payload)
    completed = str(status or "").lower() in {"completed", "skipped"}

    if normalized == "web_search":
        query = str(args.get("query", "") or output.get("query", "") or "").strip()
        count = output.get("total_results") or output.get("count") or output.get("result_count")
        display = "网页搜索"
        if completed:
            return display, f"已搜索到 {count} 条信息" if count not in (None, "") else "已完成网页搜索"
        return display, f"正在搜索「{query}」" if query else "正在搜索网页"
    if normalized == "web_fetch":
        url = str(args.get("url", "") or output.get("url", "") or "").strip()
        display = "网页读取"
        if completed:
            return display, "已读取网页"
        return display, f"正在读取网页：{url}" if url else "正在读取网页"
    if normalized == "write_file":
        path = str(args.get("path", "") or "").strip()
        display = "写入文件"
        if completed:
            return display, "文件已写入"
        return display, f"正在写入文件：{path}" if path else "正在写入文件"
    if normalized in {"present_report", "present_files"}:
        display = "整理报告文件"
        return display, "报告文件已生成" if completed else "正在整理报告文件"
    if normalized in {"bash", "shell"}:
        display = "执行命令"
        return display, "命令已执行" if completed else "正在执行命令"
    if normalized == "task":
        display = "子代理任务"
        return display, "子代理已完成" if completed else "正在运行子代理"

    display = normalized or "工具调用"
    return display, "工具已完成" if completed else "工具执行中"


def _agent_step_status(nodes: list[dict[str, Any]]) -> str:
    statuses = [
        str(node.get("node_status") or "").lower()
        for node in nodes
        if node.get("node_kind") != "planning"
    ]
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "waiting_user" for status in statuses):
        return "waiting_user"
    if any(status == "running" for status in statuses):
        return "running"
    if statuses and all(status in {"completed", "skipped"} for status in statuses):
        return "completed"
    return statuses[-1] if statuses else "completed"


def _is_hidden_workflow_log(log: dict[str, Any]) -> bool:
    step_name = str(log.get("step_name") or log.get("name") or "").strip()
    if step_name == "开始执行调研":
        return True
    if step_name != "调研完成":
        return False
    detail = log.get("detail") if isinstance(log.get("detail"), dict) else {}
    message = content_to_text(detail.get("message", ""))
    stripped = _strip_dsml_tool_markup(message)
    if _DSML_TOOL_CALLS_MARKER in message:
        return True
    if stripped.startswith("#") or "## " in stripped[:500]:
        return True
    return False


def _agent_step_node(nodes: list[dict[str, Any]], order: int) -> dict[str, Any]:
    planning_node = next((node for node in nodes if node.get("node_kind") == "planning"), None)
    tool_call_nodes = [node for node in nodes if node.get("node_kind") == "tool_call"]
    tool_return_nodes = [node for node in nodes if node.get("node_kind") == "tool_return"]
    returns_by_id = {
        str(node.get("execution_id")): node
        for node in tool_return_nodes
        if node.get("execution_id")
    }
    used_return_ids: set[str] = set()
    tools: list[dict[str, Any]] = []

    for call_node in tool_call_nodes:
        execution_id = str(call_node.get("execution_id") or "")
        return_node = returns_by_id.get(execution_id) if execution_id else None
        if return_node and return_node.get("node_id") is not None:
            used_return_ids.add(str(return_node["node_id"]))
        tools.append(_workflow_tool_payload(call_node, return_node))

    for return_node in tool_return_nodes:
        node_id = str(return_node.get("node_id"))
        if node_id not in used_return_ids:
            tools.append(_workflow_tool_payload(None, return_node))

    source_ids = [str(node.get("node_id")) for node in nodes if node.get("node_id") is not None]
    planning_text = str(((planning_node or {}).get("payload") or {}).get("text") or "").strip()
    tool_count = len(tools)
    summary = planning_text or (
        f"本轮执行了 {tool_count} 个工具调用。" if tool_count else "本轮动作已记录。"
    )
    updated_at = next((node.get("updated_at") for node in reversed(nodes) if node.get("updated_at")), None)

    return {
        "node_id": f"agent-step-{source_ids[0] if source_ids else order}",
        "node_name": f"Agent 步骤 {order}",
        "node_status": _agent_step_status(nodes),
        "description": summary,
        "summary": summary,
        "payload": {
            "planning": planning_text,
            "tools": tools,
            "source_node_ids": source_ids,
        },
        "node_kind": "agent_step",
        "event_type": "agent_step",
        "execution_id": None,
        "paired_node_id": None,
        "can_intervene": any(bool(node.get("can_intervene")) for node in nodes),
        "metrics": [],
        "updated_at": updated_at,
    }


def _collapse_agent_step_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    collapsed: list[dict[str, Any]] = []
    index = 0
    agent_step_count = 0

    while index < len(nodes):
        node = nodes[index]
        if node.get("node_kind") == "planning":
            group = [node]
            index += 1
            while index < len(nodes) and nodes[index].get("node_kind") in {"tool_call", "tool_return"}:
                group.append(nodes[index])
                index += 1
            agent_step_count += 1
            collapsed.append(_agent_step_node(group, agent_step_count))
            continue

        if node.get("node_kind") == "tool_call":
            group = []
            while index < len(nodes) and nodes[index].get("node_kind") in {"tool_call", "tool_return"}:
                group.append(nodes[index])
                index += 1
            agent_step_count += 1
            collapsed.append(_agent_step_node(group, agent_step_count))
            continue

        collapsed.append(node)
        index += 1

    return collapsed


def _split_report_message_nodes(
    nodes: list[dict[str, Any]],
    report_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []

    for node in nodes:
        if not _is_dsml_tool_message(node):
            expanded.append(node)
            continue

        payload = node.get("payload") if isinstance(node.get("payload"), dict) else {}
        planning_text = str(payload.get("text") or node.get("summary") or node.get("description") or "").strip()
        tool_names = [str(name) for name in payload.get("tool_names", []) if str(name)]
        tools = [_workflow_tool_from_dsml_message(node, tool_name, report_payload) for tool_name in tool_names]
        if any(tool.get("hide_payload") for tool in tools):
            node_name = "展示调研报告"
        else:
            node_name = str(node.get("node_name") or "准备工具调用")
        source_ids = payload.get("source_node_ids") if isinstance(payload.get("source_node_ids"), list) else []
        source_ids = [str(item) for item in source_ids] or [str(node["node_id"])]
        expanded.append(
            {
                **node,
                "node_id": f"agent-step-report-{node['node_id']}",
                "node_name": node_name,
                "node_status": "completed" if node.get("node_status") == "running" else node.get("node_status", "completed"),
                "description": planning_text,
                "summary": planning_text,
                "payload": _with_report_payload(
                    {
                        "planning": planning_text,
                        "tools": tools,
                        "source_node_ids": source_ids,
                        "hide_tool_payload": False,
                    },
                    report_payload,
                ),
                "node_kind": "agent_step",
                "event_type": "agent_step",
                "execution_id": None,
                "paired_node_id": None,
                "can_intervene": False,
                "metrics": [],
            }
        )

    return expanded


def _workflow_edges(nodes: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"from": str(nodes[index - 1]["node_id"]), "to": str(nodes[index]["node_id"])}
        for index in range(1, len(nodes))
    ]


def _visible_workflow_node_id(nodes: list[dict[str, Any]], raw_node_id: str) -> str:
    for node in nodes:
        source_ids = (((node.get("payload") or {}).get("source_node_ids")) or [])
        if raw_node_id == str(node.get("node_id")) or raw_node_id in [str(item) for item in source_ids]:
            return str(node.get("node_id") or raw_node_id)
    return raw_node_id


def _summarize_event_message(step_name: str, detail: dict[str, Any]) -> str:
    event_type = str(detail.get("event_type", "") or "").strip().lower()

    if event_type in {"tool_call", "subagent_tool_call"}:
        args = detail.get("args") if isinstance(detail.get("args"), dict) else {}
        query = str(args.get("query", "") or "").strip()
        max_results = args.get("max_results")
        if query and max_results:
            return f"已发起工具调用，查询词为“{query}”，预期返回 {max_results} 条结果。"
        if query:
            return f"已发起工具调用，查询词为“{query}”。"
        return f"{step_name} 已发起。"

    if event_type in {"tool_result", "subagent_tool_result"}:
        content = detail.get("content")
        if isinstance(content, dict):
            if content.get("ok") is False:
                error = str(content.get("error", "") or "").strip()
                query = str(content.get("query", "") or "").strip()
                if error and query:
                    return f"工具返回失败：{error}。查询词：“{query}”。"
                if error:
                    return f"工具返回失败：{error}。"
            result_count = content.get("count") or content.get("result_count") or content.get("total")
            if result_count not in (None, ""):
                return f"工具已返回结果，共 {result_count} 条。"
        text = content_to_text(content).strip()
        if text:
            condensed = " ".join(text.split())
            return condensed[:180]
        return f"{step_name} 已完成。"

    if event_type in {"message", "subagent_message", "pre_tool_text", "subagent_pre_tool_text"}:
        message = str(detail.get("message", "") or "").strip()
        if message:
            condensed = " ".join(message.split())
            return condensed[:180]

    if event_type == "subagent_start":
        description = str(detail.get("description", "") or "").strip()
        return description[:180] if description else f"{step_name} 已启动。"

    if event_type == "subagent_complete":
        message = str(detail.get("message", "") or "").strip()
        return message[:180] if message else f"{step_name} 已完成。"

    return step_name


def _task_reference_items(task: ResearchTask) -> list[dict[str, Any]]:
    conversation = getattr(task, "conversation", None)
    state = getattr(conversation, "state_snapshot", None) if conversation is not None else None
    citations = state.get("citations", []) if isinstance(state, dict) else []
    if not isinstance(citations, list):
        citations = []

    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, citation in enumerate(citations, start=1):
        if not isinstance(citation, dict):
            continue
        cite_key = str(citation.get("cite_key", "") or "").strip()
        url = str(citation.get("url", "") or "").strip()
        identity = cite_key or url
        if not identity or identity in seen_keys:
            continue
        seen_keys.add(identity)
        items.append(
            {
                "reference_id": cite_key or f"source-{index}",
                "cite_key": cite_key,
                "index_number": index,
                "title": str(citation.get("title", "") or f"参考信息 {index}"),
                "url": url,
                "source_platform": str(citation.get("source_platform", "") or ""),
                "source_type": str(citation.get("source_category", "") or citation.get("endpoint", "") or ""),
                "authority_score": citation.get("authority_score"),
                "authority_tier": str(citation.get("authority_tier", "") or ""),
                "summary": content_to_text(
                    citation.get("summary")
                    or citation.get("note")
                    or citation.get("applies_to")
                    or ""
                )[:500],
                "evidence_path": (
                    f"/mnt/user-data/workspace/evidence/{cite_key}.md"
                    if cite_key
                    else ""
                ),
                "accessed_at": str(citation.get("accessed_at", "") or ""),
            }
        )

    if items:
        return items

    rows = ScrapedContent.objects.filter(task_id=task.id).order_by("-relevance_score", "id")
    return [
        {
            "reference_id": f"source-{index}",
            "cite_key": "",
            "index_number": index,
            "title": row.source_title,
            "url": row.source_url,
            "source_platform": "",
            "source_type": row.source_type,
            "authority_score": row.relevance_score,
            "authority_tier": "",
            "summary": content_to_text(row.content_text)[:500],
            "evidence_path": "",
            "accessed_at": row.scraped_at.isoformat() if row.scraped_at else "",
        }
        for index, row in enumerate(rows, start=1)
    ]


@response_wrapper
@require_POST
@jwt_auth(perms=['research.create_research'])
def create_task(request: HttpRequest):
    """发起调研任务

    [route]: POST /api/v1/research/task
    """
    data = _request_data(request)
    object_name = data.get('object_name')
    object_type = data.get('object_type')
    title = data.get('title') or (f"{object_name} 深度调研" if object_name else None)
    llm_config_id = data.get('llm_config_id')
    model_id = data.get('model_id')

    search_params = data.get('search_params', {})
    if isinstance(search_params, str):
        try:
            search_params = json.loads(search_params or "{}")
        except (json.JSONDecodeError, TypeError):
            search_params = {}
    if not isinstance(search_params, dict):
        search_params = {}
    for key in ('time_range', 'source_authority', 'source_types', 'multi_model_ids', 'enable_cross_validation'):
        if key in data and data.get(key) is not None:
            search_params[key] = data.get(key)

    try:
        parsed_llm_config_id = int(llm_config_id) if llm_config_id else None
    except (TypeError, ValueError):
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            "llm_config_id 必须是数字",
        )

    success, message, task_id = create_research_task(
        user_id=request.user.id,
        title=title,
        object_name=object_name,
        object_type=object_type,
        llm_config_id=parsed_llm_config_id,
        model_id=model_id,
        search_params=search_params,
    )
    if not success:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            message,
        )

    task = ResearchTask.objects.filter(pk=task_id, user_id=request.user.id).first()
    return success_api_response({
        "task_id": str(task_id),
        "detected_object_type": _frontend_object_type(task.object_type if task else object_type),
        "status": "pending",
        "next_action": "poll_status",
    })


@response_wrapper
@jwt_auth()
def task_collection(request: HttpRequest):
    """兼容 /research/tasks 的 GET 列表与 POST 创建。"""
    if request.method == 'POST':
        return create_task(request)
    if request.method == 'GET':
        return task_list(request)
    return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "不支持的请求方法")


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_detail(request: HttpRequest, task_id: int):
    """获取调研任务详情

    [route]: GET /api/v1/research/tasks/{task_id}
    """
    data = get_task_detail(task_id, request.user.id)
    if not data:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")
    return success_api_response(data)


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_status(request: HttpRequest, task_id: int):
    task = _get_user_task(task_id, request.user.id)
    if not task:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")
    progress = _task_progress_percent(task)
    progress_model = _progress_model(task)
    return success_api_response({
        "task_id": str(task.id),
        "status": _frontend_status(task.status),
        "current_stage": (task.progress or {}).get("stage", task.status),
        "progress": progress_model["percent"] if isinstance(progress_model.get("percent"), int) else progress,
        "progress_model": progress_model,
        "hint": "任务已完成" if task.status == "COMPLETED" else "任务处理中",
        "object_name": task.object_name,
        "object_type": _frontend_object_type(task.object_type),
        "waiting_intervention": task.status == "WAITING_USER",
        "metrics_summary": [],
        "available_actions": ["cancel"] if task.status not in ("COMPLETED", "FAILED", "CANCELLED") else [],
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_list(request: HttpRequest):
    """获取当前用户的调研任务列表

    [route]: GET /api/v1/research/tasks
    """
    tasks = list_user_tasks(request.user.id)
    return success_api_response({
        "list": tasks,
        "total": len(tasks),
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def research_history_list(request: HttpRequest):
    tasks = ResearchTask.objects.filter(
        user_id=request.user.id,
        parent_task__isnull=True,
    ).prefetch_related("reports")
    object_type = request.GET.get("object_type")
    if object_type:
        reverse_map = {"company": "COMPANY", "stock": "STOCK", "commodity": "PRODUCT"}
        tasks = tasks.filter(object_type=reverse_map.get(object_type, object_type))
    keyword = request.GET.get("keyword")
    if keyword:
        tasks = tasks.filter(object_name__icontains=keyword)
    items = [_serialize_history_task(task) for task in tasks.order_by("-created_at")]
    page = int(request.GET.get("page") or 1)
    page_size = int(request.GET.get("page_size") or len(items) or 20)
    start = (page - 1) * page_size
    return success_api_response({
        "list": items[start:start + page_size],
        "total": len(items),
        "page": page,
        "page_size": page_size,
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def research_history_detail(request: HttpRequest, task_id: int):
    task = _get_user_task(task_id, request.user.id)
    if not task:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")
    report = task.reports.filter(is_latest=True).first()
    return success_api_response({
        "task_id": str(task.id),
        "object_name": task.object_name,
        "object_type": _frontend_object_type(task.object_type),
        "search_params": task.search_params or {},
        "fact_dataset": f"task-{task.id}",
        "report_id": str(report.id) if report else None,
        "status": _frontend_status(task.status),
        "created_at": task.created_at.isoformat(),
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['research.view_research'])
def research_history_reload(request: HttpRequest, task_id: int):
    task = _get_user_task(task_id, request.user.id)
    if not task:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")
    report = task.reports.filter(is_latest=True).first()
    return success_api_response({
        "task_id": str(task.id),
        "report_id": str(report.id) if report else None,
        "redirect_url": f"/research/tasks/{task.id}",
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['research.cancel_research'])
def cancel_research_task(request: HttpRequest, task_id: int):
    """取消调研任务

    [route]: POST /api/v1/research/tasks/{task_id}/cancel
    """
    success, message = cancel_task(task_id, request.user.id)
    if not success:
        return failed_api_response(ErrorCode.REFUSE_ACCESS, message)

    return success_api_response({"task_id": task_id, "status": "cancelled"})


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_steps(request: HttpRequest, task_id: int):
    """获取任务步骤日志 (全流程监控)

    [route]: GET /api/v1/research/tasks/{task_id}/workflow
    """
    task = _get_user_task(task_id, request.user.id)
    if not task:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")

    report_payload = _report_payload_for_task(task)
    logs = [log for log in get_task_step_logs(task_id, request.user.id) if not _is_hidden_workflow_log(log)]
    raw_nodes = [_workflow_node_from_log(log, index) for index, log in enumerate(logs)]
    _pair_workflow_nodes(raw_nodes)
    nodes = _collapse_agent_step_nodes(raw_nodes)
    nodes = _split_report_message_nodes(nodes, report_payload)
    _enrich_report_links(nodes, report_payload)
    edges = _workflow_edges(nodes)
    _pair_workflow_nodes(nodes)
    current_raw_node = str(raw_nodes[-1]["node_id"]) if raw_nodes else ""
    return success_api_response({
        "task_id": str(task_id),
        "nodes": nodes,
        "edges": edges,
        "current_node": _visible_workflow_node_id(nodes, current_raw_node) if current_raw_node else "",
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_facts(request: HttpRequest, task_id: int):
    task = (
        ResearchTask.objects
        .filter(pk=task_id, user_id=request.user.id)
        .select_related("conversation")
        .first()
    )
    if not task:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")
    rows = ScrapedContent.objects.filter(task_id=task.id)
    source_counts = {}
    for item in rows:
        source_counts[item.source_type] = source_counts.get(item.source_type, 0) + 1
    return success_api_response({
        "task_id": str(task.id),
        "fact_count": rows.count(),
        "sources": [{"source_name": key, "count": value} for key, value in source_counts.items()],
        "references": _task_reference_items(task),
        "top_entities": [task.object_name],
        "dataset_version": f"task-{task.id}",
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['research.create_research'])
def analyze_task(request: HttpRequest, task_id: int):
    task = _get_user_task(task_id, request.user.id)
    if not task:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")
    report = task.reports.filter(is_latest=True).first()
    return success_api_response({
        "task_id": str(task.id),
        "status": _frontend_status(task.status),
        "report_id": str(report.id) if report else None,
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['research.create_research'])
def retry_analysis(request: HttpRequest, task_id: int):
    task = _get_user_task(task_id, request.user.id)
    if not task:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")
    task.status = "ANALYZING"
    task.save(update_fields=["status", "updated_at"])
    return success_api_response({"task_id": str(task.id), "status": "analyzing"})


@response_wrapper
@jwt_auth(perms=['research.view_research'])
def cross_validation(request: HttpRequest, task_id: int):
    task = _get_user_task(task_id, request.user.id)
    if not task:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")
    if request.method == "POST":
        data = _request_data(request)
        success, message, run_id = enqueue_cross_validation_run(
            task.id,
            requested_model_ids=(
                data.get("multi_model_ids")
                or data.get("model_ids")
                or data.get("models")
            ),
            integrator_model_id=data.get("integrator_model_id") or data.get("integrator_model"),
            prompt=data.get("prompt"),
            run_metadata={"source": "api"},
        )
        if not success:
            return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message or "启动多模型交叉验证失败")
        return success_api_response({
            "task_id": str(task.id),
            "status": "queued",
            "run_id": run_id,
        })
    return success_api_response(get_cross_validation_payload(task))


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_events(request: HttpRequest, task_id: int):
    if not _get_user_task(task_id, request.user.id):
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")
    logs = TaskStepLog.objects.filter(task_id=task_id).order_by("created_at")
    events = []
    for log in logs:
        if _is_hidden_workflow_log({
            "step_name": log.step_name,
            "detail": log.detail,
        }):
            continue
        detail = log.detail if isinstance(log.detail, dict) else {}
        event_type = str(detail.get("event_type", "") or "")
        events.append({
            "event_id": str(log.id),
            "task_id": str(task_id),
            "node_id": str(log.id),
            "node_name": log.step_name,
            "node_status": "waiting_user" if log.step_status == "PAUSED" else log.step_status.lower(),
            "level": "error" if log.step_status == "FAILED" else "info",
            "title": log.step_name,
            "message": _summarize_event_message(log.step_name, detail),
            "metrics": {},
            "payload": _workflow_payload(detail),
            "timestamp": log.created_at.isoformat(),
            "event_type": event_type or None,
            "node_kind": _workflow_node_kind(event_type),
            "execution_id": _workflow_execution_id(detail),
        })
    return success_api_response(events)


@response_wrapper
@jwt_auth(perms=['research.create_research'])
def task_intervention(request: HttpRequest, task_id: int, node_id: str):
    task = _get_user_task(task_id, request.user.id)
    if not task:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")
    if request.method == "GET":
        return success_api_response({
            "task_id": str(task.id),
            "node_id": node_id,
            "node_name": f"节点 {node_id}",
            "intervention_type": "manual_review",
            "status": "waiting_user" if task.status == "WAITING_USER" else "resolved",
            "reason": "",
            "suggested_action": "confirm_continue",
            "current_params": {},
            "preview_data": {},
        })

    data = _request_data(request)
    return success_api_response({
        "task_id": str(task.id),
        "node_id": node_id,
        "result": data.get("action", "confirm_continue"),
        "audit_log_id": "",
        "task_status": _frontend_status(task.status),
        "node_status": "completed",
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_history(request: HttpRequest, task_id: int):
    """获取任务会话历史

    [route]: GET /api/v1/research/tasks/{task_id}/history
    """
    history = get_task_conversation_history(task_id, request.user.id)
    if not history:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务或会话不存在")
    return success_api_response(history)


@response_wrapper
@require_POST
@jwt_auth(perms=['research.view_research'])
def task_followup(request: HttpRequest, task_id: int):
    """基于已保存会话继续追问

    [route]: POST /api/v1/research/tasks/{task_id}/followup
    """
    message = request.POST.get('message')
    if not message:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            "message 不能为空",
        )

    success, error_message = continue_task_conversation(
        task_id,
        request.user.id,
        message,
    )
    if not success:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            error_message or "继续追问失败",
        )

    return success_api_response({"task_id": task_id})


@response_wrapper
@require_POST
@jwt_auth(perms=['research.create_research'])
def intervene_task(request: HttpRequest, task_id: int):
    """用户介入调研任务，提供反馈

    [route]: POST /api/v1/research/tasks/{task_id}/intervene
    """
    step_id = request.POST.get('step_id')
    action = request.POST.get('action')

    import json
    data_str = request.POST.get('response_data', '{}')
    try:
        response_data = json.loads(data_str)
    except (json.JSONDecodeError, TypeError):
        response_data = {}

    if not step_id or not action:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            "step_id 和 action 不能为空",
        )

    success, message = respond_to_step(
        task_id=task_id,
        user_id=request.user.id,
        step_id=int(step_id),
        action=action,
        response_data=response_data,
    )
    if not success:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            message,
        )

    return success_api_response({"message": "反馈已接收，任务继续安排执行"})
