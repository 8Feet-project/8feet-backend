"""
reports URL 配置
挂载点: /api/v1/reports/
"""
from django.urls import path

from reports.api.report_api import (
    append_report_qa,
    followup_question,
    report_citation_detail,
    report_citations,
    report_detail,
    report_detail_v1,
    report_list,
    report_qa_collection,
    report_versions,
    reports_collection,
    task_reports,
)

urlpatterns = [
    # 兼容旧接口
    path('detail', report_detail, name='reports-detail'),
    path('list', report_list, name='reports-list'),
    path('by-task', task_reports, name='reports-by-task'),
    path('followup', followup_question, name='reports-followup'),

    # v1 REST 风格接口
    path('', reports_collection, name='reports-collection'),
    path('<int:report_id>', report_detail_v1, name='reports-detail-v1'),
    path('<int:report_id>/citations', report_citations, name='reports-citations'),
    path('<int:report_id>/citations/<int:citation_id>', report_citation_detail, name='reports-citation-detail'),
    path('<int:report_id>/versions', report_versions, name='reports-versions'),
    path('<int:report_id>/qa', report_qa_collection, name='reports-qa-collection'),
    path('<int:report_id>/qa/<int:qa_id>/append', append_report_qa, name='reports-qa-append'),
]
