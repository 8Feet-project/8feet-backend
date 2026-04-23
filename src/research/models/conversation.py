"""
调研任务会话与消息历史模型
用于持久化 efeet thread，支持后续恢复与继续追问。
"""
from django.db import models

from research.models.research_task import ResearchTask


SESSION_STATUS_IDLE = 'IDLE'
SESSION_STATUS_RUNNING = 'RUNNING'
SESSION_STATUS_COMPLETED = 'COMPLETED'
SESSION_STATUS_FAILED = 'FAILED'
SESSION_STATUS_CANCELLED = 'CANCELLED'

SESSION_STATUS_CHOICES = [
    (SESSION_STATUS_IDLE, '空闲'),
    (SESSION_STATUS_RUNNING, '运行中'),
    (SESSION_STATUS_COMPLETED, '已完成'),
    (SESSION_STATUS_FAILED, '失败'),
    (SESSION_STATUS_CANCELLED, '已取消'),
]

MESSAGE_ROLE_HUMAN = 'HUMAN'
MESSAGE_ROLE_AI = 'AI'
MESSAGE_ROLE_TOOL = 'TOOL'

MESSAGE_ROLE_CHOICES = [
    (MESSAGE_ROLE_HUMAN, '用户'),
    (MESSAGE_ROLE_AI, 'AI'),
    (MESSAGE_ROLE_TOOL, '工具'),
]


class ResearchConversation(models.Model):
    """调研任务的 efeet 会话快照"""

    task = models.OneToOneField(
        ResearchTask, on_delete=models.CASCADE,
        related_name='conversation'
    )
    thread_id = models.CharField(
        max_length=64, unique=True,
        help_text="efeet thread_id"
    )
    system_message = models.TextField(
        blank=True, default='',
        help_text="用于重建 thread 的 system prompt"
    )
    status = models.CharField(
        max_length=16,
        choices=SESSION_STATUS_CHOICES,
        default=SESSION_STATUS_IDLE,
        help_text="会话运行状态"
    )
    history_messages = models.JSONField(
        default=list,
        help_text="efeet thread.history 的序列化结果"
    )
    state_snapshot = models.JSONField(
        default=dict,
        help_text="除 messages 外的 thread.state 快照"
    )
    latest_user_message = models.TextField(
        blank=True, default='',
        help_text="最近一次用户输入"
    )
    latest_assistant_message = models.TextField(
        blank=True, default='',
        help_text="最近一次 AI 输出"
    )
    last_error = models.TextField(
        blank=True, default='',
        help_text="最近一次运行错误"
    )
    run_count = models.PositiveIntegerField(
        default=0, help_text="累计运行次数"
    )
    last_started_at = models.DateTimeField(
        null=True, blank=True
    )
    last_finished_at = models.DateTimeField(
        null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'research_conversation'
        verbose_name = '调研会话'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.task.title} [{self.status}]"


class ResearchConversationMessage(models.Model):
    """调研会话的可读消息历史"""

    conversation = models.ForeignKey(
        ResearchConversation, on_delete=models.CASCADE,
        related_name='messages'
    )
    run_number = models.PositiveIntegerField(
        default=1, help_text="所属运行轮次"
    )
    message_index = models.PositiveIntegerField(
        help_text="消息在完整会话中的顺序号"
    )
    role = models.CharField(
        max_length=16, choices=MESSAGE_ROLE_CHOICES,
        help_text="消息角色"
    )
    message_type = models.CharField(
        max_length=32,
        help_text="LangChain 消息类型，如 human/ai/tool"
    )
    content = models.TextField(
        blank=True, default='',
        help_text="便于展示的消息文本"
    )
    payload = models.JSONField(
        default=dict,
        help_text="完整序列化后的消息载荷"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'research_conversation_message'
        verbose_name = '调研会话消息'
        verbose_name_plural = verbose_name
        ordering = ['message_index']
        unique_together = ('conversation', 'message_index')

    def __str__(self):
        return f"{self.conversation_id}:{self.message_index}:{self.role}"
