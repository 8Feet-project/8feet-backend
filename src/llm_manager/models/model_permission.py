"""
模型权限与对象映射模型
FR-SJGL-0001: 支持针对不同用户分配特定大模型的使用权限
FR-DYBG-0001: 针对每个分类，管理员可关联模型并设默认推荐
"""
from django.db import models
from django.contrib.auth import get_user_model

from llm_manager.models.llm_config import LLMConfig


class ModelPermission(models.Model):
    """模型使用权限

    实现“每个用户有不同的权限集合”的需求。包含基础访问、限额、优先级及参数覆盖。
    """
    llm_config = models.ForeignKey(
        LLMConfig, on_delete=models.CASCADE,
        related_name='user_permissions'
    )
    user = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE,
        related_name='llm_permissions',
        null=True, blank=True,
        help_text="关联特定用户"
    )
    role = models.CharField(
        max_length=16, null=True, blank=True,
        help_text="关联用户角色，为空则只匹配指定用户"
    )
    is_active = models.BooleanField(
        default=True, help_text="该用户是否可使用此模型"
    )
    daily_quota = models.PositiveIntegerField(
        default=100, help_text="该用户每日调用上限 (次数)"
    )
    priority_weight = models.SmallIntegerField(
        default=1, help_text="调度优先级 (权重越高越优先)"
    )
    custom_params_override = models.JSONField(
        default=dict, blank=True,
        help_text="针对该用户的专属参数覆盖 (如不同的 temp)"
    )

    class Meta:
        db_table = 'model_permission'
        verbose_name = '模型用户权限'
        verbose_name_plural = verbose_name
        unique_together = (
            ('llm_config', 'user'),
            ('llm_config', 'role'),
        )


# 调研用途分类
USAGE_TYPE_GENERAL = 'GENERAL'
USAGE_TYPE_REASONING = 'REASONING'
USAGE_TYPE_SUMMARIZE = 'SUMMARIZE'
USAGE_TYPES = [
    (USAGE_TYPE_GENERAL, '通用/对话'),
    (USAGE_TYPE_REASONING, '逻辑推理/拆解'),
    (USAGE_TYPE_SUMMARIZE, '内容摘要/提炼'),
]

OBJECT_TYPE_COMPANY = 'COMPANY'
OBJECT_TYPE_STOCK = 'STOCK'
OBJECT_TYPE_PRODUCT = 'PRODUCT'
OBJECT_TYPES = [
    (OBJECT_TYPE_COMPANY, '公司'),
    (OBJECT_TYPE_STOCK, '股票'),
    (OBJECT_TYPE_PRODUCT, '商品'),
]


class ModelObjectMapping(models.Model):
    """模型-调研场景多维映射

    支持针对不同对象类型、在不同研究阶段推荐不同的模型。
    """
    llm_config = models.ForeignKey(
        LLMConfig, on_delete=models.CASCADE,
        related_name='object_mappings'
    )
    object_type = models.CharField(
        max_length=16, choices=OBJECT_TYPES,
        help_text="调研对象类型"
    )
    usage_type = models.CharField(
        max_length=16, choices=USAGE_TYPES,
        default=USAGE_TYPE_GENERAL,
        help_text="建议用途"
    )
    priority = models.IntegerField(
        default=0, help_text="推荐优先级 (数值越大越优先)"
    )
    is_default = models.BooleanField(
        default=False, help_text="是否为该场景下的默认模型"
    )

    class Meta:
        db_table = 'model_object_mapping'
        verbose_name = '模型场景推荐'
        verbose_name_plural = verbose_name
        unique_together = ('llm_config', 'object_type', 'usage_type')
