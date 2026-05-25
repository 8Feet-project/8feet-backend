"""
报告导出工具
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterable

from django.conf import settings

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
    citations = list(report.citations.order_by("index_number").values(
        "index_number", "source_title", "source_url", "cited_text_snippet"
    ))
    parts = [
        f"# {report.title}",
        "",
        f"> 摘要：{report.summary}",
        "",
        content,
    ]

    if citations:
        parts.extend(["", "## 引用来源", ""])
        for item in citations:
            snippet = f" - 引文：{item['cited_text_snippet']}" if item["cited_text_snippet"] else ""
            parts.append(
                f"{item['index_number']}. {item['source_title']} ({item['source_url']}){snippet}"
            )

    return "\n".join(part for part in parts if part is not None).strip() + "\n"


def markdown_to_plain_text(markdown_text: str) -> str:
    text = markdown_text.replace("\r\n", "\n")
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\>\s?", "", text, flags=re.M)
    text = re.sub(r"^\-\s+", "• ", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
    markdown_text = build_markdown_document(report, report_mode)
    plain_text = markdown_to_plain_text(markdown_text)
    output_dir = ensure_export_root()
    filename = report_export_basename(report, report_mode, "html")
    file_path = output_dir / filename

    escaped_title = (
        report.title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    body = "".join(
        f"<p>{block.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace(chr(10), '<br/>')}</p>"
        for block in _iter_blocks(plain_text)
    )
    html = (
        "<!DOCTYPE html>"
        "<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{escaped_title}</title>"
        "<style>body{font-family:Arial,'Microsoft YaHei',sans-serif;max-width:960px;margin:40px auto;line-height:1.8;color:#1f2937;padding:0 16px;}h1{margin-bottom:24px;}p{margin:0 0 16px;white-space:pre-wrap;}</style>"
        f"</head><body><h1>{escaped_title}</h1>{body}</body></html>"
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


def _iter_blocks(text: str) -> Iterable[str]:
    return [block.strip() for block in text.split("\n\n") if block.strip()]


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
        command.extend(["--pdf-engine=weasyprint"])

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
