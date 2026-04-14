"""
管理端统计看板路由 (挂载于 /api/v1/admin/dashboard/)
"""
from django.urls import path
from analytics.api.analytics_api import dashboard, cost_report

urlpatterns = [
    path('overview', dashboard, name='admin-dashboard-overview'),
    path('cost-report', cost_report, name='admin-dashboard-cost'),
]
