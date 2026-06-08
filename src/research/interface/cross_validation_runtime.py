"""Public compatibility facade for multi-model cross-validation."""
from __future__ import annotations

from research.interface.cross_validation.model_specs import (
    resolve_cross_model_specs,
    resolve_integrator_model_spec,
)
from research.interface.cross_validation.orchestrator import enqueue_cross_validation_run
from research.interface.cross_validation.payloads import get_cross_validation_payload
from research.interface.cross_validation.prompts import (
    build_cross_validation_base_prompt,
    build_cross_integrator_prompt,
    build_cross_integrator_system_message,
)
from research.interface.cross_validation.types import (
    CROSS_VALIDATION_DEFAULT_WORKERS,
    CROSS_VALIDATION_STEP_NAME,
    CrossModelSpec,
)

__all__ = [
    "CROSS_VALIDATION_DEFAULT_WORKERS",
    "CROSS_VALIDATION_STEP_NAME",
    "CrossModelSpec",
    "enqueue_cross_validation_run",
    "get_cross_validation_payload",
    "resolve_cross_model_specs",
    "resolve_integrator_model_spec",
    "build_cross_validation_base_prompt",
    "build_cross_integrator_system_message",
    "build_cross_integrator_prompt",
]
