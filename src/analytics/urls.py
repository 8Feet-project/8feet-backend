"""
analytics URL 配置
挂载点: /api/analytics/
"""
from django.urls import path
from analytics.api.analytics_api import (
    dashboard, favorite_add, favorite_remove,
    favorite_list, alert_create, alert_list
)

urlpatterns = [
    path('dashboard', dashboard, name='analytics-dashboard'),
    path('favorite/add', favorite_add, name='analytics-favorite-add'),
    path('favorite/remove', favorite_remove, name='analytics-favorite-remove'),
    path('favorite/list', favorite_list, name='analytics-favorite-list'),
    path('alert/create', alert_create, name='analytics-alert-create'),
    path('alert/list', alert_list, name='analytics-alert-list'),
]
