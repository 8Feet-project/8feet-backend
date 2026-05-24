"""
站内消息 API
映射需求: FR-GRXX-0004
"""
from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

from shared.utils import (
    ErrorCode, failed_api_response, response_wrapper,
    success_api_response, jwt_auth
)
from analytics.interface.analytics_interface import (
    list_user_messages, mark_messages_as_read
)


def _serialize_message(item: dict) -> dict:
    return {
        "message_id": str(item.get("id")),
        "title": item.get("title") or "",
        "content": item.get("content") or "",
        "action_url": item.get("action_url") or "",
        "read_status": bool(item.get("is_read")),
        "created_at": item.get("created_at").isoformat() if hasattr(item.get("created_at"), "isoformat") else str(item.get("created_at") or ""),
    }


@response_wrapper
@require_GET
@jwt_auth()
def message_list(request: HttpRequest):
    """获取消息列表
    [route]: GET /api/v1/messages/
    """
    only_unread = request.GET.get('only_unread', 'false').lower() == 'true'
    messages = list_user_messages(request.user.id, only_unread)
    
    unread_count = sum(1 for m in messages if not m['is_read'])
    serialized = [_serialize_message(item) for item in messages]
    
    return success_api_response({
        "list": serialized,
        "unread_count": unread_count,
        "total": len(serialized)
    })


@response_wrapper
@require_POST
@jwt_auth()
def mark_read(request: HttpRequest, message_id: int = None):
    """标记消息为已读
    [route]: POST /api/v1/messages/{id}/read
    """
    ids = [message_id] if message_id else None
    count = mark_messages_as_read(request.user.id, ids)
    
    return success_api_response({
        "message_id": str(message_id) if message_id else "",
        "read_status": True,
        "marked_count": count,
    })


@response_wrapper
@require_POST
@jwt_auth()
def mark_all_read(request: HttpRequest):
    """全部标记为已读
    [route]: POST /api/v1/messages/read-all
    """
    count = mark_messages_as_read(request.user.id)
    return success_api_response({"affected_count": count, "marked_count": count})
