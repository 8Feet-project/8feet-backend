from django.contrib import admin
from reports.models.report import Report
from reports.models.citation import Citation, ReportFollowup


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ['title', 'task', 'version', 'is_latest', 'created_at']
    list_filter = ['is_latest']
    search_fields = ['title']


@admin.register(Citation)
class CitationAdmin(admin.ModelAdmin):
    list_display = ['report', 'index_number', 'source_title']


@admin.register(ReportFollowup)
class ReportFollowupAdmin(admin.ModelAdmin):
    list_display = ['report', 'user', 'question', 'created_at']
