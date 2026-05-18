"""
报告管理 API — 查看/导出/追问/历史
映射需求: FR-DYBG-0002 ~ FR-DYBG-0004, FR-JSDY-0005
"""
import json

from django.http import HttpRequest
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from research.models.scraped_content import ScrapedContent
from shared.utils import (
    ErrorCode, failed_api_response, response_wrapper,
    success_api_response, jwt_auth
)
from reports.interface.report_interface import (
    get_report_detail, list_reports_by_task,
    list_user_reports, create_followup, list_report_versions,
    export_report_file, get_export_record, trigger_manual_export,
    append_report_followup,
)
from reports.models.citation import Citation, ReportFollowup
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
        current_version = next((item for item in versions if item.get("is_latest")), None)
        return success_api_response({
            "report_id": str(report_id),
            "current_version_id": (
                current_version.get("version_id") if current_version else versions[0].get("version_id")
            ),
            "versions": versions,
        })

    report_mode = request.GET.get('report_mode', 'full')
    data = get_report_detail(report_id, report_mode)
    if not data:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "报告不存在")

    return success_api_response(_serialize_report_detail(data))


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
        "list": [_serialize_report_list_item(report) for report in reports],
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
def export_status(request: HttpRequest, export_id: str):
    """获取导出状态
    [route]: GET /api/v1/reports/exports/{export_id}/status
    """
    try:
        parsed_export_id = int(export_id)
    except (TypeError, ValueError):
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "export_id 必须是数字")

    data = get_export_record(parsed_export_id)
    if not data:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "导出记录不存在")
    return success_api_response(data)


@response_wrapper
@jwt_auth(perms=['reports.followup_report'])
def followup_question(request: HttpRequest, report_id: int):
    """报告深度追问
    [route]: POST /api/v1/reports/{report_id}/qa
    """
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


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def report_citations(request: HttpRequest, report_id: int):
    report = get_report_detail(report_id)
    if not report:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "报告不存在")
    rows = [_serialize_citation(item) for item in report.get("citations", [])]
    return success_api_response({
        "report_id": str(report_id),
        "list": rows,
        "total": len(rows),
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['reports.view_report'])
def report_citation_detail(request: HttpRequest, report_id: int, citation_id: int):
    citation = Citation.objects.filter(pk=citation_id, report_id=report_id).first()
    if not citation:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "引用不存在")

    scraped_content = (
        ScrapedContent.objects
        .filter(task_id=citation.report.task_id, source_url=citation.source_url)
        .order_by('-relevance_score', '-scraped_at', '-id')
        .first()
    )
    payload = _serialize_citation(_citation_detail_payload(citation))
    return success_api_response({
        **payload,
        "report_id": str(report_id),
        "excerpt": citation.cited_text_snippet or "",
        "published_at": (
            _isoformat(scraped_content.scraped_at)
            if scraped_content and scraped_content.scraped_at
            else ""
        ),
        "source_type": scraped_content.source_type if scraped_content else payload.get("source_type", ""),
    })


@response_wrapper
@jwt_auth(perms=['reports.view_report'])
def report_share(request: HttpRequest, report_id: int, share_id: str = None):
    if request.method == 'POST':
        return success_api_response({
            "share_id": f"report-{report_id}",
            "report_id": str(report_id),
            "share_url": f"/reports/share/report-{report_id}",
            "expires_at": None,
        })
    if request.method == 'DELETE':
        return success_api_response({"result": "success", "share_id": share_id or ""})
    return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "不支持的请求方法")


@response_wrapper
def public_shared_report(request: HttpRequest, share_id: str):
    if not share_id.startswith("report-"):
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "分享不存在")
    try:
        report_id = int(share_id.removeprefix("report-"))
    except ValueError:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "分享不存在")
    report = get_report_detail(report_id)
    if not report:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "报告不存在")
    return success_api_response({
        "share_id": share_id,
        "report": _serialize_report_detail(report),
        "allow_download": True,
        "expires_at": None,
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['reports.followup_report'])
def append_followup(request: HttpRequest, report_id: int, qa_id: int):
    try:
        payload = json.loads(request.body) if request.body else {}
    except Exception:
        payload = request.POST
    append_text = payload.get("append_text") or ""
    success, message, followup_id = append_report_followup(
        report_id,
        qa_id,
        request.user.id,
        append_text,
    )
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message or "追加追问失败")

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
        "created_at": _isoformat(followup.created_at),
        "updated_at": _isoformat(followup.created_at),
    }


def _serialize_citation(citation):
    if isinstance(citation, dict):
        citation_id = citation.get("id") or citation.get("citation_id") or citation.get("index_number") or ""
        return {
            "citation_id": str(citation_id),
            "index_number": int(citation.get("index_number") or 0),
            "cite_key": citation.get("cite_key") or "",
            "source_title": citation.get("source_title") or "",
            "source_url": citation.get("source_url") or "",
            "source_type": citation.get("source_type") or "",
            "source_platform": citation.get("source_platform") or "",
            "accessed_at": citation.get("accessed_at") or "",
            "reproduction_code": citation.get("reproduction_code") or "",
            "bibtex": citation.get("bibtex") or "",
        }
    return {
        "citation_id": str(citation.id),
        "index_number": citation.index_number,
        "cite_key": "",
        "source_title": citation.source_title,
        "source_url": citation.source_url,
        "source_type": "",
        "source_platform": "",
        "accessed_at": "",
        "reproduction_code": citation.reproduction_code or "",
        "bibtex": "",
    }


def _isoformat(value):
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return timezone.localtime(value).isoformat()
    parsed = parse_datetime(str(value))
    if parsed is not None:
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_default_timezone())
        return timezone.localtime(parsed).isoformat()
    return str(value)


def _citation_detail_payload(citation: Citation) -> dict:
    report = get_report_detail(citation.report_id)
    if report:
        citation_id = str(citation.id)
        for item in report.get("citations", []):
            if str(item.get("id") or item.get("citation_id") or "") == citation_id:
                return item
    return {
        "id": citation.id,
        "index_number": citation.index_number,
        "source_title": citation.source_title,
        "source_url": citation.source_url,
        "reproduction_code": citation.reproduction_code or "",
    }


def _serialize_report_list_item(report: dict):
    return {
        "report_id": str(report.get("id") or report.get("report_id") or ""),
        "task_id": str(report.get("task_id") or ""),
        "title": report.get("title") or "",
        "summary": report.get("summary") or "",
        "created_at": _isoformat(report.get("created_at")),
        "updated_at": _isoformat(report.get("updated_at")),
    }


def _serialize_report_detail(report: dict):
    return {
        "report_id": str(report.get("id") or report.get("report_id") or ""),
        "task_id": str(report.get("task_id") or ""),
        "title": report.get("title") or "",
        "summary": report.get("summary") or "",
        "content": report.get("content") or report.get("content_markdown") or "",
        "content_markdown": report.get("content_markdown") or "",
        "content_brief": report.get("content_brief") or "",
        "report_mode": report.get("report_mode") or "full",
        "citations": [
            _serialize_citation(item)
            for item in report.get("citations", [])
        ],
        "references_bibtex": report.get("references_bibtex") or "",
        "created_at": _isoformat(report.get("created_at")),
    }
