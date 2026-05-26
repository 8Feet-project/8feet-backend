"""
报告导出工具
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

from django.conf import settings
from django.utils import timezone

from reports.models.report import Report


def export_root() -> Path:
    return Path(
        getattr(settings, "REPORT_EXPORT_ROOT", Path(getattr(settings, "BASE_DIR", ".")).parent / "generated_reports")
    )


def ensure_export_root() -> Path:
    root = export_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def normalize_report_mode(report_mode: str | None) -> str:
    return "brief" if str(report_mode).lower() == "brief" else "full"


def get_report_content(report: Report, report_mode: str = "full") -> str:
    mode = normalize_report_mode(report_mode)
    if mode == "brief":
        if report.content_brief and report.content_brief.strip():
            return report.content_brief.strip()
        return build_brief_from_markdown(report)
    return (report.content_markdown or "").strip()


def build_brief_from_markdown(report: Report) -> str:
    markdown = (report.content_markdown or "").strip()
    if not markdown:
        return report.summary or ""

    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    brief_lines = []
    if report.summary:
        brief_lines.append(f"# {report.title}")
        brief_lines.append("")
        brief_lines.append(report.summary.strip())
        brief_lines.append("")

    heading_count = 0
    for line in lines:
        if line.startswith("#"):
            heading_count += 1
            if heading_count > 3:
                break
            brief_lines.append(line)
            continue
        if heading_count <= 3:
            brief_lines.append(line)
        if len("\n".join(brief_lines)) > 1200:
            break

    if not brief_lines:
        return markdown[:1200]
    return "\n".join(brief_lines).strip()


def build_markdown_document(report: Report, report_mode: str = "full") -> str:
    content = get_report_content(report, report_mode)
    citations = _export_citations(report)
    parts = [
        f"# {report.title}",
        "",
        f"> 报告 ID：{report.id}　｜　任务 ID：{report.task_id}　｜　生成时间：{_format_report_created_at(report)}",
        "",
        content,
    ]

    if citations:
        parts.extend(["", "---", "", "## 引用来源", ""])
        for index, item in enumerate(citations):
            number = item["index_number"] if item["index_number"] > 0 else index + 1
            key = f" @{item['cite_key']}" if item["cite_key"] else ""
            source = (
                f"[{item['source_title']}]({item['source_url']})"
                if item["source_url"] else item["source_title"]
            )
            parts.append(f"{number}. {source}{key}")

            source_meta = [item["source_platform"], item["source_type"]]
            source_meta = [value for value in source_meta if value]
            if source_meta:
                parts.append(f"   - 来源：{' / '.join(source_meta)}")
            if not item["source_url"] and item["reproduction_code"]:
                parts.extend(["   - 复现代码：", "```python", item["reproduction_code"], "```"])

    return "\n".join(part for part in parts if part is not None).strip() + "\n"


def report_export_basename(report: Report, report_mode: str, extension: str) -> str:
    safe_title = re.sub(r'[\\/:*?"<>|]+', "_", report.title).strip() or f"report_{report.id}"
    return f"report_{report.id}_v{report.version}_{report_mode}_{safe_title}.{extension}"


def export_markdown(report: Report, report_mode: str = "full") -> tuple[str, str]:
    output_dir = ensure_export_root()
    filename = report_export_basename(report, report_mode, "md")
    markdown_text = build_markdown_document(report, report_mode)
    file_path = output_dir / filename
    file_path.write_text(markdown_text, encoding="utf-8")
    return str(file_path), markdown_text


def export_html(report: Report, report_mode: str = "full") -> str:
    content = get_report_content(report, report_mode)
    citations = _export_citations(report)
    output_dir = ensure_export_root()
    filename = report_export_basename(report, report_mode, "html")
    file_path = output_dir / filename

    escaped_title = _escape_html(report.title)
    escaped_meta = _escape_html(
        f"报告 ID：{report.id}　｜　任务 ID：{report.task_id}　｜　生成时间：{_format_report_created_at(report)}"
    )
    citations_html = _build_citations_html(citations)
    html = (
        "<!DOCTYPE html>\n"
        "<html lang=\"zh-CN\">\n"
        "<head>\n"
        "<meta charset=\"UTF-8\"/>\n"
        f"<title>{escaped_title}</title>\n"
        "<style>\n"
        "  body { font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, \"Helvetica Neue\", Arial, \"Noto Sans SC\", sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; color: #1e293b; line-height: 1.8; }\n"
        "  h1 { font-size: 1.75rem; margin-bottom: 0.25em; }\n"
        "  .meta { color: #64748b; font-size: 0.85rem; margin-bottom: 1.5em; }\n"
        "  .content { white-space: pre-wrap; font-size: 1rem; }\n"
        "  hr { border: none; border-top: 1px solid #e2e8f0; margin: 2em 0; }\n"
        "  h2 { font-size: 1.25rem; }\n"
        "  ol { padding-left: 1.25em; }\n"
        "  li { margin-bottom: 0.35em; }\n"
        "  a { color: #2563eb; text-decoration: none; }\n"
        "  a:hover { text-decoration: underline; }\n"
        "  .citation-meta { color: #64748b; font-size: 0.85rem; }\n"
        "  pre { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; overflow-x: auto; padding: 0.75rem; }\n"
        "  @media print { body { margin: 0; } }\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        f"<h1>{escaped_title}</h1>\n"
        f"<p class=\"meta\">{escaped_meta}</p>\n"
        f"<div class=\"content\">{_escape_html(content)}</div>\n"
        f"{citations_html}\n"
        "</body>\n"
        "</html>"
    )
    file_path.write_text(html, encoding="utf-8")
    return str(file_path)


def export_docx(report: Report, report_mode: str = "full") -> str:
    markdown_text = build_markdown_document(report, report_mode)
    output_dir = ensure_export_root()
    filename = report_export_basename(report, report_mode, "docx")
    file_path = output_dir / filename

    _convert_markdown_with_pandoc(markdown_text, file_path, "docx")
    return str(file_path)


def export_pdf(report: Report, report_mode: str = "full") -> str:
    markdown_text = build_markdown_document(report, report_mode)
    output_dir = ensure_export_root()
    filename = report_export_basename(report, report_mode, "pdf")
    file_path = output_dir / filename

    _convert_markdown_with_pandoc(markdown_text, file_path, "pdf")
    return str(file_path)


def _convert_markdown_with_pandoc(markdown_text: str, output_path: Path, output_format: str) -> None:
    markdown_path = output_path.with_suffix(".md")
    markdown_path.write_text(markdown_text, encoding="utf-8")

    command = [
        "pandoc",
        str(markdown_path),
        "-f",
        "gfm",
        "-o",
        str(output_path),
    ]
    if output_format == "pdf":
        css_path = _write_pdf_print_css(output_path)
        command.extend(["--pdf-engine=weasyprint", "--css", str(css_path)])

    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError as exc:
        raise RuntimeError("缺少 pandoc 命令，无法导出 docx/pdf") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        message = f"pandoc 转换 {output_format} 失败"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message) from exc


def _write_pdf_print_css(output_path: Path) -> Path:
    css_path = output_path.with_suffix(".pdf.css")
    css_path.write_text(
        """
