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
    'ADMIN': [
        # 用户管理 (管理员专属)
        'users.create_user',
        'users.update_user',
        'users.toggle_user',
        'users.view_user',
        # LLM 配置管理 (管理员专属)
        'llm_manager.change_llmconfig',
        'llm_manager.view_llmconfig',
        # 调研任务
        'research.create_research',
        'research.view_research',
        'research.cancel_research',
        # 调研报告
        'reports.view_report',
        'reports.followup_report',
        # 收藏
        'analytics.add_favorite',
        'analytics.remove_favorite',
        'analytics.view_favorite',
        # 提醒
        'analytics.create_alert',
        'analytics.view_alert',
        # 统计看板
        'analytics.view_dashboard',
    ],
    'NORMAL': [
        # LLM 查看 (不含修改)
        'llm_manager.view_llmconfig',
        # 调研任务
        'research.create_research',
        'research.view_research',
        'research.cancel_research',
        # 调研报告
        'reports.view_report',
        'reports.followup_report',
        # 收藏
        'analytics.add_favorite',
        'analytics.remove_favorite',
        'analytics.view_favorite',
        # 提醒
        'analytics.create_alert',
        'analytics.view_alert',
        # 统计看板
        'analytics.view_dashboard',
    ],
}

# 角色对应的 Group 名称
ROLE_GROUP_MAP = {
    'ADMIN': 'admin_group',
    'NORMAL': 'normal_group',
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
