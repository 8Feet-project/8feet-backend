"""
8Feet URL Configuration — 总路由分发

每个业务模块拥有独立的 urls.py，在此统一挂载到 /api/<module>/ 路径下。
参照 example-backend/trebuchet/urls.py 的 include 模式。
"""
from django.contrib import admin
from django.urls import path, include
from eightfeet.health import healthz, readyz
from llm_manager.api.llm_api import available_models

urlpatterns = [
    path('healthz', healthz),
    path('readyz', readyz),
    path('admin/', admin.site.urls),
    
    # 统一 v1 接口前缀
    path('api/v1/', include([
        # 认证与账户
        path('auth/', include('users.urls.urls_auth')),
        path('users/', include('users.urls.urls_user')),
        path('admin/users/', include('users.urls.urls_admin')),
        # 平台初始化
        path('platform/', include('eightfeet.urls_platform')),
        # 大模型管理 (管理端)
        path('admin/models/', include('llm_manager.urls.urls_admin')),
        path('models/available', available_models, name='models-available'),
        path('model-routing/', include('llm_manager.urls.urls_routing')),
        # 调研任务
        path('research/', include('research.urls')),
        # 报告
        path('reports/', include('reports.urls')),
        # 收藏与提醒 (根据文档规划)
        path('favorites/', include('analytics.urls_favorites')),
        path('alerts/', include('analytics.urls_alerts')),
        path('messages/', include('analytics.urls_messages')),
        # 统计看板与日志 (管理端)
        path('admin/dashboard/', include('analytics.urls_dashboard')),
        path('admin/logs/', include('analytics.urls_logs')),
    ])),
]
