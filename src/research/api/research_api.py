"""
调研任务 API — 任务发起/查询/取消/步骤日志
映射需求: FR-JSDY-0001 ~ FR-JSDY-0006
"""
import json

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

    return success_api_response({
        "task_id": str(task_id),
        "detected_object_type": object_type,
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
    return success_api_response({
        "task_id": str(task.id),
        "status": _frontend_status(task.status),
        "current_stage": (task.progress or {}).get("stage", task.status),
        "progress": progress,
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
    tasks = ResearchTask.objects.filter(user_id=request.user.id).prefetch_related("reports")
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
        node_id = str(log.get("id") or index + 1)
        status = (log.get("step_status") or log.get("status") or "completed").lower()
        if status == "paused":
            status = "waiting_user"
        nodes.append({
            "node_id": node_id,
            "node_name": log.get("step_name") or log.get("name") or f"步骤 {index + 1}",
            "node_status": status,
            "description": str(log.get("detail") or ""),
            "can_intervene": bool(log.get("is_interactive")),
            "metrics": [],
            "updated_at": log.get("created_at"),
        })
        if previous:
            edges.append({"from": previous, "to": node_id})
        previous = node_id
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
    events = [
        {
            "event_id": str(log.id),
            "task_id": str(task_id),
            "node_id": str(log.id),
            "node_name": log.step_name,
            "node_status": "waiting_user" if log.step_status == "PAUSED" else log.step_status.lower(),
            "level": "error" if log.step_status == "FAILED" else "info",
            "title": log.step_name,
            "message": str(log.detail or ""),
            "metrics": {},
            "timestamp": log.created_at.isoformat(),
        }
        for log in logs
    ]
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
