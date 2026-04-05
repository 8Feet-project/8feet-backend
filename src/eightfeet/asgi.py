"""
ASGI config for 8Feet project.
支持 Django Channels (WebSocket) 用于全流程监控 (FR-JSDY-0003)。
"""
import os
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eightfeet.settings')

# 先初始化 Django，再导入路由
django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    # WebSocket 路由将在 research 模块中定义
    # "websocket": AuthMiddlewareStack(URLRouter(research.routing.websocket_urlpatterns)),
})
