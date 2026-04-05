"""
用户认证业务逻辑 — interface 层
纯 Python 业务逻辑，不涉及 HTTP。
参照 example-backend/core/interface/ 的设计模式。
"""
from datetime import timedelta
from typing import Tuple, Optional

import jwt
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.utils import timezone

from users.models.auth_record import AuthRecord


def generate_access_token(user_id: int, access_token_delta: int = 3) -> str:
    """生成 JWT access_token

    Args:
        user_id: 用户 ID
        access_token_delta: 过期时间 (小时)，默认 3 小时
    """
    current_time = timezone.now()
    payload = {
        "user_id": user_id,
        "exp": current_time + timedelta(hours=access_token_delta),
        "iat": current_time,
        "type": "access_token",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def generate_refresh_token(user, refresh_token_delta: int = 14) -> str:
    """生成 JWT refresh_token

    Args:
        user: User 对象
        refresh_token_delta: 过期时间 (天)，默认 14 天
    """
    current_time = timezone.now()
    auth_record = AuthRecord(
        user=user,
        login_at=current_time,
        expires_by=current_time + timedelta(days=refresh_token_delta)
    )
    auth_record.save()

    payload = {
        "user_id": user.id,
        "record_pk": auth_record.id,
        "iat": current_time,
        "type": "refresh_token",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def authenticate_user(username: str, password: str) -> Tuple[bool, Optional[str], Optional[dict]]:
    """用户登录认证

    Returns:
        (成功与否, 错误消息, token_dict)
    """
    from django.db.models import Q

    User = get_user_model()
    user = User.objects.filter(Q(email=username) | Q(username=username)).first()
    if not user:
        return (False, "用户名或密码错误", None)

    user = authenticate(username=user.username, password=password)
    if not user:
        return (False, "用户名或密码错误", None)

    if not user.is_active:
        return (False, "账户已被禁用", None)

    user.last_login = timezone.now()
    user.save()

    tokens = {
        "access_token": generate_access_token(user.id),
        "refresh_token": generate_refresh_token(user),
    }
    return (True, None, tokens)


def refresh_access_token(refresh_token_str: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """刷新 access_token

    Returns:
        (成功与否, 错误消息, 新 access_token)
    """
    try:
        token = jwt.decode(
            refresh_token_str, settings.SECRET_KEY, algorithms="HS256")
        if token.get("type") != "refresh_token":
            raise jwt.InvalidTokenError

        auth_record = AuthRecord.objects.filter(
            pk=token.get("record_pk")).first()
        if auth_record is None:
            raise jwt.InvalidTokenError
        if auth_record.expires_by < timezone.now():
            raise jwt.ExpiredSignatureError

        new_access_token = generate_access_token(token.get("user_id"))
        return (True, None, new_access_token)

    except jwt.ExpiredSignatureError:
        return (False, "Token 已过期", None)
    except jwt.InvalidTokenError:
        return (False, "无效的 Token", None)
