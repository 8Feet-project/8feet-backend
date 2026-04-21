"""
用户管理业务逻辑 — interface 层
管理员专用: 创建/修改/启禁用/查看用户

纯 Python 业务逻辑，不涉及 HTTP。
"""
from typing import Dict, List, Optional, Tuple

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError
from django.db.models import Q
from django.db import transaction

from users.models.user_profile import (
    UserProfile,
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    ROLE_USER,
)

User = get_user_model()

VALID_ROLES = {ROLE_ADMIN, ROLE_USER}
VALID_STATUS = {"enabled": True, "disabled": False}


def _is_super_admin(user) -> bool:
    profile = getattr(user, "profile", None)
    return bool(profile and profile.role == ROLE_SUPER_ADMIN)


def _serialize_user(user) -> Dict:
    profile = getattr(user, "profile", None)
    extra_permissions = sorted(
        f"{perm.content_type.app_label}.{perm.codename}"
        for perm in user.user_permissions.all()
    )
    return {
        "user_id": user.id,
        "username": user.username,
        "email": user.email,
        "phone": profile.phone if profile else None,
        "role": profile.role if profile else ROLE_USER,
        "is_active": user.is_active,
        "is_staff": user.is_staff,
        "permissions": sorted(user.get_all_permissions()),
        "extra_permissions": extra_permissions,
        "created_at": user.date_joined.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _get_target_user(target_user_id: int):
    return User.objects.filter(pk=target_user_id).select_related("profile").prefetch_related(
        "user_permissions__content_type"
    ).first()


def _validate_permissions(permission_codes: List[str]) -> Tuple[bool, str, List[Permission]]:
    if permission_codes is None:
        return True, "", []
    if not isinstance(permission_codes, list):
        return False, "permissions 必须是字符串数组", []

    normalized_codes = []
    for code in permission_codes:
        if not isinstance(code, str) or "." not in code:
            return False, "permissions 中存在非法权限码", []
        normalized_codes.append(code.strip())

    permissions = []
    for perm_code in normalized_codes:
        app_label, codename = perm_code.split(".", 1)
        perm = Permission.objects.filter(
            content_type__app_label=app_label,
            codename=codename,
        ).first()
        if perm is None:
            return False, f"权限码不存在: {perm_code}", []
        permissions.append(perm)

    return True, "", permissions


def create_user_account(
    username: str,
    password: str,
    email: str = "",
    role: str = ROLE_USER,
    phone: str = None,
) -> Tuple[bool, str, Optional[int]]:
    """创建用户账户并分配角色。"""
    username = (username or "").strip()
    email = (email or "").strip()
    phone = (phone or "").strip() or None

    if not username or not password:
        return False, "用户名和密码不能为空", None

    if role not in VALID_ROLES:
        return False, f"无效的角色类型: {role}", None

    if email and User.objects.filter(email=email).exists():
        return False, f"邮箱 '{email}' 已存在", None

    try:
        user = User.objects.create_user(
            username=username,
            password=password,
            email=email,
        )
    except IntegrityError:
        return False, f"用户名 '{username}' 已存在", None

    UserProfile.objects.create(
        user=user,
        role=role,
        phone=phone,
        nickname=username,
    )

    return True, "用户创建成功", user.id


def list_all_users(
    role: str = None,
    status: str = None,
    keyword: str = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict:
    """获取用户列表，支持筛选和分页。"""
    queryset = User.objects.all().select_related("profile").prefetch_related(
        "user_permissions__content_type"
    ).order_by("-date_joined")

    if role:
        queryset = queryset.filter(profile__role=role)

    if status:
        normalized_status = status.strip().lower()
        if normalized_status in VALID_STATUS:
            queryset = queryset.filter(is_active=VALID_STATUS[normalized_status])

    if keyword:
        trimmed_keyword = keyword.strip()
        if trimmed_keyword:
            queryset = queryset.filter(
                Q(username__icontains=trimmed_keyword)
                | Q(email__icontains=trimmed_keyword)
                | Q(profile__nickname__icontains=trimmed_keyword)
                | Q(profile__phone__icontains=trimmed_keyword)
            )

    total = queryset.count()
    safe_page = max(int(page or 1), 1)
    safe_page_size = max(min(int(page_size or 20), 100), 1)
    start = (safe_page - 1) * safe_page_size
    end = start + safe_page_size

    return {
        "list": [_serialize_user(user) for user in queryset[start:end]],
        "total": total,
        "page": safe_page,
        "page_size": safe_page_size,
    }


def get_user_detail(target_user_id: int) -> Tuple[bool, str, Optional[Dict]]:
    """查询账户详情。"""
    user = _get_target_user(target_user_id)
    if not user:
        return False, "目标用户不存在", None
    return True, "success", _serialize_user(user)


def update_user_account(
    operator,
    target_user_id: int,
    email: str = None,
    role: str = None,
    phone: str = None,
    permissions: List[str] = None,
) -> Tuple[bool, str, List[str]]:
    """修改用户信息/角色/附加权限。"""
    user = _get_target_user(target_user_id)
    if not user:
        return False, "目标用户不存在", []

    if _is_super_admin(user):
        if role is not None:
            return False, "不允许修改超级管理员角色", []
        if operator != user and not _is_super_admin(operator):
            return False, "只有超级管理员可以修改超级管理员资料", []

    updated_fields = []
    normalized_email = None
    normalized_phone = None
    permission_objects = None

    if email is not None:
        normalized_email = email.strip()
        if normalized_email and User.objects.filter(email=normalized_email).exclude(pk=user.id).exists():
            return False, f"邮箱 '{normalized_email}' 已存在", []

    profile, _ = UserProfile.objects.get_or_create(user=user)

    if role is not None:
        if role not in VALID_ROLES:
            return False, f"无效的角色类型: {role}", []

    if phone is not None:
        normalized_phone = phone.strip() or None

    if permissions is not None:
        valid, message, permission_objects = _validate_permissions(permissions)
        if not valid:
            return False, message, []

    with transaction.atomic():
        if email is not None:
            user.email = normalized_email
            user.save(update_fields=["email"])
            updated_fields.append("email")

        if role is not None:
            profile.role = role
            updated_fields.append("role")

        if phone is not None:
            profile.phone = normalized_phone
            updated_fields.append("phone")

        if "role" in updated_fields or "phone" in updated_fields:
            profile.save()

        if permissions is not None:
            user.user_permissions.set(permission_objects)
            updated_fields.append("permissions")

    return True, "用户信息已更新", updated_fields


def reset_user_password(
    operator,
    target_user_id: int,
    new_password: str,
) -> Tuple[bool, str]:
    """管理员重置账户密码。"""
    if not new_password:
        return False, "new_password 不能为空"

    user = _get_target_user(target_user_id)
    if not user:
        return False, "目标用户不存在"

    if _is_super_admin(user) and operator != user and not _is_super_admin(operator):
        return False, "只有超级管理员可以重置超级管理员密码"

    user.set_password(new_password)
    user.save(update_fields=["password"])
    return True, "密码重置成功"


def toggle_user_account(operator, target_user_id: int, is_active: bool) -> Tuple[bool, str]:
    """启用/禁用用户账户。"""
    user = _get_target_user(target_user_id)
    if not user:
        return False, "目标用户不存在"

    if _is_super_admin(user):
        return False, "不允许操作超级管理员账户"

    if operator == user and not is_active:
        return False, "不允许禁用当前登录账户"

    user.is_active = is_active
    user.save(update_fields=["is_active"])
    status = "已启用" if is_active else "已禁用"
    return True, f"用户 {user.username} {status}"
