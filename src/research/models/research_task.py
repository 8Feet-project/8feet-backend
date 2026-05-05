"""
调研任务核心模型
FR-JSDY-0001: 用户发起调研任务，系统自动完成 DeepSearch 检索
"""
from django.contrib.auth import get_user_model
from django.db import models

from llm_manager.models.llm_config import LLMConfig


# 任务状态枚举
STATUS_PENDING = 'PENDING'
STATUS_SEARCHING = 'SEARCHING'
STATUS_ANALYZING = 'ANALYZING'
STATUS_WAITING_USER = 'WAITING_USER'
STATUS_COMPLETED = 'COMPLETED'
STATUS_FAILED = 'FAILED'
STATUS_CANCELLED = 'CANCELLED'

TASK_STATUS_CHOICES = [
    (STATUS_PENDING, '待处理'),
    (STATUS_SEARCHING, '检索中'),
    (STATUS_ANALYZING, '分析中'),
    (STATUS_WAITING_USER, '等待介入'),
    (STATUS_COMPLETED, '已完成'),
    (STATUS_FAILED, '失败'),
    (STATUS_CANCELLED, '已取消'),
]

DISPATCH_PENDING = 'PENDING'
DISPATCH_QUEUED = 'QUEUED'
DISPATCH_STARTING = 'STARTING'
DISPATCH_RUNNING = 'RUNNING'
DISPATCH_FINISHED = 'FINISHED'
DISPATCH_FAILED = 'FAILED'
DISPATCH_CANCELLED = 'CANCELLED'

TASK_DISPATCH_STATUS_CHOICES = [
    (DISPATCH_PENDING, '待调度'),
    (DISPATCH_QUEUED, '已入队'),
    (DISPATCH_STARTING, '启动中'),
    (DISPATCH_RUNNING, '运行中'),
    (DISPATCH_FINISHED, '已结束'),
    (DISPATCH_FAILED, '调度失败'),
    (DISPATCH_CANCELLED, '已取消'),
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
    dispatch_status = models.CharField(
        max_length=16,
        choices=TASK_DISPATCH_STATUS_CHOICES,
        default=DISPATCH_PENDING,
        help_text="后台调度状态"
    )
    runner_token = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="当前执行实例令牌，用于避免重复执行"
    )
    progress = models.JSONField(
        default=dict,
        help_text="各阶段完成度: {searching: 80, analyzing: 0, ...}"
    )
    queued_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="任务进入异步调度队列的时间"
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="后台任务开始执行时间"
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="后台任务结束时间"
    )
    last_error = models.TextField(
        null=True,
        blank=True,
        help_text="最近一次执行失败原因"
    )
    retry_count = models.PositiveIntegerField(
        default=0,
        help_text="后台执行重试次数"
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
