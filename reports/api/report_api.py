"""
报告管理 API — 查看/导出/追问/历史
映射需求: FR-DYBG-0002 ~ FR-DYBG-0004, FR-JSDY-0005
"""
from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

from shared.utils import (
    ErrorCode, failed_api_response, response_wrapper,
    success_api_response, jwt_auth
)
from reports.interface.report_interface import (
    get_report_detail, list_reports_by_task,
    list_user_reports, create_followup
)


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def report_detail(request: HttpRequest):
    """获取报告详情

    [route]: GET /api/reports/detail?report_id=1
    """
    report_id = request.GET.get('report_id')
    if not report_id:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "report_id 不能为空")

    data = get_report_detail(int(report_id))
    if not data:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "报告不存在")

    return success_api_response(data)


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def report_list(request: HttpRequest):
    """获取用户报告列表 (历史管理)

    [route]: GET /api/reports/list?object_type=COMPANY
    """
    object_type = request.GET.get('object_type')
    reports = list_user_reports(request.user.id, object_type)
    return success_api_response(reports)


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def task_reports(request: HttpRequest):
    """获取指定任务的报告列表

    [route]: GET /api/reports/by-task?task_id=1
    """
    task_id = request.GET.get('task_id')
    if not task_id:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "task_id 不能为空")

    reports = list_reports_by_task(int(task_id))
    return success_api_response(reports)


@response_wrapper
@require_POST
@jwt_auth(perms=['reports.followup_report'])
def followup_question(request: HttpRequest):
    """报告深度追问

    [route]: POST /api/reports/followup
    """
    report_id = request.POST.get('report_id')
    question = request.POST.get('question')
    context_paragraph = request.POST.get('context_paragraph')

    if not report_id or not question:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            "report_id 和 question 不能为空"
        )

    success, message, followup_id = create_followup(
        int(report_id), request.user.id, question, context_paragraph
    )
    if not success:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, message)

    return success_api_response({"followup_id": followup_id})
