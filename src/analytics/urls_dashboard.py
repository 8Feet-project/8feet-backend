from django.urls import path

from analytics.api.analytics_api import (
    dashboard,
    dashboard_model_usage,
    dashboard_object_distribution,
    dashboard_overview,
    dashboard_user_activity,
)

urlpatterns = [
    path('', dashboard, name='analytics-dashboard'),
    path('overview', dashboard_overview, name='analytics-dashboard-overview'),
    path('object-distribution', dashboard_object_distribution, name='analytics-dashboard-object-distribution'),
    path('model-usage', dashboard_model_usage, name='analytics-dashboard-model-usage'),
    path('user-activity', dashboard_user_activity, name='analytics-dashboard-user-activity'),
]
