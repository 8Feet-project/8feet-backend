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
@jwt_auth(perms=['users.view_user'])
def list_users(request: HttpRequest):
    """账户列表查询
    [route]: GET /api/v1/admin/users
    """
    role = request.GET.get('role')
    status = request.GET.get('status')
    keyword = request.GET.get('keyword')
    
    users = list_all_users() # 实际应支持过滤和分页
    return success_api_response({
        "list": users,
        "total": len(users)
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['users.create_user'])
def create_user(request: HttpRequest):
    """创建账户 (仅限超级管理员权限逻辑一般在 interface 处理)
    [route]: POST /api/v1/admin/users
    """
    username = request.POST.get('username')
    password = request.POST.get('password', '123456') # 初始密码
    email = request.POST.get('email', '')
    role = request.POST.get('role', 'user')
    
    success, message, user_id = create_user_account(
        username, password, email, role
    )
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({
        "user_id": user_id,
        "temp_password": password
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['users.view_user'])
def get_user_detail(request: HttpRequest, user_id: int):
    """查询账户详情
    [route]: GET /api/v1/admin/users/{user_id}
    """
    # 模拟获取详情
    return success_api_response({
        "user_id": user_id,
        "basic_info": {"username": "mock_user"},
        "role": "user",
        "permissions": []
    })


@response_wrapper
@jwt_auth(perms=['users.update_user'])
def update_user_permissions(request: HttpRequest, user_id: int):
    """修改账户角色与权限 (PATCH)
    [route]: PATCH /api/v1/admin/users/{user_id}
    """
    # 模拟更新
    return success_api_response({"user_id": user_id, "updated_fields": ["role"]})


@response_wrapper
@require_POST
@jwt_auth(perms=['users.update_user'])
def reset_user_password(request: HttpRequest, user_id: int):
    """重置账户密码
    [route]: POST /api/v1/admin/users/{user_id}/reset-password
    """
    return success_api_response({"user_id": user_id, "temp_password": "new_mock_password"})


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
