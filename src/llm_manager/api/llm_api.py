"""
大模型配置 API — CRUD、启禁用、可用模型与路由推荐。
"""
import json

from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from shared.utils import (
    ErrorCode,
    failed_api_response,
    jwt_auth,
    response_wrapper,
    success_api_response,
)
from llm_manager.interface.llm_interface import (
    assign_model_permissions,
    build_routing_recommendation,
    create_or_update_llm_config,
    delete_llm_config,
    list_available_models,
    list_llm_configs,
    serialize_model_available,
    serialize_model_detail,
    test_llm_config_connection,
    toggle_llm_config,
    update_llm_config,
)
from llm_manager.models.llm_config import LLMConfig
from llm_manager.models.model_permission import ModelPermission
from llm_manager.models.model_usage import ModelUsage


def _request_data(request: HttpRequest) -> dict:
    if request.content_type and "application/json" in request.content_type:
        try:
            return json.loads(request.body or b"{}")
        except (json.JSONDecodeError, TypeError):
            return {}
    return request.POST


def _to_bool(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _create_admin_model(data: dict) -> dict:
    params = data.get('params', {})
    if isinstance(params, str):
        try:
            params = json.loads(params or "{}")
        except (json.JSONDecodeError, TypeError):
            params = {}
    if not isinstance(params, dict):
        params = {}
    if data.get('temperature') is not None:
        params = {**params, "temperature": _to_float(data.get('temperature'), 0.2)}

    success, message, config_id = create_or_update_llm_config(
        name=data.get('name') or data.get('model_name'),
        provider=data.get('provider'),
        model_id=data.get('model_name') or data.get('name'),
        api_endpoint=data.get('api_endpoint') or data.get('api_base_url'),
        api_key=data.get('api_key'),
        context_window=_to_int(data.get('context_window'), 4096),
        max_output_tokens=_to_int(data.get('max_output_tokens'), 2048),
        input_price_1m=_to_float(data.get('input_price_1m'), 0.0),
        output_price_1m=_to_float(data.get('output_price_1m'), 0.0),
        params=params,
        description=data.get('description'),
        is_enabled=_to_bool(data.get('is_enabled', data.get('enabled')), True),
        is_online=_to_bool(data.get('is_online'), True),
    )
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    ok, _error, test_payload = test_llm_config_connection(config_id)
    connectivity_status = "unknown"
    if ok and test_payload:
        connectivity_status = "connected" if test_payload.get("success") else "failed"
    return success_api_response({
        "model_id": str(config_id),
        "connectivity_status": connectivity_status,
    })


def _model_list_response(request: HttpRequest) -> dict:
    configs = list_llm_configs()
    total = len(configs)
    page = request.GET.get("page")
    page_size = request.GET.get("page_size")
    if page is not None or page_size is not None:
        safe_page = max(_to_int(page, 1), 1)
        safe_page_size = max(min(_to_int(page_size, 100), 500), 1)
        start = (safe_page - 1) * safe_page_size
        end = start + safe_page_size
        return success_api_response({
            "list": configs[start:end],
            "total": total,
            "page": safe_page,
            "page_size": safe_page_size,
        })
    return success_api_response({"list": configs, "total": total})


@response_wrapper
@require_POST
@jwt_auth(perms=['llm_manager.change_llmconfig'])
def create_config(request: HttpRequest):
    """创建模型配置；保留函数供内部兼容，公开创建路径为 POST /admin/models。"""
    return _create_admin_model(_request_data(request))


@response_wrapper
@require_http_methods(["GET", "POST"])
@jwt_auth()
def config_collection(request: HttpRequest):
    """兼容 /admin/models 的 GET 列表与 POST 创建。"""
    if request.method == 'POST':
        if not request.user.has_perm('llm_manager.change_llmconfig'):
            return failed_api_response(ErrorCode.REFUSE_ACCESS, "您无权进行此操作")
        return _create_admin_model(_request_data(request))
    if request.method == 'GET':
        if not request.user.has_perm('llm_manager.view_llmconfig'):
            return failed_api_response(ErrorCode.REFUSE_ACCESS, "您无权进行此操作")
        return _model_list_response(request)
    return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "不支持的请求方法")


@response_wrapper
@require_GET
@jwt_auth(perms=['llm_manager.view_llmconfig'])
def list_configs(request: HttpRequest):
    """获取管理端模型列表。"""
    return _model_list_response(request)


@response_wrapper
@require_GET
@jwt_auth()
def available_models(request: HttpRequest):
    """获取当前用户可用模型列表。"""
    object_type = request.GET.get('object_type') or request.GET.get('scene')
    usage_type = request.GET.get('usage_type') or request.GET.get('research_category')
    configs = list_available_models(request.user, object_type, usage_type)
    return success_api_response({
        "models": [serialize_model_available(config) for config in configs],
        "recommended_model_id": str(configs[0].id) if configs else None,
    })


