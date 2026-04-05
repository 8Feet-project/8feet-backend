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

    细粒度控制哪些用户/角色可以使用哪些模型。
    """
    llm_config = models.ForeignKey(
        LLMConfig, on_delete=models.CASCADE,
        related_name='permissions'
    )
    user = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE,
        null=True, blank=True,
        help_text="指定用户（为空则对全体生效）"
    )
    role = models.CharField(
        max_length=16, null=True, blank=True,
        help_text="指定角色 (ADMIN/NORMAL)，与 user 互斥"
    )
    can_use = models.BooleanField(
        default=True, help_text="是否允许使用"
    )

    class Meta:
        db_table = 'model_permission'
        verbose_name = '模型使用权限'
        verbose_name_plural = verbose_name


OBJECT_TYPE_COMPANY = 'COMPANY'
OBJECT_TYPE_STOCK = 'STOCK'
OBJECT_TYPE_PRODUCT = 'PRODUCT'
OBJECT_TYPES = [
    (OBJECT_TYPE_COMPANY, '公司'),
    (OBJECT_TYPE_STOCK, '股票'),
    (OBJECT_TYPE_PRODUCT, '商品'),
]


class ModelObjectMapping(models.Model):
    """模型-调研对象类型映射

    FR-DYBG-0001: 针对每个调研对象类型，管理员可关联模型并标记默认推荐。
    """
    llm_config = models.ForeignKey(
        LLMConfig, on_delete=models.CASCADE,
        related_name='object_mappings'
    )
    object_type = models.CharField(
        max_length=16, choices=OBJECT_TYPES,
        help_text="调研对象类型"
    )
    is_default = models.BooleanField(
        default=False, help_text="是否为该类型的默认推荐模型"
    )

    class Meta:
        db_table = 'model_object_mapping'
        verbose_name = '模型对象映射'
        verbose_name_plural = verbose_name
        unique_together = ('llm_config', 'object_type')
