"""
reports URL 配置
挂载点: /api/reports/
"""
from django.urls import path
from reports.api.report_api import (
    report_detail, report_list, task_reports, followup_question
)

urlpatterns = [
    path('detail', report_detail, name='reports-detail'),
    path('list', report_list, name='reports-list'),
    path('by-task', task_reports, name='reports-by-task'),
    path('followup', followup_question, name='reports-followup'),
]
