from __future__ import annotations

import os
from decimal import Decimal
from time import perf_counter
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q

from llm_manager.models.llm_config import LLMConfig
from llm_manager.models.model_permission import (
    ModelObjectMapping,
    ModelPermission,
    OBJECT_TYPE_COMPANY,
    OBJECT_TYPE_PRODUCT,
    OBJECT_TYPE_STOCK,
    USAGE_TYPE_GENERAL,
)
from llm_manager.models.model_usage import ModelUsage


OBJECT_TYPE_ALIASES = {
    "company": OBJECT_TYPE_COMPANY,
    "stock": OBJECT_TYPE_STOCK,
    "commodity": OBJECT_TYPE_PRODUCT,
    "product": OBJECT_TYPE_PRODUCT,
    OBJECT_TYPE_COMPANY.lower(): OBJECT_TYPE_COMPANY,
    OBJECT_TYPE_STOCK.lower(): OBJECT_TYPE_STOCK,
    OBJECT_TYPE_PRODUCT.lower(): OBJECT_TYPE_PRODUCT,
}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_object_type(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return OBJECT_TYPE_ALIASES.get(text.lower(), text.upper())


def normalize_usage_type(value: str | None) -> str:
    return str(value or USAGE_TYPE_GENERAL).strip().upper() or USAGE_TYPE_GENERAL


def normalize_provider_base_url(provider: str | None, base_url: str | None) -> str:
    """Normalize known OpenAI-compatible provider dashboard URLs to API base URLs."""
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        return ""

    parsed = urlparse(text)
    provider_name = str(provider or "").strip().lower()
    if "meteor" not in provider_name:
        return text
    if not parsed.scheme or not parsed.netloc:
        return text
    if parsed.path not in ("", "/"):
        return text

    return urlunparse(parsed._replace(path="/v1"))


def create_or_update_llm_config(
    name: str,
    provider: str,
    api_endpoint: str = None,
    api_key: str = None,
    context_window: int = 4096,
    max_output_tokens: int = 2048,
    input_price_1m: float = 0.0,
    output_price_1m: float = 0.0,
    params: dict = None,
    description: str = None,
    is_enabled: bool = True,
    is_online: bool = True,
) -> tuple[bool, Optional[str], Optional[int]]:
    """创建或更新大模型配置。

    对外 model_id 始终表示 LLMConfig 主键。前端 model_name 写入 name，并作为
    AI 运行时传给 provider SDK 的模型名称。
    """
    if not name or not provider:
        return (False, "model_name, provider 均为必填项", None)

    defaults = {
        'api_endpoint': api_endpoint,
        'api_key_encrypted': api_key,
        'context_window': int(context_window or 4096),
        'max_output_tokens': int(max_output_tokens or 2048),
        'input_price_1m': Decimal(str(input_price_1m or 0)),
        'output_price_1m': Decimal(str(output_price_1m or 0)),
        'params': params or {},
        'description': description,
        'is_enabled': _coerce_bool(is_enabled, True),
        'is_online': _coerce_bool(is_online, True),
    }
    config = LLMConfig.objects.filter(provider=provider, name=name).order_by('id').first()
    if config:
        for field, value in defaults.items():
            setattr(config, field, value)
        config.save(update_fields=[*defaults.keys(), 'updated_at'])
    else:
        config = LLMConfig.objects.create(provider=provider, name=name, **defaults)
    return (True, None, config.id)


def update_llm_config(config_id: int, payload: dict) -> tuple[bool, str | None, list[str]]:
    """按前端 PATCH 契约更新模型配置。"""
    config = LLMConfig.objects.filter(pk=config_id).first()
    if not config:
        return (False, "模型不存在", [])

    updated_fields: list[str] = []
    field_map = {
        "provider": "provider",
        "api_base_url": "api_endpoint",
        "api_endpoint": "api_endpoint",
    }
    if "model_name" in payload and payload.get("model_name") is not None:
        value = str(payload.get("model_name") or "").strip()
        if not value:
            return (False, "model_name 不能为空", [])
        config.name = value
        updated_fields.append("model_name")
    elif "name" in payload and payload.get("name") is not None:
        value = str(payload.get("name") or "").strip()
        if not value:
            return (False, "name 不能为空", [])
        config.name = value
        updated_fields.append("name")

    for incoming, model_field in field_map.items():
        if incoming in payload and payload.get(incoming) is not None:
            value = str(payload.get(incoming) or "").strip()
            if model_field == "provider" and not value:
                return (False, f"{incoming} 不能为空", [])
            setattr(config, model_field, value)
            updated_fields.append(incoming)

    if payload.get("api_key"):
        config.api_key_encrypted = str(payload.get("api_key")).strip()
        updated_fields.append("api_key")

    if "context_window" in payload and payload.get("context_window") is not None:
        context_window = _parse_positive_int(payload.get("context_window"), "context_window")
        if isinstance(context_window, str):
            return (False, context_window, [])
        config.context_window = context_window
        updated_fields.append("context_window")

    if "max_output_tokens" in payload and payload.get("max_output_tokens") is not None:
        max_output_tokens = _parse_positive_int(payload.get("max_output_tokens"), "max_output_tokens")
        if isinstance(max_output_tokens, str):
            return (False, max_output_tokens, [])
        config.max_output_tokens = max_output_tokens
        updated_fields.append("max_output_tokens")

    if "enabled" in payload or "is_enabled" in payload:
        config.is_enabled = _coerce_bool(payload.get("enabled", payload.get("is_enabled")))
        updated_fields.append("enabled")

    params = dict(config.params or {})
    if isinstance(payload.get("params"), dict):
        params.update(payload["params"])
        updated_fields.append("params")
    if "temperature" in payload and payload.get("temperature") is not None:
        temperature = _parse_float(payload.get("temperature"), "temperature")
        if isinstance(temperature, str):
            return (False, temperature, [])
        params["temperature"] = temperature
        updated_fields.append("temperature")
    config.params = params

    if "input_price_1m" in payload:
        input_price = _parse_decimal(payload.get("input_price_1m"), "input_price_1m")
        if isinstance(input_price, str):
            return (False, input_price, [])
        config.input_price_1m = input_price
        updated_fields.append("input_price_1m")
    if "output_price_1m" in payload:
        output_price = _parse_decimal(payload.get("output_price_1m"), "output_price_1m")
        if isinstance(output_price, str):
            return (False, output_price, [])
        config.output_price_1m = output_price
        updated_fields.append("output_price_1m")
    if "description" in payload:
        config.description = payload.get("description")
        updated_fields.append("description")

    if not updated_fields:
        return (True, None, [])

    config.save()
    return (True, None, sorted(set(updated_fields)))


def _parse_positive_int(value: Any, field_name: str) -> int | str:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return f"{field_name} 必须是正整数"
    if parsed <= 0:
        return f"{field_name} 必须是正整数"
    return parsed


def _parse_float(value: Any, field_name: str) -> float | str:
    try:
        return float(value)
    except (TypeError, ValueError):
        return f"{field_name} 必须是数字"


def _parse_decimal(value: Any, field_name: str) -> Decimal | str:
    try:
        parsed = Decimal(str(value or 0))
    except Exception:
        return f"{field_name} 必须是数字"
    if parsed < 0:
        return f"{field_name} 不能为负数"
    return parsed


def delete_llm_config(config_id: int) -> tuple[bool, str | None]:
    config = LLMConfig.objects.filter(pk=config_id).first()
    if not config:
        return (False, "模型不存在")
    config.delete()
    return (True, None)


def toggle_llm_config(config_id: int, is_enabled: bool) -> tuple[bool, str | None]:
    success, message, _updated_fields = update_llm_config(
        config_id,
        {"enabled": _coerce_bool(is_enabled)},
    )
    return (success, message)


def user_role(user) -> str:
    try:
        profile = getattr(user, "profile", None)
    except Exception:
        profile = None
    return str(getattr(profile, "role", "") or "").strip()


def is_super_admin(user) -> bool:
    return bool(
        getattr(user, "is_superuser", False)
        or user_role(user) == "super_admin"
    )


def get_user_model_permission(user, config_id: int) -> Optional[dict]:
    """获取用户对特定模型的权限。默认禁止，super_admin 默认允许。"""
    if is_super_admin(user):
        return {
            'daily_quota': None,
            'priority_weight': 100,
            'params_override': {},
        }

    role = user_role(user)
    query = Q(user=user)
    if role:
        role_aliases = {role, role.upper()}
        if role == "user":
            role_aliases.add("NORMAL")
        if role == "admin":
            role_aliases.add("ADMIN")
        query |= Q(role__in=role_aliases)

    perm = (
        ModelPermission.objects
        .filter(query, llm_config_id=config_id, is_active=True)
        .order_by('-priority_weight')
        .first()
    )
    if not perm:
        return None

    return {
        'daily_quota': perm.daily_quota,
        'priority_weight': perm.priority_weight,
        'params_override': perm.custom_params_override or {},
    }


def user_can_use_model(user, config: LLMConfig) -> bool:
    return bool(
        config
        and config.is_enabled
        and config.is_online
        and get_user_model_permission(user, config.id) is not None
    )


def serialize_model_available(config: LLMConfig) -> dict:
    return {
        "model_id": str(config.id),
        "model_name": config.name,
        "provider": config.provider,
    }


def serialize_model_detail(config: LLMConfig) -> dict:
    params = config.params or {}
    return {
        "id": config.id,
        "model_id": str(config.id),
        "name": config.name,
        "model_name": config.name,
        "provider": config.provider,
        "api_endpoint": config.api_endpoint,
        "api_base_url": config.api_endpoint or "",
        "context_window": config.context_window,
        "max_output_tokens": config.max_output_tokens,
        "temperature": float(params.get("temperature", 0.2)),
        "pricing": {
            "input": float(config.input_price_1m),
            "output": float(config.output_price_1m),
        },
        "params": params,
        "is_enabled": config.is_enabled,
        "enabled": config.is_enabled,
        "is_online": config.is_online,
        "connectivity_status": _connectivity_status(config),
        "granted_scope_summary": _grant_scope_summary(config),
        "description": config.description,
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


def _connectivity_status(config: LLMConfig) -> str:
    if not config.is_enabled:
        return "unknown"
    return "connected" if config.is_online else "failed"


def _grant_scope_summary(config: LLMConfig) -> str:
    active = config.user_permissions.filter(is_active=True)
    user_count = active.filter(user__isnull=False).count()
    roles = list(
        active.filter(role__isnull=False)
        .exclude(role="")
        .values_list("role", flat=True)
        .distinct()
    )
    if not user_count and not roles:
        return "未分配"
    parts = []
    if user_count:
        parts.append(f"{user_count} 个用户")
    if roles:
        parts.append(f"{len(roles)} 个用户组")
    return "、".join(parts)


def serialize_admin_model_item(config: LLMConfig) -> dict:
    params = config.params or {}
    return {
        "model_id": str(config.id),
        "model_name": config.name,
        "provider": config.provider,
        "api_base_url": config.api_endpoint or "",
        "context_window": config.context_window,
        "temperature": float(params.get("temperature", 0.2)),
        "enabled": config.is_enabled,
        "connectivity_status": _connectivity_status(config),
        "updated_at": config.updated_at.isoformat() if config.updated_at else "",
        "granted_scope_summary": _grant_scope_summary(config),
    }


def list_llm_configs(is_enabled: bool = None) -> list[dict]:
    """获取大模型配置列表，管理端使用。"""
    query = LLMConfig.objects.all().prefetch_related('user_permissions').order_by('id')
    if is_enabled is not None:
        query = query.filter(is_enabled=is_enabled)
    return [serialize_admin_model_item(config) for config in query]


def test_llm_config_connection(config_id: int) -> tuple[bool, str | None, dict | None]:
    """校验模型运行时必需配置，并更新在线状态。

    这里不直接请求第三方模型，避免管理端保存动作因外部网络阻塞；真实调用仍由
    research runtime 使用 get_provider_runtime_config 后进入 provider SDK。
    """
    config = LLMConfig.objects.filter(pk=config_id).first()
    if not config:
        return (False, "模型不存在", None)
    started = perf_counter()
    ok, message, _runtime = get_provider_runtime_config(config)
    latency_ms = max(int((perf_counter() - started) * 1000), 1)
    config.is_online = bool(ok)
    config.save(update_fields=["is_online", "updated_at"])
    return (
        True,
        None,
        {
            "model_id": str(config.id),
            "success": bool(ok),
            "latency_ms": latency_ms,
            "message": "模型配置完整，可进入运行时调用" if ok else message,
        },
    )


GROUP_ROLE_ALIASES = {
    "group-admin": "admin",
    "admin": "admin",
    "admin_group": "admin",
    "group-user": "user",
    "user": "user",
    "user_group": "user",
    "group-research": "user",
    "research": "user",
    "group-super-admin": "super_admin",
    "super_admin": "super_admin",
    "super_admin_group": "super_admin",
}


def _resolve_user_id(raw: Any):
    text = str(raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    suffix = text.rsplit("-", 1)[-1]
    return int(suffix) if suffix.isdigit() else None


def assign_model_permissions(
    config_id: int,
    user_ids: list[Any] | None = None,
    group_ids: list[Any] | None = None,
) -> tuple[bool, str | None, int]:
    """覆盖式分配模型权限，兼容前端 user_ids/group_ids 字段。"""
    config = LLMConfig.objects.filter(pk=config_id).first()
    if not config:
        return (False, "模型不存在", 0)

    User = get_user_model()
    resolved_user_ids = [
        user_id for user_id in (_resolve_user_id(value) for value in (user_ids or []))
        if user_id is not None
    ]
    users = list(User.objects.filter(pk__in=resolved_user_ids))

    roles = []
    for group_id in group_ids or []:
        role = GROUP_ROLE_ALIASES.get(str(group_id or "").strip())
        if role and role not in roles:
            roles.append(role)

    with transaction.atomic():
        ModelPermission.objects.filter(llm_config=config).delete()
        for user in users:
            ModelPermission.objects.create(
                llm_config=config,
                user=user,
                is_active=True,
                daily_quota=100,
                priority_weight=1,
            )
        for role in roles:
            ModelPermission.objects.create(
                llm_config=config,
                role=role,
                is_active=True,
                daily_quota=100,
                priority_weight=1,
            )

    return (True, None, len(users) + len(roles))


def list_available_models(
    user,
    object_type: str | None = None,
    usage_type: str = USAGE_TYPE_GENERAL,
) -> list[LLMConfig]:
    """列出当前用户可使用模型；传 object_type 时优先按场景映射排序。"""
    object_type = normalize_object_type(object_type)
    usage_type = normalize_usage_type(usage_type)
    configs = list(
        LLMConfig.objects
        .filter(is_enabled=True, is_online=True)
        .order_by('id')
    )
    configs = [config for config in configs if user_can_use_model(user, config)]
    if not object_type:
        return configs

    mappings = list(
        ModelObjectMapping.objects
        .filter(
            object_type=object_type,
            usage_type=usage_type,
            llm_config_id__in=[config.id for config in configs],
        )
        .select_related('llm_config')
        .order_by('-priority', '-is_default', 'llm_config_id')
    )
    mapped_ids = [mapping.llm_config_id for mapping in mappings]
    by_id = {config.id: config for config in configs}
    ordered = [by_id[config_id] for config_id in mapped_ids if config_id in by_id]
    ordered.extend(config for config in configs if config.id not in mapped_ids)
    return ordered


def get_recommended_config(
    user,
    object_type: str,
    usage_type: str = USAGE_TYPE_GENERAL,
) -> LLMConfig | None:
    candidates = list_available_models(user, object_type, usage_type)
    return candidates[0] if candidates else None


def get_recommended_model(
    user,
    object_type: str,
    usage_type: str = USAGE_TYPE_GENERAL,
) -> Optional[dict]:
    config = get_recommended_config(user, object_type, usage_type)
    return serialize_model_available(config) if config else None


def build_routing_recommendation(
    user,
    object_type: str | None,
    usage_type: str = USAGE_TYPE_GENERAL,
) -> dict:
    configs = list_available_models(user, object_type, usage_type)
    recommended = configs[0] if configs else None
    normalized_object_type = normalize_object_type(object_type)
    return {
        "recommended_model_id": str(recommended.id) if recommended else None,
        "candidate_models": [serialize_model_available(config) for config in configs],
        "reason": (
            f"根据对象类型 {normalized_object_type or 'GENERAL'}、用途 "
            f"{normalize_usage_type(usage_type)} 与当前用户授权推荐。"
            if recommended else "当前用户没有可用模型，请联系管理员分配权限。"
        ),
    }


def resolve_user_model_config(
    user,
    model_id: str | int | None = None,
    object_type: str | None = None,
    usage_type: str = USAGE_TYPE_GENERAL,
) -> tuple[bool, str | None, LLMConfig | None, dict]:
    """解析前端 model_id 或按场景推荐模型，并合并用户参数覆盖。"""
    config = None
    if model_id not in (None, ""):
        try:
            config = LLMConfig.objects.filter(pk=int(model_id)).first()
        except (TypeError, ValueError):
            return (False, "model_id 必须是平台模型配置 ID", None, {})
        if not config:
            return (False, "模型不存在", None, {})
    else:
        config = get_recommended_config(user, object_type, usage_type)
        if not config:
            return (False, "未找到当前用户可用模型", None, {})

    if not config.is_enabled or not config.is_online:
        return (False, "模型未启用或当前离线", None, {})

    permission = get_user_model_permission(user, config.id)
    if permission is None:
        return (False, "当前用户无权使用该模型", None, {})

    params = dict(config.params or {})
    params.update(permission.get("params_override") or {})
    return (True, None, config, params)


def resolve_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("${") and text.endswith("}"):
        return os.getenv(text[2:-1].strip(), "").strip()
    if text.lower().startswith("env:"):
        return os.getenv(text[4:].strip(), "").strip()
    return text


def resolve_env_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        return ""
    return os.getenv(name, "").strip()


def get_provider_runtime_config(
    config: LLMConfig,
    params: dict | None = None,
) -> tuple[bool, str | None, dict]:
    params = dict(params or config.params or {})
    api_key = (
        resolve_secret(config.api_key_encrypted)
        or resolve_secret(params.get("api_key"))
        or resolve_env_name(params.get("api_key_env"))
    )
    base_url = (
        resolve_secret(config.api_endpoint)
        or resolve_secret(params.get("base_url"))
        or resolve_secret(params.get("api_base"))
        or resolve_env_name(params.get("api_endpoint_env"))
        or resolve_env_name(params.get("base_url_env"))
    )
    base_url = normalize_provider_base_url(config.provider, base_url)
    model_name = str(config.name or "").strip()
    if not model_name or not api_key or not base_url:
        return (False, "选定模型缺少 model_name / api_key / api_endpoint 配置", {})
    return (
        True,
        None,
        {
            "model": model_name,
            "api_key": api_key,
            "base_url": base_url,
            "debug_provider_http": bool(params.get("debug_provider_http", False)),
        },
    )


def log_model_usage(
    user,
    config_id: int | None,
    request_id: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int = 0,
    usage_type: str = USAGE_TYPE_GENERAL,
    status_code: int = 200,
) -> ModelUsage | None:
    """记录模型调用流水并自动计算成本。config_id 为空时跳过。"""
    if not config_id:
        return None
    config = LLMConfig.objects.filter(pk=config_id).first()
    if not config:
        return None
    total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
    cost = (
        Decimal(int(prompt_tokens or 0)) * config.input_price_1m / Decimal(1000000)
        + Decimal(int(completion_tokens or 0)) * config.output_price_1m / Decimal(1000000)
    )
    return ModelUsage.objects.create(
        user=user,
        llm_config=config,
        request_id=request_id or str(uuid4()),
        usage_type=normalize_usage_type(usage_type),
        prompt_tokens=int(prompt_tokens or 0),
        completion_tokens=int(completion_tokens or 0),
        total_tokens=total_tokens,
        cost=cost,
        latency_ms=int(latency_ms or 0),
        status_code=int(status_code or 200),
    )
