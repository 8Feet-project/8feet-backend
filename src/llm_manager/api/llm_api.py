"""
大模型配置 API — CRUD、启禁用、可用模型与路由推荐。
"""
import json

from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

from shared.utils import (
    ErrorCode,
    failed_api_response,
    jwt_auth,
    response_wrapper,
    success_api_response,
)
from llm_manager.interface.llm_interface import (
    build_routing_recommendation,
    create_or_update_llm_config,
    list_available_models,
    list_llm_configs,
    serialize_model_available,
    serialize_model_detail,
    toggle_llm_config,
)
from llm_manager.models.llm_config import LLMConfig
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


@response_wrapper
@require_POST
@jwt_auth(perms=['llm_manager.change_llmconfig'])
def create_config(request: HttpRequest):
    """创建/更新大模型配置 (管理员)。"""
    data = _request_data(request)
    params = data.get('params', {})
    if isinstance(params, str):
        try:
            params = json.loads(params or "{}")
        except (json.JSONDecodeError, TypeError):
            params = {}

    success, message, config_id = create_or_update_llm_config(
        name=data.get('name') or data.get('model_name'),
        provider=data.get('provider'),
        model_id=data.get('provider_model_id') or data.get('model_id'),
        api_endpoint=data.get('api_endpoint') or data.get('api_base_url'),
        api_key=data.get('api_key'),
        context_window=_to_int(data.get('context_window'), 4096),
        max_output_tokens=_to_int(data.get('max_output_tokens'), 2048),
        input_price_1m=_to_float(data.get('input_price_1m'), 0.0),
        output_price_1m=_to_float(data.get('output_price_1m'), 0.0),
        params=params if isinstance(params, dict) else {},
        description=data.get('description'),
        is_enabled=_to_bool(data.get('is_enabled', data.get('enabled')), True),
        is_online=_to_bool(data.get('is_online'), True),
    )
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({"id": config_id, "model_id": str(config_id)})


@response_wrapper
@jwt_auth()
def config_collection(request: HttpRequest):
    """兼容 /admin/models 的 GET 列表与 POST 创建。"""
    if request.method == 'POST':
        return create_config(request)
    if request.method == 'GET':
        return list_configs(request)
    return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "不支持的请求方法")


@response_wrapper
@require_GET
@jwt_auth(perms=['llm_manager.view_llmconfig'])
def list_configs(request: HttpRequest):
    """获取管理端模型列表。"""
    configs = list_llm_configs()
    return success_api_response({"list": configs, "total": len(configs)})


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
@require_GET
@jwt_auth(perms=['llm_manager.view_modelusage'])
def get_usage_dashboard(request: HttpRequest):
    """获取模型使用统计摘要 (管理员)。"""
    from django.db.models import Count, Sum

    stats = ModelUsage.objects.aggregate(
        total_calls=Count('id'),
        total_tokens=Sum('total_tokens'),
        total_cost=Sum('cost'),
    )
    latest_logs = list(
        ModelUsage.objects
        .select_related('user', 'llm_config')
        .values('id', 'user__username', 'llm_config__name', 'total_tokens', 'cost', 'created_at')[:10]
    )
    return success_api_response({
        "summary": stats,
        "latest_logs": latest_logs,
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['llm_manager.change_llmconfig'])
def toggle_config(request: HttpRequest, model_id: int):
    """启用/禁用大模型 (管理员)。"""
    data = _request_data(request)
    success, message = toggle_llm_config(
        model_id,
        _to_bool(data.get('is_enabled', data.get('enabled')), True),
    )
    if not success:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, message)
    return success_api_response()


@response_wrapper
@require_GET
@jwt_auth(perms=['llm_manager.view_llmconfig'])
def get_config_detail(request: HttpRequest, model_id: int):
    """获取特定模型的详细配置与统计。"""
    config = LLMConfig.objects.filter(pk=model_id).first()
    if not config:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "模型不存在")
    return success_api_response(serialize_model_detail(config))
