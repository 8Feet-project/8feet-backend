"""
调研任务 API — 任务发起/查询/取消/步骤日志
映射需求: FR-JSDY-0001 ~ FR-JSDY-0006
"""
import json

from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from research.interface.research_interface import (
    cancel_task,
    create_research_task,
    get_task_detail,
    get_task_events,
    get_task_intervention_detail,
    get_task_status_view,
    get_task_step_logs,
    list_user_tasks,
    respond_to_step,
)
from shared.utils import (
    ErrorCode,
    failed_api_response,
    jwt_auth,
    parse_json_body,
    response_wrapper,
    success_api_response,
)


@response_wrapper
@require_http_methods(["GET", "POST"])
@jwt_auth()
def tasks_collection(request: HttpRequest):
    """调研任务集合路由

    [route]: GET /api/v1/research/tasks
    [route]: POST /api/v1/research/tasks
    """
    if request.method == 'GET':
        tasks = list_user_tasks(request.user.id)
        return success_api_response({
            "list": tasks,
            "total": len(tasks),
        })

    if not request.user.has_perms(['research.create_research']):
        return failed_api_response(ErrorCode.REFUSE_ACCESS, "您无权进行此操作")

    payload = parse_json_body(request)

    title = payload.get('title') or request.POST.get('title')
    object_name = payload.get('object_name') or request.POST.get('object_name')
    object_type = payload.get('object_type') or request.POST.get('object_type')
    llm_config_id = payload.get('llm_config_id') or request.POST.get('llm_config_id')

    search_params = payload.get('search_params')
    if search_params is None:
        params_str = request.POST.get('search_params', '{}')
        try:
            search_params = json.loads(params_str)
        except (json.JSONDecodeError, TypeError):
            search_params = {}

    success, message, task_id = create_research_task(
        user_id=request.user.id,
        title=title,
        object_name=object_name,
        object_type=object_type,
        llm_config_id=int(llm_config_id) if llm_config_id else None,
        search_params=search_params,
    )
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({"task_id": task_id})


