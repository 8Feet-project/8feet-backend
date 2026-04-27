"""Compatibility facade for the multi-model cross-validation runtime.

The implementation is split under ``research.interface.cross_validation`` by
responsibility. API callers can keep importing this module.
"""
from __future__ import annotations

from django.db import transaction

from llm_manager.interface.llm_interface import log_model_usage
from llm_manager.models.llm_config import LLMConfig
from research.interface import research_runtime
from research.interface.cross_validation.artifacts import (
    _ensure_report_payloads,
    _is_llm_failure_output,
    _report_paths_from_payloads,
    _rewrite_output_report,
    _sanitize_report_payloads,
    _strip_tool_call_markup,
)
from research.interface.cross_validation.model_specs import (
    _coerce_model_id_list,
    _create_model,
    _env_bool,
    _env_first,
    _resolve_config_model_spec,
    _resolve_env_model_spec,
    _resolve_model_spec,
    resolve_cross_model_specs,
    resolve_integrator_model_spec,
)
from research.interface.cross_validation.orchestrator import (
    _CROSS_EXECUTOR,
    _bounded_turns,
    _cross_integrator_max_turns,
    _cross_model_input_directory,
    _cross_model_max_turns,
    _cross_worker_count,
    _integrator_candidates,
    _run_cross_validation,
    _run_integrator_thread,
    _run_integrator_with_fallbacks,
    _run_single_model_thread,
    _task_prompt_payload,
    enqueue_cross_validation_run,
)
from research.interface.cross_validation.prompts import (
    build_cross_integrator_prompt,
    build_cross_integrator_system_message,
    build_cross_model_research_prompt,
)
from research.interface.cross_validation.records import (
    _consensus_score,
    _conversation_status,
    _create_cross_model_child_tasks,
    _cross_child_search_params,
    _cross_child_task_title,
    _empty_cross_payload,
    _last_report_path,
    _latest_cross_log,
    _mark_cross_failed,
    _mark_model_child_failed,
    _mark_model_child_running,
    _model_child_progress,
    _numbered_bullet,
    _payload_from_result,
    _persist_cross_success,
    _persist_model_child_success,
    _public_model_output,
    _public_used_models,
    _record_cross_event,
    _record_cross_step,
    _section_bullets,
    _update_cross_log,
    _update_cross_progress,
    get_cross_validation_payload,
)
from research.interface.cross_validation.types import (
    CROSS_VALIDATION_DEFAULT_WORKERS,
    CROSS_VALIDATION_STEP_NAME,
    CrossModelSpec,
)
from research.models import (
    AnalysisResult,
    ResearchConversation,
    ResearchTask,
    SESSION_STATUS_COMPLETED,
    SESSION_STATUS_FAILED,
    SESSION_STATUS_RUNNING,
    STATUS_ANALYZING,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SEARCHING,
    TASK_ROLE_CROSS_MODEL,
    TaskStepLog,
)

__all__ = [
    "CROSS_VALIDATION_DEFAULT_WORKERS",
    "CROSS_VALIDATION_STEP_NAME",
    "CrossModelSpec",
    "enqueue_cross_validation_run",
    "get_cross_validation_payload",
    "resolve_cross_model_specs",
    "resolve_integrator_model_spec",
    "build_cross_model_research_prompt",
    "build_cross_integrator_system_message",
    "build_cross_integrator_prompt",
]
