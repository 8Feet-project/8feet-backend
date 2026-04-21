"""
大模型配置 API — CRUD 与启禁用
映射需求: FR-SJGL-0001 (大模型配置管理)
"""
import json
from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

from shared.utils import (
    ErrorCode, failed_api_response, response_wrapper,
    success_api_response, jwt_auth
)
from llm_manager.interface.llm_interface import (
    create_or_update_llm_config, toggle_llm_config,
    list_llm_configs, get_recommended_model
)


from llm_manager.interface.llm_interface import (
    create_or_update_llm_config, list_llm_configs
)
from llm_manager.interface.router import get_routed_model
from llm_manager.models.model_usage import ModelUsage


@response_wrapper
@require_POST
@jwt_auth(perms=['llm_manager.change_llmconfig'])
def create_config(request: HttpRequest):
    """创建/更新大模型配置 (管理员)"""
    name = request.POST.get('name')
    provider = request.POST.get('provider')
    model_id = request.POST.get('model_id')
    api_endpoint = request.POST.get('api_endpoint')
    api_key = request.POST.get('api_key')
    
    # 专家参数
    context_window = int(request.POST.get('context_window', 4096))
    max_output_tokens = int(request.POST.get('max_output_tokens', 2048))
    input_price = float(request.POST.get('input_price_1m', 0.0))
    output_price = float(request.POST.get('output_price_1m', 0.0))
    
    description = request.POST.get('description')
    params_str = request.POST.get('params', '{}')

    try:
        params = json.loads(params_str)
    except (json.JSONDecodeError, TypeError):
        params = {}

    success, message, config_id = create_or_update_llm_config(
        name, provider, model_id, api_endpoint, api_key,
        context_window, max_output_tokens, input_price, output_price,
        params, description
    )
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({"id": config_id})


@response_wrapper
@require_GET
@jwt_auth(perms=['llm_manager.view_llmconfig'])
def list_configs(request: HttpRequest):
    """获取管理端模型列表"""
    configs = list_llm_configs()
    return success_api_response(configs)


@response_wrapper
@require_GET
@jwt_auth()
def routing_recommendation(request: HttpRequest):
    """根据应用场景智能获取推荐模型 (用户/Agent 侧)
    [route]: GET /api/v1/model-routing/recommendation?object_type=COMPANY&usage_type=REASONING
    """
    object_type = request.GET.get('object_type')
    usage_type = request.GET.get('usage_type', 'GENERAL')
    
    if not object_type:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "object_type 不能为空")

    model = get_routed_model(request.user, object_type, usage_type)
    if not model:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "未找到匹配的可选模型，请联系管理员分配权限")

    return success_api_response(model)


@response_wrapper
@require_GET
@jwt_auth(perms=['llm_manager.view_modelusage'])
def get_usage_dashboard(request: HttpRequest):
    """获取模型使用统计摘要 (管理员)"""
    from django.db.models import Sum, Count
    
    stats = ModelUsage.objects.aggregate(
        total_calls=Count('id'),
        total_tokens=Sum('total_tokens'),
        total_cost=Sum('cost')
    )
    
    # 最近 10 条流水
    latest_logs = list(ModelUsage.objects.select_related('user', 'llm_config').values(
        'id', 'user__username', 'llm_config__name', 'total_tokens', 'cost', 'created_at'
    )[:10])
    
    return success_api_response({
        "summary": stats,
        "latest_logs": latest_logs
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['llm_manager.change_llmconfig'])
def toggle_config(request: HttpRequest, model_id: int):
    """启用/禁用大模型 (管理员)"""
    is_enabled = request.POST.get('is_enabled', 'true').lower() == 'true'

    from llm_manager.models.llm_config import LLMConfig
    config = LLMConfig.objects.filter(pk=model_id).first()
    if not config:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "模型不存在")
    
    config.is_enabled = is_enabled
    config.save()
    return success_api_response()


@response_wrapper
@require_GET
@jwt_auth(perms=['llm_manager.view_llmconfig'])
def get_config_detail(request: HttpRequest, model_id: int):
    """获取特定模型的详细配置与统计"""
    from llm_manager.models.llm_config import LLMConfig
    config = LLMConfig.objects.filter(pk=model_id).first()
    if not config:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "模型不存在")

    return success_api_response({
        "id": config.id,
        "name": config.name,
        "provider": config.provider,
        "model_id": config.model_id,
        "api_endpoint": config.api_endpoint,
        "context_window": config.context_window,
        "max_output_tokens": config.max_output_tokens,
        "pricing": {
            "input": float(config.input_price_1m),
            "output": float(config.output_price_1m)
        },
        "params": config.params,
        "is_enabled": config.is_enabled,
        "is_online": config.is_online,
        "description": config.description
    })
