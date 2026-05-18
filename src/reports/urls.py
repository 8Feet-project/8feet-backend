"""
reports URL 配置
挂载点: /api/reports/
"""
from django.urls import path
from reports.api.report_api import (
    append_followup,
    report_detail, report_list, followup_question, export_download, export_report, export_status,
    manual_export_report,
    public_shared_report,
    report_citation_detail,
    report_citations,
    report_share,
)

urlpatterns = [
    path('', report_list, name='report-list'), # GET /api/v1/reports
    path('exports/<str:export_id>/status', export_status, name='report-export-status'),
    path('exports/<str:export_id>/download', export_download, name='report-export-download'),
    path('<int:report_id>', report_detail, name='report-detail'), # GET/PATCH
    path('<int:report_id>/citations', report_citations, name='report-citations'),
    path('<int:report_id>/citations/<int:citation_id>', report_citation_detail, name='report-citation-detail'),
    path('<int:report_id>/versions', report_detail, name='report-versions'),
    path('<int:report_id>/export', export_report, name='report-export'),
    path('<int:report_id>/manual-export', manual_export_report, name='report-manual-export'),
    path('<int:report_id>/share', report_share, name='report-share'),
    path('<int:report_id>/share/<str:share_id>', report_share, name='report-share-delete'),
    path('<int:report_id>/qa', followup_question, name='report-qa'), # POST/GET
    path('<int:report_id>/qa/<int:qa_id>/append', append_followup, name='report-qa-append'),
    path('share/<str:share_id>', public_shared_report, name='public-shared-report'),
]
