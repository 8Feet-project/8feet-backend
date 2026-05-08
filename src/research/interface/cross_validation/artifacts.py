from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from research.interface import research_runtime

try:
    from efeet import SandboxPaths, extract_presented_reports
    from efeet.sandbox.paths import OUTPUTS_VIRTUAL_PATH
except Exception:
    SandboxPaths = Any
    OUTPUTS_VIRTUAL_PATH = "/mnt/user-data/outputs"

    def extract_presented_reports(_state_snapshot):
        return []


def _ensure_report_payloads(
    *,
    sandbox_paths: SandboxPaths,
    thread_id: str,
    state_snapshot: dict[str, Any],
    final_output: str,
    fallback_filename: str,
    fallback_brief_filename: str | None = None,
) -> list[dict[str, Any]]:
    reports = [report.to_payload() for report in extract_presented_reports(state_snapshot)]
    if reports:
        return _sanitize_report_payloads(
            sandbox_paths=sandbox_paths,
            thread_id=thread_id,
            reports=reports,
        )

    text = str(final_output or "").strip()
    if not text or text == research_runtime.STOPPED_MESSAGE or _is_llm_failure_output(text):
        return []

    output_dir = sandbox_paths.outputs_dir(thread_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    fallback_path = output_dir / fallback_filename
    brief_filename = fallback_brief_filename or _brief_filename_for(fallback_filename)
    fallback_brief_path = output_dir / brief_filename
    brief_text = research_runtime._extract_summary(text) or text[:1200]
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    fallback_brief_path.parent.mkdir(parents=True, exist_ok=True)
    fallback_path.write_text(text, encoding="utf-8")
    fallback_brief_path.write_text(brief_text, encoding="utf-8")
    return [
        {
            "path": f"{OUTPUTS_VIRTUAL_PATH}/{fallback_filename}",
            "full_path": f"{OUTPUTS_VIRTUAL_PATH}/{fallback_filename}",
            "content": text,
            "brief_path": f"{OUTPUTS_VIRTUAL_PATH}/{brief_filename}",
            "brief_content": brief_text,
            "citation_keys": [],
            "citations": [],
            "generated_reference_count": 0,
            "fallback_generated": True,
        }
    ]


def _sanitize_report_payloads(
    *,
    sandbox_paths: SandboxPaths,
    thread_id: str,
    reports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for report in reports:
        payload = dict(report)
        original = str(payload.get("content") or "")
        cleaned = _strip_tool_call_markup(original)
        if cleaned != original:
            payload["content"] = cleaned
            _rewrite_output_report(
                sandbox_paths=sandbox_paths,
                thread_id=thread_id,
                virtual_path=str(payload.get("path") or ""),
                content=cleaned,
            )
        brief_original = str(payload.get("brief_content") or "")
        brief_cleaned = _strip_tool_call_markup(brief_original)
        if brief_cleaned != brief_original:
            payload["brief_content"] = brief_cleaned
            _rewrite_output_report(
                sandbox_paths=sandbox_paths,
                thread_id=thread_id,
                virtual_path=str(payload.get("brief_path") or ""),
                content=brief_cleaned,
            )
        sanitized.append(payload)
    return sanitized


def _report_paths_from_payloads(reports: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for item in reports:
        for key in ("full_path", "path", "brief_path"):
            path = str(item.get(key) or "").strip()
            if not path or path in seen:
                continue
            seen.add(path)
            paths.append(path)
    return paths


def _latest_report_payload(reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    for report in reversed(reports):
        if str(report.get("content") or "").strip():
            return report
    return None


def _brief_filename_for(filename: str) -> str:
    path = PurePosixPath(str(filename or "report.md"))
    suffix = "".join(path.suffixes)
    if suffix:
        stem = path.name[: -len(suffix)]
        brief_name = f"{stem}_brief{suffix}"
    else:
        brief_name = f"{path.name}_brief"
    if str(path.parent) in {"", "."}:
        return brief_name
    return str(path.parent / brief_name)


def _rewrite_output_report(
    *,
    sandbox_paths: SandboxPaths,
    thread_id: str,
    virtual_path: str,
    content: str,
) -> None:
    normalized = PurePosixPath(str(virtual_path or "").strip()).as_posix()
    if not normalized.startswith(f"{OUTPUTS_VIRTUAL_PATH}/"):
        return
    relative = PurePosixPath(normalized[len(OUTPUTS_VIRTUAL_PATH) :].lstrip("/"))
    if any(part == ".." for part in relative.parts):
        return
    target = sandbox_paths.outputs_dir(thread_id).joinpath(*relative.parts)
    try:
        target.write_text(content, encoding="utf-8")
    except OSError:
        return


def _strip_tool_call_markup(text: str) -> str:
    cleaned = str(text or "")
    markers = (
        "<longcat_tool_call>",
        "<tool_call>",
        "<function_call>",
        "<tool_calls>",
    )
    positions = [cleaned.find(marker) for marker in markers if marker in cleaned]
    if not positions:
        return cleaned.strip()
    return cleaned[: min(positions)].rstrip()


def _is_llm_failure_output(text: str) -> bool:
    return research_runtime._is_llm_failure_output(text)
