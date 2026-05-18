import jwt
import random
import string
from datetime import timedelta
from typing import Tuple, Optional, Dict, Any, List

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from users.models.auth_record import AuthRecord
from users.models.user_profile import UserProfile, ROLE_SUPER_ADMIN, ROLE_USER
from shared.permissions import to_product_permission_codes


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


def get_token_dict(user) -> Dict[str, Any]:
    """获取标准的 token 返回结构"""
    return {
        "access_token": generate_access_token(user.id),
        "refresh_token": generate_refresh_token(user),
        "expires_in": 3 * 3600  # 3 小时
    }


def register_user(
    username: str,
    nickname: str,
    password: str,
    email: str,
    phone: str = None,
    invite_code: str = None,
    email_verified: bool = False,
) -> Tuple[bool, str, Optional[Dict]]:
    """用户注册逻辑"""
    User = get_user_model()
    if User.objects.filter(username=username).exists():
        return False, "用户名已存在", None
    if User.objects.filter(email=email).exists():
        return False, "邮箱已被注册", None

    try:
        with transaction.atomic():
            # 检查是否是首个用户（初始化平台逻辑）
            is_first_user = not User.objects.exists()
            role = ROLE_SUPER_ADMIN if is_first_user else ROLE_USER
            
            user = User.objects.create_user(
                username=username,
                password=password,
                email=email,
                first_name=username  # 默认将 nickname 存入 first_name 保证 Django 兼容性
            )
            
            # 创建 Profile
            UserProfile.objects.create(
                user=user,
                phone=phone,
                role=role,
                nickname=nickname,
                email_verified=email_verified,
            )
            
            data = {
                "user_id": user.id,
                "role": role,
                "need_initialize": is_first_user,
                "should_prompt_persona": not is_first_user,
                **get_token_dict(user)
            }
            return True, "注册成功", data
    except Exception as e:
        return False, f"注册失败: {str(e)}", None


def authenticate_by_username(username: str, password: str) -> Tuple[bool, Optional[str], Optional[dict]]:
    """用户名登录认证"""
    User = get_user_model()
    user = User.objects.filter(username=username).first()
    if not user:
        return False, "用户名或密码错误", None

    return _perform_login(user, password)


def authenticate_by_email(email: str, password: str) -> Tuple[bool, Optional[str], Optional[dict]]:
    """邮箱登录认证"""
    User = get_user_model()
    user = User.objects.filter(email=email).first()
    if not user:
        return False, "邮箱或密码错误", None

    return _perform_login(user, password)


def _perform_login(user, password) -> Tuple[bool, Optional[str], Optional[dict]]:
    """执行通用的登录后校验逻辑"""
    user = authenticate(username=user.username, password=password)
    if not user:
        return False, "密码错误", None

    if not user.is_active:
        return False, "账户已被禁用", None

    user.last_login = timezone.now()
    user.save()

    profile = getattr(user, 'profile', None)
    from users.interface.persona_interface import should_prompt_persona
    
    tokens = get_token_dict(user)
    data = {
        "user_id": user.id,
        "nickname": profile.nickname if profile else user.get_full_name(),
        "role": profile.role if profile else "user",
        "permissions": to_product_permission_codes(user.get_all_permissions()),
        "should_prompt_persona": should_prompt_persona(user),
        **tokens
    }
    return True, None, data


def refresh_access_token(refresh_token_str: str) -> Tuple[bool, Optional[str], Optional[Dict]]:
    """刷新 access_token"""
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

        user_id = token.get("user_id")
        User = get_user_model()
        user = User.objects.get(pk=user_id)
        
        # 生成新的 token 对
        return True, None, get_token_dict(user)

    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return False, "无效或已过期的刷新令牌", None
    except Exception as e:
        return False, f"刷新失败: {str(e)}", None


def revoke_refresh_token(refresh_token_str: str, user_id: int = None) -> Tuple[bool, str]:
    """废弃 refresh_token 对应的会话记录。"""
    try:
        token = jwt.decode(
            refresh_token_str, settings.SECRET_KEY, algorithms="HS256")
        if token.get("type") != "refresh_token":
            raise jwt.InvalidTokenError

        record_pk = token.get("record_pk")
        token_user_id = token.get("user_id")
        if not record_pk or not token_user_id:
            raise jwt.InvalidTokenError

        if user_id is not None and int(token_user_id) != int(user_id):
            return False, "refresh_token 不属于当前用户"

        auth_record = AuthRecord.objects.filter(
            pk=record_pk,
            user_id=token_user_id,
        ).first()
        if auth_record is None:
            raise jwt.InvalidTokenError

        auth_record.delete()
        return True, "refresh_token 已失效"

    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return False, "无效或已过期的刷新令牌"
    except Exception as e:
        return False, f"注销失败: {str(e)}"


