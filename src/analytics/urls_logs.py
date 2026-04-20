from django.urls import path

from analytics.api.analytics_api import (
    llm_call_logs,
    operation_logs,
    system_logs,
)

urlpatterns = [
    path('operations', operation_logs, name='admin-logs-operations'),
    path('llm-calls', llm_call_logs, name='admin-logs-llm-calls'),
    path('system', system_logs, name='admin-logs-system'),
]
