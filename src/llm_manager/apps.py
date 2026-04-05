"""
llm_manager App — 大模型配置管理模块
映射需求: FR-SJGL-0001 (大模型配置管理), FR-DYBG-0001 (智能模型路由)
"""
from django.apps import AppConfig


class LlmManagerConfig(AppConfig):
    name = 'llm_manager'
    verbose_name = '大模型配置管理'
