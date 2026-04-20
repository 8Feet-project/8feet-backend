"""
统计/日志/个性化 API
映射需求: FR-SJGL-0003 ~ 0004, FR-GRXX-0001 ~ 0003
"""
from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from analytics.interface.analytics_interface import (
    add_favorite,
    create_alert,
    get_cost_report,
    get_dashboard_stats,
    list_alerts,
    list_favorites,
    remove_favorite,
)
from analytics.models.logs import LLMCallLog, OperationLog, SystemLog
from analytics.models.personalization import Alert, Favorite
from shared.utils import (
    ErrorCode,
    failed_api_response,
    jwt_auth,
    parse_json_body,
    response_wrapper,
    success_api_response,
)


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_dashboard'])
def dashboard(request: HttpRequest):
    """统计看板聚合接口

    [route]: GET /api/v1/admin/dashboard
    """
    return success_api_response({
        "overview": _build_dashboard_overview(),
        "object_distribution": _build_object_distribution(),
        "model_usage": _build_model_usage(),
        "user_activity": _build_user_activity(),
    })


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_dashboard'])
def dashboard_overview(request: HttpRequest):
    """统计看板概览

    [route]: GET /api/v1/admin/dashboard/overview
    """
    return success_api_response(_build_dashboard_overview())


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_dashboard'])
def dashboard_object_distribution(request: HttpRequest):
    """调研对象分布

    [route]: GET /api/v1/admin/dashboard/object-distribution
    """
    return success_api_response(_build_object_distribution())


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_dashboard'])
def dashboard_model_usage(request: HttpRequest):
    """模型调用排行与趋势

    [route]: GET /api/v1/admin/dashboard/model-usage
    """
    return success_api_response(_build_model_usage())


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_dashboard'])
def dashboard_user_activity(request: HttpRequest):
    """用户活跃与留存

    [route]: GET /api/v1/admin/dashboard/user-activity
    """
    return success_api_response(_build_user_activity())


def _build_dashboard_overview():
    total_research_requests = 128
    active_users_trend = [
        {"date": "04-14", "value": 18},
        {"date": "04-15", "value": 22},
        {"date": "04-16", "value": 26},
        {"date": "04-17", "value": 24},
        {"date": "04-18", "value": 31},
        {"date": "04-19", "value": 29},
        {"date": "04-20", "value": 35},
    ]
    return {
        "total_research_requests": total_research_requests,
        "dau": active_users_trend[-1]["value"],
        "mau": 96,
        "active_users_trend": active_users_trend,
    }



def _build_object_distribution():
    return {
        "company_ratio": 52,
        "stock_ratio": 33,
        "commodity_ratio": 15,
    }



def _build_model_usage():
    ranking = [
        {
            "model_id": "model-gpt-4o",
            "model_name": "GPT-4o",
            "provider": "OpenAI",
            "call_count": 84,
        },
        {
            "model_id": "model-claude-3-5-sonnet",
            "model_name": "Claude 3.5 Sonnet",
            "provider": "Anthropic",
            "call_count": 63,
        },
        {
            "model_id": "model-deepseek-v3",
            "model_name": "DeepSeek V3",
            "provider": "DeepSeek",
            "call_count": 41,
        },
    ]
    return {
        "model_usage_ranking": ranking,
        "trend_series": [
            {"date": "04-14", "call_count": 16},
            {"date": "04-15", "call_count": 21},
            {"date": "04-16", "call_count": 27},
            {"date": "04-17", "call_count": 24},
            {"date": "04-18", "call_count": 33},
            {"date": "04-19", "call_count": 29},
            {"date": "04-20", "call_count": 38},
        ],
    }



