"""
用户扩展 Profile 模型
扩展 Django 内置 User 模型，增加业务相关字段
"""
from django.db import models
from django.contrib.auth import get_user_model


ROLE_ADMIN = 'ADMIN'
ROLE_NORMAL = 'NORMAL'
ROLE_CHOICES = [
    (ROLE_ADMIN, '管理员'),
    (ROLE_NORMAL, '普通用户'),
]


class UserProfile(models.Model):
    """用户扩展信息

    FR-SJGL-0002: 超级管理员可创建并维护管理员/普通用户账户，
    支持细粒度权限分配。
    """
    user = models.OneToOneField(
        get_user_model(), on_delete=models.CASCADE,
        related_name='profile', help_text="关联 Django 内置 User"
    )
    avatar = models.CharField(
        max_length=512, null=True, blank=True,
        help_text="头像 URL (Minio 路径)"
    )
    phone = models.CharField(
        max_length=20, null=True, blank=True,
        help_text="联系电话"
    )
    organization = models.CharField(
        max_length=256, null=True, blank=True,
        help_text="所属单位/组织"
    )
    role = models.CharField(
        max_length=16, choices=ROLE_CHOICES, default=ROLE_NORMAL,
        help_text="用户角色: ADMIN 或 NORMAL"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_profile'
        verbose_name = '用户扩展信息'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"
