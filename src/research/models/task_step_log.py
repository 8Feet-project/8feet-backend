"""
任务步骤日志模型
FR-JSDY-0003: 全流程可视化监控
FR-JSDY-0004: 中间过程介入
"""
from django.db import models

from research.models.research_task import ResearchTask


STEP_STATUS_CHOICES = [
    ('RUNNING', '执行中'),
    ('COMPLETED', '已完成'),
    ('FAILED', '失败'),
    ('PAUSED', '已暂停'),
    ('SKIPPED', '已跳过'),
]


class TaskStepLog(models.Model):
    """调研任务步骤日志

    实时记录 Agent 工作链条的每一步，用于 WebSocket 推送至前端可视化。
    如: "正在拆解问题"、"正在阅读第 3 篇文献"、"正在生成报告"
    """
    task = models.ForeignKey(
        ResearchTask, on_delete=models.CASCADE,
        related_name='step_logs'
    )
    step_name = models.CharField(
        max_length=256,
        help_text="步骤名称，如 '拆解问题'、'检索信息'、'分析数据'"
    )
    step_status = models.CharField(
        max_length=16, choices=STEP_STATUS_CHOICES, default='RUNNING'
    )
    detail = models.JSONField(
        default=dict,
        help_text="步骤详情: 当前处理的文档名、迭代次数等"
    )
    is_interactive = models.BooleanField(
        default=False,
        help_text="此步骤是否需要用户介入反馈"
    )
    user_response = models.JSONField(
        null=True, blank=True,
        help_text="用户介入后的反馈数据"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'task_step_log'
        verbose_name = '任务步骤日志'
        verbose_name_plural = verbose_name
        ordering = ['created_at']