def _build_user_activity():
    activity_series = [
        {"date": "04-14", "active_users": 18},
        {"date": "04-15", "active_users": 22},
        {"date": "04-16", "active_users": 26},
        {"date": "04-17", "active_users": 24},
        {"date": "04-18", "active_users": 31},
        {"date": "04-19", "active_users": 29},
        {"date": "04-20", "active_users": 35},
    ]
    return {
        "activity_series": activity_series,
        "retention_summary": [
            {"label": "次日留存", "value": "68%"},
            {"label": "7日留存", "value": "41%"},
            {"label": "近7日新增", "value": "14"},
            {"label": "异常活跃用户", "value": "3"},
        ],
    }


@response_wrapper
@require_http_methods(["GET", "POST"])
@jwt_auth()
def favorite_folders_collection(request: HttpRequest):
    """收藏文件夹集合

    [route]: GET /api/v1/favorites/folders
    [route]: POST /api/v1/favorites/folders
    """
    if request.method == 'GET':
        folders = []
        folder_names = (
            Favorite.objects.filter(user_id=request.user.id)
            .exclude(folder__isnull=True)
            .exclude(folder='')
            .values_list('folder', flat=True)
            .distinct()
            .order_by('folder')
        )
        for index, folder_name in enumerate(folder_names, start=1):
            folders.append({
                "folder_id": f"folder-{index}",
                "folder_name": folder_name,
                "parent_id": None,
                "item_count": Favorite.objects.filter(user_id=request.user.id, folder=folder_name).count(),
            })
        return success_api_response({"list": folders, "total": len(folders)})

    payload = parse_json_body(request)
    folder_name = payload.get('folder_name') or request.POST.get('folder_name')
    if not folder_name:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "folder_name 不能为空")
    return success_api_response({
        "folder_id": f"folder-{folder_name}",
        "folder_name": folder_name,
        "parent_id": payload.get('parent_id'),
    })


@response_wrapper
@require_GET
@jwt_auth()
def favorite_folder_detail(request: HttpRequest, folder_id: str):
    """单个收藏文件夹详情

    [route]: GET /api/v1/favorites/folders/{folder_id}
    """
    queryset = Favorite.objects.filter(user_id=request.user.id, folder=folder_id).order_by('-created_at')
    items = [
        {
            "favorite_id": item.id,
            "target_id": item.item_id,
            "favorite_type": item.item_type,
            "folder_id": item.folder,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in queryset
    ]
    return success_api_response({
        "folder_id": folder_id,
        "list": items,
        "total": len(items),
    })


@response_wrapper
@require_http_methods(["GET", "POST"])
@jwt_auth()
def favorite_items_collection(request: HttpRequest):
    """收藏项集合

    [route]: GET /api/v1/favorites/items
    [route]: POST /api/v1/favorites/items
    """
    if request.method == 'GET':
        favorite_type = request.GET.get('favorite_type')
        folder_id = request.GET.get('folder_id')
        queryset = Favorite.objects.filter(user_id=request.user.id)
        if favorite_type:
            queryset = queryset.filter(item_type=favorite_type)
        if folder_id:
            queryset = queryset.filter(folder=folder_id)
        items = [
            {
                "favorite_id": item.id,
                "target_id": item.item_id,
                "favorite_type": item.item_type,
                "folder_id": item.folder,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in queryset.order_by('-created_at')
        ]
        return success_api_response({"list": items, "total": len(items)})

    payload = parse_json_body(request)
    item_type = payload.get('favorite_type') or request.POST.get('favorite_type')
    item_id = payload.get('target_id') or request.POST.get('target_id')
    folder_id = payload.get('folder_id') or request.POST.get('folder_id')
    if not item_type or not item_id:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "缺少必要参数")

    add_favorite(request.user.id, item_type, int(item_id), folder_id)
    return success_api_response({
        "favorite_id": 999,
        "favorite_status": True,
    })


@response_wrapper
@require_POST
@jwt_auth()
def favorite_item_move(request: HttpRequest, favorite_id: int):
    """移动收藏项到指定文件夹

    [route]: POST /api/v1/favorites/items/{favorite_id}/move
    """
    payload = parse_json_body(request)
    folder_id = payload.get('folder_id') or request.POST.get('folder_id')
    favorite = Favorite.objects.filter(id=favorite_id, user_id=request.user.id).first()
    if favorite is None:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "收藏项不存在")
    favorite.folder = folder_id
    favorite.save(update_fields=['folder'])
    return success_api_response({
        "favorite_id": favorite.id,
        "folder_id": favorite.folder,
    })


