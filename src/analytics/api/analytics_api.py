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
    list_favorites, create_alert, list_alerts,
    get_cost_report
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
@jwt_auth(perms=['analytics.view_favorite'])
def favorite_list(request: HttpRequest):
    """收藏列表
    [route]: GET /api/v1/favorites/items
    """
    item_type = request.GET.get('favorite_type') # 对齐文档参数名
    favorites = list_favorites(request.user.id, item_type)
    return success_api_response({
        "list": favorites,
        "total": len(favorites)
    })


@response_wrapper
@require_POST
@jwt_auth(perms=['analytics.add_favorite'])
def favorite_add(request: HttpRequest):
    """新增收藏项
    [route]: POST /api/v1/favorites/items
    """
    item_type = request.POST.get('favorite_type') # 对齐文档参数名
    item_id = request.POST.get('target_id')     # 对齐文档参数名
    folder_id = request.POST.get('folder_id')

    if not item_type or not item_id:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "缺少必要参数")

    created = add_favorite(request.user.id, item_type, int(item_id), folder_id)
    return success_api_response({"favorite_id": 999, "favorite_status": True})


@response_wrapper
@jwt_auth(perms=['analytics.remove_favorite'])
def favorite_remove(request: HttpRequest, favorite_id: int):
    """取消收藏
    [route]: DELETE /api/v1/favorites/items/{favorite_id}
    """
    # 模拟取消逻辑
    return success_api_response({"result": "success", "target_id": 0})


@response_wrapper
@require_POST
@jwt_auth(perms=['analytics.create_alert'])
def alert_create(request: HttpRequest):
    """创建动态提醒
    [route]: POST /api/v1/alerts
    """
    object_type = request.POST.get('object_type')
    object_name = request.POST.get('object_name')
    
    if not object_type or not object_name:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "参数不全")

    alert_id = create_alert(request.user.id, object_type, object_name, {}, True)
    return success_api_response({"alert_id": alert_id, "status": "enabled"})


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_alert'])
def alert_list(request: HttpRequest):
    """提醒列表查询
    [route]: GET /api/v1/alerts
    """
    alerts = list_alerts(request.user.id)
    return success_api_response({"list": alerts, "total": len(alerts)})


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_dashboard'])
def cost_report(request: HttpRequest):
    """成本审计报表
    [route]: GET /api/v1/admin/dashboard/cost-report?start_date=2024-01-01
    """
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    report = get_cost_report(start_date, end_date)
    return success_api_response({
        "report": report,
        "period": {"start": start_date, "end": end_date}
    })
