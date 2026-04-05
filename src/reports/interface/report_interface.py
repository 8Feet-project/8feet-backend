"""
报告管理业务逻辑 — interface 层
"""
from typing import Tuple, Optional, List

from reports.models.report import Report
from reports.models.citation import Citation, ReportFollowup


def get_report_detail(report_id: int) -> Optional[dict]:
    """获取报告详情"""
    report = Report.objects.filter(pk=report_id).first()
    if not report:
        return None

    citations = list(Citation.objects.filter(report=report).values(
        'index_number', 'source_url', 'source_title', 'cited_text_snippet'
    ))

    return {
        "id": report.id,
        "title": report.title,
        "summary": report.summary,
        "content_markdown": report.content_markdown,
        "content_brief": report.content_brief,
        "file_pdf_path": report.file_pdf_path,
        "file_word_path": report.file_word_path,
        "version": report.version,
        "citations": citations,
        "created_at": report.created_at.isoformat(),
    }


def list_reports_by_task(task_id: int) -> List[dict]:
    """获取某任务的所有报告版本"""
    return list(Report.objects.filter(task_id=task_id).values(
        'id', 'title', 'version', 'is_latest', 'created_at'
    ))


def list_user_reports(user_id: int, object_type: str = None) -> List[dict]:
    """获取用户所有调研报告 (历史管理)

    FR-DYBG-0004: 调研历史管理
    """
    query = Report.objects.filter(task__user_id=user_id, is_latest=True)
    if object_type:
        query = query.filter(task__object_type=object_type)

    return list(query.values(
        'id', 'title', 'task__object_name', 'task__object_type',
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
        answer=None,  # TODO: 后续集成 LLM 回答
    )
    return (True, None, followup.id)
