"""
用户管理 API — 管理员专用 (创建/修改/启禁用/列表)
映射需求: FR-SJGL-0002 (账户与权限管理)

所有接口均需管理员权限，通过 jwt_auth(perms=[...]) 独立配置。
"""
import json

from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

from shared.utils import (
    ErrorCode,
    failed_api_response,
    jwt_auth,
    response_wrapper,
    success_api_response,
)
from users.interface.user_interface import (
    create_user_account,
    get_user_detail as get_user_account_detail,
    list_all_users,
    reset_user_password as reset_user_password_for_target,
    toggle_user_account,
    update_user_account,
)


def _load_request_data(request: HttpRequest):
    try:
        return json.loads(request.body or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return request.POST


def _parse_positive_int(value, default: int) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default


@response_wrapper
@jwt_auth(perms=["users.view_user"])
def list_users(request: HttpRequest):
    """账户列表查询
    [route]: GET /api/v1/admin/users
    """
    role = request.GET.get("role")
    status = request.GET.get("status")
    keyword = request.GET.get("keyword")
    page = _parse_positive_int(request.GET.get("page"), 1)
    page_size = _parse_positive_int(request.GET.get("page_size"), 20)

    result = list_all_users(
        role=role,
        status=status,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return success_api_response(result)


@response_wrapper
@require_POST
@jwt_auth(perms=["users.create_user"])
def create_user(request: HttpRequest):
    """创建账户
    [route]: POST /api/v1/admin/users
    """
    data = _load_request_data(request)
    username = data.get("username")
    password = data.get("password", "123456")
    email = data.get("email", "")
    phone = data.get("phone")
    role = data.get("role", "user")

    success, message, user_id = create_user_account(
        username=username,
        password=password,
        email=email,
        role=role,
        phone=phone,
    )
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({
        "user_id": user_id,
        "temp_password": password,
    })


@response_wrapper
@require_GET
@jwt_auth(perms=["users.view_user"])
def get_user_detail(request: HttpRequest, user_id: int):
    """查询账户详情
    [route]: GET /api/v1/admin/users/{user_id}
    """
    success, message, result = get_user_account_detail(user_id)
    if not success:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, message)
    return success_api_response(result)


@response_wrapper
@jwt_auth(perms=["users.update_user"])
def update_user_permissions(request: HttpRequest, user_id: int):
    """修改账户角色与权限 (PATCH)
    [route]: PATCH /api/v1/admin/users/{user_id}
    """
    if request.method != "PATCH":
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "请使用 PATCH 方法")

    data = _load_request_data(request)
    success, message, updated_fields = update_user_account(
        operator=request.user,
        target_user_id=user_id,
        email=data.get("email"),
        role=data.get("role"),
        phone=data.get("phone"),
        permissions=data.get("permissions"),
    )
    if not success:
        error_code = ErrorCode.ITEM_NOT_FOUND if message == "目标用户不存在" else ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR
        return failed_api_response(error_code, message)

    return success_api_response({
        "user_id": user_id,
        "updated_fields": updated_fields,
    })


@response_wrapper
@require_POST
@jwt_auth(perms=["users.update_user"])
def reset_user_password(request: HttpRequest, user_id: int):
    """重置账户密码
    [route]: POST /api/v1/admin/users/{user_id}/reset-password
    """
    data = _load_request_data(request)
    new_password = data.get("new_password")
    if not new_password:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "new_password 不能为空")

    success, message = reset_user_password_for_target(
        operator=request.user,
        target_user_id=user_id,
        new_password=new_password,
    )
    if not success:
        error_code = ErrorCode.ITEM_NOT_FOUND if message == "目标用户不存在" else ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR
        return failed_api_response(error_code, message)

    return success_api_response({
        "user_id": user_id,
        "result": "success",
    })


@response_wrapper
@require_POST
@jwt_auth(perms=["users.toggle_user"])
def toggle_user(request: HttpRequest):
    """启用/禁用用户。"""
    data = _load_request_data(request)
    user_id = data.get("user_id")
    if user_id is None or user_id == "":
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "user_id 不能为空")

    is_active = str(data.get("is_active", "true")).lower() == "true"

    try:
        target_user_id = int(user_id)
    except (TypeError, ValueError):
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "user_id 必须是整数")

    success, message = toggle_user_account(request.user, target_user_id, is_active)
    if not success:
        error_code = ErrorCode.ITEM_NOT_FOUND if message == "目标用户不存在" else ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR
        return failed_api_response(error_code, message)

    return success_api_response({
        "user_id": target_user_id,
        "is_active": is_active,
        "message": message,
    })
