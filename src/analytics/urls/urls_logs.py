"""
管理端系统日志路由 (挂载于 /api/v1/admin/logs/)
"""
from django.urls import path
from django.http import JsonResponse

def log_list(request):
    return JsonResponse({"list": [], "total": 0})

urlpatterns = [
    path('', log_list, name='admin-log-list'),
]
