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
    create_research_task, get_task_detail,
    list_user_tasks, cancel_task, get_task_step_logs,
    get_task_conversation_history, continue_task_conversation,
)


@response_wrapper
@require_POST
@jwt_auth(perms=['research.create_research'])
def create_task(request: HttpRequest):
    """发起调研任务

    [route]: POST /api/research/task
    """
    title = request.POST.get('title')
    object_name = request.POST.get('object_name')
    object_type = request.POST.get('object_type')
    llm_config_id = request.POST.get('llm_config_id')
    # search_params 暂由前端传 JSON 字符串
    import json
    params_str = request.POST.get('search_params', '{}')
    try:
        search_params = json.loads(params_str)
    except (json.JSONDecodeError, TypeError):
        search_params = {}

    success, message, task_id = create_research_task(
        user_id=request.user.id,
        title=title, object_name=object_name,
        object_type=object_type,
        llm_config_id=int(llm_config_id) if llm_config_id else None,
        search_params=search_params,
    )
    if not success:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({"task_id": task_id})


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_detail(request: HttpRequest):
    """获取调研任务详情

    [route]: GET /api/research/task/detail?task_id=1
    """
    task_id = request.GET.get('task_id')
    if not task_id:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "task_id 不能为空")

    data = get_task_detail(int(task_id), request.user.id)
    if not data:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")

    return success_api_response(data)


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_list(request: HttpRequest):
    """获取当前用户的调研任务列表

    [route]: GET /api/research/task/list
    """
    tasks = list_user_tasks(request.user.id)
    return success_api_response(tasks)


@response_wrapper
@require_POST
@jwt_auth(perms=['research.cancel_research'])
def cancel_research_task(request: HttpRequest):
    """取消调研任务

    [route]: POST /api/research/task/cancel
    """
    task_id = request.POST.get('task_id')
    if not task_id:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "task_id 不能为空")

    success, message = cancel_task(int(task_id), request.user.id)
    if not success:
        return failed_api_response(ErrorCode.REFUSE_ACCESS, message)

    return success_api_response()


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_steps(request: HttpRequest):
    """获取任务步骤日志 (全流程监控)

    [route]: GET /api/research/task/steps?task_id=1
    """
    task_id = request.GET.get('task_id')
    if not task_id:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "task_id 不能为空")

    logs = get_task_step_logs(int(task_id), request.user.id)
    return success_api_response(logs)


@response_wrapper
@require_GET
@jwt_auth(perms=['research.view_research'])
def task_history(request: HttpRequest):
    """获取任务会话历史

    [route]: GET /api/research/task/history?task_id=1
    """
    task_id = request.GET.get('task_id')
    if not task_id:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "task_id 不能为空")

    history = get_task_conversation_history(int(task_id), request.user.id)
    if not history:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务或会话不存在")
    return success_api_response(history)


@response_wrapper
@require_POST
@jwt_auth(perms=['research.view_research'])
def task_followup(request: HttpRequest):
    """基于已保存会话继续追问

    [route]: POST /api/research/task/followup
    """
    task_id = request.POST.get('task_id')
    message = request.POST.get('message')
    if not task_id or not message:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            "task_id 和 message 不能为空"
        )

    success, error_message = continue_task_conversation(
        int(task_id), request.user.id, message
    )
    if not success:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            error_message or "继续追问失败"
        )

    return success_api_response({"task_id": int(task_id)})
