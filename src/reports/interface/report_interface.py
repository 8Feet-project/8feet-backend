"""
报告管理业务逻辑 — interface 层
"""
import re
from typing import Any, Tuple, Optional, List

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


DSML_WRITE_FILE_CONTENT_RE = re.compile(
    r'<｜DSML｜parameter\s+name="content"[^>]*>(?P<content>.*?)(?:</｜DSML｜parameter>|</｜DSML｜invoke>|</｜DSML｜tool_calls>|$)',
    re.S,
)
DSML_BLOCK_RE = re.compile(r"<｜DSML｜.*", re.S)
AUTO_REFERENCES_RE = re.compile(
    r"\n?<!-- efeet:auto-references:start -->.*?<!-- efeet:auto-references:end -->\s*",
    re.S,
)
CITE_KEY_RE = re.compile(r"\[@([A-Za-z0-9_.:-]+)\]")


def _normalize_cite_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _python_literal(value: Any) -> str:
    return repr(value)


def _structured_tool_reproduction_code(state_item: dict[str, Any]) -> str:
    if str(state_item.get("url") or "").strip():
        return ""
    params = state_item.get("tool_params")
    if not isinstance(params, dict) or not params:
        return ""
    tool_name = str(state_item.get("tool_name") or "").strip()
    provider = str(state_item.get("provider") or state_item.get("source_platform") or "").strip()
    if not tool_name:
        return ""
    if provider.lower() not in {"akshare", "csrc", "tushare"} and not tool_name.startswith(("akshare", "csrc", "tushare")):
        return ""
    rendered_args = ", ".join(
        f"{_python_literal(key)}: {_python_literal(value)}"
        for key, value in params.items()
    )
    return "\n".join(
        [
            "from efeet.tools import get_available_tools",
            "",
            f"tool = next(tool for tool in get_available_tools() if tool.name == {_python_literal(tool_name)})",
            f"result = tool.invoke({{{rendered_args}}})",
            "print(result)",
        ]
    )


