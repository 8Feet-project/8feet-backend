"""
管理端模型配置路由 (挂载于 /api/v1/admin/models/)
"""
from django.urls import path
from llm_manager.api.llm_api import (
    assign_config_permissions,
    config_collection,
    config_detail_collection,
    test_config_connection,
    toggle_config,
)

urlpatterns = [
    path('', config_collection, name='admin-model-list'),          # GET/POST /api/v1/admin/models/
    path('<int:model_id>', config_detail_collection, name='admin-model-detail'), # GET/PATCH/DELETE /api/v1/admin/models/1
    path('<int:model_id>/test-connection', test_config_connection, name='admin-model-test-connection'),
    path('<int:model_id>/permissions', assign_config_permissions, name='admin-model-permissions'),
    path('<int:model_id>/toggle', toggle_config, name='admin-model-toggle'), # POST /api/v1/admin/models/1/toggle
]
