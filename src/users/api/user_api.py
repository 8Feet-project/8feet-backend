"""
用户管理 API — 管理员专用 (创建/修改/启禁用/列表)
映射需求: FR-SJGL-0002 (账户与权限管理)

所有接口均需管理员权限，通过 jwt_auth(perms=[...]) 独立配置。
"""
import secrets

from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from llm_manager.models.model_permission import ModelPermission
from shared.utils import (
    ErrorCode, failed_api_response, parse_json_body, response_wrapper,
    success_api_response, jwt_auth
)
from users.interface.user_interface import (
    create_user_account, update_user_account,
    toggle_user_account, list_all_users
)

User = get_user_model()


@response_wrapper
@require_GET
@jwt_auth(perms=['users.view_user'])
def list_users(request: HttpRequest):
    """账户列表查询
    [route]: GET /api/v1/admin/users
    """
    role = request.GET.get('role')
    status = request.GET.get('status')
    keyword = (request.GET.get('keyword') or '').strip().lower()

    raw_users = list_all_users()
    users = []
    for item in raw_users:
        normalized_status = 'active' if item.get('is_active') else 'disabled'
        normalized = {
            "user_id": str(item['id']),
            "username": item.get('username') or '',
            "nickname": item.get('username') or '',
            "email": item.get('email') or '',
            "phone": item.get('phone') or None,
            "role": item.get('role') or 'user',
            "status": normalized_status,
            "last_login_at": None,
            "created_at": item.get('date_joined'),
        }
        if role and normalized['role'] != role:
            continue
        if status and normalized['status'] != status:
            continue
        if keyword:
            haystacks = [
                normalized['username'].lower(),
                normalized['nickname'].lower(),
                normalized['email'].lower(),
            ]
            if not any(keyword in text for text in haystacks):
                continue
        users.append(normalized)

    return success_api_response({
        "list": users,
        "total": len(users)
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['users.create_user'])
def create_user(request: HttpRequest):
    """创建账户
    [route]: POST /api/v1/admin/users
    """
    payload = parse_json_body(request)
    username = (payload.get('username') or request.POST.get('username') or '').strip()
    email = (payload.get('email') or request.POST.get('email') or '').strip()
    phone = (payload.get('phone') or request.POST.get('phone') or '').strip() or None
    role = (payload.get('role') or request.POST.get('role') or 'user').strip() or 'user'
    password = secrets.token_urlsafe(8)

    success, message, user_id = create_user_account(
        username, password, email, role, phone
    )
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({
        "user_id": str(user_id),
        "temp_password": password
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['users.view_user'])
def get_user_detail(request: HttpRequest, user_id: int):
    """查询账户详情
    [route]: GET /api/v1/admin/users/{user_id}
    """
    user = User.objects.filter(pk=user_id).select_related('profile').first()
    if not user:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "目标用户不存在")

    profile = getattr(user, 'profile', None)
    user_permissions = sorted(user.get_all_permissions())
    model_permissions = []
    for permission in ModelPermission.objects.filter(user=user, can_use=True).select_related('llm_config'):
        model_permissions.append({
            "model_id": str(permission.llm_config_id),
            "model_name": permission.llm_config.name,
        })

    permission_tree = _build_permission_tree(user_permissions)
    return success_api_response({
        "user_id": str(user.id),
        "basic_info": {
            "username": user.username,
            "nickname": profile.nickname if profile and profile.nickname else user.username,
            "email": user.email or '',
            "phone": profile.phone if profile else None,
            "created_at": user.date_joined.isoformat() if user.date_joined else None,
            "last_login_at": user.last_login.isoformat() if user.last_login else None,
        },
        "role": profile.role if profile else 'user',
        "status": 'active' if user.is_active else 'disabled',
        "permissions": user_permissions,
        "permission_tree": permission_tree,
        "model_permissions": model_permissions,
    })


@response_wrapper
@require_http_methods(["PATCH"])
@jwt_auth(perms=['users.update_user'])
def update_user_permissions(request: HttpRequest, user_id: int):
    """修改账户角色与权限 (PATCH)
    [route]: PATCH /api/v1/admin/users/{user_id}
    """
    payload = parse_json_body(request)
    role = payload.get('role')
    status = payload.get('status')
    permissions = payload.get('permissions')

    updated_fields = []
    if role is not None:
        success, message = update_user_account(user_id, role=role)
        if not success:
            return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)
        updated_fields.append('role')

    if status is not None:
        is_active = status == 'active'
        success, message = toggle_user_account(user_id, is_active)
        if not success:
            return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)
        updated_fields.append('status')

    if permissions is not None:
        updated_fields.append('permissions')

    return success_api_response({
        "user_id": str(user_id),
        "updated_fields": updated_fields,
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['users.update_user'])
def reset_user_password(request: HttpRequest, user_id: int):
    """重置账户密码
    [route]: POST /api/v1/admin/users/{user_id}/reset-password
    """
    user = User.objects.filter(pk=user_id).first()
    if not user:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "目标用户不存在")

    temp_password = secrets.token_urlsafe(8)
    user.set_password(temp_password)
    user.save(update_fields=['password'])
    return success_api_response({"user_id": str(user_id), "temp_password": temp_password})


@response_wrapper
@require_POST
@jwt_auth(perms=['users.toggle_user'])
def toggle_user(request: HttpRequest):
    """启用/禁用用户 (保持兼容或作为单独动作)
    """
    user_id = request.POST.get('user_id')
    is_active = request.POST.get('is_active', 'true').lower() == 'true'

    if not user_id:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "user_id 不能为空")

    success, message = toggle_user_account(int(user_id), is_active)
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({"message": message})



def _build_permission_tree(permissions):
    groups = {
        'dashboard': [],
        'user': [],
        'model': [],
        'research': [],
        'report': [],
        'other': [],
    }
    for permission in permissions:
        normalized = permission.lower()
        if 'analytics.' in normalized or 'dashboard' in normalized:
            groups['dashboard'].append(permission)
        elif 'users.' in normalized or 'user' in normalized:
            groups['user'].append(permission)
        elif 'llm_manager.' in normalized or 'model' in normalized:
            groups['model'].append(permission)
        elif 'research.' in normalized:
            groups['research'].append(permission)
        elif 'reports.' in normalized or 'report' in normalized:
            groups['report'].append(permission)
        else:
            groups['other'].append(permission)

    labels = {
        'dashboard': '看板权限',
        'user': '用户权限',
        'model': '模型权限',
        'research': '调研权限',
        'report': '报告权限',
        'other': '其他权限',
    }
    result = []
    for key, items in groups.items():
        result.append({
            'key': key,
            'label': labels[key],
            'checked': any(items),
            'children': [
                {
                    'key': item,
                    'label': item,
                    'checked': True,
                }
                for item in items
            ],
        })
    return result
