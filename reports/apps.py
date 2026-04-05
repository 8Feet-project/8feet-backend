"""
reports App — 报告生成与管理模块
映射需求: FR-DYBG-0002 ~ FR-DYBG-0004, FR-JSDY-0005
"""
from django.apps import AppConfig


class ReportsConfig(AppConfig):
    name = 'reports'
    verbose_name = '调研报告管理'
