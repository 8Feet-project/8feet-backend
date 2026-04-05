"""
调研任务核心模型
FR-JSDY-0001: 用户发起调研任务，系统自动完成 DeepSearch 检索
"""
from django.db import models
from django.contrib.auth import get_user_model

from llm_manager.models.llm_config import LLMConfig


# 任务状态枚举
STATUS_PENDING = 'PENDING'
STATUS_SEARCHING = 'SEARCHING'
STATUS_ANALYZING = 'ANALYZING'
STATUS_COMPLETED = 'COMPLETED'
STATUS_FAILED = 'FAILED'
STATUS_CANCELLED = 'CANCELLED'

TASK_STATUS_CHOICES = [
    (STATUS_PENDING, '待处理'),
    (STATUS_SEARCHING, '检索中'),
    (STATUS_ANALYZING, '分析中'),
    (STATUS_COMPLETED, '已完成'),
    (STATUS_FAILED, '失败'),
    (STATUS_CANCELLED, '已取消'),
]

OBJECT_TYPE_COMPANY = 'COMPANY'
OBJECT_TYPE_STOCK = 'STOCK'
OBJECT_TYPE_PRODUCT = 'PRODUCT'

OBJECT_TYPES = [
    (OBJECT_TYPE_COMPANY, '公司'),
    (OBJECT_TYPE_STOCK, '股票'),
    (OBJECT_TYPE_PRODUCT, '商品'),
]


class ResearchTask(models.Model):
    """调研任务核心实体

    一次完整的调研流程：提交 → 检索 → 分析 → 产出报告
    """
    user = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE,
        related_name='research_tasks',
        help_text="发起调研的用户"
    )
    title = models.CharField(
        max_length=256, help_text="调研任务标题"
    )
    object_name = models.CharField(
        max_length=256, help_text="调研对象名称（公司名/股票代码/商品名）"
    )
    object_type = models.CharField(
        max_length=16, choices=OBJECT_TYPES,
        help_text="调研对象类型"
    )
    llm_config = models.ForeignKey(
        LLMConfig, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='research_tasks',
        help_text="用户选择的大模型配置"
    )
    search_params = models.JSONField(
        default=dict,
        help_text="检索参数: 时间范围/信息源权威度/研究深度"
    )
    status = models.CharField(
        max_length=16, choices=TASK_STATUS_CHOICES, default=STATUS_PENDING,
        help_text="任务当前状态"
    )
    progress = models.JSONField(
        default=dict,
        help_text="各阶段完成度: {searching: 80, analyzing: 0, ...}"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'research_task'
        verbose_name = '调研任务'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        permissions = [
            ('create_research', '发起调研任务'),
            ('view_research', '查看调研任务'),
            ('cancel_research', '取消调研任务'),
        ]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.title}"
