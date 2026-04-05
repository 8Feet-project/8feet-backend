"""
DeepSearch 抓取内容模型
FR-JSDY-0001: DeepSearch 自动完成全网权威信息的抓取、语义整合与去重
"""
from django.db import models

from research.models.research_task import ResearchTask


SOURCE_TYPES = [
    ('WEB', '网页'),
    ('PAPER', '学术论文'),
    ('FINANCIAL', '财务报表'),
    ('NEWS', '新闻资讯'),
    ('GOV', '政府公告'),
    ('OTHER', '其他'),
]


class ScrapedContent(models.Model):
    """DeepSearch 抓取的原始内容

    每条代表一个被抓取的信息源。
    """
    task = models.ForeignKey(
        ResearchTask, on_delete=models.CASCADE,
        related_name='scraped_contents',
        help_text="归属的调研任务"
    )
    source_url = models.URLField(
        max_length=1024, help_text="信息源 URL"
    )
    source_title = models.CharField(
        max_length=512, help_text="信息源标题"
    )
    source_type = models.CharField(
        max_length=16, choices=SOURCE_TYPES, default='WEB',
        help_text="信息源分类"
    )
    content_text = models.TextField(
        help_text="抓取到的文本内容"
    )
    relevance_score = models.FloatField(
        default=0.0, help_text="语义相关度评分 (0.0 ~ 1.0)"
    )
    scraped_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'scraped_content'
        verbose_name = '抓取内容'
        verbose_name_plural = verbose_name
        ordering = ['-relevance_score']
