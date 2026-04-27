from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from efeet import SandboxPaths, extract_presented_reports
from efeet.sandbox.paths import OUTPUTS_VIRTUAL_PATH
from research.interface import research_runtime


def _ensure_report_payloads(
    *,
    sandbox_paths: SandboxPaths,
    thread_id: str,
    state_snapshot: dict[str, Any],
    final_output: str,
    fallback_filename: str,
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
    fallback_path.write_text(text, encoding="utf-8")
    return [
        {
            "path": f"{OUTPUTS_VIRTUAL_PATH}/{fallback_filename}",
            "content": text,
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
        sanitized.append(payload)
    return sanitized


def _report_paths_from_payloads(reports: list[dict[str, Any]]) -> list[str]:
    return [
        str(item.get("path") or "").strip()
        for item in reports
        if str(item.get("path") or "").strip()
    ]


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
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    failure_markers = (
        "the configured llm provider is temporarily unavailable",
        "the configured llm provider rejected the request",
        "llm request failed:",
    )
    return any(marker in normalized for marker in failure_markers)
