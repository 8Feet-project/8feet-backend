"""
用户认证 API — 登录、注册、Token 刷新
映射需求: FR-SJGL-0002 (账户与权限管理)

装饰器使用说明:
- @response_wrapper: 统一将 dict 返回值转为 JsonResponse
- @require_POST / @require_GET: 限制 HTTP 方法
- @jwt_auth(): 需要认证的接口
"""
from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

from shared.utils import (
    ErrorCode, failed_api_response, response_wrapper,
    success_api_response, jwt_auth
)
from users.interface.auth_interface import (
    authenticate_user, refresh_access_token
)


@response_wrapper
@require_POST
def login(request: HttpRequest):
    """用户登录

    [route]: POST /api/users/login
    [params]: username, password
    """
    username = request.POST.get('username')
    password = request.POST.get('password')

    if not username or not password:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            "用户名和密码不能为空"
        )

    success, message, tokens = authenticate_user(username, password)
    if not success:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response(tokens)


@response_wrapper
@require_GET
def refresh_token(request: HttpRequest):
    """刷新 access_token

    [route]: GET /api/users/token-refresh
    [header]: Authorization: Bearer <refresh_token>
    """
    header = request.META.get("HTTP_AUTHORIZATION")
    if not header:
        return failed_api_response(ErrorCode.UNAUTHORIZED, "缺少 Authorization 头")

    parts = header.split(" ")
    if len(parts) != 2 or parts[0] != "Bearer":
        return failed_api_response(ErrorCode.UNAUTHORIZED, "Authorization 格式错误")

    success, message, new_token = refresh_access_token(parts[1])
    if not success:
        return failed_api_response(ErrorCode.UNAUTHORIZED, message)

    return success_api_response({"access_token": new_token})


@response_wrapper
@require_GET
@jwt_auth()
def get_profile(request: HttpRequest):
    """获取当前用户信息

    [route]: GET /api/users/profile
    """
    user = request.user
    profile = getattr(user, 'profile', None)
    data = {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_staff": user.is_staff,
        "role": profile.role if profile else "NORMAL",
        "organization": profile.organization if profile else None,
        "avatar": profile.avatar if profile else None,
    }
    return success_api_response(data)
