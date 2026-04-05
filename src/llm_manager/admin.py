from django.contrib import admin
from llm_manager.models.llm_config import LLMConfig
from llm_manager.models.model_permission import ModelPermission, ModelObjectMapping


@admin.register(LLMConfig)
class LLMConfigAdmin(admin.ModelAdmin):
    list_display = ['name', 'provider', 'model_id', 'is_enabled', 'created_at']
    list_filter = ['provider', 'is_enabled']
    search_fields = ['name', 'model_id']


@admin.register(ModelPermission)
class ModelPermissionAdmin(admin.ModelAdmin):
    list_display = ['llm_config', 'user', 'role', 'can_use']


@admin.register(ModelObjectMapping)
class ModelObjectMappingAdmin(admin.ModelAdmin):
    list_display = ['llm_config', 'object_type', 'is_default']
    list_filter = ['object_type', 'is_default']
