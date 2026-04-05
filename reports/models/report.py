"""
调研报告模型
FR-DYBG-0002: 高质量报告交付 (Markdown/PDF/Word, 简版/详版)
"""
from django.db import models

from research.models.research_task import ResearchTask


class Report(models.Model):
    """调研报告核心表"""
    task = models.ForeignKey(
        ResearchTask, on_delete=models.CASCADE,
        related_name='reports',
        help_text="关联的调研任务"
    )
    title = models.CharField(
        max_length=256, help_text="报告标题"
    )
    summary = models.TextField(
        help_text="报告摘要"
    )
    content_markdown = models.TextField(
        help_text="报告正文 (Markdown 格式，详版)"
    )
    content_brief = models.TextField(
        null=True, blank=True,
        help_text="报告简版内容"
    )
    file_pdf_path = models.CharField(
        max_length=512, null=True, blank=True,
        help_text="PDF 文件路径 (Minio)"
    )
    file_word_path = models.CharField(
        max_length=512, null=True, blank=True,
        help_text="Word 文件路径 (Minio)"
    )
    version = models.PositiveIntegerField(
        default=1, help_text="报告版本号"
    )
    is_latest = models.BooleanField(
        default=True, help_text="是否为最新版本"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'report'
        verbose_name = '调研报告'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} (v{self.version})"
