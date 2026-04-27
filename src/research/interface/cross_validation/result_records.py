from __future__ import annotations

from typing import Any

from django.db import transaction

from llm_manager.interface.llm_interface import log_model_usage
from research.interface import research_runtime
from research.interface.thread_codec import json_safe
from research.models import AnalysisResult, ResearchTask, TaskStepLog

from .artifacts import _latest_report_payload
from .run_logs import _update_cross_log, _update_cross_progress
from .types import CrossModelSpec


def _persist_cross_success(
    *,
    task: ResearchTask,
    run_id: str,
    prompt: str,
    model_results: list[dict[str, Any]],
    integrator_result: dict[str, Any],
    integrator_spec: CrossModelSpec,
    latency_ms: float,
    run_metadata: dict[str, Any],
) -> None:
    final_output = str(integrator_result.get("final_output") or "").strip()
    citations = research_runtime._extract_citations(integrator_result.get("state_snapshot") or {})
    presented_reports = integrator_result.get("presented_reports")
    latest_report = _latest_report_payload(presented_reports if isinstance(presented_reports, list) else [])
    latest_report_citations = latest_report.get("citations", []) if latest_report else []
    report_citations = (
        [item for item in latest_report_citations if isinstance(item, dict)]
        if isinstance(latest_report_citations, list)
        else []
    )
    brief_output = str(latest_report.get("brief_content") or "").strip() if latest_report else ""
    with transaction.atomic():
        analysis = AnalysisResult.objects.create(
            task=task,
            llm_config_id=integrator_spec.llm_config_id,
            analysis_type="CROSS",
            conclusion=research_runtime._extract_summary(final_output),
            raw_output={
                "cross_validation_run_id": run_id,
                "prompt": prompt,
                "model_outputs": json_safe(model_results),
                "integrator": json_safe(integrator_result),
                "used_models": [item.get("model") for item in model_results],
                "latency_ms": latency_ms,
                "run_metadata": json_safe(run_metadata),
                "skip_auto_report": True,
            },
        )
        if final_output:
            research_runtime._create_report(
                task,
                final_output,
                report_citations or citations,
                brief_output=brief_output,
            )

        _update_cross_log(
            task,
            run_id,
            status="completed",
            step_status="COMPLETED",
            analysis_result_id=analysis.id,
            integrator_report_paths=integrator_result.get("report_paths", []),
            latency_ms=latency_ms,
        )
        _update_cross_progress(
            task,
            run_id,
            "completed",
            model_count=len(model_results),
            result_id=analysis.id,
        )
        TaskStepLog.objects.create(
            task=task,
            step_name="多模型交叉验证完成",
            step_status="COMPLETED",
            detail={
                "cross_validation_run_id": run_id,
                "analysis_result_id": analysis.id,
                "message": final_output[:1000],
            },
        )

    log_model_usage(
        task.user,
        integrator_spec.llm_config_id,
        f"research-cross-{task.id}-{run_id}",
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=int(latency_ms or 0),
        usage_type="GENERAL",
    )
