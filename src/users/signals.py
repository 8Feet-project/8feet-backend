"""
users 信号处理器 — 角色变更时自动同步 Django Group

当 UserProfile 创建或 role 字段变更时，自动将用户分配到对应的
Django Group，从而继承该 Group 绑定的权限。
"""
from django.db.models.signals import post_save
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from users.models.user_profile import UserProfile
from shared.permissions import ROLE_PERMISSIONS, assign_user_to_role_group, setup_groups


@receiver(post_save, sender=UserProfile)
def sync_user_group_on_profile_save(sender, instance, created, **kwargs):
    """UserProfile 保存后，根据 role 同步用户所属 Group

    - 新建 Profile: 直接分配对应 Group
    - 修改 role: 移除旧 Group，加入新 Group
    """
    assign_user_to_role_group(instance.user, instance.role)


@receiver(post_migrate)
def sync_role_group_permissions_after_migrate(sender, **kwargs):
    """Migrate 后把数据库中的 Group 权限同步到 ROLE_PERMISSIONS 配置。"""
    from django.contrib.auth.models import Permission

    configured_codes = {
        code
        for permission_codes in ROLE_PERMISSIONS.values()
        for code in permission_codes
    }
    existing_codes = {
        f"{permission.content_type.app_label}.{permission.codename}"
        for permission in Permission.objects.select_related("content_type")
    }
    if not configured_codes.issubset(existing_codes):
        return

    setup_groups()
