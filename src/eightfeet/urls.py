"""
8Feet URL Configuration — 总路由分发

每个业务模块拥有独立的 urls.py，在此统一挂载到 /api/<module>/ 路径下。
参照 example-backend/trebuchet/urls.py 的 include 模式。
"""
from django.contrib import admin
from django.urls import path, include
from eightfeet.health import healthz, readyz
from llm_manager.api.llm_api import available_models, config_collection
from reports.api.report_api import public_shared_report
from users.api.user_api import current_permissions

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
        path('admin/permissions/current', current_permissions, name='admin-current-permissions'),
        # 平台初始化
        path('platform/', include('eightfeet.urls_platform')),
        # 大模型管理 (管理端)
        path('admin/models', config_collection, name='admin-model-list-no-slash'),
        path('admin/models/', include('llm_manager.urls.urls_admin')),
        path('models/available', available_models, name='models-available'),
        path('model-routing/', include('llm_manager.urls.urls_routing')),
        # 调研任务
        path('research/', include('research.urls')),
        # 报告
        path('reports/', include('reports.urls')),
        path('public/reports/share/<str:share_id>', public_shared_report, name='public-shared-report'),
        # 收藏与提醒 (根据文档规划)
        path('favorites/', include('analytics.urls.urls_favorites')),
        path('alerts/', include('analytics.urls.urls_alerts')),
        path('messages/', include('analytics.urls.urls_messages')),
        # 统计看板与日志 (管理端)
        path('admin/dashboard/', include('analytics.urls.urls_dashboard')),
        path('admin/logs/', include('analytics.urls.urls_logs')),
    ])),
]