@response_wrapper
@require_GET
@jwt_auth()
def routing_recommendation(request: HttpRequest):
    """根据应用场景获取推荐模型。"""
    object_type = request.GET.get('object_type')
    usage_type = request.GET.get('usage_type') or request.GET.get('research_category')
    if not object_type:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            "object_type 不能为空",
        )

    payload = build_routing_recommendation(request.user, object_type, usage_type)
    if not payload["recommended_model_id"]:
        return failed_api_response(
            ErrorCode.ITEM_NOT_FOUND,
            "未找到匹配的可选模型，请联系管理员分配权限",
            payload,
        )
    return success_api_response(payload)


@response_wrapper
@require_POST
@jwt_auth(perms=['llm_manager.change_llmconfig'])
def toggle_config(request: HttpRequest, model_id: int):
    """启用/禁用大模型 (管理员)。"""
    data = _request_data(request)
    enabled_value = data.get('enabled', data.get('is_enabled'))
    success, message = toggle_llm_config(
        model_id,
        _to_bool(enabled_value, True),
    )
    if not success:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, message)
    return success_api_response({
        "model_id": str(model_id),
        "updated_fields": ["enabled"],
    })


@response_wrapper
@jwt_auth(perms=['llm_manager.view_llmconfig'])
def model_detail_resource(request: HttpRequest, model_id: int):
    """获取、更新或删除特定模型配置。"""
    config = LLMConfig.objects.filter(pk=model_id).first()
    if not config:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "模型不存在")

    if request.method == 'GET':
        return success_api_response(serialize_model_detail(config))

    if request.method == 'PATCH':
        data = _request_data(request)
        params = data.get('params', config.params or {})
        if isinstance(params, str):
            try:
                params = json.loads(params or "{}")
            except (json.JSONDecodeError, TypeError):
                params = config.params or {}

        config.name = data.get('name') or data.get('model_name') or config.name
        config.provider = data.get('provider') or config.provider
        config.model_id = data.get('provider_model_id') or data.get('model_id') or config.model_id
        config.api_endpoint = data.get('api_endpoint') or data.get('api_base_url') or config.api_endpoint
        if data.get('api_key') not in (None, ""):
            config.api_key_encrypted = data.get('api_key')
        if data.get('context_window') is not None:
            config.context_window = _to_int(data.get('context_window'), config.context_window)
        if data.get('max_output_tokens') is not None:
            config.max_output_tokens = _to_int(data.get('max_output_tokens'), config.max_output_tokens)
        if data.get('input_price_1m') is not None:
            config.input_price_1m = _to_float(data.get('input_price_1m'), config.input_price_1m)
        if data.get('output_price_1m') is not None:
            config.output_price_1m = _to_float(data.get('output_price_1m'), config.output_price_1m)
        if data.get('description') is not None:
            config.description = data.get('description')
        if data.get('enabled') is not None or data.get('is_enabled') is not None:
            config.is_enabled = _to_bool(data.get('is_enabled', data.get('enabled')), config.is_enabled)
        if data.get('is_online') is not None:
            config.is_online = _to_bool(data.get('is_online'), config.is_online)
        if isinstance(params, dict):
            config.params = params
        config.save()
        return success_api_response({
            "model_id": str(config.id),
            "updated_fields": [
                "model_name", "provider", "api_base_url", "context_window",
                "max_output_tokens", "input_price_1m", "output_price_1m",
                "description", "enabled",
            ],
        })

    if request.method == 'DELETE':
        config.delete()
        return success_api_response({"result": "success"})

    return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "不支持的请求方法")


get_config_detail = model_detail_resource


@response_wrapper
@require_POST
@jwt_auth(perms=['llm_manager.change_llmconfig'])
def test_config_connection(request: HttpRequest, model_id: int):
    """管理端连接测试。当前先校验配置完整性，避免前端调用缺失路由返回 HTML。"""
    config = LLMConfig.objects.filter(pk=model_id).first()
    if not config:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "模型不存在")

    ok = bool(config.api_endpoint)
    return success_api_response({
        "model_id": str(config.id),
        "success": ok,
        "latency_ms": 0,
        "message": "连接配置完整" if ok else "API Base URL 不能为空",
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['llm_manager.change_modelpermission'])
def assign_model_permissions(request: HttpRequest, model_id: int):
    """为指定用户授予模型使用权限。group_ids 按角色名兼容处理。"""
    config = LLMConfig.objects.filter(pk=model_id).first()
    if not config:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "模型不存在")

    data = _request_data(request)
    user_ids = data.get("user_ids") or []
    group_ids = data.get("group_ids") or []
    granted = 0

    User = get_user_model()
    for user in User.objects.filter(id__in=user_ids):
        ModelPermission.objects.update_or_create(
            llm_config=config,
            user=user,
            defaults={
                "is_active": True,
                "daily_quota": 100,
                "priority_weight": 10,
                "custom_params_override": {},
            },
        )
        granted += 1

    for role in group_ids:
        role_name = str(role).replace("group-", "").strip()
        if not role_name:
            continue
        ModelPermission.objects.update_or_create(
            llm_config=config,
            role=role_name,
            defaults={
                "is_active": True,
                "daily_quota": 100,
                "priority_weight": 5,
                "custom_params_override": {},
            },
        )
        granted += 1

    return success_api_response({"model_id": str(config.id), "granted_count": granted})
