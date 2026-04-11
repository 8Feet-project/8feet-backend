"""
平台初始化路由 (挂载于 /api/v1/platform/)
"""
from django.urls import path
from eightfeet.health import healthz # 借用位置
from django.http import JsonResponse

# TODO: 尚未实现，仅简单Mock
def init_status(request):
    return JsonResponse({"initialized": True, "has_super_admin": True})

def initialize(request):
    return JsonResponse({"initialized": True, "super_admin_user_id": 1})

urlpatterns = [
    path('init-status', init_status, name='platform-init-status'),
    path('initialize', initialize, name='platform-initialize'),
]
