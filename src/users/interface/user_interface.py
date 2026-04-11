"""
用户管理业务逻辑 — interface 层
管理员专用: 创建/修改/启禁用/查看用户

纯 Python 业务逻辑，不涉及 HTTP。
"""
from typing import Tuple, Optional, List

from django.contrib.auth import get_user_model
from django.db import IntegrityError

from users.models.user_profile import UserProfile, ROLE_ADMIN, ROLE_USER

User = get_user_model()

VALID_ROLES = {ROLE_ADMIN, ROLE_USER}


def create_user_account(
    username: str, password: str, email: str = '',
    role: str = ROLE_USER, phone: str = None
) -> Tuple[bool, str, Optional[int]]:
    """创建用户账户并分配角色

    Returns:
        (成功与否, 消息, 用户ID)
    """
    if not username or not password:
        return False, "用户名和密码不能为空", None

    if role not in VALID_ROLES:
        return False, f"无效的角色类型: {role}", None

    try:
        user = User.objects.create_user(
            username=username, password=password, email=email
        )
    except IntegrityError:
        return False, f"用户名 '{username}' 已存在", None

    # 创建 Profile (信号会自动分配 Group)
    UserProfile.objects.create(
        user=user, role=role, phone=phone
    )

    return True, "用户创建成功", user.id


def update_user_account(
    target_user_id: int, email: str = None, role: str = None,
    phone: str = None
) -> Tuple[bool, str]:
    """修改用户信息/角色

    Returns:
        (成功与否, 消息)
    """
    user = User.objects.filter(pk=target_user_id).first()
    if not user:
        return False, "目标用户不存在"

    if email is not None:
        user.email = email
        user.save(update_fields=['email'])

    profile, _ = UserProfile.objects.get_or_create(user=user)

    if role is not None:
        if role not in VALID_ROLES:
            return False, f"无效的角色类型: {role}"
        profile.role = role

    if phone is not None:
        profile.phone = phone

    profile.save()  # 信号会自动同步 Group
    return True, "用户信息已更新"


def toggle_user_account(target_user_id: int, is_active: bool) -> Tuple[bool, str]:
    """启用/禁用用户账户

    Returns:
        (成功与否, 消息)
    """
    user = User.objects.filter(pk=target_user_id).first()
    if not user:
        return False, "目标用户不存在"

    if user.is_superuser:
        return False, "不允许操作超级管理员账户"

    user.is_active = is_active
    user.save(update_fields=['is_active'])
    status = "已启用" if is_active else "已禁用"
    return True, f"用户 {user.username} {status}"


def list_all_users() -> List[dict]:
    """获取所有用户列表

    Returns:
        用户信息字典列表
    """
    users = User.objects.all().select_related('profile').order_by('-date_joined')
    result = []
    for user in users:
        profile = getattr(user, 'profile', None)
        result.append({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_active": user.is_active,
            "is_staff": user.is_staff,
            "role": profile.role if profile else ROLE_USER,
            "phone": profile.phone if profile else None,
            "date_joined": user.date_joined.strftime('%Y-%m-%d %H:%M:%S'),
        })
    return result
