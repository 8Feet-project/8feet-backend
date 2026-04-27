"""
动态提醒相关路由 (挂载于 /api/v1/alerts/)
"""
from django.urls import path
from analytics.api.analytics_api import (
    alert_collection, alert_detail
)

urlpatterns = [
    path('', alert_collection, name='alert-collection'),
    path('<int:alert_id>', alert_detail, name='alert-detail'),
]
