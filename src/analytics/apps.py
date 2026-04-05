"""
analytics App — 日志/统计/个性化模块
映射需求: FR-SJGL-0003 ~ FR-SJGL-0004, FR-GRXX-0001 ~ FR-GRXX-0003
"""
from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    name = 'analytics'
    verbose_name = '日志统计与个性化'
