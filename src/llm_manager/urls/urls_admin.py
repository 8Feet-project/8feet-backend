"""
llm_manager 管理端模型路由
"""
from django.urls import path

from llm_manager.api.llm_api import (
    assign_model_permissions,
    create_config,
    list_configs,
    model_detail,
    test_config_connection,
    toggle_config,
)

urlpatterns = [
    path('', list_configs),
    path('list', list_configs),
    path('configs', create_config),
    path('toggle', toggle_config),
    path('<int:model_id>', model_detail),
    path('<int:model_id>/', model_detail),
    path('<int:model_id>/test-connection', test_config_connection),
    path('<int:model_id>/test-connection/', test_config_connection),
    path('<int:model_id>/permissions', assign_model_permissions),
    path('<int:model_id>/permissions/', assign_model_permissions),
]
