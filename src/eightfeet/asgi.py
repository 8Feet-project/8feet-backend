"""
ASGI config for 8Feet project.
支持 Django Channels (WebSocket) 用于全流程监控 (FR-JSDY-0003)。
"""
import os
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.conf import settings
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eightfeet.settings')

# 先初始化 Django，再导入路由
django_asgi_app = get_asgi_application()
http_application = (
    ASGIStaticFilesHandler(django_asgi_app)
    if settings.DEBUG or os.getenv('DJANGO_SERVE_STATIC', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    else django_asgi_app
)

import research.routing

application = ProtocolTypeRouter({
    "http": http_application,
    "websocket": AuthMiddlewareStack(URLRouter(research.routing.websocket_urlpatterns)),
})
