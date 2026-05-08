"""
调研任务 API — 任务发起/查询/取消/步骤日志
映射需求: FR-JSDY-0001 ~ FR-JSDY-0006
"""
import json
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
    logs = get_task_step_logs(task_id, request.user.id)
    nodes = []
    edges = []
    previous = None
    for index, log in enumerate(logs):
        detail = log.get("detail") if isinstance(log.get("detail"), dict) else {}
        event_type = str(detail.get("event_type", "") or "")
        node_id = str(log.get("id") or index + 1)
        status = (log.get("step_status") or log.get("status") or "completed").lower()
        if status == "paused":
            status = "waiting_user"
        nodes.append({
            "node_id": node_id,
            "node_name": log.get("step_name") or log.get("name") or f"步骤 {index + 1}",
            "node_status": status,
            "description": str(log.get("detail") or ""),
            "payload": _workflow_payload(detail),
            "node_kind": _workflow_node_kind(event_type),
            "event_type": event_type or None,
            "execution_id": _workflow_execution_id(detail),
            "paired_node_id": None,
            "can_intervene": bool(log.get("is_interactive")),
            "metrics": [],
            "updated_at": log.get("created_at"),
        })
        if previous:
            edges.append({"from": previous, "to": node_id})
        previous = node_id
    _pair_workflow_nodes(nodes)
    return success_api_response({
        "task_id": str(task_id),
        "nodes": nodes,
        "edges": edges,
        "current_node": nodes[-1]["node_id"] if nodes else "",
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_facts(request: HttpRequest, task_id: int):
    task = _get_user_task(task_id, request.user.id)
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
