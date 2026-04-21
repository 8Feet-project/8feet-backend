from typing import Tuple, Optional, List
from decimal import Decimal
from llm_manager.models.llm_config import LLMConfig
from llm_manager.models.model_permission import ModelObjectMapping, ModelPermission
from llm_manager.models.model_usage import ModelUsage


def create_or_update_llm_config(
    name: str, provider: str, model_id: str,
    api_endpoint: str = None, api_key: str = None,
    context_window: int = 4096, max_output_tokens: int = 2048,
    input_price_1m: float = 0.0, output_price_1m: float = 0.0,
    params: dict = None, description: str = None
) -> Tuple[bool, Optional[str], Optional[int]]:
    """创建或更新大模型配置"""
    if not name or not provider or not model_id:
        return (False, "name, provider, model_id 均为必填项", None)

    config, created = LLMConfig.objects.update_or_create(
        provider=provider, model_id=model_id,
        defaults={
            'name': name,
            'api_endpoint': api_endpoint,
            'api_key_encrypted': api_key,
            'context_window': context_window,
            'max_output_tokens': max_output_tokens,
            'input_price_1m': Decimal(str(input_price_1m)),
            'output_price_1m': Decimal(str(output_price_1m)),
            'params': params or {},
            'description': description,
        }
    )
    return (True, None, config.id)


def get_user_model_permission(user, config_id: int) -> Optional[dict]:
    """获取用户对特定模型的权限集合 (包含参数覆盖)"""
    perm = ModelPermission.objects.filter(user=user, llm_config_id=config_id, is_active=True).first()
    if not perm:
        return None
    
    return {
        'daily_quota': perm.daily_quota,
        'priority_weight': perm.priority_weight,
        'params_override': perm.custom_params_override
    }


def log_model_usage(
    user, config_id: int, request_id: str, 
    prompt_tokens: int, completion_tokens: int,
    latency_ms: int = 0, usage_type: str = 'GENERAL',
    status_code: int = 200
) -> ModelUsage:
    """记录模型调用流水并自动计算成本"""
    config = LLMConfig.objects.get(pk=config_id)
    total_tokens = prompt_tokens + completion_tokens
    
    # 计算成本 (按百万 Token 单价)
    cost = (Decimal(prompt_tokens) * config.input_price_1m / Decimal(1000000)) + \
           (Decimal(completion_tokens) * config.output_price_1m / Decimal(1000000))
    
    usage = ModelUsage.objects.create(
        user=user,
        llm_config=config,
        request_id=request_id,
        usage_type=usage_type,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost=cost,
        latency_ms=latency_ms,
        status_code=status_code
    )
    return usage


def list_llm_configs(is_enabled: bool = None) -> List[dict]:
    """获取大模型配置列表 (管理员视角)"""
    query = LLMConfig.objects.all()
    if is_enabled is not None:
        query = query.filter(is_enabled=is_enabled)
    return list(query.values(
        'id', 'name', 'provider', 'model_id',
        'is_enabled', 'is_online', 'description', 
        'input_price_1m', 'output_price_1m', 'created_at'
    ))


def get_recommended_model(object_type: str, usage_type: str = 'GENERAL') -> Optional[dict]:
    """获取某调研对象类型在特定用途下的最优推荐模型"""
    mapping = ModelObjectMapping.objects.filter(
        object_type=object_type, 
        usage_type=usage_type,
        llm_config__is_enabled=True,
        llm_config__is_online=True
    ).select_related('llm_config').order_by('-priority', '-is_default').first()

    if not mapping:
        return None

    config = mapping.llm_config
    return {
        'id': config.id,
        'name': config.name,
        'provider': config.provider,
        'model_id': config.model_id,
        'context_window': config.context_window
    }
