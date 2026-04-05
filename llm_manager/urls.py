"""
llm_manager URL 配置
挂载点: /api/llm/
"""
from django.urls import path
from llm_manager.api.llm_api import (
    create_config, toggle_config, list_configs, recommended_model
)

urlpatterns = [
    path('config', create_config, name='llm-create-config'),
    path('toggle', toggle_config, name='llm-toggle-config'),
    path('list', list_configs, name='llm-list-configs'),
    path('recommend', recommended_model, name='llm-recommend'),
]
