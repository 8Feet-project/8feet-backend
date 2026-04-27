"""
报告管理业务逻辑 — interface 层
"""
from typing import Tuple, Optional, List

from reports.models.report import Report
from reports.models.export_record import ReportExportRecord
from reports.models.citation import Citation, ReportFollowup
from reports.interface.export_utils import (
    build_brief_from_markdown,
    export_docx,
    export_html,
    export_markdown,
    export_pdf,
    get_report_content,
    normalize_report_mode,
)
from reports.interface.storage_utils import upload_report_file


def get_report_detail(report_id: int, report_mode: str = 'full') -> Optional[dict]:
    """获取报告详情"""
    report = Report.objects.filter(pk=report_id).first()
    if not report:
        return None

    report_mode = normalize_report_mode(report_mode)
    if not report.content_brief and report.content_markdown:
        report.content_brief = build_brief_from_markdown(report)
        report.save(update_fields=['content_brief'])

    citations = list(Citation.objects.filter(report=report).values(
        'index_number', 'source_url', 'source_title', 'cited_text_snippet'
    ))

    return {
        "id": report.id,
        "task_id": report.task_id,
        "title": report.title,
        "summary": report.summary,
        "content_markdown": report.content_markdown,
        "content_brief": report.content_brief,
        "content": get_report_content(report, report_mode),
        "report_mode": report_mode,
        "file_pdf_path": report.file_pdf_path,
        "file_word_path": report.file_word_path,
        "version": report.version,
        "citations": citations,
        "created_at": report.created_at.isoformat(),
    }


def list_reports_by_task(task_id: int) -> List[dict]:
    """获取某任务的所有报告版本"""
    return list(Report.objects.filter(task_id=task_id).values(
        'id', 'task_id', 'title', 'summary', 'version', 'is_latest', 'created_at'
    ))


def list_report_versions(report_id: int) -> List[dict]:
    """获取报告版本列表"""
    report = Report.objects.filter(pk=report_id).select_related('task').first()
    if not report:
        return []

    query = Report.objects.filter(task_id=report.task_id).order_by('-version', '-created_at')
    return [
        {
            "version_id": str(item.id),
            "version_no": item.version,
            "id": item.id,
            "title": item.title,
            "version": item.version,
            "is_latest": item.is_latest,
            "created_at": item.created_at.isoformat(),
        }
        for item in query
    ]


def list_user_reports(user_id: int, object_type: str = None) -> List[dict]:
    """获取用户所有调研报告 (历史管理)

    FR-DYBG-0004: 调研历史管理
    """
    query = Report.objects.filter(task__user_id=user_id, is_latest=True)
    if object_type:
        query = query.filter(task__object_type=object_type)

    return list(query.values(
        'id', 'task_id', 'title', 'summary', 'task__object_name', 'task__object_type',
        'version', 'created_at'
    ))


def create_followup(
    report_id: int, user_id: int,
    question: str, context_paragraph: str = None
) -> Tuple[bool, Optional[str], Optional[int]]:
    """创建报告追问

    FR-JSDY-0005: 报告深度追问
    """
    report = Report.objects.filter(pk=report_id).first()
    if not report:
        return (False, "报告不存在", None)

    followup = ReportFollowup.objects.create(
        report=report,
        user_id=user_id,
        question=question,
        context_paragraph=context_paragraph,
        answer=None,
    )
    from research.interface.research_interface import continue_task_conversation

    prompt = question
    if context_paragraph:
        prompt = f"请优先围绕以下报告段落回答。\n\n{context_paragraph}\n\n问题:\n{question}"
    success, message = continue_task_conversation(
        report.task_id,
        user_id,
        prompt,
        run_metadata={"report_followup_id": followup.id},
    )
    if not success:
        followup.answer = f"追问任务启动失败: {message}"
        followup.save(update_fields=['answer'])
        return (False, message, None)
    return (True, None, followup.id)


def export_report_file(report_id: int, export_format: str, report_mode: str = 'full') -> Tuple[bool, str, Optional[dict]]:
    """创建导出任务"""
    report = Report.objects.filter(pk=report_id).prefetch_related('citations').first()
    if not report:
        return False, "报告不存在", None

    report_mode = normalize_report_mode(report_mode)
    if not report.content_brief and report.content_markdown:
        report.content_brief = build_brief_from_markdown(report)
        report.save(update_fields=['content_brief'])

    fmt = (export_format or '').lower()
    if fmt not in ('md', 'pdf', 'docx', 'word', 'html'):
        return False, "仅支持导出 md、pdf、docx、word、html 格式", None

    export_record = ReportExportRecord.objects.create(
        report=report,
        export_format=fmt,
        report_mode=report_mode,
        status='QUEUED',
    )

    return True, "导出成功", {
        "report_id": report.id,
        "export_id": str(export_record.id),
        "format": fmt,
        "report_mode": report_mode,
        "status": "queued",
    }


def get_export_record(export_id: int) -> Optional[dict]:
    """获取导出状态"""
    record = ReportExportRecord.objects.select_related('report').filter(pk=export_id).first()
    if not record:
        return None

    return {
        "export_id": str(record.id),
        "report_id": record.report_id,
        "status": record.status.lower(),
        "format": record.export_format,
        "report_mode": record.report_mode,
        "download_url": record.download_url,
        "storage_path": record.storage_path,
        "error_message": record.error_message,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def run_export_job(export_id: int) -> dict:
    """执行真实导出任务。"""
    record = ReportExportRecord.objects.select_related('report').prefetch_related('report__citations').filter(pk=export_id).first()
    if not record:
        raise ValueError(f"导出记录不存在: {export_id}")

    report = record.report
    record.status = 'PROCESSING'
    record.error_message = None
    record.save(update_fields=['status', 'error_message', 'updated_at'])

    try:
        if record.export_format == 'md':
            file_path, _ = export_markdown(report, record.report_mode)
        elif record.export_format in ('docx', 'word'):
            file_path = export_docx(report, record.report_mode)
        elif record.export_format == 'pdf':
            file_path = export_pdf(report, record.report_mode)
        elif record.export_format == 'html':
            file_path = export_html(report, record.report_mode)
        else:
            raise ValueError("仅支持导出 md、pdf、docx、word、html 格式")

        object_key, download_url = upload_report_file(
            file_path, object_name=_build_export_object_key(report, record.export_format, record.report_mode, file_path)
        )

        if record.export_format in ('docx', 'word'):
            report.file_word_path = download_url
            report.save(update_fields=['file_word_path'])
        elif record.export_format == 'pdf':
            report.file_pdf_path = download_url
            report.save(update_fields=['file_pdf_path'])

        record.status = 'COMPLETED'
        record.storage_path = object_key
        record.download_url = download_url
        record.error_message = None
        record.save(update_fields=['status', 'storage_path', 'download_url', 'error_message', 'updated_at'])
        return {
            "export_id": str(record.id),
            "report_id": report.id,
            "status": "completed",
            "download_url": download_url,
        }
    except Exception as exc:
        record.status = 'FAILED'
        record.error_message = str(exc)
        record.save(update_fields=['status', 'error_message', 'updated_at'])
        raise


def trigger_manual_export(report_id: int, export_format: str, report_mode: str = 'full') -> Tuple[bool, str, Optional[dict]]:
    """手动触发报告导出任务。"""
    return export_report_file(report_id, export_format, report_mode)


def _build_export_object_key(report: Report, export_format: str, report_mode: str, file_path: str) -> str:
    from pathlib import Path

    filename = Path(file_path).name
    return f"reports/{report.task_id}/v{report.version}/{report_mode}/{export_format}/{filename}"
