"""
大模型配置业务逻辑 — interface 层
纯业务逻辑，不涉及 HTTP。
"""
from typing import Tuple, Optional, List

from llm_manager.models.llm_config import LLMConfig
from llm_manager.models.model_permission import ModelObjectMapping


def create_or_update_llm_config(
    name: str, provider: str, model_id: str,
    api_endpoint: str = None, api_key: str = None,
    params: dict = None, description: str = None
) -> Tuple[bool, Optional[str], Optional[int]]:
    """创建或更新大模型配置

    Returns:
        (成功与否, 错误消息, config_id)
    """
    if not name or not provider or not model_id:
        return (False, "name, provider, model_id 均为必填项", None)

    config, created = LLMConfig.objects.update_or_create(
        provider=provider, model_id=model_id,
        defaults={
            'name': name,
            'api_endpoint': api_endpoint,
            'api_key_encrypted': api_key,
            'params': params or {},
            'description': description,
        }
    )
    return (True, None, config.id)


def toggle_llm_config(config_id: int, is_enabled: bool) -> Tuple[bool, Optional[str]]:
    """启用/禁用大模型"""
    config = LLMConfig.objects.filter(pk=config_id).first()
    if not config:
        return (False, "模型配置不存在")
    config.is_enabled = is_enabled
    config.save()
    return (True, None)


def list_llm_configs(is_enabled: bool = None) -> List[dict]:
    """获取大模型配置列表"""
    query = LLMConfig.objects.all()
    if is_enabled is not None:
        query = query.filter(is_enabled=is_enabled)
    return list(query.values(
        'id', 'name', 'provider', 'model_id',
        'is_enabled', 'description', 'params', 'created_at'
    ))


def get_recommended_model(object_type: str) -> Optional[dict]:
    """获取某调研对象类型的默认推荐模型

    FR-DYBG-0001: 智能模型路由
    """
    mapping = ModelObjectMapping.objects.filter(
        object_type=object_type, is_default=True,
        llm_config__is_enabled=True
    ).select_related('llm_config').first()

    if not mapping:
        return None

    config = mapping.llm_config
    return {
        'id': config.id,
        'name': config.name,
        'provider': config.provider,
        'model_id': config.model_id,
    }