def get_report_detail(report_id: int, report_mode: str = 'full') -> Optional[dict]:
    """获取报告详情"""
    report = Report.objects.filter(pk=report_id).first()
    if not report:
        return None

    report_mode = normalize_report_mode(report_mode)
    full_content = normalize_report_markdown(report.content_markdown)
    brief_content = normalize_report_markdown(report.content_brief)
    if not report.content_brief and report.content_markdown:
        report.content_brief = build_brief_from_markdown(report)
        report.save(update_fields=['content_brief'])
        brief_content = normalize_report_markdown(report.content_brief)

    citations = list(Citation.objects.filter(report=report).values(
        'id', 'index_number', 'source_url', 'source_title', 'cited_text_snippet',
        'reproduction_code'
    ))
    enriched_citations = _enrich_citations_from_state(report, citations)

    return {
        "id": report.id,
        "task_id": report.task_id,
        "title": report.title,
        "summary": normalize_report_markdown(report.summary),
        "content_markdown": full_content,
        "content_brief": brief_content,
        "content": brief_content if report_mode == "brief" else full_content,
        "report_mode": report_mode,
        "file_pdf_path": report.file_pdf_path,
        "file_word_path": report.file_word_path,
        "version": report.version,
        "citations": enriched_citations,
        "references_bibtex": _render_bibtex(enriched_citations),
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


def normalize_report_markdown(text: str | None) -> str:
    """Strip agent tool-call envelopes that can leak into stored markdown."""
    raw = (text or "").strip()
    if not raw:
        return ""

    matched = DSML_WRITE_FILE_CONTENT_RE.search(raw)
    if matched:
        raw = matched.group("content").strip()

    raw = AUTO_REFERENCES_RE.sub("", raw).strip()
    raw = DSML_BLOCK_RE.sub("", raw).strip()
    return raw


def cite_keys_from_markdown(markdown_text: str) -> list[str]:
    seen: set[str] = set()
    keys: list[str] = []
    for match in CITE_KEY_RE.finditer(markdown_text or ""):
        key = _normalize_cite_key(match.group(1))
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def _enrich_citations_from_state(report: Report, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    state_citations = _state_citations_by_url(report)
    state_citations_by_key = _state_citations_by_key(report)
    used_keys = cite_keys_from_markdown(
        "\n".join(
            [
                normalize_report_markdown(report.content_markdown),
                normalize_report_markdown(report.content_brief),
                normalize_report_markdown(report.summary),
            ]
        )
    )
    used_key_set = set(used_keys)
    used_key_by_url = {}
    for item in state_citations.values():
        url = str(item.get("url", "") or "").strip()
        cite_key = _normalize_cite_key(item.get("cite_key"))
        if url and cite_key and cite_key in used_key_set:
            used_key_by_url[url] = cite_key

    enriched: list[dict[str, Any]] = []
    for position, item in enumerate(citations):
        url = str(item.get("source_url", "") or "").strip()
        state_item = state_citations.get(url, {})
        fallback_key = used_keys[position] if position < len(used_keys) else ""
        cite_key = _normalize_cite_key(state_item.get("cite_key") or used_key_by_url.get(url) or fallback_key)
        if cite_key and not state_item:
            state_item = state_citations_by_key.get(cite_key, {})
        enriched.append(
            {
                **item,
                "cite_key": cite_key,
                "source_platform": state_item.get("source_platform") or "",
                "source_type": _source_type_from_state_item(state_item),
                "accessed_at": state_item.get("accessed_at") or "",
                "reproduction_code": (
                    item.get("reproduction_code")
                    or state_item.get("reproduction_code")
                    or _structured_tool_reproduction_code(state_item)
                    or ""
                ),
                "bibtex": _render_bibtex_entry(cite_key, item, state_item) if cite_key else "",
            }
        )
    return enriched


def _source_type_from_state_item(state_item: dict[str, Any]) -> str:
    source_category = str(state_item.get("source_category") or "").strip()
    provider = str(state_item.get("provider") or state_item.get("source_platform") or "").strip().lower()
    tool_name = str(state_item.get("tool_name") or "").strip().lower()
    if source_category and source_category != "web":
        return source_category
    if not str(state_item.get("url") or "").strip() and (
        provider in {"akshare", "csrc", "tushare"}
        or tool_name.startswith(("akshare", "csrc", "tushare"))
    ):
        return "structured_financial_data"
    return source_category or state_item.get("endpoint") or ""


def _state_citations_by_url(report: Report) -> dict[str, dict[str, Any]]:
    conversation = getattr(report.task, "conversation", None)
    state = getattr(conversation, "state_snapshot", None) if conversation is not None else None
    citations = state.get("citations", []) if isinstance(state, dict) else []
    if not isinstance(citations, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in citations:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "") or "").strip()
        if not url:
            continue
        result[url] = item
    return result


def _state_citations_by_key(report: Report) -> dict[str, dict[str, Any]]:
    conversation = getattr(report.task, "conversation", None)
    state = getattr(conversation, "state_snapshot", None) if conversation is not None else None
    citations = state.get("citations", []) if isinstance(state, dict) else []
    if not isinstance(citations, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in citations:
        if not isinstance(item, dict):
            continue
        cite_key = _normalize_cite_key(item.get("cite_key"))
        if not cite_key:
            continue
        result[cite_key] = item
    return result


def _bibtex_escape(value: Any) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace('"', '\\"')
    )


def _render_bibtex_entry(cite_key: str, citation: dict[str, Any], state_item: dict[str, Any]) -> str:
    cite_key = _normalize_cite_key(cite_key)
    title = state_item.get("title") or citation.get("source_title") or cite_key
    fields = [
        ("title", title),
        ("url", state_item.get("url") or citation.get("source_url") or ""),
        ("sourceplatform", state_item.get("source_platform") or ""),
        ("provider", state_item.get("provider") or ""),
        ("toolname", state_item.get("tool_name") or ""),
        ("howpublished", state_item.get("howpublished") or ""),
        ("note", state_item.get("note") or ""),
        ("accessedat", state_item.get("accessed_at") or ""),
    ]
    rendered_fields = [
        f"  {name} = {{{_bibtex_escape(value)}}}"
        for name, value in fields
        if str(value or "").strip()
    ]
    entry_type = str(state_item.get("entry_type") or "misc")
    return f"@{entry_type}{{{cite_key},\n" + ",\n".join(rendered_fields) + "\n}"


def _render_bibtex(citations: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        str(item.get("bibtex") or "").strip()
        for item in citations
        if str(item.get("bibtex") or "").strip()
    )