@response_wrapper
@require_http_methods(["DELETE"])
@jwt_auth()
def favorite_item_detail(request: HttpRequest, favorite_id: int):
    """单个收藏项

    [route]: DELETE /api/v1/favorites/items/{favorite_id}
    """
    remove_favorite(request.user.id, favorite_id)
    return success_api_response({"result": "success", "target_id": favorite_id})


@response_wrapper
@require_http_methods(["GET", "POST"])
@jwt_auth()
def alerts_collection(request: HttpRequest):
    """提醒集合

    [route]: GET /api/v1/alerts
    [route]: POST /api/v1/alerts
    """
    if request.method == 'GET':
        alerts = [
            {
                "alert_id": item.id,
                "object_type": item.object_type,
                "object_name": item.object_name,
                "enabled": item.enabled,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in Alert.objects.filter(user_id=request.user.id).order_by('-created_at')
        ]
        return success_api_response({"list": alerts, "total": len(alerts)})

    payload = parse_json_body(request)
    object_type = payload.get('object_type') or request.POST.get('object_type')
    object_name = payload.get('object_name') or request.POST.get('object_name')
    if not object_type or not object_name:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "参数不全")

    alert_id = create_alert(request.user.id, object_type, object_name, payload.get('trigger_rules') or {}, True)
    return success_api_response({"alert_id": alert_id, "status": "enabled"})


@response_wrapper
@require_http_methods(["PATCH"])
@jwt_auth()
def alert_detail(request: HttpRequest, alert_id: int):
    """更新提醒开关

    [route]: PATCH /api/v1/alerts/{alert_id}
    """
    payload = parse_json_body(request)
    enabled = payload.get('enabled')
    alert = Alert.objects.filter(id=alert_id, user_id=request.user.id).first()
    if alert is None:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "提醒不存在")
    if enabled is not None:
        alert.enabled = bool(enabled)
        alert.save(update_fields=['enabled'])
    return success_api_response({"alert_id": alert.id, "status": "enabled" if alert.enabled else "disabled"})


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_logs'])
def operation_logs(request: HttpRequest):
    """操作日志列表

    [route]: GET /api/v1/admin/logs/operations
    """
    logs = [
        {
            "log_id": item.id,
            "operator": item.user.username if item.user else None,
            "action": item.action,
            "resource_type": item.resource_type,
            "resource_id": item.resource_id,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in OperationLog.objects.select_related('user').order_by('-created_at')[:50]
    ]
    return success_api_response({"list": logs, "total": len(logs)})


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_logs'])
def llm_call_logs(request: HttpRequest):
    """LLM 调用日志列表

    [route]: GET /api/v1/admin/logs/llm-calls
    """
    logs = [
        {
            "log_id": item.id,
            "model_id": item.model_id,
            "provider": item.provider,
            "request_tokens": item.request_tokens,
            "response_tokens": item.response_tokens,
            "cost": float(item.cost) if item.cost is not None else None,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in LLMCallLog.objects.order_by('-created_at')[:50]
    ]
    return success_api_response({"list": logs, "total": len(logs)})


@response_wrapper
@require_GET
@jwt_auth(perms=['analytics.view_logs'])
def system_logs(request: HttpRequest):
    """系统日志列表

    [route]: GET /api/v1/admin/logs/system
    """
    logs = [
        {
            "log_id": item.id,
            "level": item.level,
            "message": item.message,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in SystemLog.objects.order_by('-created_at')[:50]
    ]
    return success_api_response({"list": logs, "total": len(logs)})


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
        "period": {"start": start_date, "end": end_date},
    })
