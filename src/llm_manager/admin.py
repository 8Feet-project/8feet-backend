from django.contrib import admin
from llm_manager.models.llm_config import LLMConfig
from llm_manager.models.model_permission import ModelPermission, ModelObjectMapping
from llm_manager.models.model_usage import ModelUsage


@admin.register(LLMConfig)
class LLMConfigAdmin(admin.ModelAdmin):
    list_display = ['name', 'provider', 'model_id', 'is_enabled', 'is_online', 'created_at']
    list_filter = ['provider', 'is_enabled', 'is_online']
    search_fields = ['name', 'model_id']


@admin.register(ModelPermission)
class ModelPermissionAdmin(admin.ModelAdmin):
    list_display = ['llm_config', 'user', 'role', 'is_active', 'daily_quota', 'priority_weight']
    list_filter = ['is_active', 'role', 'llm_config']
    search_fields = ['user__username', 'role', 'llm_config__name']


@admin.register(ModelObjectMapping)
class ModelObjectMappingAdmin(admin.ModelAdmin):
    list_display = ['llm_config', 'object_type', 'usage_type', 'priority', 'is_default']
    list_filter = ['object_type', 'usage_type', 'is_default']


@admin.register(ModelUsage)
class ModelUsageAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'user', 'llm_config', 'usage_type', 'total_tokens', 'cost', 'latency_ms']
    list_filter = ['usage_type', 'llm_config']
    search_fields = ['user__username', 'request_id']
    readonly_fields = ['created_at']
