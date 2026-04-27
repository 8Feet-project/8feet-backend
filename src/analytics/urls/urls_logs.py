"""
管理端系统日志路由 (挂载于 /api/v1/admin/logs/)
"""
from django.urls import path

from analytics.models.logs import OperationLog, SystemLog
from shared.utils import ErrorCode, failed_api_response, jwt_auth, response_wrapper, success_api_response


def _serialize_operation_log(log):
    return {
        "log_id": f"op-{log.id}",
        "level": "info",
        "module": log.target_module or "operation",
        "user_keyword": getattr(log.user, "username", "") if log.user else "",
        "object_type": None,
        "model_id": "",
        "action_summary": log.action_type,
        "created_at": log.created_at.isoformat(),
    }


def _serialize_system_log(log):
    level = (log.level or "INFO").lower()
    if level == "warn":
        level = "warning"
    return {
        "log_id": f"sys-{log.id}",
        "level": level,
        "module": log.module or "system",
        "user_keyword": "",
        "object_type": None,
        "model_id": "",
        "action_summary": log.message[:120],
        "created_at": log.created_at.isoformat(),
    }

@response_wrapper
@jwt_auth(perms=['analytics.view_dashboard'])
def log_list(request):
    operation_logs = [_serialize_operation_log(item) for item in OperationLog.objects.select_related("user").all()[:100]]
    system_logs = [_serialize_system_log(item) for item in SystemLog.objects.all()[:100]]
    logs = sorted(operation_logs + system_logs, key=lambda item: item["created_at"], reverse=True)
    return success_api_response({"list": logs, "total": len(logs)})


@response_wrapper
@jwt_auth(perms=['analytics.view_dashboard'])
def log_detail(request, log_id: str):
    if log_id.startswith("op-"):
        raw_id = log_id.removeprefix("op-")
        log = OperationLog.objects.filter(pk=raw_id).select_related("user").first()
        if not log:
            return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "日志不存在")
        return success_api_response({
            "log_id": log_id,
            "user_action": log.action_type,
            "search_intent": "",
            "agent_trace": [{"step": "operation", "detail": str(log.detail or {})}],
            "prompt_raw": "",
            "response_raw": "",
            "error_stack": "",
        })

    raw_id = log_id.removeprefix("sys-")
    log = SystemLog.objects.filter(pk=raw_id).first()
    if not log:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "日志不存在")
    return success_api_response({
        "log_id": log_id,
        "user_action": log.message,
        "search_intent": "",
        "agent_trace": [{"step": log.module, "detail": log.message}],
        "prompt_raw": "",
        "response_raw": "",
        "error_stack": log.stack_trace or "",
    })


@response_wrapper
@jwt_auth(perms=['analytics.view_dashboard'])
def log_export(request):
    return success_api_response({"export_id": "logs-latest", "status": "completed"})


@response_wrapper
@jwt_auth(perms=['analytics.view_dashboard'])
def log_export_status(request, export_id: str):
    return success_api_response({
        "export_id": export_id,
        "status": "completed",
        "download_url": "",
    })

urlpatterns = [
    path('', log_list, name='admin-log-list'),
    path('export', log_export, name='admin-log-export'),
    path('export/', log_export, name='admin-log-export-slash'),
    path('export/<str:export_id>/status', log_export_status, name='admin-log-export-status'),
    path('export/<str:export_id>/status/', log_export_status, name='admin-log-export-status-slash'),
    path('<str:log_id>', log_detail, name='admin-log-detail'),
    path('<str:log_id>/', log_detail, name='admin-log-detail-slash'),
]
