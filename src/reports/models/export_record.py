"""
报告导出记录
"""
from django.db import models

from reports.models.report import Report


EXPORT_STATUS_CHOICES = [
    ('QUEUED', '排队中'),
    ('PROCESSING', '处理中'),
    ('COMPLETED', '已完成'),
    ('FAILED', '失败'),
]


class ReportExportRecord(models.Model):
    """报告导出任务记录"""
    report = models.ForeignKey(
        Report, on_delete=models.CASCADE,
        related_name='export_records',
        help_text="关联报告"
    )
    export_format = models.CharField(
        max_length=16,
        help_text="导出格式: md/pdf/docx/word/html"
    )
    report_mode = models.CharField(
        max_length=16,
        default='full',
        help_text="导出内容模式: brief/full"
    )
    status = models.CharField(
        max_length=16,
        choices=EXPORT_STATUS_CHOICES,
        default='QUEUED',
        help_text="导出状态"
    )
    storage_path = models.CharField(
        max_length=1024, null=True, blank=True,
        help_text="对象存储路径或本地路径"
    )
    download_url = models.CharField(
        max_length=1024, null=True, blank=True,
        help_text="下载地址"
    )
    error_message = models.TextField(
        null=True, blank=True,
        help_text="失败原因"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'report_export_record'
        verbose_name = '报告导出记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"Export<{self.report_id}:{self.export_format}:{self.report_mode}:{self.status}>"
