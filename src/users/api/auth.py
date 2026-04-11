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
    authenticate_by_username, authenticate_by_email, refresh_access_token, register_user,
    send_verification_email, verify_email_code, request_password_reset,
    confirm_password_reset, update_user_profile, change_user_password, blacklist_token
)


@response_wrapper
@require_POST
def register(request: HttpRequest):
    """用户注册
    [route]: POST /api/v1/auth/register
    """
    import json
    try:
        data = json.loads(request.body)
    except:
        data = request.POST

    username = data.get('username')
    nickname = data.get('nickname')
    password = data.get('password')
    email = data.get('email')
    phone = data.get('phone')
    invite_code = data.get('invite_code')

    if not all([username, nickname, password, email]):
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "用户名、昵称、密码和邮箱不能为空")

    success, message, result = register_user(username, nickname, password, email, phone, invite_code)
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response(result)


@response_wrapper
@require_POST
def login_by_username(request: HttpRequest):
    """用户名登录
    [route]: POST /api/v1/auth/login/username
    """
    import json
    try:
        data = json.loads(request.body)
    except:
        data = request.POST

    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "用户名和密码不能为空")

    success, message, result = authenticate_by_username(username, password)
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response(result)


@response_wrapper
@require_POST
def login_by_email(request: HttpRequest):
    """邮箱登录
    [route]: POST /api/v1/auth/login/email
    """
    import json
    try:
        data = json.loads(request.body)
    except:
        data = request.POST

    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "邮箱和密码不能为空")

    success, message, result = authenticate_by_email(email, password)
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response(result)


@response_wrapper
@require_POST
@jwt_auth()
def logout(request: HttpRequest):
    """用户退出登录
    [route]: POST /api/v1/auth/logout
    """
    header = request.META.get("HTTP_AUTHORIZATION")
    blacklist_token(header)
    return success_api_response({"result": "success"})


@response_wrapper
@require_POST
def refresh_token(request: HttpRequest):
    """刷新令牌
    [route]: POST /api/v1/auth/refresh
    """
    import json
    try:
        data = json.loads(request.body)
    except:
        data = request.POST

    refresh_token_str = data.get('refresh_token')
    if not refresh_token_str:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "缺少 refresh_token")
    
    success, message, tokens = refresh_access_token(refresh_token_str)
    if not success:
        return failed_api_response(ErrorCode.UNAUTHORIZED, message)

    return success_api_response(tokens)


@response_wrapper
@require_POST
def send_email_code(request: HttpRequest):
    """发送邮箱验证码"""
    import json
    try:
        data = json.loads(request.body)
    except:
        data = request.POST

    email = data.get('email')
    scene = data.get('scene', 'register')
    
    if not email:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "邮箱不能为空")

    success, expire_in = send_verification_email(email, scene)
    return success_api_response({"result": "success", "expire_in": expire_in})


@response_wrapper
@require_POST
def verify_email(request: HttpRequest):
    """验证邮箱"""
    import json
    try:
        data = json.loads(request.body)
    except:
        data = request.POST

    email = data.get('email')
    code = data.get('code')
    
    if not email or not code:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "邮箱和验证码不能为空")

    verified = verify_email_code(email, code)
    return success_api_response({"verified": verified})


@response_wrapper
@require_POST
def reset_password_request(request: HttpRequest):
    """发起密码重置"""
    import json
    try:
        data = json.loads(request.body)
    except:
        data = request.POST

    username = data.get('username')
    if not username:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "用户名/邮箱不能为空")

    success, message = request_password_reset(username)
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)
        
    return success_api_response({"result": "success", "message": message})


@response_wrapper
@require_POST
def reset_password_confirm(request: HttpRequest):
    """确认密码重置"""
    import json
    try:
        data = json.loads(request.body)
    except:
        data = request.POST

    reset_token = data.get('reset_token')
    new_password = data.get('new_password')
    
    if not reset_token or not new_password:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "重置令牌和新密码不能为空")

    success, message = confirm_password_reset(reset_token, new_password)
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({"result": "success"})


@response_wrapper
@jwt_auth()
def get_profile(request: HttpRequest):
    """获取/更新当前用户信息
    [route]: GET/PATCH /api/v1/users/me
    """
    user = request.user
    profile = getattr(user, 'profile', None)

    if request.method == 'GET':
        return success_api_response({
            "user_id": user.id,
            "username": user.username,
            "nickname": profile.nickname if profile else user.get_full_name(),
            "email": user.email,
            "phone": profile.phone if profile else None,
            "avatar_url": profile.avatar if profile else None,
            "role": profile.role if profile else "user",
            "permissions": list(user.get_all_permissions()),
            "email_verified": profile.email_verified if profile else False,
            "last_login_at": user.last_login.isoformat() if user.last_login else None
        })
    elif request.method == 'PATCH':
        import json
        try:
            data = json.loads(request.body)
        except:
            data = {}
            
        success, message, updated_fields = update_user_profile(user, data)
        if not success:
            return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)
            
        return success_api_response({
            "user_id": user.id,
            "updated_fields": updated_fields
        })
    return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "不支持的方法")


@response_wrapper
@require_POST
@jwt_auth()
def update_profile(request: HttpRequest):
    """更新用户信息 (兼容路由)"""
    import json
    try:
        data = json.loads(request.body)
    except:
        data = {}
        
    success, message, updated_fields = update_user_profile(request.user, data)
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)
        
    return success_api_response({
        "user_id": request.user.id,
        "updated_fields": updated_fields
    })


@response_wrapper
@jwt_auth()
def change_password(request: HttpRequest):
    """修改密码
    [route]: PATCH /api/v1/users/me/password
    """
    if request.method != 'PATCH' and request.method != 'POST':
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "请使用 PATCH 方法")
    import json
    try:
        data = json.loads(request.body)
    except:
        data = {}
        
    old_password = data.get('old_password')
    new_password = data.get('new_password')
    
    if not old_password or not new_password:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "旧密码和新密码不能为空")

    success, message = change_user_password(request.user, old_password, new_password)
    if not success:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({"result": "success"})
