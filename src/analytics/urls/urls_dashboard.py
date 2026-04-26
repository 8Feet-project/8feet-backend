"""
管理端统计看板路由 (挂载于 /api/v1/admin/dashboard/)
"""
from django.urls import path
from analytics.api.analytics_api import (
    dashboard,
    cost_report,
    model_usage,
    object_distribution,
    user_activity,
)

urlpatterns = [
    path('overview', dashboard, name='admin-dashboard-overview'),
    path('object-distribution', object_distribution, name='admin-dashboard-object-distribution'),
    path('model-usage', model_usage, name='admin-dashboard-model-usage'),
    path('user-activity', user_activity, name='admin-dashboard-user-activity'),
    path('cost-report', cost_report, name='admin-dashboard-cost'),
]
