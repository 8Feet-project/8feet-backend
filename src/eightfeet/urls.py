"""
8Feet URL Configuration — 总路由分发

每个业务模块拥有独立的 urls.py，在此统一挂载到 /api/<module>/ 路径下。
参照 example-backend/trebuchet/urls.py 的 include 模式。
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    # 用户与权限管理
    path('api/users/', include('users.urls')),
    # 大模型配置管理
    path('api/llm/', include('llm_manager.urls')),
    # 调研任务与 DeepSearch
    path('api/research/', include('research.urls')),
    # 报告生成与管理
    path('api/reports/', include('reports.urls')),
    # 日志/统计/个性化
    path('api/analytics/', include('analytics.urls')),
]
