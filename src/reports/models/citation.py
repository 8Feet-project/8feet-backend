"""
引用溯源与深度追问模型
FR-DYBG-0003: 动态引用 (角标 + 来源溯源)
FR-JSDY-0005: 报告深度追问
"""
from django.db import models
from django.contrib.auth import get_user_model

from reports.models.report import Report


class Citation(models.Model):
    """动态引用角标

    FR-DYBG-0003: 报告正文中的引用角标 → 末尾来源汇总
    """
    report = models.ForeignKey(
        Report, on_delete=models.CASCADE,
        related_name='citations'
    )
    index_number = models.PositiveIntegerField(
        help_text="引用角标编号 (如 [1], [2])"
    )
    source_url = models.URLField(
        max_length=1024, blank=True, default='',
        help_text="原始来源链接（结构化数据来源可能没有 URL）"
    )
    source_title = models.CharField(
        max_length=512, help_text="来源标题"
    )
    cited_text_snippet = models.TextField(
        null=True, blank=True,
        help_text="报告中引用的文本片段"
    )
    reproduction_code = models.TextField(
        null=True, blank=True,
        help_text="复现代码（适用于 akshare 等结构化数据来源）"
    )

    class Meta:
        db_table = 'citation'
        verbose_name = '引用来源'
        verbose_name_plural = verbose_name
        ordering = ['index_number']
        unique_together = ('report', 'index_number')


class ReportFollowup(models.Model):
    """报告深度追问

    FR-JSDY-0005: 用户针对报告特定段落提问，系统基于上下文解答
    """
    report = models.ForeignKey(
        Report, on_delete=models.CASCADE,
        related_name='followups'
    )
    user = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE,
        related_name='report_followups'
    )
    question = models.TextField(
        help_text="用户提出的追问"
    )
    answer = models.TextField(
        null=True, blank=True,
        help_text="系统生成的回答"
    )
    context_paragraph = models.TextField(
        null=True, blank=True,
        help_text="追问所针对的报告段落"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'report_followup'
        verbose_name = '报告追问'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
