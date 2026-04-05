"""
大模型配置 API — CRUD 与启禁用
映射需求: FR-SJGL-0001 (大模型配置管理)
"""
import json
from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

from shared.utils import (
    ErrorCode, failed_api_response, response_wrapper,
    success_api_response, jwt_auth
)
from llm_manager.interface.llm_interface import (
    create_or_update_llm_config, toggle_llm_config,
    list_llm_configs, get_recommended_model
)


@response_wrapper
@require_POST
@jwt_auth(perms=['llm_manager.change_llmconfig'])
def create_config(request: HttpRequest):
    """创建/更新大模型配置 (管理员)

    [route]: POST /api/llm/config
    """
    name = request.POST.get('name')
    provider = request.POST.get('provider')
    model_id = request.POST.get('model_id')
    api_endpoint = request.POST.get('api_endpoint')
    api_key = request.POST.get('api_key')
    params_str = request.POST.get('params', '{}')
    description = request.POST.get('description')

    try:
        params = json.loads(params_str)
    except (json.JSONDecodeError, TypeError):
        params = {}

    success, message, config_id = create_or_update_llm_config(
        name, provider, model_id, api_endpoint, api_key, params, description
    )
    if not success:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, message)

    return success_api_response({"id": config_id})


@response_wrapper
@require_POST
@jwt_auth(perms=['llm_manager.change_llmconfig'])
def toggle_config(request: HttpRequest):
    """启用/禁用大模型 (管理员)

    [route]: POST /api/llm/toggle
    """
    config_id = request.POST.get('config_id')
    is_enabled = request.POST.get('is_enabled', 'true').lower() == 'true'

    if not config_id:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "config_id 不能为空")

    success, message = toggle_llm_config(int(config_id), is_enabled)
    if not success:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, message)

    return success_api_response()


@response_wrapper
@require_GET
@jwt_auth(perms=['llm_manager.view_llmconfig'])
def list_configs(request: HttpRequest):
    """获取模型列表

    [route]: GET /api/llm/list
    """
    configs = list_llm_configs(is_enabled=True)
    return success_api_response(configs)


@response_wrapper
@require_GET
@jwt_auth(perms=['llm_manager.view_llmconfig'])
def recommended_model(request: HttpRequest):
    """获取调研对象类型的推荐模型

    [route]: GET /api/llm/recommend?object_type=COMPANY
    """
    object_type = request.GET.get('object_type')
    if not object_type:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "object_type 不能为空")

    model = get_recommended_model(object_type)
    return success_api_response(model)
