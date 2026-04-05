"""
统计/日志/个性化 API
映射需求: FR-SJGL-0003 ~ 0004, FR-GRXX-0001 ~ 0003
"""
from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

from shared.utils import (
    ErrorCode, failed_api_response, response_wrapper,
    success_api_response, jwt_auth
)
from analytics.interface.analytics_interface import (
    get_dashboard_stats, add_favorite, remove_favorite,
    list_favorites, create_alert, list_alerts
)


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_dashboard'])
def dashboard(request: HttpRequest):
    """统计看板

    [route]: GET /api/analytics/dashboard
    """
    stats = get_dashboard_stats()
    return success_api_response(stats)


@response_wrapper
@require_POST
@jwt_auth(perms=['analytics.add_favorite'])
def favorite_add(request: HttpRequest):
    """添加收藏

    [route]: POST /api/analytics/favorite/add
    """
    item_type = request.POST.get('item_type')
    item_id = request.POST.get('item_id')
    folder = request.POST.get('folder')

    if not item_type or not item_id:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            "item_type 和 item_id 不能为空"
        )

    created = add_favorite(request.user.id, item_type, int(item_id), folder)
    return success_api_response({"created": created})


@response_wrapper
@require_POST
@jwt_auth(perms=['analytics.remove_favorite'])
def favorite_remove(request: HttpRequest):
    """取消收藏

    [route]: POST /api/analytics/favorite/remove
    """
    item_type = request.POST.get('item_type')
    item_id = request.POST.get('item_id')

    if not item_type or not item_id:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            "item_type 和 item_id 不能为空"
        )

    removed = remove_favorite(request.user.id, item_type, int(item_id))
    return success_api_response({"removed": removed})


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_favorite'])
def favorite_list(request: HttpRequest):
    """收藏列表

    [route]: GET /api/analytics/favorite/list?item_type=REPORT
    """
    item_type = request.GET.get('item_type')
    favorites = list_favorites(request.user.id, item_type)
    return success_api_response(favorites)


@response_wrapper
@require_POST
@jwt_auth(perms=['analytics.create_alert'])
def alert_create(request: HttpRequest):
    """创建动态提醒

    [route]: POST /api/analytics/alert/create
    """
    import json
    object_type = request.POST.get('object_type')
    object_name = request.POST.get('object_name')
    condition_str = request.POST.get('condition', '{}')
    notify_email = request.POST.get('notify_email', 'true').lower() == 'true'

    if not object_type or not object_name:
        return failed_api_response(
            ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR,
            "object_type 和 object_name 不能为空"
        )

    try:
        condition = json.loads(condition_str)
    except (json.JSONDecodeError, TypeError):
        condition = {}

    alert_id = create_alert(
        request.user.id, object_type, object_name, condition, notify_email
    )
    return success_api_response({"alert_id": alert_id})


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_alert'])
def alert_list(request: HttpRequest):
    """提醒列表

    [route]: GET /api/analytics/alert/list
    """
    alerts = list_alerts(request.user.id)
    return success_api_response(alerts)
