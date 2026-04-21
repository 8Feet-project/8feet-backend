"""
智能模型路由器
实现基于场景、用途、用户权限及成本的智能模型选择逻辑。
"""
from typing import Optional, List
from llm_manager.models.llm_config import LLMConfig
from llm_manager.models.model_permission import (
    ModelObjectMapping, ModelPermission,
    USAGE_TYPE_GENERAL
)
from llm_manager.interface.llm_interface import get_user_model_permission


class IntelligenceRouter:
    """智能路由引擎"""

    @staticmethod
    def get_best_model(
        user, 
        object_type: str, 
        usage_type: str = USAGE_TYPE_GENERAL,
        require_high_priority: bool = False
    ) -> Optional[dict]:
        """为特定用户和场景选择最佳模型
        
        逻辑优先级:
        1. 检查用户是否有专属的该场景推荐 (未来扩展)
        2. 查询场景映射表 (ModelObjectMapping) 获取推荐列表
        3. 过滤掉用户无权使用的模型 (ModelPermission)
        4. 根据 priority 排序并返回最高优的一个
        """
        # 1. 获取该场景下的所有候选模型
        candidates = ModelObjectMapping.objects.filter(
            object_type=object_type,
            usage_type=usage_type,
            llm_config__is_enabled=True,
            llm_config__is_online=True
        ).select_related('llm_config').order_by('-priority')
        
        for mapping in candidates:
            config = mapping.llm_config
            
            # 2. 检查用户权限 (如果是超级管理员则跳过个体检查)
            if hasattr(user, 'profile') and user.profile.role == 'super_admin':
                 return IntelligenceRouter._build_model_info(config)
            
            # 获取用户对该模型的专属权限
            user_perm = ModelPermission.objects.filter(
                user=user, llm_config=config, is_active=True
            ).first()
            
            if user_perm:
                # 如果用户有权限，构造信息并应用参数覆盖
                info = IntelligenceRouter._build_model_info(config)
                if user_perm.custom_params_override:
                    info['params'].update(user_perm.custom_params_override)
                return info
            
            # 3. 如果用户没有专属权限项，检查是否有“全员可用”逻辑 (暂定为：如果没有专属项则不可用，除非是公开模型)
            # 这里可以根据需求调整：是“默认禁止”还是“默认允许”
            
        return None

    @staticmethod
    def _build_model_info(config: LLMConfig) -> dict:
        return {
            'id': config.id,
            'name': config.name,
            'provider': config.provider,
            'model_id': config.model_id,
            'context_window': config.context_window,
            'params': config.params.copy() if config.params else {}
        }


def get_routed_model(user, object_type: str, usage_type: str = USAGE_TYPE_GENERAL) -> Optional[dict]:
    """快捷路由入口"""
    return IntelligenceRouter.get_best_model(user, object_type, usage_type)
