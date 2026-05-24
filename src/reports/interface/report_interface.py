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
from reports.interface.storage_utils import build_report_download_url, store_report_file
from efeet.references import (
    assess_source_authority,
    authority_display_label,
    authority_tier_for_score,
    normalize_authority_score,
)


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


CSRC_BASE_URL = "https://www.csrc.gov.cn"
CSRC_CHANNELS = {
    "administrative_penalties": {
        "channel_id": "28de6b87eda140cb93de4dd10d11867d",
        "source_page": "https://www.csrc.gov.cn/csrc/c101928/common_list.shtml?channelid=28de6b87eda140cb93de4dd10d11867d",
    },
    "market_bans": {
        "channel_id": "e5a95ae5aea54c1f9dde255f21f67799",
        "source_page": "https://www.csrc.gov.cn/csrc/c101927/common_list.shtml?channelid=e5a95ae5aea54c1f9dde255f21f67799",
    },
}


def _csrc_reproduction_code(params: dict[str, Any]) -> str:
    endpoint = str(params.get("endpoint") or "").strip()
    channel = CSRC_CHANNELS.get(endpoint)
    if not channel:
        return ""
    request_params = {
        "_isAgg": "true",
        "_isJson": "true",
        "_pageSize": str(params.get("limit") or 5),
        "_template": "index",
        "_rangeTimeGte": str(params.get("start_date") or ""),
        "_channelName": "",
        "page": str(params.get("page") or 1),
    }
    request_url = f"{CSRC_BASE_URL}/searchList/{channel['channel_id']}"
    return "\n".join(
        [
            "import re",
            "import requests",
            "",
            f"endpoint = {_python_literal(endpoint)}",
            f"keyword = {_python_literal(params.get('keyword') or '')}",
            f"end_date = {_python_literal(params.get('end_date') or '')}",
            f"include_content = {_python_literal(bool(params.get('include_content', True)))}",
            f"url = {_python_literal(request_url)}",
            f"source_page = {_python_literal(channel['source_page'])}",
            f"params = {_python_literal(request_params)}",
            "response = requests.get(url, params=params, headers={'User-Agent': 'Mozilla/5.0', 'Referer': source_page}, timeout=20)",
            "response.raise_for_status()",
            "rows = response.json().get('data', {}).get('results', [])",
            "records = []",
            "for row in rows:",
            "    published = str(row.get('publishedTimeStr', ''))",
            "    published_date = published.split(' ')[0] if published else ''",
            "    if end_date and published_date and published_date > end_date:",
            "        continue",
            "    title = str(row.get('title', ''))",
            "    summary = str(row.get('memo', '')).strip()",
            "    content_text = re.sub(r'\\s+', ' ', re.sub(r'<[^>]+>', ' ', str(row.get('contentHtml', '')))).strip()",
            "    searchable = f'{title}\\n{summary}\\n{content_text}'.lower()",
            "    if keyword and keyword.lower() not in searchable:",
            "        continue",
            "    source_url = str(row.get('url') or '')",
            "    if source_url.startswith('/'):",
            f"        source_url = {_python_literal(CSRC_BASE_URL)} + source_url",
            "    record = {'title': title, 'published_time': published or None, 'url': source_url, 'summary': summary, 'manuscript_id': row.get('manuscriptId')}",
            "    if include_content:",
            "        record['content_text'] = content_text",
            "    records.append(record)",
            "print(records)",
        ]
    )


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
    if provider.lower() == "csrc" and tool_name == "csrc_enforcement_data":
        return _csrc_reproduction_code(params)
    return ""


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


