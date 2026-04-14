"""
统计与日志业务逻辑 — interface 层
"""
from typing import List, Optional
from datetime import datetime

from django.db.models import Count
from django.db.models.functions import TruncDate

from analytics.models.logs import OperationLog, LLMCallLog
from analytics.models.personalization import Favorite, Alert


def log_operation(user_id: int, action_type: str, target_module: str,
                  target_id: int = None, detail: dict = None,
                  ip_address: str = None):
    """记录用户操作日志"""
    OperationLog.objects.create(
        user_id=user_id,
        action_type=action_type,
        target_module=target_module,
        target_id=target_id,
        detail=detail or {},
        ip_address=ip_address,
    )


def get_dashboard_stats() -> dict:
    """获取统计看板数据

    FR-SJGL-0003: 全局调研请求量、各对象调研频次、大模型调用量排行、用户活跃度
    """
    from research.models.research_task import ResearchTask

    # 全局调研请求量
    total_tasks = ResearchTask.objects.count()

    # 各对象类型调研频次
    type_stats = list(
        ResearchTask.objects.values('object_type')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    # 大模型调用量排行
    llm_stats = list(
        LLMCallLog.objects.values('llm_config__name')
        .annotate(call_count=Count('id'))
        .order_by('-call_count')[:10]
    )

    # 用户活跃度 (近 30 天每日操作数)
    from django.utils import timezone
    from datetime import timedelta
    thirty_days_ago = timezone.now() - timedelta(days=30)
    daily_active = list(
        OperationLog.objects.filter(created_at__gte=thirty_days_ago)
        .annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(count=Count('id'))
        .order_by('date')
    )

    return {
        "total_tasks": total_tasks,
        "type_stats": type_stats,
        "llm_call_ranking": llm_stats,
        "daily_active_ops": daily_active,
    }


def add_favorite(user_id: int, item_type: str, item_id: int,
                 folder: str = None) -> bool:
    """添加收藏"""
    _, created = Favorite.objects.get_or_create(
        user_id=user_id, item_type=item_type, item_id=item_id,
        defaults={'folder': folder}
    )
    return created


def remove_favorite(user_id: int, item_type: str, item_id: int) -> bool:
    """取消收藏"""
    deleted, _ = Favorite.objects.filter(
        user_id=user_id, item_type=item_type, item_id=item_id
    ).delete()
    return deleted > 0


def list_favorites(user_id: int, item_type: str = None) -> List[dict]:
    """获取收藏列表"""
    query = Favorite.objects.filter(user_id=user_id)
    if item_type:
        query = query.filter(item_type=item_type)
    return list(query.values('id', 'item_type', 'item_id', 'folder', 'created_at'))


def create_alert(user_id: int, object_type: str, object_name: str,
                 condition: dict = None, notify_email: bool = True) -> int:
    """创建动态提醒"""
    alert = Alert.objects.create(
        user_id=user_id,
        object_type=object_type,
        object_name=object_name,
        condition=condition or {},
        notify_email=notify_email,
    )
    return alert.id


from analytics.models.personalization import Favorite, Alert, UserMessage


def create_user_message(user_id: int, title: str, content: str, 
                        msg_type: str = 'ALERT', alert_id: int = None) -> int:
    """创建一条站内消息"""
    msg = UserMessage.objects.create(
        user_id=user_id,
        title=title,
        content=content,
        message_type=msg_type,
        source_alert_id=alert_id
    )
    return msg.id


def list_user_messages(user_id: int, only_unread: bool = False) -> List[dict]:
    """获取用户的站内消息列表"""
    query = UserMessage.objects.filter(user_id=user_id)
    if only_unread:
        query = query.filter(is_read=False)
    
    return list(query.values(
        'id', 'title', 'content', 'message_type', 
        'is_read', 'created_at'
    ))


def mark_messages_as_read(user_id: int, message_ids: List[int] = None) -> int:
    """标记消息为已读"""
    query = UserMessage.objects.filter(user_id=user_id, is_read=False)
    if message_ids:
        query = query.filter(id__in=message_ids)
    
    count = query.update(is_read=True)
    return count


def check_and_dispatch_alerts(object_type: str, object_name: str, 
                              info_title: str, info_id: int):
    """(核心) 检查新内容是否匹配任何用户的订阅提醒
    
    当系统抓取到新信息(ScrapedContent)或生成新报告时触发。
    """
    # 查找匹配的活跃提醒设置
    matched_alerts = Alert.objects.filter(
        object_type=object_type,
        object_name__icontains=object_name,
        is_active=True
    ).select_related('user')

    for alert in matched_alerts:
        # 生成通知消息
        msg_title = f"您关注的{alert.get_object_type_display()} [{alert.object_name}] 有更新"
        msg_content = f"系统检测到新信息: {info_title}。点击查看详情。"
        
        create_user_message(
            user_id=alert.user.id,
            title=msg_title,
            content=msg_content,
            alert_id=alert.id
        )
        
        # 更新触发时间
        from django.utils import timezone
        alert.last_triggered_at = timezone.now()
        alert.save()


def list_alerts(user_id: int) -> List[dict]:
    """获取用户提醒列表"""
    return list(Alert.objects.filter(user_id=user_id).values(
        'id', 'object_type', 'object_name', 'condition',
        'is_active', 'last_triggered_at', 'created_at'
    ))
