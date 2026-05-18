"""
统计/日志/个性化 API
映射需求: FR-SJGL-0003 ~ 0004, FR-GRXX-0001 ~ 0003
"""
import json

from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

from shared.utils import (
    ErrorCode, failed_api_response, response_wrapper,
    success_api_response, jwt_auth
)
from analytics.interface.analytics_interface import (
    get_dashboard_stats, add_favorite,
    list_favorites, create_alert, list_alerts,
    get_cost_report
)


def _request_data(request: HttpRequest) -> dict:
    if request.content_type and "application/json" in request.content_type:
        try:
            return json.loads(request.body or b"{}")
        except (json.JSONDecodeError, TypeError):
            return {}
    return request.POST


def _normalize_object_type(value: str) -> str:
    mapping = {
        "COMPANY": "company",
        "STOCK": "stock",
        "PRODUCT": "commodity",
        "COMMODITY": "commodity",
        "company": "company",
        "stock": "stock",
        "product": "commodity",
        "commodity": "commodity",
    }
    return mapping.get(value or "", value or "company")


def _backend_favorite_type(value: str) -> str:
    mapping = {
        "insight": "INFO",
        "info": "INFO",
        "report": "REPORT",
        "model": "MODEL",
        "INFO": "INFO",
        "REPORT": "REPORT",
        "MODEL": "MODEL",
    }
    return mapping.get(value or "", (value or "").upper())


def _backend_object_type(value: str) -> str:
    mapping = {
        "company": "COMPANY",
        "stock": "STOCK",
        "commodity": "PRODUCT",
        "product": "PRODUCT",
    }
    return mapping.get(value or "", value or "")


def _serialize_favorite(item: dict) -> dict:
    item_type = (item.get("item_type") or "").lower()
    if item_type == "info":
        item_type = "insight"
    return {
        "favorite_id": str(item.get("id")),
        "favorite_type": item_type,
        "target_id": str(item.get("item_id")),
        "remark": "",
    }