def append_report_followup(
    report_id: int,
    followup_id: int,
    user_id: int,
    append_text: str,
) -> Tuple[bool, Optional[str], Optional[int]]:
    """追加报告追问，并继续原调研任务会话。"""
    if not append_text or not append_text.strip():
        return (False, "append_text 不能为空", None)

    followup = (
        ReportFollowup.objects
        .select_related('report')
        .filter(pk=followup_id, report_id=report_id, user_id=user_id)
        .first()
    )
    if not followup:
        return (False, "追问不存在", None)

    from research.interface.research_interface import continue_task_conversation

    appended_question = append_text.strip()
    followup.question = f"{followup.question}\n\n{appended_question}"
    followup.answer = None
    followup.save(update_fields=['question', 'answer'])

    success, message = continue_task_conversation(
        followup.report.task_id,
        user_id,
        appended_question,
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

        storage_path = store_report_file(
            file_path, object_name=_build_export_object_key(report, record.export_format, record.report_mode, file_path)
        )
        download_url = build_report_download_url(record.id)

        if record.export_format in ('docx', 'word'):
            report.file_word_path = download_url
            report.save(update_fields=['file_word_path'])
        elif record.export_format == 'pdf':
            report.file_pdf_path = download_url
            report.save(update_fields=['file_pdf_path'])

        record.status = 'COMPLETED'
        record.storage_path = storage_path
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
        authority = _authority_from_state_item(state_item)
        enriched.append(
            {
                **item,
                "cite_key": cite_key,
                "source_platform": state_item.get("source_platform") or "",
                "source_type": _source_type_from_state_item(state_item),
                "authority_score": authority["authority_score"],
                "authority_tier": authority["authority_tier"],
                "authority_label": authority["authority_label"],
                "authority_reason": authority["authority_reason"],
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
    if len(enriched) >= len(used_keys):
        return enriched

    existing_keys = {
        _normalize_cite_key(item.get("cite_key"))
        for item in enriched
        if _normalize_cite_key(item.get("cite_key"))
    }
    next_index = len(enriched) + 1
    for cite_key in used_keys:
        if cite_key in existing_keys:
            continue
        state_item = state_citations_by_key.get(cite_key)
        if not state_item:
            continue
        authority = _authority_from_state_item(state_item)
        source_url = str(state_item.get("url") or "").strip()
        fallback_item = {
            "id": f"state-{cite_key}",
            "index_number": next_index,
            "source_url": source_url,
            "source_title": str(state_item.get("title") or cite_key).strip()[:512],
            "cited_text_snippet": str(
                state_item.get("summary")
                or state_item.get("note")
                or state_item.get("howpublished")
                or ""
            )[:2000],
            "reproduction_code": (
                state_item.get("reproduction_code")
                or _structured_tool_reproduction_code(state_item)
                or ""
            ),
        }
        enriched.append(
            {
                **fallback_item,
                "cite_key": cite_key,
                "source_platform": state_item.get("source_platform") or "",
                "source_type": _source_type_from_state_item(state_item),
                "authority_score": authority["authority_score"],
                "authority_tier": authority["authority_tier"],
                "authority_label": authority["authority_label"],
                "authority_reason": authority["authority_reason"],
                "accessed_at": state_item.get("accessed_at") or "",
                "bibtex": _render_bibtex_entry(cite_key, fallback_item, state_item),
            }
        )
        existing_keys.add(cite_key)
        next_index += 1
    return enriched


def _authority_from_state_item(state_item: dict[str, Any]) -> dict[str, Any]:
    authority_score = state_item.get("authority_score")
    authority_reason = str(state_item.get("authority_reason") or "").strip()
    authority_tier = str(state_item.get("authority_tier") or "").strip()
    if authority_score in (None, ""):
        assessed = assess_source_authority(
            url=str(state_item.get("url") or ""),
            source_platform=str(state_item.get("source_platform") or ""),
            provider=str(state_item.get("provider") or ""),
            tool_name=str(state_item.get("tool_name") or ""),
        )
        authority_score = assessed.get("authority_score")
        authority_tier = authority_tier or str(assessed.get("authority_tier") or "")
        authority_reason = authority_reason or str(assessed.get("authority_reason") or "")
    normalized_score = normalize_authority_score(authority_score)
    return {
        "authority_score": normalized_score,
        "authority_tier": authority_tier or authority_tier_for_score(normalized_score),
        "authority_label": authority_display_label(normalized_score),
        "authority_reason": authority_reason,
    }


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


def _state_citation_items(report: Report) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def collect(state: Any) -> None:
        citations = state.get("citations", []) if isinstance(state, dict) else []
        if not isinstance(citations, list):
            return
        items.extend(item for item in citations if isinstance(item, dict))

    conversation = getattr(report.task, "conversation", None)
    collect(getattr(conversation, "state_snapshot", None) if conversation is not None else None)

    child_tasks = getattr(report.task, "child_tasks", None)
    if child_tasks is not None:
        for child_task in child_tasks.all():
            child_conversation = getattr(child_task, "conversation", None)
            collect(getattr(child_conversation, "state_snapshot", None) if child_conversation is not None else None)

    return items


def _state_citations_by_url(report: Report) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in _state_citation_items(report):
        url = str(item.get("url", "") or "").strip()
        if not url:
            continue
        if url not in result:
            result[url] = item
    return result


def _state_citations_by_key(report: Report) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in _state_citation_items(report):
        cite_key = _normalize_cite_key(item.get("cite_key"))
        if not cite_key:
            continue
        if cite_key not in result:
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
