from __future__ import annotations

from typing import Any

from django.db import transaction

from llm_manager.interface.llm_interface import log_model_usage
from research.interface import research_runtime
from reports.interface.report_interface import cite_keys_from_markdown, normalize_report_markdown
from research.interface.thread_codec import json_safe
from research.models import AnalysisResult, ResearchConversation, ResearchTask, STATUS_COMPLETED, TaskStepLog

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
    final_citations = _cross_report_citations(
        report_markdown="\n".join(
            part
            for part in (
                final_output,
                brief_output,
                str(latest_report.get("content") or "") if latest_report else "",
            )
            if str(part or "").strip()
        ),
        integrator_citations=report_citations or citations,
        model_results=model_results,
    )
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
            _sync_parent_conversation_citations(task, final_citations)
            research_runtime._create_report(
                task,
                final_output,
                final_citations,
                brief_output=brief_output,
            )
        task_progress = dict(task.progress or {})
        task_progress.update({
            "searching": 100,
            "analyzing": 100,
            "report": 100,
            "stage": STATUS_COMPLETED,
        })
        ResearchTask.objects.filter(pk=task.id).update(
            status=STATUS_COMPLETED,
            progress=json_safe(task_progress),
        )
        task.status = STATUS_COMPLETED
        task.progress = task_progress

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


def _cross_report_citations(
    *,
    report_markdown: str,
    integrator_citations: list[dict[str, Any]],
    model_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    citations = research_runtime._merge_citation_snapshots([], integrator_citations)
    for result in model_results:
        if not isinstance(result, dict) or result.get("status") != "completed":
            continue
        citations = research_runtime._merge_citation_snapshots(
            citations,
            _model_result_citations(result),
        )

    used_keys = cite_keys_from_markdown(normalize_report_markdown(report_markdown))
    if not used_keys:
        return citations

    by_key = {
        str(item.get("cite_key") or "").strip().lower(): item
        for item in citations
        if str(item.get("cite_key") or "").strip()
    }
    prioritized_keys: set[str] = set()
    prioritized: list[dict[str, Any]] = []
    for key in used_keys:
        item = by_key.get(key)
        if not item:
            continue
        prioritized.append(item)
        prioritized_keys.add(key)
    prioritized.extend(
        item
        for item in citations
        if str(item.get("cite_key") or "").strip().lower() not in prioritized_keys
    )
    return prioritized or citations


def _model_result_citations(result: dict[str, Any]) -> list[dict[str, Any]]:
    citations = research_runtime._extract_citations(result.get("state_snapshot") or {})
    presented_reports = result.get("presented_reports")
    if isinstance(presented_reports, list):
        for report in presented_reports:
            if not isinstance(report, dict):
                continue
            report_citations = report.get("citations")
            if isinstance(report_citations, list):
                citations = research_runtime._merge_citation_snapshots(
                    citations,
                    [item for item in report_citations if isinstance(item, dict)],
                )
    return citations


def _sync_parent_conversation_citations(task: ResearchTask, citations: list[dict[str, Any]]) -> None:
    if not citations:
        return
    conversation = ResearchConversation.objects.filter(task=task).first()
    if conversation is None:
        return
    state_snapshot = conversation.state_snapshot if isinstance(conversation.state_snapshot, dict) else {}
    state_snapshot = {
        **state_snapshot,
        "citations": research_runtime._merge_citation_snapshots(
            state_snapshot.get("citations"),
            citations,
        ),
    }
    conversation.state_snapshot = state_snapshot
    conversation.save(update_fields=["state_snapshot", "updated_at"])
