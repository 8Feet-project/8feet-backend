from django.contrib import admin
from research.models.research_task import ResearchTask
from research.models.scraped_content import ScrapedContent
from research.models.analysis_result import AnalysisResult
from research.models.task_step_log import TaskStepLog


@admin.register(ResearchTask)
class ResearchTaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'user', 'object_type', 'status', 'created_at']
    list_filter = ['status', 'object_type']
    search_fields = ['title', 'object_name']


@admin.register(ScrapedContent)
class ScrapedContentAdmin(admin.ModelAdmin):
    list_display = ['task', 'source_title', 'source_type', 'relevance_score']
    list_filter = ['source_type']


@admin.register(AnalysisResult)
class AnalysisResultAdmin(admin.ModelAdmin):
    list_display = ['task', 'llm_config', 'analysis_type', 'created_at']
    list_filter = ['analysis_type']


@admin.register(TaskStepLog)
class TaskStepLogAdmin(admin.ModelAdmin):
    list_display = ['task', 'step_name', 'step_status', 'created_at']
    list_filter = ['step_status']