def _serialize_alert(item: dict) -> dict:
    return {
        "alert_id": str(item.get("id")),
        "object_name": item.get("object_name") or "",
        "object_type": _normalize_object_type(item.get("object_type")),
        "push_in_app": True,
        "push_email": bool(item.get("notify_email", True)),
        "schedule_rule": (item.get("condition") or {}).get("schedule_rule", "daily"),
        "status": "enabled" if item.get("is_active", True) else "disabled",
    }


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_dashboard'])
def dashboard(request: HttpRequest):
    """统计看板

    [route]: GET /api/analytics/dashboard
    """
    stats = get_dashboard_stats()
    summary = stats.get("summary", {})
    daily_ops = stats.get("trends", {}).get("daily_active_ops", [])
    return success_api_response({
        "total_research_requests": summary.get("total_research_tasks", 0),
        "dau": daily_ops[-1]["count"] if daily_ops else 0,
        "mau": sum(item.get("count", 0) for item in daily_ops),
        "active_users_trend": [
            {"date": item["date"].isoformat() if hasattr(item["date"], "isoformat") else str(item["date"]), "value": item.get("count", 0)}
            for item in daily_ops
        ],
        "raw": stats,
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_dashboard'])
def object_distribution(request: HttpRequest):
    stats = get_dashboard_stats()
    distribution = stats.get("type_distribution", [])
    counts = {
        _normalize_object_type(item.get("object_type")): item.get("count", 0)
        for item in distribution
    }
    total = sum(counts.values()) or 1
    return success_api_response({
        "company_ratio": round(counts.get("company", 0) / total, 4),
        "stock_ratio": round(counts.get("stock", 0) / total, 4),
        "commodity_ratio": round(counts.get("commodity", 0) / total, 4),
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_dashboard'])
def model_usage(request: HttpRequest):
    stats = get_dashboard_stats()
    ranking = [
        {
            "model_id": str(item.get("llm_config_id") or ""),
            "model_name": item.get("llm_config__name") or "Unknown",
            "provider": "",
            "call_count": item.get("calls", 0),
        }
        for item in stats.get("llm_usage_ranking", [])
    ]
    trend_series = [
        {
            "date": item["date"].isoformat() if hasattr(item["date"], "isoformat") else str(item["date"]),
            "values": [{"model_id": "all", "value": item.get("calls", 0)}],
        }
        for item in stats.get("trends", {}).get("daily_llm_usage", [])
    ]
    return success_api_response({
        "model_usage_ranking": ranking,
        "trend_series": trend_series,
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_dashboard'])
def user_activity(request: HttpRequest):
    stats = get_dashboard_stats()
    daily_ops = stats.get("trends", {}).get("daily_active_ops", [])
    return success_api_response({
        "activity_series": [
            {"date": item["date"].isoformat() if hasattr(item["date"], "isoformat") else str(item["date"]), "active_users": item.get("count", 0)}
            for item in daily_ops
        ],
        "retention_summary": [
            {"label": "近 30 日操作数", "value": str(sum(item.get("count", 0) for item in daily_ops))},
        ],
    })


@response_wrapper
@jwt_auth(perms=['analytics.view_favorite'])
def favorite_items(request: HttpRequest):
    if request.method == 'GET':
        return favorite_list(request)
    if request.method == 'POST':
        return favorite_add(request)
    return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "不支持的请求方法")


@response_wrapper
@jwt_auth(perms=['analytics.view_favorite'])
def favorite_list(request: HttpRequest):
    """收藏列表
    [route]: GET /api/v1/favorites/items
    """
    item_type = request.GET.get('favorite_type') # 对齐文档参数名
    if item_type:
        item_type = _backend_favorite_type(item_type)
    favorites = [_serialize_favorite(item) for item in list_favorites(request.user.id, item_type)]
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
    data = _request_data(request)
    item_type = data.get('favorite_type') # 对齐文档参数名
    item_type = _backend_favorite_type(item_type)
    item_id = data.get('target_id')     # 对齐文档参数名

    if not item_type or not item_id:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "缺少必要参数")

    item_id = str(item_id).strip()
    if not item_id:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "target_id 不能为空")

    created = add_favorite(request.user.id, item_type, item_id)
    favorites = list_favorites(request.user.id, item_type)
    latest = next((item for item in favorites if str(item.get("item_id")) == str(item_id)), None)
    return success_api_response({
        "favorite_id": str(latest.get("id")) if latest else "",
        "favorite_status": "created" if created else "favorited",
    })


@response_wrapper
@jwt_auth(perms=['analytics.remove_favorite'])
def favorite_remove(request: HttpRequest, favorite_id: int):
    """取消收藏
    [route]: DELETE /api/v1/favorites/items/{favorite_id}
    """
    from analytics.models.personalization import Favorite

    favorite = Favorite.objects.filter(pk=favorite_id, user_id=request.user.id).first()
    target_id = favorite.item_id if favorite else 0
    if favorite:
        favorite.delete()
    return success_api_response({"result": "success", "target_id": str(target_id)})


@response_wrapper
@require_POST
@jwt_auth(perms=['analytics.create_alert'])
def alert_create(request: HttpRequest):
    """创建动态提醒
    [route]: POST /api/v1/alerts
    """
    data = _request_data(request)
    object_type = _backend_object_type(data.get('object_type'))
    object_name = data.get('object_name')
    
    if not object_type or not object_name:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "参数不全")

    condition = {"schedule_rule": data.get("schedule_rule", "daily")}
    alert_id = create_alert(request.user.id, object_type, object_name, condition, bool(data.get("push_email", True)))
    return success_api_response({"alert_id": str(alert_id), "status": "enabled"})


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_alert'])
def alert_list(request: HttpRequest):
    """提醒列表查询
    [route]: GET /api/v1/alerts
    """
    alerts = [_serialize_alert(item) for item in list_alerts(request.user.id)]
    return success_api_response({"list": alerts, "total": len(alerts)})


@response_wrapper
@jwt_auth(perms=['analytics.view_alert'])
def alert_collection(request: HttpRequest):
    if request.method == 'GET':
        return alert_list(request)
    if request.method == 'POST':
        return alert_create(request)
    return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "不支持的请求方法")


@response_wrapper
@jwt_auth(perms=['analytics.create_alert'])
def alert_detail(request: HttpRequest, alert_id: int):
    from analytics.models.personalization import Alert

    alert = Alert.objects.filter(pk=alert_id, user_id=request.user.id).first()
    if not alert:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "提醒不存在")

    if request.method == 'PATCH':
        data = _request_data(request)
        updated = []
        if "push_email" in data:
            alert.notify_email = bool(data.get("push_email"))
            updated.append("push_email")
        if "status" in data:
            alert.is_active = data.get("status") == "enabled"
            updated.append("status")
        if "schedule_rule" in data:
            alert.condition = {**(alert.condition or {}), "schedule_rule": data.get("schedule_rule")}
            updated.append("schedule_rule")
        alert.save(update_fields=["notify_email", "is_active", "condition"])
        return success_api_response({"alert_id": str(alert.id), "updated_fields": updated})

    if request.method == 'DELETE':
        alert.delete()
        return success_api_response({"result": "success"})

    return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "不支持的请求方法")


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
