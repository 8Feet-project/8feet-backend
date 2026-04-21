"""
reports URL 配置
挂载点: /api/reports/
"""
from django.urls import path
from reports.api.report_api import (
    report_detail, report_list, followup_question, export_report, export_status,
    manual_export_report
)

urlpatterns = [
    path('', report_list, name='report-list'), # GET /api/v1/reports
    path('exports/<int:export_id>/status', export_status, name='report-export-status'),
    path('<int:report_id>', report_detail, name='report-detail'), # GET/PATCH
    path('<int:report_id>/versions', report_detail, name='report-versions'),
    path('<int:report_id>/export', export_report, name='report-export'),
    path('<int:report_id>/manual-export', manual_export_report, name='report-manual-export'),
    path('<int:report_id>/share', report_detail, name='report-share'), # Mocked
    path('<int:report_id>/qa', followup_question, name='report-qa'), # POST/GET
]