@response_wrapper
@require_POST
@jwt_auth(perms=['research.create_research'])
def create_task(request: HttpRequest):
    """发起调研任务

    [route]: POST /api/research/task
    """
    payload = parse_json_body(request)

    title = payload.get('title') or request.POST.get('title')
    object_name = payload.get('object_name') or request.POST.get('object_name')
    object_type = payload.get('object_type') or request.POST.get('object_type')
    llm_config_id = payload.get('llm_config_id') or request.POST.get('llm_config_id')

    search_params = payload.get('search_params')
    if search_params is None:
        params_str = request.POST.get('search_params', '{}')
        try:
            search_params = json.loads(params_str)
        except (json.JSONDecodeError, TypeError):
            search_params = {}

    success, message, task_id = create_research_task(
        user_id=request.user.id,
        title=title,
        object_name=object_name,
        object_type=object_type,
        llm_config_id=int(llm_config_id) if llm_config_id else None,
        search_params=search_params,
    )
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({"task_id": task_id})


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_detail(request: HttpRequest, task_id: int):
    """获取调研任务详情
    [route]: GET /api/v1/research/tasks/{task_id}
    """
    data = get_task_detail(task_id)
    if not data:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")

    return success_api_response(data)


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
def task_status(request: HttpRequest, task_id: int):
    """获取调研任务执行状态摘要
    [route]: GET /api/v1/research/tasks/{task_id}/status
    """
    data = get_task_status_view(task_id)
    if not data:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")

    return success_api_response(data)


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
    """获取任务步骤日志（全流程监控）
    [route]: GET /api/v1/research/tasks/{task_id}/workflow
    """
    return success_api_response(get_task_step_logs(task_id))


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_events(request: HttpRequest, task_id: int):
    """获取任务事件流
    [route]: GET /api/v1/research/tasks/{task_id}/events
    """
    return success_api_response(get_task_events(task_id))


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_facts(request: HttpRequest, task_id: int):
    """获取事实数据池摘要
    [route]: GET /api/v1/research/tasks/{task_id}/facts
    """
    detail = get_task_detail(task_id)
    if not detail:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")

    search_params = detail.get('search_params') or {}
    source_types = search_params.get('source_types') if isinstance(search_params, dict) else None
    sources = []
    if isinstance(source_types, list) and source_types:
        for source_name in source_types:
            sources.append({"source_name": str(source_name), "count": 0})
    else:
        sources = [{"source_name": "default", "count": 0}]

    return success_api_response({
        "task_id": str(task_id),
        "fact_count": 0,
        "sources": sources,
        "top_entities": [detail.get('object_name')] if detail.get('object_name') else [],
        "dataset_version": detail.get('updated_at') or detail.get('created_at'),
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['research.create_research'])
def analyze_task(request: HttpRequest, task_id: int):
    """触发分析流程
    [route]: POST /api/v1/research/tasks/{task_id}/analyze
    """
    detail = get_task_detail(task_id)
    if not detail:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")

    return success_api_response({
        "task_id": str(task_id),
        "status": "analyzing",
        "report_id": None,
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['research.create_research'])
def retry_task_analysis(request: HttpRequest, task_id: int):
    """重试分析
    [route]: POST /api/v1/research/tasks/{task_id}/retry-analysis
    """
    detail = get_task_detail(task_id)
    if not detail:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")

    return success_api_response({
        "task_id": str(task_id),
        "status": "analyzing",
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['research.create_research'])
def trigger_cross_validation(request: HttpRequest, task_id: int):
    """触发交叉验证
    [route]: POST /api/v1/research/tasks/{task_id}/cross-validation
    """
    detail = get_task_detail(task_id)
    if not detail:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")

    return success_api_response({
        "task_id": str(task_id),
        "status": "queued",
        "result_id": f"cv-{task_id}",
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def cross_validation_result(request: HttpRequest, task_id: int):
    """获取交叉验证结果
    [route]: GET /api/v1/research/tasks/{task_id}/cross-validation/result
    """
    detail = get_task_detail(task_id)
    if not detail:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")

    object_name = detail.get('object_name') or '当前调研对象'
    llm_config_id = detail.get('llm_config_id')
    fallback_model_id = str(llm_config_id) if llm_config_id is not None else 'default-model'

    return success_api_response({
        "task_id": str(task_id),
        "status": "completed",
        "consensus_summary": f"围绕 {object_name} 的交叉验证已完成，当前结果为骨架占位数据。",
        "consensus_score": 0.76,
        "disagreements": [],
        "results": [
            {
                "model_id": fallback_model_id,
                "conclusion": f"{object_name} 的主要结论待后续真实分析流程补齐。",
                "confidence": 0.76,
                "evidence_count": 0,
            }
        ],
        "updated_at": detail.get('updated_at'),
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def history_list(request: HttpRequest):
    """历史调研记录列表
    [route]: GET /api/v1/research/history
    """
    tasks = list_user_tasks(request.user.id)
    page = int(request.GET.get('page', 1) or 1)
    page_size = int(request.GET.get('page_size', 10) or 10)
    start = (page - 1) * page_size
    end = start + page_size

    history_items = []
    for task in tasks:
        history_items.append({
            "task_id": task.get('task_id'),
            "object_name": task.get('object_name'),
            "object_type": task.get('object_type'),
            "report_id": None,
            "status": task.get('status'),
            "created_at": task.get('created_at'),
        })

    return success_api_response({
        "list": history_items[start:end],
        "total": len(history_items),
        "page": page,
        "page_size": page_size,
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def history_detail(request: HttpRequest, task_id: int):
    """历史调研记录详情
    [route]: GET /api/v1/research/history/{task_id}
    """
    detail = get_task_detail(task_id)
    if not detail:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")

    return success_api_response({
        "task_id": str(task_id),
        "object_name": detail.get('object_name'),
        "object_type": detail.get('object_type'),
        "search_params": detail.get('search_params') or {},
        "fact_dataset": f"facts-{task_id}",
        "report_id": None,
        "status": detail.get('status'),
        "created_at": detail.get('created_at'),
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['research.view_research'])
def history_reload(request: HttpRequest, task_id: int):
    """重载历史调研结果
    [route]: POST /api/v1/research/history/{task_id}/reload
    """
    detail = get_task_detail(task_id)
    if not detail:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")

    return success_api_response({
        "task_id": str(task_id),
        "report_id": None,
        "redirect_url": f"/tasks/process?task_id={task_id}",
    })


@response_wrapper
@require_http_methods(["GET", "POST"])
@jwt_auth()
def task_intervention_entry(request: HttpRequest, task_id: int, node_id: int):
    """可干预节点入口
    [route]: GET /api/v1/research/tasks/{task_id}/interventions/{node_id}
    [route]: POST /api/v1/research/tasks/{task_id}/interventions/{node_id}
    """
    if request.method == 'GET':
        if not request.user.has_perms(['research.view_research']):
            return failed_api_response(ErrorCode.REFUSE_ACCESS, "您无权进行此操作")

        detail = get_task_intervention_detail(task_id, request.user.id, str(node_id))
        if not detail:
            return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "未找到可干预节点")

        return success_api_response(detail)

    if not request.user.has_perms(['research.create_research']):
        return failed_api_response(ErrorCode.REFUSE_ACCESS, "您无权进行此操作")

    payload = parse_json_body(request)
    action = payload.get('action') or request.POST.get('action')
    rule_changes = payload.get('rule_changes')
    comment = payload.get('comment')

    if not action:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "action 不能为空")

    success, message, response_payload = respond_to_step(
        task_id=task_id,
        user_id=request.user.id,
        node_id=str(node_id),
        action=action,
        response_data={
            'rule_changes': rule_changes,
            'comment': comment,
        },
    )

    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response(response_payload)


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_intervention_detail(request: HttpRequest, task_id: int, node_id: int):
    """获取可干预节点详情
    [route]: GET /api/v1/research/tasks/{task_id}/interventions/{node_id}
    """
    detail = get_task_intervention_detail(task_id, request.user.id, str(node_id))
    if not detail:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "未找到可干预节点")

    return success_api_response(detail)


@response_wrapper
@require_POST
@jwt_auth(perms=['research.create_research'])
def task_intervention_submit(request: HttpRequest, task_id: int, node_id: int):
    """用户介入调研任务，提供反馈
    [route]: POST /api/v1/research/tasks/{task_id}/interventions/{node_id}/submit
    """
    payload = parse_json_body(request)
    action = payload.get('action') or request.POST.get('action')
    rule_changes = payload.get('rule_changes')
    comment = payload.get('comment')

    if not action:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "action 不能为空")

    success, message, response_payload = respond_to_step(
        task_id=task_id,
        user_id=request.user.id,
        node_id=str(node_id),
        action=action,
        response_data={
            'rule_changes': rule_changes,
            'comment': comment,
        },
    )

    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response(response_payload)