@page {
  size: A4;
  margin: 16mm 14mm;
}

* {
  box-sizing: border-box;
}

html {
  font-size: 11pt;
}

body {
  color: #111827;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans SC", sans-serif;
  line-height: 1.65;
}

table {
  border-collapse: collapse;
  font-size: 9.5pt;
  margin: 1em 0;
  max-width: 100%;
  table-layout: fixed;
  width: 100%;
}

thead {
  display: table-header-group;
}

tr {
  break-inside: avoid;
}

th,
td {
  border: 1px solid #d1d5db;
  overflow-wrap: anywhere;
  padding: 4pt 5pt;
  vertical-align: top;
  word-break: break-word;
}

img,
svg {
  height: auto;
  max-width: 100%;
}

pre {
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  word-break: break-word;
}

code {
  overflow-wrap: anywhere;
  word-break: break-word;
}

blockquote {
  border-left: 3pt solid #d1d5db;
  color: #4b5563;
  margin-left: 0;
  padding-left: 10pt;
}
""".lstrip(),
        encoding="utf-8",
    )
    return css_path


def _format_report_created_at(report: Report) -> str:
    value = report.created_at
    if not value:
        return ""
    if timezone.is_naive(value):
        return value.isoformat()
    return timezone.localtime(value).isoformat()


def _escape_html(text: Any) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _encode_uri(value: str) -> str:
    return quote(value, safe=":/?#[]@!$&'()*+,;=%")


def _export_citations(report: Report) -> list[dict[str, Any]]:
    rows = list(
        report.citations.order_by("index_number").values(
            "id",
            "index_number",
            "source_title",
            "source_url",
            "cited_text_snippet",
            "reproduction_code",
        )
    )
    from reports.interface.report_interface import _enrich_citations_from_state

    enriched_rows = _enrich_citations_from_state(report, rows)
    return [
        {
            "index_number": int(item.get("index_number") or 0),
            "cite_key": str(item.get("cite_key") or "").strip(),
            "source_title": str(item.get("source_title") or "").strip(),
            "source_url": str(item.get("source_url") or "").strip(),
            "source_platform": str(item.get("source_platform") or "").strip(),
            "source_type": str(item.get("source_type") or "").strip(),
            "reproduction_code": str(item.get("reproduction_code") or ""),
        }
        for item in enriched_rows
    ]


def _build_citations_html(citations: list[dict[str, Any]]) -> str:
    if not citations:
        return ""

    items = []
    for citation in citations:
        if citation["source_url"]:
            title = (
                f"<a href=\"{_encode_uri(citation['source_url'])}\" target=\"_blank\" rel=\"noopener noreferrer\">"
                f"{_escape_html(citation['source_title'])}</a>"
            )
        else:
            title = _escape_html(citation["source_title"])
        key = f" <code>@{_escape_html(citation['cite_key'])}</code>" if citation["cite_key"] else ""
        meta = " / ".join(
            _escape_html(value)
            for value in [citation["source_platform"], citation["source_type"]]
            if value
        )
        meta_html = f"<div class=\"citation-meta\">{meta}</div>" if meta else ""
        code_html = (
            f"<pre><code>{_escape_html(citation['reproduction_code'])}</code></pre>"
            if not citation["source_url"] and citation["reproduction_code"] else ""
        )
        items.append(f"<li>{title}{key}{meta_html}{code_html}</li>")
    return f"<hr/><h2>引用来源</h2><ol>{''.join(items)}</ol>"
