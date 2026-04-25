"""
智能模型路由器。
对外保留旧入口，内部统一委托 llm_interface 的权限与场景推荐逻辑。
"""
from typing import Optional

from llm_manager.interface.llm_interface import (
    build_routing_recommendation,
    get_recommended_model,
)
from llm_manager.models.model_permission import USAGE_TYPE_GENERAL


class IntelligenceRouter:
    """智能路由引擎。"""

    @staticmethod
    def get_best_model(
        user,
        object_type: str,
        usage_type: str = USAGE_TYPE_GENERAL,
        require_high_priority: bool = False,
    ) -> Optional[dict]:
        return get_recommended_model(user, object_type, usage_type)


def get_routed_model(
    user,
    object_type: str,
    usage_type: str = USAGE_TYPE_GENERAL,
) -> Optional[dict]:
    return IntelligenceRouter.get_best_model(user, object_type, usage_type)


def get_routing_payload(
    user,
    object_type: str,
    usage_type: str = USAGE_TYPE_GENERAL,
) -> dict:
    return build_routing_recommendation(user, object_type, usage_type)
