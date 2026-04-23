"""
调研任务 API — 任务发起/查询/取消/步骤日志
映射需求: FR-JSDY-0001 ~ FR-JSDY-0006
"""
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


@response_wrapper
@require_POST
@jwt_auth(perms=['research.create_research'])
def create_task(request: HttpRequest):
    """发起调研任务

    [route]: POST /api/v1/research/task
    """
    title = request.POST.get('title')
    object_name = request.POST.get('object_name')
    object_type = request.POST.get('object_type')
    llm_config_id = request.POST.get('llm_config_id')

    import json
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
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            message,
        )

    return success_api_response({"task_id": task_id})


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
    return success_api_response({"task_id": task_id, "nodes": logs})


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
