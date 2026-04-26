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
        'users.create_user',
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

PRODUCT_PERMISSION_MAP = {
    'users.view_user': ['admin:user:read'],
    'users.create_user': ['admin:user:write'],
    'users.update_user': ['admin:user:write', 'admin:user:reset-password'],
    'users.toggle_user': ['admin:user:write'],
    'llm_manager.view_llmconfig': ['admin:model:read'],
    'llm_manager.change_llmconfig': ['admin:model:write', 'admin:model:permission'],
    'analytics.view_dashboard': ['admin:dashboard:read'],
    'analytics.view_audit_log': ['admin:logs:read', 'admin:logs:export'],
    'research.create_research': ['research:task:create'],
    'research.view_research': ['research:task:read'],
    'research.cancel_research': ['research:task:cancel'],
    'reports.view_report': ['report:read'],
    'reports.followup_report': ['report:followup'],
    'analytics.add_favorite': ['favorite:write'],
    'analytics.view_favorite': ['favorite:read'],
    'analytics.create_alert': ['alert:write'],
    'analytics.view_alert': ['alert:read'],
}

PRODUCT_TO_DJANGO_PERMISSION = {
    product_code: django_code
    for django_code, product_codes in PRODUCT_PERMISSION_MAP.items()
    for product_code in product_codes
}

ADMIN_PERMISSION_TREE = [
    {
        'key': 'admin:models',
        'label': '模型管理',
        'children': [
            {'key': 'admin:model:read', 'label': '查看模型配置'},
            {'key': 'admin:model:write', 'label': '编辑模型配置'},
            {'key': 'admin:model:permission', 'label': '分配模型权限'},
        ],
    },
    {
        'key': 'admin:users',
        'label': '用户管理',
        'children': [
            {'key': 'admin:user:read', 'label': '查看用户'},
            {'key': 'admin:user:write', 'label': '编辑用户'},
            {'key': 'admin:user:reset-password', 'label': '重置密码'},
        ],
    },
    {
        'key': 'admin:dashboard',
        'label': '统计与日志',
        'children': [
            {'key': 'admin:dashboard:read', 'label': '查看统计看板'},
            {'key': 'admin:logs:read', 'label': '查看系统日志'},
            {'key': 'admin:logs:export', 'label': '导出系统日志'},
        ],
    },
]


def to_product_permission_codes(django_perm_codes):
    """Convert Django permission codes to frontend product permission codes."""
    product_codes = set()
    for perm_code in django_perm_codes or []:
        mapped_codes = PRODUCT_PERMISSION_MAP.get(perm_code)
        if mapped_codes:
            product_codes.update(mapped_codes)
        else:
            product_codes.add(perm_code)
    return sorted(product_codes)


def to_django_permission_codes(permission_codes):
    """Convert frontend product permission codes to Django permission codes."""
    django_codes = set()
    for perm_code in permission_codes or []:
        django_codes.add(PRODUCT_TO_DJANGO_PERMISSION.get(perm_code, perm_code))
    return sorted(django_codes)


def build_admin_permission_tree(checked_codes=None):
    """Return frontend permission tree with checked state filled in."""
    checked = set(checked_codes or [])
    tree = []
    for node in ADMIN_PERMISSION_TREE:
        children = []
        for child in node.get('children', []):
            children.append({
                **child,
                'checked': child['key'] in checked,
            })
        tree.append({
            'key': node['key'],
            'label': node['label'],
            'checked': bool(children) and all(child['checked'] for child in children),
            'children': children,
        })
    return tree


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
