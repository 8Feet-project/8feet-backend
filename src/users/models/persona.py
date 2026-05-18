"""
用户人设模型。
"""
from django.conf import settings
from django.db import models


PERSONA_STATUS_DRAFT = "DRAFT"
PERSONA_STATUS_COMPLETED = "COMPLETED"
PERSONA_STATUS_FAILED = "FAILED"

PERSONA_STATUS_CHOICES = [
    (PERSONA_STATUS_DRAFT, "草稿"),
    (PERSONA_STATUS_COMPLETED, "已完成"),
    (PERSONA_STATUS_FAILED, "失败"),
]


class UserPersona(models.Model):
    """当前用户人设；每个用户至多一条。"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="persona",
        help_text="关联用户",
    )
    content_markdown = models.TextField(
        blank=True,
        default="",
        help_text="AI 生成的人设分析报告 Markdown",
    )
    summary = models.TextField(
        blank=True,
        default="",
        help_text="人设摘要，便于列表或上下文显示",
    )
    source_thread_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="最近一次生成人设的 efeet thread_id",
    )
    model_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="最近一次生成人设使用的平台模型 ID",
    )
    skipped_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="用户跳过新手人设引导的时间",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_persona"
        verbose_name = "用户人设"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"Persona<{self.user_id}>"


class UserPersonaConversation(models.Model):
    """一次人设设定对话；每次重新设定都会创建新的 thread。"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="persona_conversations",
        help_text="关联用户",
    )
    thread_id = models.CharField(
        max_length=64,
        unique=True,
        help_text="efeet thread_id",
    )
    model_id = models.CharField(
        max_length=64,
        help_text="平台模型 ID",
    )
    system_message = models.TextField(
        blank=True,
        default="",
        help_text="用于恢复 thread 的 system prompt",
    )
    status = models.CharField(
        max_length=16,
        choices=PERSONA_STATUS_CHOICES,
        default=PERSONA_STATUS_DRAFT,
        help_text="会话状态",
    )
    history_messages = models.JSONField(
        default=list,
        help_text="efeet thread.history 序列化结果",
    )
    state_snapshot = models.JSONField(
        default=dict,
        help_text="efeet thread.state 快照",
    )
    latest_user_message = models.TextField(blank=True, default="")
    latest_assistant_message = models.TextField(blank=True, default="")
    last_error = models.TextField(blank=True, default="")
    presented_report_path = models.CharField(max_length=512, blank=True, default="")
    presented_report_markdown = models.TextField(blank=True, default="")
    run_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_persona_conversation"
        verbose_name = "用户人设设定对话"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return f"PersonaConversation<{self.user_id}:{self.thread_id}>"
