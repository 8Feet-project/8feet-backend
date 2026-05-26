"""
统计/日志/个性化 API
映射需求: FR-SJGL-0003 ~ 0004, FR-GRXX-0001 ~ 0003
"""
import json

from django.http import HttpRequest
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_GET, require_POST

from shared.utils import (
    ErrorCode, failed_api_response, response_wrapper,
    success_api_response, jwt_auth
)
from analytics.interface.analytics_interface import (
    get_dashboard_stats, add_favorite,
    list_favorites, create_alert, list_alerts,
    get_cost_report, trigger_alert_now, following_run_after
)


def _request_data(request: HttpRequest) -> dict:
    if request.content_type and "application/json" in request.content_type:
        try:
            return json.loads(request.body or b"{}")
        except (json.JSONDecodeError, TypeError):
            return {}
    return request.POST


def _parse_time_param(value: str | None, *, end_of_day: bool = False):
    if not value:
        return None
    text = str(value).strip()
    is_date_only = "T" not in text and " " not in text
    parsed = None if is_date_only else parse_datetime(text)
    if parsed is None:
        parsed_date = parse_date(text)
        if parsed_date is None:
            return None
        parsed = timezone.datetime.combine(
            parsed_date,
            timezone.datetime.max.time() if end_of_day else timezone.datetime.min.time(),
        )
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _dashboard_time_range(request: HttpRequest):
    start_time = _parse_time_param(request.GET.get("start_time") or request.GET.get("start_date"))
    end_time = _parse_time_param(
        request.GET.get("end_time") or request.GET.get("end_date"),
        end_of_day=True,
    )
    return start_time, end_time


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
        "remark": item.get("remark") or "",
    }