def send_verification_email(email: str, scene: str) -> Tuple[bool, int]:
    """发送邮箱验证码"""
    code = ''.join(random.choices(string.digits, k=6))
    expire_in = 300 # 5分钟
    
    cache_key = f"email_code_{scene}_{email}"
    cache.set(cache_key, code, expire_in)
    
    # 补全发送邮件逻辑
    subject = "【8Feet】验证码"
    scene_map = {
        'register': '注册',
        'bind': '绑定邮箱',
        'reset_password': '重置密码'
    }
    action_name = scene_map.get(scene, '安全校验')
    message = f"您正在进行{action_name}操作，验证码为：{code}。有效期5分钟，请勿泄露给他人。"
    
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
    except Exception as e:
        print(f"Failed to send verification email to {email}: {str(e)}")
        # 生产环境下应使用 logger.error
    
    return True, expire_in


def verify_email_code(email: str, code: str) -> bool:
    """验证邮箱验证码 (通用场景)"""
    # 尝试多种可能的场景
    for scene in ['register', 'bind', 'reset_password']:
        cache_key = f"email_code_{scene}_{email}"
        saved_code = cache.get(cache_key)
        if saved_code and saved_code == code:
            cache.delete(cache_key)
            return True
    return False


def verify_email_code_for_scene(email: str, code: str, scene: str, consume: bool = True) -> bool:
    """按场景验证邮箱验证码。

    注册流程需要只校验 register 场景，避免其他场景验证码被误用。
    """
    cache_key = f"email_code_{scene}_{email}"
    saved_code = cache.get(cache_key)
    if not saved_code or saved_code != code:
        return False

    if consume:
        cache.delete(cache_key)
    return True


def request_password_reset(username_or_email: str) -> Tuple[bool, str]:
    """发起密码重置参与者：
    1. 用户名或邮箱是否存在
    2. 生成重置令牌并存储在缓存
    """
    from django.db.models import Q
    User = get_user_model()
    user = User.objects.filter(Q(username=username_or_email) | Q(email=username_or_email)).first()
    if not user:
        return False, "用户不存在"
    
    if not user.email:
        return False, "用户未绑定邮箱，无法重置"
    
    # 生成重置令牌
    reset_token = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
    cache.set(f"password_reset_{reset_token}", user.id, 1800) # 30分钟有效
    
    # 补全发送邮件逻辑
    subject = "【8Feet】密码重置请求"
    message = f"您请求重置 8Feet 账户的密码。您的重置令牌为：{reset_token}\n请在 30 分钟内完成操作。如果您没有发起此请求，请忽略。"
    
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception as e:
        print(f"Failed to send password reset email to {user.email}: {str(e)}")
    
    return True, "验证邮件已发送"


def confirm_password_reset(reset_token: str, new_password: str) -> Tuple[bool, str]:
    """确认密码重置"""
    user_id = cache.get(f"password_reset_{reset_token}")
    if not user_id:
        return False, "重置令牌无效或已过期"
    
    User = get_user_model()
    user = User.objects.filter(pk=user_id).first()
    if not user:
        return False, "用户不存在"
    
    user.set_password(new_password)
    user.save()
    cache.delete(f"password_reset_{reset_token}")
    
    return True, "密码更新成功"


def update_user_profile(user, data: Dict[str, Any]) -> Tuple[bool, str, List[str]]:
    """更新用户信息"""
    profile = getattr(user, 'profile', None)
    if not profile:
        return False, "用户 Profile 不存在", []
    
    updated_fields = []

    # 前端资料接口使用 avatar_url，模型实际字段为 avatar。
    profile_field_map = {
        'nickname': 'nickname',
        'phone': 'phone',
        'avatar_url': 'avatar',
    }
    profile_updated_fields = []

    for request_field, model_field in profile_field_map.items():
        if request_field in data:
            setattr(profile, model_field, data[request_field])
            profile_updated_fields.append(model_field)
            updated_fields.append(request_field)

    if 'email' in data:
        user.email = data['email']
        user.save(update_fields=['email'])
        updated_fields.append('email')

    if profile_updated_fields:
        profile.save(update_fields=profile_updated_fields)
        
    return True, "更新成功", updated_fields


def change_user_password(user, old_password: str, new_password: str) -> Tuple[bool, str]:
    """修改密码"""
    if not user.check_password(old_password):
        return False, "旧密码错误"
    
    user.set_password(new_password)
    user.save()
    return True, "密码修改成功"


def blacklist_token(header: str) -> bool:
    """将 Token 加入黑名单"""
    if not header:
        return False
        
    try:
        parts = header.split(" ")
        if len(parts) != 2 or parts[0] != "Bearer":
            return False
        
        token_str = parts[1]
        
        # 解开 token 获取过期时间
        payload = jwt.decode(token_str, settings.SECRET_KEY, algorithms="HS256")
        exp = payload.get("exp")
        
        if exp:
            # 计算剩余有效期
            current_ts = timezone.now().timestamp()
            timeout = int(exp - current_ts)
            if timeout > 0:
                cache.set(f"blacklist_{token_str}", True, timeout)
                return True
        return False
    except Exception:
        return False
