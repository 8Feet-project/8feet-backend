"""
模型分析结果模型
FR-JSDY-0002: 分析与产出
FR-JSDY-0006: 智能结论提炼（多模型交叉验证）
"""
from django.db import models

from llm_manager.models.llm_config import LLMConfig
from research.models.research_task import ResearchTask


ANALYSIS_TYPES = [
    ('SINGLE', '单模型分析'),
    ('CROSS', '多模型交叉验证'),
]


class AnalysisResult(models.Model):
    """大模型分析结果

    存储单次模型调用或多模型交叉验证的分析结论。
    """
    task = models.ForeignKey(
        ResearchTask, on_delete=models.CASCADE,
        related_name='analysis_results'
    )
    llm_config = models.ForeignKey(
        LLMConfig, on_delete=models.SET_NULL, null=True,
        related_name='analysis_results',
        help_text="使用的模型配置"
    )
    analysis_type = models.CharField(
        max_length=16, choices=ANALYSIS_TYPES, default='SINGLE'
    )
    conclusion = models.TextField(
        help_text="分析结论摘要"
    )
    raw_output = models.JSONField(
        default=dict,
        help_text="模型原始输出（完整 JSON）"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'analysis_result'
        verbose_name = '分析结果'
        verbose_name_plural = verbose_name
