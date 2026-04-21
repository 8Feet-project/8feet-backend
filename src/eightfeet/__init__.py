"""
eightfeet 项目配置中心
"""
from .celery import app as celery_app

__all__ = ('celery_app',)
