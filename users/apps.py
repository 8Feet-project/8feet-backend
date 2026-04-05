"""
users App — 用户与权限管理模块
映射需求: FR-SJGL-0002 (账户与权限管理)
"""
from django.apps import AppConfig


class UsersConfig(AppConfig):
    name = 'users'
    verbose_name = '用户与权限管理'
