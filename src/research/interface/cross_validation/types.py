from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CROSS_VALIDATION_STEP_NAME = "多模型交叉验证"
CROSS_VALIDATION_DEFAULT_WORKERS = 4


@dataclass(frozen=True, slots=True)
class CrossModelSpec:
    model_key: str
    model_name: str
    provider: str
    runtime_config: dict[str, Any]
    llm_config_id: int | None = None
    order: int = 0

    def public_payload(self) -> dict[str, Any]:
        return {
            "model_key": self.model_key,
            "model_id": str(self.llm_config_id) if self.llm_config_id else self.model_key,
            "model_name": self.model_name,
            "provider": self.provider,
            "llm_config_id": self.llm_config_id,
            "order": self.order,
        }
