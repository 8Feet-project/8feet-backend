from django.urls import path

from analytics.api.analytics_api import alert_detail, alerts_collection

urlpatterns = [
    path('', alerts_collection, name='alerts-collection'),
    path('<int:alert_id>', alert_detail, name='alerts-detail'),
]
