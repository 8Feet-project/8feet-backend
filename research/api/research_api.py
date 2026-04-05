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
    list_user_tasks, cancel_task, get_task_step_logs
)


@response_wrapper
@require_POST
@jwt_auth()
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
@jwt_auth()
def task_detail(request: HttpRequest):
    """获取调研任务详情

    [route]: GET /api/research/task/detail?task_id=1
    """
    task_id = request.GET.get('task_id')
    if not task_id:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "task_id 不能为空")

    data = get_task_detail(int(task_id))
    if not data:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "任务不存在")

    return success_api_response(data)


@response_wrapper
@require_GET
@jwt_auth()
def task_list(request: HttpRequest):
    """获取当前用户的调研任务列表

    [route]: GET /api/research/task/list
    """
    tasks = list_user_tasks(request.user.id)
    return success_api_response(tasks)


@response_wrapper
@require_POST
@jwt_auth()
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
@jwt_auth()
def task_steps(request: HttpRequest):
    """获取任务步骤日志 (全流程监控)

    [route]: GET /api/research/task/steps?task_id=1
    """
    task_id = request.GET.get('task_id')
    if not task_id:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "task_id 不能为空")

    logs = get_task_step_logs(int(task_id))
    return success_api_response(logs)
