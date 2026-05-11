"""
日志模型
FR-SJGL-0004: 系统日志管理 (操作日志/系统日志/模型调用日志)
FR-SJGL-0003: 多维度数据统计
"""
from django.db import models
from django.contrib.auth import get_user_model

from llm_manager.models.llm_config import LLMConfig
from research.models.research_task import ResearchTask


class OperationLog(models.Model):
    """用户操作日志

    记录用户在平台上的所有关键操作。
    """
    user = models.ForeignKey(
        get_user_model(), on_delete=models.SET_NULL,
        null=True, related_name='operation_logs'
    )
    action_type = models.CharField(
        max_length=64, help_text="操作类型: LOGIN/CREATE_TASK/VIEW_REPORT/..."
    )
    target_module = models.CharField(
        max_length=64, help_text="目标模块: users/research/reports/..."
    )
    target_id = models.IntegerField(
        null=True, blank=True,
        help_text="操作对象 ID"
    )
    detail = models.JSONField(
        default=dict, help_text="操作详情"
    )
    ip_address = models.GenericIPAddressField(
        null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'operation_log'
        verbose_name = '操作日志'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        permissions = [
            ('view_dashboard', '查看统计看板'),
        ]


class SystemLog(models.Model):
    """系统运行日志"""
    LEVEL_CHOICES = [
        ('INFO', '信息'),
        ('WARN', '警告'),
        ('ERROR', '错误'),
    ]

    level = models.CharField(
        max_length=8, choices=LEVEL_CHOICES, default='INFO'
    )
    module = models.CharField(
        max_length=64, help_text="来源模块"
    )
    message = models.TextField(help_text="日志消息")
    stack_trace = models.TextField(
        null=True, blank=True, help_text="异常堆栈"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'system_log'
        verbose_name = '系统日志'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        permissions = [
            ('view_audit_log', '查看系统日志'),
            ('export_audit_log', '导出系统日志'),
        ]


class LLMCallLog(models.Model):
    """大模型调用日志

    FR-SJGL-0003: 统计各大模型的调用量趋势排行
    """
    user = models.ForeignKey(
        get_user_model(), on_delete=models.SET_NULL,
        null=True, related_name='llm_call_logs'
    )
    llm_config = models.ForeignKey(
        LLMConfig, on_delete=models.SET_NULL,
        null=True, related_name='call_logs'
    )
    task = models.ForeignKey(
        ResearchTask, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='llm_call_logs'
    )
    request_tokens = models.IntegerField(
        default=0, help_text="请求 Token 数"
    )
    response_tokens = models.IntegerField(
        default=0, help_text="响应 Token 数"
    )
    latency_ms = models.FloatField(
        default=0.0, help_text="调用延迟 (毫秒)"
    )
    status = models.CharField(
        max_length=16, default='SUCCESS',
        help_text="调用状态: SUCCESS/FAILED/TIMEOUT"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'llm_call_log'
        verbose_name = '模型调用日志'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
