"""
权限映射中心 — 角色(Role) → 权限(Permission) 映射表

集中定义 ADMIN / NORMAL 两种角色分别拥有的 Django Permission 权限码。
供 Group 初始化信号和数据迁移脚本使用。
"""
from django.contrib.auth.models import Group, Permission

# ============================================================
# 角色-权限映射配置
# ============================================================
ROLE_PERMISSIONS = {
    'super_admin': [
        # 用户管理
        'users.create_user',
        'users.update_user',
        'users.toggle_user',
        'users.view_user',
        # 平台初始化与模型管理
        'llm_manager.change_llmconfig',
        'llm_manager.view_llmconfig',
        'llm_manager.view_modelusage',
        # 统计与日志
        'analytics.view_dashboard',
        'analytics.view_audit_log',
        # 所有业务权限
        'research.create_research',
        'research.view_research',
        'research.cancel_research',
        'reports.view_report',
        'reports.followup_report',
        'analytics.add_favorite',
        'analytics.view_favorite',
        'analytics.create_alert',
        'analytics.view_alert',
    ],
    'admin': [
        # 部分用户管理
        'users.view_user',
        'users.update_user',
        # 模型查看
        'llm_manager.view_llmconfig',
        # 统计与日志
        'analytics.view_dashboard',
        # 基础业务权限
        'research.create_research',
        'research.view_research',
        'reports.view_report',
        'reports.followup_report',
        'analytics.add_favorite',
        'analytics.view_favorite',
    ],
    'user': [
        # 仅基础业务权限
        'research.create_research',
        'research.view_research',
        'reports.view_report',
        'reports.followup_report',
        'analytics.add_favorite',
        'analytics.view_favorite',
        'analytics.create_alert',
    ],
}

# 角色对应的 Group 名称
ROLE_GROUP_MAP = {
    'super_admin': 'super_admin_group',
    'admin': 'admin_group',
    'user': 'user_group',
}


def _parse_perm(perm_str: str):
    """将 'app_label.codename' 解析为 Permission 对象"""
    app_label, codename = perm_str.split('.')
    return Permission.objects.filter(
        content_type__app_label=app_label, codename=codename
    ).first()


def setup_groups():
    """初始化角色 Group 并绑定权限

    可在数据迁移或 manage.py 命令中调用。
    确保幂等: 重复执行不会重复创建。
    """
    for role, perm_codes in ROLE_PERMISSIONS.items():
        group_name = ROLE_GROUP_MAP[role]
        group, _ = Group.objects.get_or_create(name=group_name)

        # 清空旧权限后重新赋值，保证与 ROLE_PERMISSIONS 配置同步
        group.permissions.clear()
        for perm_str in perm_codes:
            perm = _parse_perm(perm_str)
            if perm:
                group.permissions.add(perm)


def assign_user_to_role_group(user, role: str):
    """将用户加入对应角色的 Group（移除其他角色 Group）"""
    all_role_groups = set(ROLE_GROUP_MAP.values())
    # 移除用户当前所有角色 Group
    for g in user.groups.filter(name__in=all_role_groups):
        user.groups.remove(g)
    # 加入新角色 Group
    group_name = ROLE_GROUP_MAP.get(role)
    if group_name:
        group, _ = Group.objects.get_or_create(name=group_name)
        user.groups.add(group)
