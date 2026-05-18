"""
用户人设 API。
"""
import json

from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

from shared.utils import ErrorCode, failed_api_response, jwt_auth, response_wrapper, success_api_response
from users.interface.persona_interface import (
    clear_user_persona,
    serialize_user_persona,
    skip_user_persona_prompt,
)


@response_wrapper
@require_GET
@jwt_auth()
def persona_detail(request: HttpRequest):
    return success_api_response(serialize_user_persona(request.user))


@response_wrapper
@require_POST
@jwt_auth()
def persona_skip(request: HttpRequest):
    return success_api_response(skip_user_persona_prompt(request.user))


@response_wrapper
@require_POST
@jwt_auth()
def persona_clear(request: HttpRequest):
    return success_api_response(clear_user_persona(request.user))


def request_data(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def missing_runtime_response(message: str = "人设设定运行时暂不可用"):
    return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)
