"""
大模型配置模型
FR-SJGL-0001: 管理员可添加/删除/启用/禁用各类大模型接口，
配置 API 密钥、调用参数等。
"""
from django.db import models


class LLMConfig(models.Model):
    """大模型配置实体

    存储每个大模型接口的完整配置信息。
    """
    name = models.CharField(
        max_length=128, help_text="模型显示名称，如 GPT-4、Claude-3"
    )
    provider = models.CharField(
        max_length=64, help_text="模型供应商，如 OpenAI、Anthropic、百度"
    )
    model_id = models.CharField(
        max_length=128, help_text="模型版本标识，如 gpt-4-0613"
    )
    api_endpoint = models.CharField(
        max_length=512, null=True, blank=True,
        help_text="API 接口地址"
    )
    api_key_encrypted = models.CharField(
        max_length=512, null=True, blank=True,
        help_text="加密存储的 API Key"
    )
    # 能力参数
    context_window = models.PositiveIntegerField(
        default=4096, help_text="上下文窗口大小 (Tokens)"
    )
    max_output_tokens = models.PositiveIntegerField(
        default=2048, help_text="单次最大输出 Tokens"
    )
    # 计费参数 (每 1M Tokens 的价格，单位：元)
    input_price_1m = models.DecimalField(
        max_digits=10, decimal_places=4, default=0.0,
        help_text="输入价格 (每百万 Tokens)"
    )
    output_price_1m = models.DecimalField(
        max_digits=10, decimal_places=4, default=0.0,
        help_text="输出价格 (每百万 Tokens)"
    )
    params = models.JSONField(
        default=dict,
        help_text="其他调用参数: temperature, stop_sequences 等"
    )
    is_enabled = models.BooleanField(
        default=True, help_text="是否启用"
    )
    is_online = models.BooleanField(
        default=True, help_text="当前运行状态 (正常/离线)"
    )
    description = models.TextField(
        null=True, blank=True,
        help_text="推荐场景描述"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'llm_config'
        verbose_name = '大模型配置'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.name} ({self.provider})"
