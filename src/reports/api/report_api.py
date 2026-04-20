"""
报告管理 API — 查看/导出/追问/历史
映射需求: FR-DYBG-0002 ~ FR-DYBG-0004, FR-JSDY-0005
"""
from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from reports.interface.report_interface import (
    create_followup,
    get_report_detail,
    list_reports_by_task,
    list_user_reports,
)
from reports.models.citation import Citation, ReportFollowup
from reports.models.report import Report
from shared.utils import (
    ErrorCode,
    failed_api_response,
    jwt_auth,
    parse_json_body,
    response_wrapper,
    success_api_response,
)


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def report_detail(request: HttpRequest):
    """获取报告详情（旧接口）

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
    """获取用户报告列表（旧接口）

    [route]: GET /api/reports/list?object_type=COMPANY
    """
    object_type = request.GET.get('object_type')
    reports = list_user_reports(request.user.id, object_type)
    return success_api_response(reports)


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def task_reports(request: HttpRequest):
    """获取指定任务的报告列表（旧接口）

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
    """报告深度追问（旧接口）

    [route]: POST /api/reports/followup
    """
    payload = parse_json_body(request)

    report_id = payload.get('report_id') or request.POST.get('report_id')
    question = payload.get('question') or request.POST.get('question')
    context_paragraph = payload.get('context_paragraph') or request.POST.get('context_paragraph')

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

    followup = ReportFollowup.objects.filter(pk=followup_id).first()
    return success_api_response({
        "followup_id": followup_id,
        "qa": serialize_report_followup(followup),
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def reports_collection(request: HttpRequest):
    """报告集合路由

    [route]: GET /api/v1/reports
    """
    keyword = (request.GET.get('keyword') or '').strip().lower()
    page = int(request.GET.get('page', 1) or 1)
    page_size = int(request.GET.get('page_size', 20) or 20)

    reports = list_user_reports(request.user.id)
    normalized_reports = []
    for item in reports:
        report_id = str(item.get('id'))
        title = item.get('title') or f"报告 {report_id}"
        object_name = item.get('task__object_name') or ''
        summary = f"面向 {object_name} 的调研报告" if object_name else None
        normalized_reports.append({
            "report_id": report_id,
            "task_id": str(item.get('task_id') or ''),
            "title": title,
            "summary": summary,
            "created_at": item.get('created_at').isoformat() if item.get('created_at') else None,
            "updated_at": item.get('created_at').isoformat() if item.get('created_at') else None,
        })

    if keyword:
        normalized_reports = [
            item for item in normalized_reports
            if keyword in (item.get('title') or '').lower()
            or keyword in (item.get('summary') or '').lower()
        ]

    total = len(normalized_reports)
    start = max(page - 1, 0) * page_size
    end = start + page_size

    return success_api_response({
        "list": normalized_reports[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def report_detail_v1(request: HttpRequest, report_id: int):
    """获取报告详情

    [route]: GET /api/v1/reports/{report_id}
    """
    report = Report.objects.filter(pk=report_id).select_related('task').first()
    if not report:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "报告不存在")

    citations = [serialize_report_citation(item) for item in report.citations.all().order_by('index_number')]
    return success_api_response({
        "report_id": str(report.id),
        "task_id": str(report.task_id),
        "title": report.title,
        "content": report.content_markdown or report.content_brief or report.summary or '',
        "citations": citations,
        "created_at": report.created_at.isoformat(),
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def report_citations(request: HttpRequest, report_id: int):
    """获取报告引用列表

    [route]: GET /api/v1/reports/{report_id}/citations
    """
    report = Report.objects.filter(pk=report_id).first()
    if not report:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "报告不存在")

    citations = [serialize_report_citation(item) for item in Citation.objects.filter(report=report).order_by('index_number')]
    return success_api_response({
        "report_id": str(report_id),
        "list": citations,
        "total": len(citations),
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def report_citation_detail(request: HttpRequest, report_id: int, citation_id: int):
    """获取单条引用详情

    [route]: GET /api/v1/reports/{report_id}/citations/{citation_id}
    """
    citation = Citation.objects.filter(report_id=report_id, pk=citation_id).first()
    if not citation:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "引用不存在")

    return success_api_response({
        **serialize_report_citation(citation),
        "report_id": str(report_id),
        "excerpt": citation.cited_text_snippet or '',
        "published_at": None,
        "source_type": 'web',
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def report_versions(request: HttpRequest, report_id: int):
    """获取报告版本列表

    [route]: GET /api/v1/reports/{report_id}/versions
    """
    report = Report.objects.filter(pk=report_id).select_related('task').first()
    if not report:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "报告不存在")

    versions = Report.objects.filter(task_id=report.task_id).order_by('-version', '-created_at')
    serialized_versions = [{
        "version_id": str(item.id),
        "version_no": item.version,
        "title": item.title,
        "created_at": item.created_at.isoformat(),
        "created_by": None,
        "change_note": '自动生成版本',
    } for item in versions]

    return success_api_response({
        "report_id": str(report_id),
        "current_version_id": str(report.id),
        "versions": serialized_versions,
    })


@response_wrapper
@require_http_methods(["GET", "POST"])
@jwt_auth(perms=['reports.followup_report'])
def report_qa_collection(request: HttpRequest, report_id: int):
    """报告追问集合

    [route]: GET /api/v1/reports/{report_id}/qa
    [route]: POST /api/v1/reports/{report_id}/qa
    """
    report = Report.objects.filter(pk=report_id).first()
    if not report:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "报告不存在")

    if request.method == 'GET':
        qa_items = ReportFollowup.objects.filter(report_id=report_id, user_id=request.user.id).order_by('-created_at')
        serialized = [serialize_report_followup(item) for item in qa_items]
        return success_api_response({
            "report_id": str(report_id),
            "list": serialized,
            "total": len(serialized),
        })

    payload = parse_json_body(request)
    question = (payload.get('question') or request.POST.get('question') or '').strip()
    if not question:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "question 不能为空")

    success, message, followup_id = create_followup(report_id, request.user.id, question)
    if not success:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, message)

    followup = ReportFollowup.objects.filter(pk=followup_id).first()
    return success_api_response({
        "report_id": str(report_id),
        "qa": serialize_report_followup(followup),
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['reports.followup_report'])
def append_report_qa(request: HttpRequest, report_id: int, qa_id: int):
    """追加追问

    [route]: POST /api/v1/reports/{report_id}/qa/{qa_id}/append
    """
    followup = ReportFollowup.objects.filter(report_id=report_id, pk=qa_id, user_id=request.user.id).first()
    if not followup:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "追问记录不存在")

    payload = parse_json_body(request)
    append_text = (payload.get('append_text') or request.POST.get('append_text') or '').strip()
    if not append_text:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "append_text 不能为空")

    followup.question = f"{followup.question}\n\n补充追问：{append_text}"
    followup.answer = (followup.answer or '待补充回答') + f"\n\n针对追加问题：{append_text}"
    followup.save(update_fields=['question', 'answer'])

    return success_api_response({
        "report_id": str(report_id),
        "qa": serialize_report_followup(followup),
    })


def serialize_report_citation(citation: Citation) -> dict:
    return {
        "citation_id": str(citation.id),
        "source_title": citation.source_title,
        "source_url": citation.source_url,
    }


def serialize_report_followup(followup: ReportFollowup | None) -> dict | None:
    if not followup:
        return None

    updated_at = getattr(followup, 'updated_at', None) or followup.created_at
    return {
        "qa_id": str(followup.id),
        "question": followup.question,
        "answer": followup.answer or '待生成回答',
        "status": 'completed' if followup.answer else 'pending',
        "created_at": followup.created_at.isoformat(),
        "updated_at": updated_at.isoformat() if updated_at else followup.created_at.isoformat(),
    }
