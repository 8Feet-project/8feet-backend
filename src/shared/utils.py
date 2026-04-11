"""
公共工具模块 — 所有 App 共享的基础设施

包含: ErrorCode, response_wrapper, jwt_auth 等装饰器与工具函数。
参照 example-backend/core/api/utils.py 的设计模式。
"""
from enum import Enum
from functools import wraps
from typing import List

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest, JsonResponse


# ============================================================
# 业务错误码
# ============================================================
class ErrorCode(Enum):
    """统一业务错误码定义"""
    SUCCESS = 0
    INVALID_REQUEST_ARGUMENT_ERROR = 400
    UNAUTHORIZED = 401
    REFUSE_ACCESS = 403
    ITEM_NOT_FOUND = 404
    INTERNAL_SERVER_ERROR = 500


# ============================================================
# 统一响应构造
# ============================================================
def success_api_response(data=None):
    """构造成功响应"""
    return {
        "code": ErrorCode.SUCCESS.value,
        "message": "success",
        "data": data
    }


def failed_api_response(code: ErrorCode, message: str):
    """构造失败响应"""
    return {
        "code": code.value,
        "message": message,
        "data": None
    }


# ============================================================
# 装饰器: response_wrapper
# 将视图函数返回的 dict 自动包装为 JsonResponse
# ============================================================
def response_wrapper(func):
    """统一响应包装装饰器

    被装饰的视图函数只需返回 dict，装饰器自动转为 JsonResponse。
    参照 example-backend 的同名装饰器。
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        res = func(*args, **kwargs)
        if isinstance(res, dict):
            return JsonResponse(res)
        return res
    return wrapper


# ============================================================
# JWT 工具函数
# ============================================================
def verify_jwt_token(request: HttpRequest):
    """验证 JWT access_token

    Returns:
        (bool, str, int): (是否通过, 错误消息, user_id)
    """
    flag = True
    message = ""
    user_id = ""
    header = request.META.get("HTTP_AUTHORIZATION")
    try:
        if header is None:
            raise jwt.InvalidTokenError

        auth_info = header.split(" ")
        if len(auth_info) != 2:
            raise jwt.InvalidTokenError
        auth_type, auth_token = auth_info

        if auth_type != "Bearer":
            raise jwt.InvalidTokenError
            
        # 检查黑名单
        from django.core.cache import cache
        if cache.get(f"blacklist_{auth_token}"):
            return (False, "Token 已失效 (已注销)", "")

        token = jwt.decode(
            auth_token, settings.SECRET_KEY, algorithms="HS256")
        if token.get("type") != "access_token":
            raise jwt.InvalidTokenError
        user_id = token["user_id"]
    except jwt.ExpiredSignatureError:
        flag, message = False, "Token 已过期"
    except jwt.InvalidTokenError:
        flag, message = False, "无效的 Token"
    return (flag, message, user_id)


# ============================================================
# 装饰器: jwt_auth
# 权限校验装饰器，支持权限列表和固定 Token 白名单
# ============================================================
def jwt_auth(perms: List[str] = None, whitelisted_tokens: List[str] = None):
    """JWT 认证与权限控制装饰器

    支持:
    - Bearer Token 认证 (JWT)
    - 固定 Token 白名单 (用于系统间调用)
    - 可选的 Django Permission 检查

    Usage:
        @response_wrapper
        @require_POST
        @jwt_auth(perms=['llm_manager.can_manage_models'])
        def my_view(request):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(request: HttpRequest, *args, **kwargs):
            # 白名单 Token 快速通过
            if whitelisted_tokens:
                header = request.META.get("HTTP_AUTHORIZATION")
                if header:
                    parts = header.split(" ")
                    if len(parts) == 2 and parts[0] == "Token" and parts[1] in whitelisted_tokens:
                        return func(request, *args, **kwargs)

            # JWT 验证
            (flag, message, user_id) = verify_jwt_token(request)
            if not flag:
                return failed_api_response(ErrorCode.UNAUTHORIZED, message)

            request_user = get_user_model().objects.filter(pk=user_id).first()
            if request_user is None:
                return failed_api_response(
                    ErrorCode.UNAUTHORIZED, "用户不存在或已被禁用")
            request.user = request_user

            # 权限检查
            if perms is not None and not request_user.has_perms(perms):
                return failed_api_response(
                    ErrorCode.REFUSE_ACCESS, "您无权进行此操作")

            return func(request, *args, **kwargs)
        return wrapper
    return decorator
