"""
数据迁移: 初始化角色 Group 与权限绑定

执行内容:
1. 创建 admin_group / normal_group 两个 Django Group
2. 将 ROLE_PERMISSIONS 中定义的权限分配给对应 Group
3. 为存量用户根据 UserProfile.role 分配到对应 Group

依赖: 所有 App 的 Model Migration 已完成（自定义权限已注册到 auth_permission 表）
"""
from django.db import migrations


def setup_groups_and_assign_users(apps, schema_editor):
    """正向迁移: 初始化 Group 并为存量用户分配权限"""
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    UserProfile = apps.get_model('users', 'UserProfile')

    # 角色-权限映射 (与 shared/permissions.py 保持一致)
    ROLE_PERMISSIONS = {
        'ADMIN': [
            'users.create_user',
            'users.update_user',
            'users.toggle_user',
            'users.view_user',
            'llm_manager.change_llmconfig',
            'llm_manager.view_llmconfig',
            'research.create_research',
            'research.view_research',
            'research.cancel_research',
            'reports.view_report',
            'reports.followup_report',
            'analytics.add_favorite',
            'analytics.remove_favorite',
            'analytics.view_favorite',
            'analytics.create_alert',
            'analytics.view_alert',
            'analytics.view_dashboard',
        ],
        'NORMAL': [
            'llm_manager.view_llmconfig',
            'research.create_research',
            'research.view_research',
            'research.cancel_research',
            'reports.view_report',
            'reports.followup_report',
            'analytics.add_favorite',
            'analytics.remove_favorite',
            'analytics.view_favorite',
            'analytics.create_alert',
            'analytics.view_alert',
            'analytics.view_dashboard',
        ],
    }

    ROLE_GROUP_MAP = {
        'ADMIN': 'admin_group',
        'NORMAL': 'normal_group',
    }

    def get_perm(perm_str):
        app_label, codename = perm_str.split('.')
        try:
            ct = ContentType.objects.get(app_label=app_label)
        except ContentType.DoesNotExist:
            # 尝试通过 codename 模糊匹配
            return Permission.objects.filter(
                content_type__app_label=app_label, codename=codename
            ).first()
        return Permission.objects.filter(
            content_type=ct, codename=codename
        ).first()

    # 1. 创建 Group 并绑定权限
    for role, perm_codes in ROLE_PERMISSIONS.items():
        group_name = ROLE_GROUP_MAP[role]
        group, _ = Group.objects.get_or_create(name=group_name)
        group.permissions.clear()
        for perm_str in perm_codes:
            perm = get_perm(perm_str)
            if perm:
                group.permissions.add(perm)

    # 2. 为存量用户分配 Group
    all_role_group_names = set(ROLE_GROUP_MAP.values())
    for profile in UserProfile.objects.select_related('user').all():
        user = profile.user
        # 移除旧角色 Group
        for g in user.groups.filter(name__in=all_role_group_names):
            user.groups.remove(g)
        # 分配新角色 Group
        group_name = ROLE_GROUP_MAP.get(profile.role, 'normal_group')
        group = Group.objects.get(name=group_name)
        user.groups.add(group)


def reverse_setup(apps, schema_editor):
    """反向迁移: 删除角色 Group"""
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=['admin_group', 'normal_group']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0001_initial'),
        ('auth', '__latest__'),
        ('contenttypes', '__latest__'),
        # 确保所有 App 的自定义权限迁移已执行
        ('llm_manager', '__latest__'),
        ('research', '__latest__'),
        ('reports', '__latest__'),
        ('analytics', '__latest__'),
    ]

    operations = [
        migrations.RunPython(
            setup_groups_and_assign_users,
            reverse_code=reverse_setup,
        ),
    ]
