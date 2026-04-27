from __future__ import annotations

from typing import Any

from research.models import AnalysisResult, ResearchTask

from .run_logs import _latest_cross_log


def get_cross_validation_payload(task: ResearchTask) -> dict[str, Any]:
    latest = (
        AnalysisResult.objects
        .filter(task_id=task.id, analysis_type="CROSS")
        .order_by("-created_at")
        .first()
    )
    latest_run = _latest_cross_log(task)

    payload = _empty_cross_payload(task)
    if latest is not None:
        payload.update(_payload_from_result(task, latest))

    if latest_run is not None:
        detail = latest_run.detail if isinstance(latest_run.detail, dict) else {}
        log_status = str(detail.get("status") or "").strip().lower()
        if latest_run.step_status == "RUNNING":
            payload.update(
                {
                    "status": log_status or "running",
                    "run_id": detail.get("cross_validation_run_id"),
                    "updated_at": latest_run.created_at.isoformat(),
                }
            )
        elif latest_run.step_status == "FAILED" and (
            latest is None or latest_run.created_at > latest.created_at
        ):
            payload.update(
                {
                    "status": "failed",
                    "run_id": detail.get("cross_validation_run_id"),
                    "error": detail.get("error") or "",
                    "updated_at": latest_run.created_at.isoformat(),
                }
            )
    return payload


def _empty_cross_payload(task: ResearchTask) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "status": "queued",
        "run_id": None,
        "result_id": None,
        "consensus_points": [],
        "difference_points": [],
        "model_outputs": [],
        "used_models": [],
        "consensus_summary": "",
        "consensus_score": 0,
        "report_path": "",
        "report_content": "",
        "error": "",
        "updated_at": task.updated_at.isoformat(),
    }


def _payload_from_result(task: ResearchTask, result: AnalysisResult) -> dict[str, Any]:
    raw = result.raw_output if isinstance(result.raw_output, dict) else {}
    integrator = raw.get("integrator") if isinstance(raw.get("integrator"), dict) else {}
    final_report = str(integrator.get("final_output") or "")
    model_outputs = [
        _public_model_output(item)
        for item in raw.get("model_outputs", [])
        if isinstance(item, dict)
    ]
    return {
        "task_id": str(task.id),
        "status": "completed",
        "run_id": raw.get("cross_validation_run_id"),
        "result_id": str(result.id),
        "consensus_points": _section_bullets(final_report, ("共识", "一致", "核心结论")),
        "difference_points": _section_bullets(final_report, ("分歧", "差异", "冲突")),
        "model_outputs": model_outputs,
        "used_models": _public_used_models(raw.get("used_models", []), model_outputs),
        "consensus_summary": result.conclusion,
        "consensus_score": _consensus_score(model_outputs),
        "report_path": _last_report_path(integrator),
        "report_content": final_report,
        "error": "",
        "updated_at": result.created_at.isoformat(),
    }


def _public_model_output(item: dict[str, Any]) -> dict[str, Any]:
    model = item.get("model") if isinstance(item.get("model"), dict) else {}
    model_id = str(model.get("model_name") or model.get("model_id") or item.get("thread_id") or "")
    return {
        "model_id": model_id,
        "status": item.get("status"),
        "model": model,
        "thread_id": item.get("thread_id"),
        "child_task_id": item.get("child_task_id"),
        "child_report_id": item.get("child_report_id"),
        "summary": item.get("summary"),
        "report_paths": item.get("report_paths", []),
        "presented_reports": [
            {
                "path": report.get("path"),
                "citation_keys": report.get("citation_keys", []),
                "generated_reference_count": report.get("generated_reference_count", 0),
            }
            for report in item.get("presented_reports", [])
            if isinstance(report, dict)
        ],
        "error": item.get("error", ""),
        "latency_ms": item.get("latency_ms", 0),
    }


def _public_used_models(raw_used_models: Any, model_outputs: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    if isinstance(raw_used_models, list):
        for item in raw_used_models:
            if isinstance(item, dict):
                value = item.get("model_name") or item.get("model_id")
            else:
                value = item
            text = str(value or "").strip()
            if text and text not in names:
                names.append(text)
    for item in model_outputs:
        text = str(item.get("model_id") or "").strip()
        if text and text not in names:
            names.append(text)
    return names


def _section_bullets(markdown_text: str, headings: tuple[str, ...]) -> list[str]:
    lines = (markdown_text or "").splitlines()
    capture = False
    bullets: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            capture = any(keyword in heading for keyword in headings)
            continue
        if not capture:
            continue
        if stripped.startswith(("-", "*")):
            bullets.append(stripped.lstrip("-* ").strip())
        elif re_match := _numbered_bullet(stripped):
            bullets.append(re_match)
        elif stripped.startswith("#"):
            capture = False
        if len(bullets) >= 12:
            break
    return [item for item in bullets if item]


def _numbered_bullet(line: str) -> str:
    parts = line.split(".", 1)
    if len(parts) == 2 and parts[0].isdigit():
        return parts[1].strip()
    return ""


def _consensus_score(model_outputs: list[dict[str, Any]]) -> int:
    total = len(model_outputs)
    if not total:
        return 0
    completed = sum(1 for item in model_outputs if item.get("status") == "completed")
    return round(completed / total * 100)


def _last_report_path(integrator: dict[str, Any]) -> str:
    paths = integrator.get("report_paths")
    if isinstance(paths, list) and paths:
        return str(paths[-1] or "")
    return ""