def _serialize_alert(item: dict) -> dict:
    condition = item.get("condition") or {}
    return {
        "alert_id": str(item.get("id")),
        "object_name": item.get("object_name") or "",
        "object_type": _normalize_object_type(item.get("object_type")),
        "push_in_app": bool(item.get("notify_in_app", True)),
        "push_email": bool(item.get("notify_email", True)),
        "schedule_rule": condition.get("schedule_rule", "daily"),
        "schedule_time": condition.get("schedule_time", "09:00"),
        "status": "enabled" if item.get("is_active", True) else "disabled",
        "next_run_at": item.get("next_run_at").isoformat() if hasattr(item.get("next_run_at"), "isoformat") else str(item.get("next_run_at") or ""),
        "last_triggered_at": item.get("last_triggered_at").isoformat() if hasattr(item.get("last_triggered_at"), "isoformat") else str(item.get("last_triggered_at") or ""),
        "last_task_id": str(item.get("last_task_id") or ""),
    }


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_dashboard'])
def dashboard(request: HttpRequest):
    """统计看板

    [route]: GET /api/analytics/dashboard
    """
    stats = get_dashboard_stats(*_dashboard_time_range(request), user=request.user)
    summary = stats.get("summary", {})
    daily_ops = stats.get("trends", {}).get("daily_active_ops", [])
    return success_api_response({
        "total_research_requests": summary.get("total_research_tasks", 0),
        "dau": daily_ops[-1]["count"] if daily_ops else 0,
        "mau": summary.get("active_user_total", 0),
        "active_users_trend": [
            {"date": item["date"].isoformat() if hasattr(item["date"], "isoformat") else str(item["date"]), "value": item.get("count", 0)}
            for item in daily_ops
        ],
        "operation_log_total": summary.get("operation_log_total", 0),
        "raw": stats,
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_dashboard'])
def object_distribution(request: HttpRequest):
    stats = get_dashboard_stats(*_dashboard_time_range(request), user=request.user)
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
        "total": 0 if not counts else sum(counts.values()),
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_dashboard'])
def model_usage(request: HttpRequest):
    stats = get_dashboard_stats(*_dashboard_time_range(request), user=request.user)
    ranking = [
        {
            "model_id": str(item.get("llm_config_id") or ""),
            "model_name": item.get("model_name") or "已删除模型",
            "provider": item.get("provider") or "",
            "is_deleted": item.get("llm_config_id") is None,
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
    stats = get_dashboard_stats(*_dashboard_time_range(request), user=request.user)
    daily_ops = stats.get("trends", {}).get("daily_active_ops", [])
    return success_api_response({
        "activity_series": [
            {"date": item["date"].isoformat() if hasattr(item["date"], "isoformat") else str(item["date"]), "active_users": item.get("count", 0)}
            for item in daily_ops
        ],
        "retention_summary": [
            {"label": "区间活跃用户", "value": str(stats.get("summary", {}).get("active_user_total", 0))},
            {"label": "区间操作日志", "value": str(stats.get("summary", {}).get("operation_log_total", 0))},
        ],
    })


@response_wrapper
@jwt_auth()
def favorite_items(request: HttpRequest):
    if request.method == 'GET':
        return favorite_list(request)
    if request.method == 'POST':
        return favorite_add(request)
    return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "不支持的请求方法")


@response_wrapper
@jwt_auth()
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
@jwt_auth()
def favorite_add(request: HttpRequest):
    """新增收藏项
    [route]: POST /api/v1/favorites/items
    """
    data = _request_data(request)
    item_type = data.get('favorite_type') # 对齐文档参数名
    item_type = _backend_favorite_type(item_type)
    item_id = data.get('target_id')     # 对齐文档参数名
    remark = str(data.get('remark') or '').strip()

    if not item_type or not item_id:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "缺少必要参数")

    item_id = str(item_id).strip()
    if not item_id:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "target_id 不能为空")

    created = add_favorite(request.user.id, item_type, item_id, remark)
    favorites = list_favorites(request.user.id, item_type)
    latest = next((item for item in favorites if str(item.get("item_id")) == str(item_id)), None)
    return success_api_response({
        "favorite_id": str(latest.get("id")) if latest else "",
        "favorite_status": "created" if created else "favorited",
    })


@response_wrapper
@jwt_auth()
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

    condition = {
        "schedule_rule": data.get("schedule_rule", "daily"),
        "schedule_time": data.get("schedule_time", "09:00"),
        "time_range": data.get("time_range", "30d"),
        "source_authority": data.get("source_authority", "unrestricted"),
        "source_types": data.get("source_types") or ["official", "data", "research", "news"],
        "research_focus": data.get("research_focus") or ["overview"],
        "search_params": data.get("search_params") or {},
        "model_id": data.get("model_id") or "",
    }
    alert_id = create_alert(
        request.user.id,
        object_type,
        object_name,
        condition,
        bool(data.get("push_email", True)),
        bool(data.get("push_in_app", True)),
    )
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
        if "push_in_app" in data:
            alert.notify_in_app = bool(data.get("push_in_app"))
            updated.append("push_in_app")
        if "status" in data:
            alert.is_active = data.get("status") == "enabled"
            updated.append("status")
        if "schedule_rule" in data:
            alert.condition = {**(alert.condition or {}), "schedule_rule": data.get("schedule_rule")}
            updated.append("schedule_rule")
        if "schedule_time" in data:
            alert.condition = {**(alert.condition or {}), "schedule_time": data.get("schedule_time")}
            updated.append("schedule_time")
        if "schedule_rule" in data or "schedule_time" in data:
            alert.next_run_at = following_run_after(alert)
            updated.append("next_run_at")
        alert.save(update_fields=["notify_email", "notify_in_app", "is_active", "condition", "next_run_at"])

        triggered_task_id = None
        trigger_error = ""
        if "status" in data and alert.is_active:
            ok, trigger_error, triggered_task_id = trigger_alert_now(alert)
            if ok:
                updated.extend(["last_triggered_at", "next_run_at", "last_task"])

        response = {"alert_id": str(alert.id), "updated_fields": updated}
        if triggered_task_id:
            response["triggered_task_id"] = str(triggered_task_id)
        if trigger_error:
            response["trigger_error"] = trigger_error
        return success_api_response(response)

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
    
    report = get_cost_report(start_date, end_date, user=request.user)
    return success_api_response({
        "report": report,
        "period": {"start": start_date, "end": end_date}
    })
