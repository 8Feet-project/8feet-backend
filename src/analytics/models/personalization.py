"""
个性化设置模型
FR-GRXX-0002: 收藏状态管理
FR-GRXX-0003: 动态更新提醒
"""
from django.db import models
from django.contrib.auth import get_user_model

from research.models.research_task import ResearchTask


class Favorite(models.Model):
    """收藏记录

    FR-GRXX-0002: 用户可收藏调研信息、报告和常用大模型。
    FR-GRXX-0003: 收藏夹分类管理 —— 按 调研信息/调研报告/大模型 三类集中查看与管理。
    """
    ITEM_TYPE_CHOICES = [
        ('INFO', '调研信息'),
        ('REPORT', '调研报告'),
        ('MODEL', '大模型'),
    ]

    user = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE,
        related_name='favorites'
    )
    item_type = models.CharField(
        max_length=16, choices=ITEM_TYPE_CHOICES
    )
    item_id = models.CharField(
        max_length=128,
        help_text="收藏对象业务 ID (如 report-001/model-001)"
    )
    remark = models.CharField(
        max_length=255, blank=True, default='',
        help_text="收藏备注"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'favorite'
        verbose_name = '收藏记录'
        verbose_name_plural = verbose_name
        unique_together = ('user', 'item_type', 'item_id')


class Alert(models.Model):
    """动态更新提醒

    FR-GRXX-0003: 用户为关注的公司/股票/商品设置信息更新提醒。
    系统轮询检查新信息 → 平台推送 + 邮件同步。
    """
    OBJECT_TYPE_CHOICES = [
        ('COMPANY', '公司'),
        ('STOCK', '股票'),
        ('PRODUCT', '商品'),
    ]

    user = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE,
        related_name='alerts'
    )
    object_type = models.CharField(
        max_length=16, choices=OBJECT_TYPE_CHOICES
    )
    object_name = models.CharField(
        max_length=256, help_text="关注对象名称"
    )
    condition = models.JSONField(
        default=dict,
        help_text="提醒条件: 关键词/价格阈值/更新频率等"
    )
    notify_email = models.BooleanField(
        default=True, help_text="是否邮件同步推送"
    )
    notify_in_app = models.BooleanField(
        default=True, help_text="是否站内消息推送"
    )
    is_active = models.BooleanField(default=True)
    last_triggered_at = models.DateTimeField(
        null=True, blank=True,
        help_text="上次触发时间"
    )
    next_run_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="下次计划触发时间"
    )
    last_task = models.ForeignKey(
        ResearchTask, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='triggered_alerts',
        help_text="最近一次由该提醒触发的调研任务"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'alert_setting'
        verbose_name = '推送提醒设置'
        verbose_name_plural = verbose_name
        permissions = [
            ('create_alert', '创建动态提醒'),
        ]


class UserMessage(models.Model):
    """站内消息中心记录
    
    FR-GRXX-0004: 动态更新提醒的消息存储载体。
    """
    MESSAGE_TYPE_CHOICES = [
        ('ALERT', '提醒消息'),
        ('SYSTEM', '系统通知'),
        ('TASK', '任务状态'),
    ]

    user = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE,
        related_name='messages'
    )
    title = models.CharField(max_length=256)
    content = models.TextField()
    action_url = models.CharField(
        max_length=512, blank=True, default='',
        help_text="消息点击跳转地址"
    )
    message_type = models.CharField(
        max_length=16, choices=MESSAGE_TYPE_CHOICES, default='ALERT'
    )
    source_alert = models.ForeignKey(
        Alert, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='triggered_messages'
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_message'
        verbose_name = '站内消息'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"[{'已读' if self.is_read else '未读'}] {self.title}"
