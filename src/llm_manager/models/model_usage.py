"""
模型使用记录模型
记录每次调用的 Token 消耗、成本和性能，支持审计与财务统计。
"""
from django.db import models
from django.contrib.auth import get_user_model
from llm_manager.models.llm_config import LLMConfig


class ModelUsage(models.Model):
    """模型调用日志

    记录真实的 Token 消耗及产生的费用。
    """
    user = models.ForeignKey(
        get_user_model(), on_delete=models.SET_NULL, null=True,
        related_name='llm_usages'
    )
    llm_config = models.ForeignKey(
        LLMConfig, on_delete=models.SET_NULL, null=True,
        related_name='usage_logs'
    )
    model_name_snapshot = models.CharField(
        max_length=128, blank=True, default='',
        help_text="调用发生时的模型名称快照"
    )
    provider_snapshot = models.CharField(
        max_length=64, blank=True, default='',
        help_text="调用发生时的供应商快照"
    )
    
    # 调用元数据
    request_id = models.CharField(
        max_length=64, unique=True, db_index=True,
        help_text="关联业务请求 ID (X-Request-Id)"
    )
    usage_type = models.CharField(
        max_length=32, null=True, blank=True,
        help_text="用途: REASONING, SUMMARIZE 等"
    )
    
    # 消耗统计
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    
    # 费用计算 (快照当时的定价)
    cost = models.DecimalField(
        max_digits=12, decimal_places=6, default=0.0,
        help_text="本次调用产生的估算费用 (元)"
    )
    
    # 性能监控
    latency_ms = models.PositiveIntegerField(
        default=0, help_text="响应耗时 (毫秒)"
    )
    status_code = models.IntegerField(
        default=200, help_text="HTTP 状态码"
    )
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'model_usage'
        verbose_name = '模型使用记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user} use {self.llm_config} at {self.created_at}"
