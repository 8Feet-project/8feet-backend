"""
管理端系统日志路由 (挂载于 /api/v1/admin/logs/)
"""
from django.urls import path

from shared.utils import response_wrapper, success_api_response

@response_wrapper
def log_list(request):
    return success_api_response({"list": [], "total": 0})

urlpatterns = [
    path('', log_list, name='admin-log-list'),
]
