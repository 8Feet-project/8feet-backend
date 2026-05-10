from __future__ import annotations

from dataclasses import replace
import json
import os
from typing import Any

from llm_manager.interface.llm_interface import (
    get_provider_runtime_config,
    resolve_user_model_config,
    user_can_use_model,
)
from llm_manager.models.llm_config import LLMConfig
from research.models import ResearchTask

from .types import CrossModelSpec


def resolve_cross_model_specs(
    task: ResearchTask,
    requested_model_ids: Any = None,
    *,
    allow_env_models: bool = False,
) -> list[CrossModelSpec]:
    raw_items = _coerce_model_id_list(requested_model_ids)
    if not raw_items:
        raw_items = _coerce_model_id_list((task.search_params or {}).get("multi_model_ids"))
    if not raw_items:
        cross_params = (task.search_params or {}).get("cross_validation")
        if isinstance(cross_params, dict):
            raw_items = _coerce_model_id_list(cross_params.get("model_ids") or cross_params.get("models"))

    specs: list[CrossModelSpec] = []
    seen: set[str] = set()
    for item in raw_items:
        spec = _resolve_model_spec(task, item, allow_env_models=allow_env_models)
        dedupe_key = f"{spec.provider}:{spec.model_name}".lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        specs.append(replace(spec, order=len(specs) + 1))
    return specs


def resolve_integrator_model_spec(
    task: ResearchTask,
    integrator_model_id: Any,
    model_specs: list[CrossModelSpec],
    *,
    allow_env_models: bool = False,
) -> CrossModelSpec:
    raw = str(integrator_model_id or "").strip()
    if raw:
        return _resolve_model_spec(task, raw, allow_env_models=allow_env_models)
    cross_params = (task.search_params or {}).get("cross_validation")
    if isinstance(cross_params, dict):
        raw = str(cross_params.get("integrator_model_id") or cross_params.get("integrator_model") or "").strip()
        if raw:
            return _resolve_model_spec(task, raw, allow_env_models=allow_env_models)
    if task.llm_config_id:
        return _resolve_model_spec(task, str(task.llm_config_id), allow_env_models=allow_env_models)
    if model_specs:
        return model_specs[0]
    if allow_env_models:
        env_model = _env_first("EFEET_MODEL_NAME", "MODEL_NAME")
        if env_model:
            return _resolve_env_model_spec(env_model)
    raise ValueError("未找到可用于智能整合的模型配置")


def _resolve_model_spec(
    task: ResearchTask,
    raw_id: Any,
    *,
    allow_env_models: bool = False,
) -> CrossModelSpec:
    text = str(raw_id or "").strip()
    if not text:
        raise ValueError("模型 ID 不能为空")
    if text.isdigit():
        return _resolve_config_model_spec(task, int(text))

    config = (
        LLMConfig.objects
        .filter(name=text)
        .order_by("id")
        .first()
    )
    if config is not None:
        if not user_can_use_model(task.user, config):
            raise ValueError(f"当前用户无权使用模型: {text}")
        return _resolve_config_model_spec(task, config.id)
    if not allow_env_models:
        raise ValueError(f"模型 {text} 未在平台配置中找到或当前用户无权使用")
    return _resolve_env_model_spec(text)


def _resolve_config_model_spec(task: ResearchTask, config_id: int) -> CrossModelSpec:
    ok, message, config, params = resolve_user_model_config(
        task.user,
        model_id=config_id,
        object_type=task.object_type,
    )
    if not ok or config is None:
        raise ValueError(message or f"模型不可用: {config_id}")
    ok, message, runtime_config = get_provider_runtime_config(config, params)
    if not ok:
        raise ValueError(message or f"模型运行配置不可用: {config.name}")
    return CrossModelSpec(
        model_key=str(config.id),
        model_name=config.name,
        provider=config.provider,
        runtime_config=runtime_config,
        llm_config_id=config.id,
    )


def _resolve_env_model_spec(model_name: str) -> CrossModelSpec:
    api_key = _env_first("EFEET_MODEL_API_KEY", "MODEL_API_KEY")
    base_url = _env_first("EFEET_MODEL_BASE_URL", "MODEL_BASE_URL")
    if not api_key or not base_url:
        raise ValueError(
            f"模型 {model_name} 未在平台配置中找到，且环境变量缺少 MODEL_API_KEY / MODEL_BASE_URL"
        )
    return CrossModelSpec(
        model_key=str(model_name),
        model_name=str(model_name),
        provider=_env_first("EFEET_MODEL_PROVIDER", "MODEL_PROVIDER") or "env",
        runtime_config={
            "model": str(model_name),
            "api_key": api_key,
            "base_url": base_url,
            "debug_provider_http": _env_bool("EFEET_DEBUG_PROVIDER_HTTP", False),
            "streaming": _env_bool_first(
                ("EFEET_MODEL_STREAM", "MODEL_STREAM", "EFEET_MODEL_STREAMING", "MODEL_STREAMING"),
                True,
            ),
        },
        llm_config_id=None,
    )


def _create_model(spec: CrossModelSpec):
    from efeet import create_chat_model

    return create_chat_model(
        model=str(spec.runtime_config["model"]),
        api_key=str(spec.runtime_config["api_key"]),
        base_url=str(spec.runtime_config["base_url"]),
        debug_provider_http=_coerce_bool_value(spec.runtime_config.get("debug_provider_http"), False),
        streaming=_coerce_bool_value(spec.runtime_config.get("streaming"), True),
    )


def _coerce_model_id_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                return _coerce_model_id_list(json.loads(text))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return [item.strip() for item in text.split(",") if item.strip()]
    if isinstance(value, dict):
        return _coerce_model_id_list(value.get("model_ids") or value.get("models"))
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return [str(value).strip()]


def _env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _coerce_bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_bool_first(names: tuple[str, ...], default: bool) -> bool:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value.lower() in {"1", "true", "yes", "on"}
    return default
