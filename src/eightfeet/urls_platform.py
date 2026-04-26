"""
平台初始化路由 (挂载于 /api/v1/platform/)
"""
import json
import secrets

from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import path

from llm_manager.models.llm_config import LLMConfig
from shared.permissions import setup_groups
from shared.utils import (
    ErrorCode,
    failed_api_response,
    response_wrapper,
    success_api_response,
)
from users.models.user_profile import UserProfile, ROLE_SUPER_ADMIN


def _json_error(message: str, status: int = 400):
    error_code = ErrorCode.SERVICE_UNAVAILABLE if status == 503 else ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR
    return failed_api_response(error_code, message)


def _build_init_response(super_admin_profile, message: str = "", **extra_fields):
    User = get_user_model()
    payload = {
        "initialized": User.objects.exists(),
        "super_admin_user_id": super_admin_profile.user_id if super_admin_profile else None,
    }
    if message:
        payload["message"] = message
    payload.update(extra_fields)
    return success_api_response(payload)


def _load_request_data(request):
    try:
        return json.loads(request.body or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _build_super_admin_username(base_email: str) -> str:
    User = get_user_model()
    local_part = base_email.split("@", 1)[0].strip().lower().replace(" ", "_")
    seed = "".join(ch for ch in local_part if ch.isalnum() or ch in {"_", ".", "-"})
    candidate = seed or "super_admin"

    if not User.objects.filter(username=candidate).exists():
        return candidate

    index = 1
    while True:
        candidate_with_suffix = f"{candidate}_{index}"
        if not User.objects.filter(username=candidate_with_suffix).exists():
            return candidate_with_suffix
        index += 1


@response_wrapper
def init_status(request):
    """获取平台初始化状态"""
    User = get_user_model()
    # 逻辑：只要存在任何用户，即视为已通过基础初始化流程
    has_any_user = User.objects.exists()
    
    # 检查是否存在超级管理员
    has_super_admin = UserProfile.objects.filter(role=ROLE_SUPER_ADMIN).exists()
    
    return success_api_response({
        "initialized": has_any_user,
        "has_super_admin": has_super_admin
    })


@response_wrapper
def initialize(request):
    """引导初始化过程
    使用管理员邮箱创建或提升超级管理员账户。
    """
    super_admin_profile = UserProfile.objects.filter(role=ROLE_SUPER_ADMIN).first()

    if request.method != 'POST':
        return _json_error("请使用 POST 方法", status=405)

    payload = _load_request_data(request)
    if payload is None:
        return _json_error("请求体不是合法 JSON")

    site_name = (payload.get('site_name') or '').strip()
    admin_email = (payload.get('admin_email') or '').strip()
    default_model_id = (payload.get('default_model_id') or '').strip()

    if not site_name:
        return _json_error("site_name 不能为空")
    if not admin_email:
        return _json_error("admin_email 不能为空")
    try:
        validate_email(admin_email)
    except ValidationError:
        return _json_error("admin_email 格式不合法")

    selected_model = None
    if default_model_id:
        try:
            selected_model = LLMConfig.objects.filter(pk=int(default_model_id)).first()
        except (TypeError, ValueError):
            selected_model = None
        if selected_model is None:
            return _json_error("default_model_id 不存在")

    # 确保角色组存在，后续 Profile 保存时才能正确继承权限。
    setup_groups()

    if super_admin_profile:
        return _build_init_response(
            super_admin_profile,
            message="平台已初始化，已存在超级管理员",
            site_name=site_name,
            default_model_id=str(selected_model.id) if selected_model else default_model_id or None,
            admin_email=super_admin_profile.user.email,
        )

    User = get_user_model()
    temp_password = secrets.token_urlsafe(12)

    with transaction.atomic():
        existing_user = User.objects.filter(email=admin_email).select_related('profile').first()

        if existing_user:
            profile, _ = UserProfile.objects.get_or_create(user=existing_user)
            if not profile.nickname:
                profile.nickname = site_name
            profile.role = ROLE_SUPER_ADMIN
            profile.email_verified = True
            profile.save()
            super_admin_profile = profile
            created = False
            username = existing_user.username
        else:
            username = _build_super_admin_username(admin_email)
            user = User.objects.create_user(
                username=username,
                email=admin_email,
                password=temp_password,
                first_name=site_name,
            )
            super_admin_profile = UserProfile.objects.create(
                user=user,
                nickname=site_name,
                role=ROLE_SUPER_ADMIN,
                email_verified=True,
            )
            created = True

    try:
        send_mail(
            subject=f"【{site_name}】平台初始化完成",
            message=(
                f"平台已初始化。\n"
                f"管理员用户名：{username}\n"
                f"临时密码：{temp_password}\n"
                f"请登录后尽快修改密码。"
            ),
            from_email=None,
            recipient_list=[admin_email],
            fail_silently=False,
        )
        mail_sent = True
    except Exception:
        mail_sent = False

    return _build_init_response(
        super_admin_profile,
        message="平台初始化完成" if created else "已将现有账户提升为超级管理员",
    )

urlpatterns = [
    path('init-status', init_status, name='platform-init-status'),
    path('initialize', initialize, name='platform-initialize'),
]
