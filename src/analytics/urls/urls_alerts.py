"""
动态提醒相关路由 (挂载于 /api/v1/alerts/)
"""
from django.urls import path
from analytics.api.analytics_api import (
    alert_create, alert_list
)

urlpatterns = [
    path('', alert_list, name='alert-list'), # GET
    path('', alert_create, name='alert-create'), # POST
]
