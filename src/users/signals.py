"""
users 信号处理器 — 角色变更时自动同步 Django Group

当 UserProfile 创建或 role 字段变更时，自动将用户分配到对应的
Django Group，从而继承该 Group 绑定的权限。
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from users.models.user_profile import UserProfile
from shared.permissions import assign_user_to_role_group


@receiver(post_save, sender=UserProfile)
def sync_user_group_on_profile_save(sender, instance, created, **kwargs):
    """UserProfile 保存后，根据 role 同步用户所属 Group

    - 新建 Profile: 直接分配对应 Group
    - 修改 role: 移除旧 Group，加入新 Group
    """
    assign_user_to_role_group(instance.user, instance.role)
