"""
用户扩展 Profile 模型
扩展 Django 内置 User 模型，增加业务相关字段
"""
from django.db import models
from django.contrib.auth import get_user_model


ROLE_SUPER_ADMIN = 'super_admin'
ROLE_ADMIN = 'admin'
ROLE_USER = 'user'
ROLE_NORMAL = ROLE_USER
ROLE_CHOICES = [
    (ROLE_SUPER_ADMIN, '超级管理员'),
    (ROLE_ADMIN, '管理员'),
    (ROLE_USER, '普通用户'),
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
    created_by = models.ForeignKey(
        get_user_model(), on_delete=models.SET_NULL,
        null=True, blank=True, related_name='managed_user_profiles',
        help_text="Account manager who created or owns this user"
    )
    avatar = models.CharField(
        max_length=512, null=True, blank=True,
        help_text="头像 URL (Minio 路径)"
    )
    nickname = models.CharField(
        max_length=64, null=True, blank=True,
        help_text="用户昵称"
    )
    phone = models.CharField(
        max_length=20, null=True, blank=True,
        help_text="联系电话"
    )
    role = models.CharField(
        max_length=16, choices=ROLE_CHOICES, default=ROLE_USER,
        help_text="用户角色"
    )
    email_verified = models.BooleanField(
        default=False, help_text="邮箱是否已验证"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_profile'
        verbose_name = '用户扩展信息'
        verbose_name_plural = verbose_name
        permissions = [
            ('create_user', '创建用户账户'),
            ('update_user', '修改用户信息及角色'),
            ('toggle_user', '启用/禁用用户账户'),
            ('view_user', '查看用户列表'),
        ]

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"
