"""
报告管理 API — 查看/导出/追问/历史
映射需求: FR-DYBG-0002 ~ FR-DYBG-0004, FR-JSDY-0005
"""
import json

from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

from shared.utils import (
    ErrorCode, failed_api_response, response_wrapper,
    success_api_response, jwt_auth
)
from reports.interface.report_interface import (
    get_report_detail, list_reports_by_task,
    list_user_reports, create_followup, list_report_versions,
    export_report_file, get_export_record, trigger_manual_export
)
from reports.tasks import export_report_task


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def report_detail(request: HttpRequest, report_id: int):
    """获取报告详情/版本/导出
    [route]: GET /api/v1/reports/{report_id}
    """
    if 'versions' in request.path:
        versions = list_report_versions(report_id)
        if not versions:
            return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "报告不存在")
        return success_api_response({"list": versions, "total": len(versions)})

    report_mode = request.GET.get('report_mode', 'full')
    data = get_report_detail(report_id, report_mode)
    if not data:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "报告不存在")

    return success_api_response(data)


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def report_list(request: HttpRequest):
    """获取报告列表
    [route]: GET /api/v1/reports
    """
    task_id = request.GET.get('task_id')
    object_type = request.GET.get('object_type')
    
    if task_id:
        reports = list_reports_by_task(int(task_id))
    else:
        reports = list_user_reports(request.user.id, object_type)
        
    return success_api_response({
        "list": reports,
        "total": len(reports)
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['reports.view_report'])
def export_report(request: HttpRequest, report_id: int):
    """导出报告
    [route]: POST /api/v1/reports/{report_id}/export
    """
    try:
        payload = json.loads(request.body) if request.body else {}
    except Exception:
        payload = request.POST

    export_format = payload.get('format')
    report_mode = payload.get('report_mode', 'full')
    if not export_format:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "format 不能为空"
        )

    success, message, data = export_report_file(report_id, export_format, report_mode)
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    export_report_task.delay(int(data['export_id']))
    return success_api_response(data)


@response_wrapper
@require_POST
@jwt_auth(perms=['reports.view_report'])
def manual_export_report(request: HttpRequest, report_id: int):
    """手动触发报告导出
    [route]: POST /api/v1/reports/{report_id}/manual-export
    """
    try:
        payload = json.loads(request.body) if request.body else {}
    except Exception:
        payload = request.POST

    export_format = payload.get('format')
    report_mode = payload.get('report_mode', 'full')
    if not export_format:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "format 不能为空"
        )

    success, message, data = trigger_manual_export(report_id, export_format, report_mode)
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    export_report_task.delay(int(data['export_id']))
    return success_api_response(data)


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def export_status(request: HttpRequest, export_id: int):
    """获取导出状态
    [route]: GET /api/v1/reports/exports/{export_id}/status
    """
    data = get_export_record(export_id)
    if not data:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "导出记录不存在")
    return success_api_response(data)


@response_wrapper
@jwt_auth(perms=['reports.followup_report'])
def followup_question(request: HttpRequest, report_id: int):
    """报告深度追问
    [route]: POST /api/v1/reports/{report_id}/qa
    """
    from reports.models.citation import ReportFollowup

    if request.method == 'GET':
        rows = [
            _serialize_followup(item)
            for item in ReportFollowup.objects.filter(report_id=report_id, user=request.user)
        ]
        return success_api_response({
            "report_id": str(report_id),
            "list": rows,
            "total": len(rows),
        })

    if request.method != 'POST':
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "不支持的请求方法")

    try:
        payload = json.loads(request.body) if request.body else {}
    except Exception:
        payload = request.POST

    question = payload.get('question')
    context_paragraph = payload.get('context_paragraph')

    if not question:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "question 不能为空")

    success, message, followup_id = create_followup(
        report_id, request.user.id, question, context_paragraph
    )
    if not success:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, message)

    followup = ReportFollowup.objects.get(pk=followup_id)
    return success_api_response({
        "report_id": str(report_id),
        "qa": _serialize_followup(followup),
    })


def _serialize_followup(followup):
    return {
        "qa_id": str(followup.id),
        "question": followup.question,
        "answer": followup.answer or "",
        "status": "completed" if followup.answer else "pending",
        "created_at": followup.created_at.isoformat(),
        "updated_at": followup.created_at.isoformat(),
    }
