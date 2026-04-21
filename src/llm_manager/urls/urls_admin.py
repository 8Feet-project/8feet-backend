"""
管理端模型配置路由 (挂载于 /api/v1/admin/models/)
"""
from django.urls import path
from llm_manager.api.llm_api import (
    list_configs, create_config, toggle_config,
    get_config_detail, get_usage_dashboard
)

urlpatterns = [
    path('', list_configs, name='admin-model-list'),               # GET /api/v1/admin/models/
    path('create', create_config, name='admin-model-create'),      # POST /api/v1/admin/models/create
    path('dashboard', get_usage_dashboard, name='admin-model-dashboard'), # GET /api/v1/admin/models/dashboard
    path('<int:model_id>', get_config_detail, name='admin-model-detail'), # GET /api/v1/admin/models/1
    path('<int:model_id>/toggle', toggle_config, name='admin-model-toggle'), # POST /api/v1/admin/models/1/toggle
]
