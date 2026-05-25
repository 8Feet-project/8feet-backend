"""
管理端系统日志路由 (挂载于 /api/v1/admin/logs/)
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.http import FileResponse, Http404, HttpRequest
from django.urls import path
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from analytics.models.logs import OperationLog, SystemLog
from llm_manager.models.model_usage import ModelUsage
from research.models import TaskStepLog
from shared.utils import ErrorCode, failed_api_response, jwt_auth, response_wrapper, success_api_response
from users.scope import apply_user_scope, is_super_admin

EXPORT_ROOT = "admin_logs"
EXPORT_STATUS_CACHE: dict[str, dict] = {}


def _normalize_level(value: str | None) -> str:
    level = str(value or "INFO").strip().lower()
    return "warning" if level == "warn" else level


def _normalize_object_type(value: str | None) -> str:
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
    return mapping.get(str(value or "").strip(), str(value or "").strip())


def _parse_datetime(value: str | None):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_default_timezone())
    return parsed


def _split_param(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _detail_text(detail: dict | None, *keys: str) -> str:
    if not isinstance(detail, dict):
        return ""
    for key in keys:
        value = detail.get(key)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, indent=2)
        return str(value)
    return ""


def _detail_list(detail: dict | None, *keys: str) -> list:
    if not isinstance(detail, dict):
        return []
    for key in keys:
        value = detail.get(key)
        if isinstance(value, list):
            return value
    return []


def _serialize_trace_items(items: list, fallback_step: str, fallback_detail: str) -> list[dict[str, str]]:
    traces: list[dict[str, str]] = []
    for index, item in enumerate(items):
        if isinstance(item, dict):
            step = str(item.get("step") or item.get("node") or item.get("name") or f"step-{index + 1}")
            detail_value = item.get("detail") or item.get("message") or item.get("payload") or item
            detail = (
                json.dumps(detail_value, ensure_ascii=False, indent=2)
                if isinstance(detail_value, (dict, list))
                else str(detail_value)
            )
            traces.append({"step": step, "detail": detail})
        else:
            traces.append({"step": f"step-{index + 1}", "detail": str(item)})
    return traces or [{"step": fallback_step, "detail": fallback_detail}]


def _serialize_operation_log(log: OperationLog) -> dict:
    detail = log.detail if isinstance(log.detail, dict) else {}
    object_type = _normalize_object_type(_detail_text(detail, "object_type", "target_object_type"))
    model_id = _detail_text(detail, "model_id", "llm_config_id")
    action_summary = (
        _detail_text(detail, "action_summary", "summary", "message")
        or log.action_type
    )
    return {
        "log_id": f"op-{log.id}",
        "level": _normalize_level(_detail_text(detail, "level") or "info"),
        "module": log.target_module or "operation",
        "user_keyword": getattr(log.user, "username", "") if log.user else "",
        "object_type": object_type or None,
        "model_id": str(model_id or ""),
        "model_name": _detail_text(detail, "model_name", "llm_config_name"),
        "action_summary": action_summary[:120],
        "created_at": timezone.localtime(log.created_at).isoformat(),
    }


def _serialize_system_log(log: SystemLog) -> dict:
    return {
        "log_id": f"sys-{log.id}",
        "level": _normalize_level(log.level),
        "module": log.module or "system",
        "user_keyword": "",
        "object_type": None,
        "model_id": "",
        "model_name": "",
        "action_summary": (log.message or "")[:120],
        "created_at": timezone.localtime(log.created_at).isoformat(),
    }


def _model_usage_name(log: ModelUsage) -> str:
    if log.model_name_snapshot:
        return log.model_name_snapshot
    config = getattr(log, "llm_config", None)
    if config:
        return config.name or "已删除模型"
    return "已删除模型"


def _model_usage_provider(log: ModelUsage) -> str:
    if log.provider_snapshot:
        return log.provider_snapshot
    config = getattr(log, "llm_config", None)
    return config.provider if config else ""


def _serialize_model_usage_log(log: ModelUsage) -> dict:
    status_code = int(log.status_code or 0)
    model_name = _model_usage_name(log)
    deleted_suffix = "（已删除）" if log.llm_config_id is None else ""
    return {
        "log_id": f"usage-{log.id}",
        "level": "error" if status_code >= 400 else "info",
        "module": "model_usage",
        "user_keyword": getattr(log.user, "username", "") if log.user else "",
        "object_type": None,
        "model_id": str(log.llm_config_id or ""),
        "model_name": model_name,
        "action_summary": f"{model_name}{deleted_suffix} 调用状态 {status_code}，tokens {log.total_tokens}",
        "created_at": timezone.localtime(log.created_at).isoformat(),
    }


def _serialize_task_step_log(log: TaskStepLog) -> dict:
    task = log.task
    detail = log.detail if isinstance(log.detail, dict) else {}
    error = _detail_text(detail, "error", "message")
    message = error or _detail_text(detail, "message")
    status_label = "失败" if log.step_status == "FAILED" else "完成"
    return {
        "log_id": f"taskstep-{log.id}",
        "level": "error" if log.step_status == "FAILED" else "info",
        "module": "research.task",
        "user_keyword": getattr(task.user, "username", "") if task and task.user_id else "",
        "object_type": _normalize_object_type(getattr(task, "object_type", "")) or None,
        "model_id": str(getattr(task, "llm_config_id", "") or ""),
        "model_name": getattr(getattr(task, "llm_config", None), "name", "") if task else "",
        "action_summary": (f"{getattr(task, 'object_name', '')} 调研{status_label}: {message}" or log.step_name)[:120],
        "created_at": timezone.localtime(log.created_at).isoformat(),
    }


def _combined_logs(params: dict | None = None, user=None) -> list[dict]:
    params = params or {}
    start_time = _parse_datetime(params.get("start_time"))
    end_time = _parse_datetime(params.get("end_time"))
    levels = {_normalize_level(item) for item in _split_param(params.get("level"))}
    user_keywords = {item.lower() for item in _split_param(params.get("user_keyword"))}
    model_ids = set(_split_param(params.get("model_id")))
    modules = set(_split_param(params.get("module")))
    object_types = {_normalize_object_type(item) for item in _split_param(params.get("object_type"))}

    operation_query = apply_user_scope(OperationLog.objects.select_related("user").all(), user)
    system_query = SystemLog.objects.all() if is_super_admin(user) else SystemLog.objects.none()
    usage_query = apply_user_scope(ModelUsage.objects.select_related("user", "llm_config").all(), user)
    task_step_query = (
        TaskStepLog.objects
        .select_related("task", "task__user", "task__llm_config")
        .filter(step_status__in=("FAILED", "COMPLETED"), step_name__in=("调研失败", "调研完成"))
    )
    task_step_query = apply_user_scope(task_step_query, user, "task__user_id")

    if start_time:
        operation_query = operation_query.filter(created_at__gte=start_time)
        system_query = system_query.filter(created_at__gte=start_time)
        usage_query = usage_query.filter(created_at__gte=start_time)
        task_step_query = task_step_query.filter(created_at__gte=start_time)
    if end_time:
        operation_query = operation_query.filter(created_at__lte=end_time)
        system_query = system_query.filter(created_at__lte=end_time)
        usage_query = usage_query.filter(created_at__lte=end_time)
        task_step_query = task_step_query.filter(created_at__lte=end_time)

    logs = [_serialize_operation_log(item) for item in operation_query[:500]]
    logs.extend(_serialize_system_log(item) for item in system_query[:500])
    logs.extend(_serialize_model_usage_log(item) for item in usage_query[:500])
    logs.extend(_serialize_task_step_log(item) for item in task_step_query[:500])

    def matches(item: dict) -> bool:
        if levels and item["level"] not in levels:
            return False
        if user_keywords and not any(keyword in item["user_keyword"].lower() for keyword in user_keywords):
            return False
        if model_ids and str(item.get("model_id") or "") not in model_ids:
            return False
        if modules and item.get("module") not in modules:
            return False
        if object_types and (item.get("object_type") or "") not in object_types:
            return False
        return True

    return sorted((item for item in logs if matches(item)), key=lambda item: item["created_at"], reverse=True)


def _paginate(items: list[dict], request: HttpRequest) -> tuple[list[dict], int, int]:
    try:
        page = max(int(request.GET.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(min(int(request.GET.get("page_size", 100)), 500), 1)
    except (TypeError, ValueError):
        page_size = 100
    start = (page - 1) * page_size
    return items[start:start + page_size], page, page_size


@response_wrapper
@jwt_auth(perms=['analytics.view_audit_log'])
def log_list(request):
    logs = _combined_logs(request.GET, request.user)
    page_items, page, page_size = _paginate(logs, request)
    return success_api_response({
        "list": page_items,
        "total": len(logs),
        "page": page,
        "page_size": page_size,
    })


@response_wrapper
@jwt_auth(perms=['analytics.view_audit_log'])
def log_detail(request, log_id: str):
    if log_id.startswith("op-"):
        raw_id = log_id.removeprefix("op-")
        log = apply_user_scope(
            OperationLog.objects.filter(pk=raw_id).select_related("user"),
            request.user,
        ).first()
        if not log:
            return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "日志不存在")

        detail = log.detail if isinstance(log.detail, dict) else {}
        traces = _serialize_trace_items(
            _detail_list(detail, "agent_trace", "trace", "steps"),
            log.target_module or "operation",
            json.dumps(detail, ensure_ascii=False, indent=2) if detail else log.action_type,
        )
        return success_api_response({
            "log_id": log_id,
            "user_action": _detail_text(detail, "user_action", "action") or log.action_type,
            "search_intent": _detail_text(detail, "search_intent", "intent", "query"),
            "agent_trace": traces,
            "prompt_raw": _detail_text(detail, "prompt_raw", "prompt", "request_prompt"),
            "response_raw": _detail_text(detail, "response_raw", "response", "model_response"),
            "error_stack": _detail_text(detail, "error_stack", "stack_trace", "exception"),
        })

    if log_id.startswith("usage-"):
        raw_id = log_id.removeprefix("usage-")
        log = apply_user_scope(
            ModelUsage.objects.filter(pk=raw_id).select_related("user", "llm_config"),
            request.user,
        ).first()
        if not log:
            return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "log not found")
        model_name = _model_usage_name(log)
        return success_api_response({
            "log_id": log_id,
            "user_action": "模型调用",
            "search_intent": log.usage_type or "",
            "agent_trace": [
                {"step": "model", "detail": f"{model_name} / {_model_usage_provider(log)}"},
                {"step": "usage", "detail": f"prompt={log.prompt_tokens}, completion={log.completion_tokens}, total={log.total_tokens}"},
            ],
            "prompt_raw": "",
            "response_raw": f"status_code={log.status_code}, latency_ms={log.latency_ms}, cost={log.cost}",
            "error_stack": "" if int(log.status_code or 0) < 400 else f"模型调用失败，状态码 {log.status_code}",
        })

    if log_id.startswith("taskstep-"):
        raw_id = log_id.removeprefix("taskstep-")
        log = (
            apply_user_scope(
                TaskStepLog.objects.filter(pk=raw_id).select_related("task", "task__user", "task__llm_config"),
                request.user,
                "task__user_id",
            )
            .first()
        )
        if not log:
            return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "log not found")
        detail = log.detail if isinstance(log.detail, dict) else {}
        return success_api_response({
            "log_id": log_id,
            "user_action": log.step_name,
            "search_intent": getattr(log.task, "title", ""),
            "agent_trace": _serialize_trace_items(
                _detail_list(detail, "agent_trace", "trace", "steps"),
                log.step_name,
                json.dumps(detail, ensure_ascii=False, indent=2) if detail else log.step_status,
            ),
            "prompt_raw": _detail_text(detail, "prompt_raw", "prompt", "request_prompt", "message"),
            "response_raw": _detail_text(detail, "response_raw", "response", "model_response"),
            "error_stack": _detail_text(detail, "error", "error_stack", "stack_trace", "exception"),
        })

    raw_id = log_id.removeprefix("sys-")
    if not is_super_admin(request.user):
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "log not found")
    log = SystemLog.objects.filter(pk=raw_id).first()
    if not log:
        return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "日志不存在")
    return success_api_response({
        "log_id": log_id,
        "user_action": log.message,
        "search_intent": "",
        "agent_trace": [{"step": log.module or "system", "detail": log.message}],
        "prompt_raw": "",
        "response_raw": "",
        "error_stack": log.stack_trace or "",
    })


def _request_data(request: HttpRequest) -> dict:
    if request.content_type and "application/json" in request.content_type:
        try:
            return json.loads(request.body or b"{}")
        except (json.JSONDecodeError, TypeError):
            return {}
    return request.POST


def _export_root() -> Path:
    root = Path(getattr(settings, "REPORT_EXPORT_ROOT", Path(settings.BASE_DIR).parent / "generated_reports"))
    path = (root / EXPORT_ROOT).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _export_path(export_id: str) -> Path:
    safe_id = "".join(ch for ch in str(export_id) if ch.isalnum() or ch in {"-", "_"})
    if safe_id != export_id:
        raise ValueError("非法的导出编号")
    file_path = (_export_root() / f"{safe_id}.csv").resolve()
    if _export_root() != file_path.parent:
        raise ValueError("非法的导出文件路径")
    return file_path


def _write_csv(export_id: str, rows: list[dict]) -> None:
    file_path = _export_path(export_id)
    with open(file_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "log_id",
                "level",
                "module",
                "user_keyword",
                "object_type",
                "model_id",
                "model_name",
                "action_summary",
                "created_at",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


@response_wrapper
@jwt_auth(perms=['analytics.export_audit_log'])
def log_export(request):
    data = _request_data(request)
    if data.get("format", "csv") not in ("csv", "", None):
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "日志导出仅支持 csv 格式")

    logs = _combined_logs(data, request.user)
    export_id = f"logs-{timezone.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
    _write_csv(export_id, logs)
    download_url = f"/api/v1/admin/logs/export/{export_id}/download"
    EXPORT_STATUS_CACHE[export_id] = {
        "export_id": export_id,
        "status": "completed",
        "download_url": download_url,
    }
    return success_api_response({"export_id": export_id, "status": "completed"})


@response_wrapper
@jwt_auth(perms=['analytics.export_audit_log'])
def log_export_status(request, export_id: str):
    status = EXPORT_STATUS_CACHE.get(export_id)
    if status:
        return success_api_response(status)
    try:
        file_path = _export_path(export_id)
    except ValueError:
        return failed_api_response(ErrorCode.INVALID_REQUEST_ARGUMENT_ERROR, "非法的导出编号")
    if file_path.exists():
        return success_api_response({
            "export_id": export_id,
            "status": "completed",
            "download_url": f"/api/v1/admin/logs/export/{export_id}/download",
        })
    return failed_api_response(ErrorCode.ITEM_NOT_FOUND, "导出记录不存在")


@response_wrapper
@jwt_auth(perms=['analytics.export_audit_log'])
def log_export_download(request, export_id: str):
    try:
        file_path = _export_path(export_id)
    except ValueError:
        raise Http404("导出记录不存在")
    if not file_path.exists() or not file_path.is_file():
        raise Http404("导出文件不存在")
    return FileResponse(
        open(file_path, "rb"),
        as_attachment=True,
        filename=f"{export_id}.csv",
        content_type="text/csv; charset=utf-8",
    )


urlpatterns = [
    path('', log_list, name='admin-log-list'),
    path('export', log_export, name='admin-log-export'),
    path('export/', log_export, name='admin-log-export-slash'),
    path('export/<str:export_id>/status', log_export_status, name='admin-log-export-status'),
    path('export/<str:export_id>/status/', log_export_status, name='admin-log-export-status-slash'),
    path('export/<str:export_id>/download', log_export_download, name='admin-log-export-download'),
    path('export/<str:export_id>/download/', log_export_download, name='admin-log-export-download-slash'),
    path('<str:log_id>', log_detail, name='admin-log-detail'),
    path('<str:log_id>/', log_detail, name='admin-log-detail-slash'),
]
