"""
调研任务 API — 任务发起/查询/取消/步骤日志
映射需求: FR-JSDY-0001 ~ FR-JSDY-0006
"""
import json

from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

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
