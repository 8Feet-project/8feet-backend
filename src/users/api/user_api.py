"""
用户管理 API — 管理员专用 (创建/修改/启禁用/列表)
映射需求: FR-SJGL-0002 (账户与权限管理)

所有接口均需管理员权限，通过 jwt_auth(perms=[...]) 独立配置。
"""
from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

from shared.utils import (
    ErrorCode, failed_api_response, response_wrapper,
    success_api_response, jwt_auth
)
from users.interface.user_interface import (
    create_user_account, update_user_account,
    toggle_user_account, list_all_users
)


@response_wrapper
@require_POST
@jwt_auth(perms=['users.create_user'])
def create_user(request: HttpRequest):
    """创建用户

    [route]: POST /api/users/create
    [params]: username, password, email, role, phone, organization
    """
    username = request.POST.get('username')
    password = request.POST.get('password')
    email = request.POST.get('email', '')
    role = request.POST.get('role', 'NORMAL')
    phone = request.POST.get('phone')
    organization = request.POST.get('organization')

    success, message, user_id = create_user_account(
        username, password, email, role, phone, organization
    )
    if not success:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({"user_id": user_id, "message": message})


@response_wrapper
@require_POST
@jwt_auth(perms=['users.update_user'])
def update_user(request: HttpRequest):
    """修改用户信息/角色

    [route]: POST /api/users/update
    [params]: user_id, email, role, phone, organization
    """
    user_id = request.POST.get('user_id')
    if not user_id:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "user_id 不能为空")

    success, message = update_user_account(
        target_user_id=int(user_id),
        email=request.POST.get('email'),
        role=request.POST.get('role'),
        phone=request.POST.get('phone'),
        organization=request.POST.get('organization'),
    )
    if not success:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({"message": message})


@response_wrapper
@require_POST
@jwt_auth(perms=['users.toggle_user'])
def toggle_user(request: HttpRequest):
    """启用/禁用用户

    [route]: POST /api/users/toggle
    [params]: user_id, is_active (true/false)
    """
    user_id = request.POST.get('user_id')
    is_active = request.POST.get('is_active', 'true').lower() == 'true'

    if not user_id:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "user_id 不能为空")

    success, message = toggle_user_account(int(user_id), is_active)
    if not success:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({"message": message})


@response_wrapper
@require_GET
@jwt_auth(perms=['users.view_user'])
def list_users(request: HttpRequest):
    """查看用户列表

    [route]: GET /api/users/list
    """
    users = list_all_users()
    return success_api_response(users)
