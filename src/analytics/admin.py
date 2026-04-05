from django.contrib import admin
from analytics.models.logs import OperationLog, SystemLog, LLMCallLog
from analytics.models.personalization import Favorite, Alert


@admin.register(OperationLog)
class OperationLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'action_type', 'target_module', 'created_at']
    list_filter = ['action_type', 'target_module']
    search_fields = ['user__username']


@admin.register(SystemLog)
class SystemLogAdmin(admin.ModelAdmin):
    list_display = ['level', 'module', 'message', 'created_at']
    list_filter = ['level', 'module']


@admin.register(LLMCallLog)
class LLMCallLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'llm_config', 'status', 'latency_ms', 'created_at']
    list_filter = ['status']


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ['user', 'item_type', 'item_id', 'folder', 'created_at']
    list_filter = ['item_type']


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ['user', 'object_type', 'object_name', 'is_active', 'created_at']
    list_filter = ['object_type', 'is_active']
