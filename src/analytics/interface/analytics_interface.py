"""
统计与日志业务逻辑 — interface 层
"""
from datetime import datetime, timedelta, time
from typing import List, Optional, Tuple

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, F
from django.db.models.functions import TruncDate
from django.utils import timezone

from analytics.models.logs import OperationLog, LLMCallLog
from analytics.models.personalization import Favorite, Alert
from users.scope import apply_user_scope


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


def get_dashboard_stats(start_time=None, end_time=None, user=None) -> dict:
    """获取综合统计看板数据

    FR-SJGL-0003: 多维度数据统计
    FR-SJGL-0005: 成本审计 (Token/费用)
    """
    from django.db.models import Sum, Count, Avg
    from django.utils import timezone
    from datetime import timedelta
    from research.models.research_task import ResearchTask
    from llm_manager.models.model_usage import ModelUsage
    
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)

    # 1. 调研任务统计
    primary_tasks = apply_user_scope(
        ResearchTask.objects.filter(parent_task__isnull=True),
        user,
    )
    if start_time:
        primary_tasks = primary_tasks.filter(created_at__gte=start_time)
    if end_time:
        primary_tasks = primary_tasks.filter(created_at__lte=end_time)
    total_tasks = primary_tasks.count()
    type_stats = list(
        primary_tasks.values('object_type')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    # 2. 模型使用及成本统计 (基于 ModelUsage)
    usage_query = apply_user_scope(ModelUsage.objects.all(), user)
    if start_time:
        usage_query = usage_query.filter(created_at__gte=start_time)
    if end_time:
        usage_query = usage_query.filter(created_at__lte=end_time)

    llm_summary = usage_query.aggregate(
        total_tokens=Sum('total_tokens'),
        total_cost=Sum('cost'),
        avg_latency=Avg('latency_ms')
    )

    # 模型调用排行 (按 Token 消耗)
    llm_usage_ranking = list(
        usage_query.annotate(
            model_name=F('model_name_snapshot'),
            provider=F('provider_snapshot'),
        )
        .values('llm_config_id', 'model_name', 'provider')
        .annotate(
            tokens=Sum('total_tokens'),
            cost=Sum('cost'),
            calls=Count('id')
        )
        .order_by('-calls', '-tokens')[:10]
    )

    # 3. 趋势统计 (近 30 天)
    daily_stats = list(
        (usage_query if (start_time or end_time) else usage_query.filter(created_at__gte=thirty_days_ago))
        .annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(
            tokens=Sum('total_tokens'),
            cost=Sum('cost'),
            calls=Count('id')
        )
        .order_by('date')
    )

    # 4. 用户活跃度
    operation_query = apply_user_scope(OperationLog.objects.all(), user)
    if start_time:
        operation_query = operation_query.filter(created_at__gte=start_time)
    if end_time:
        operation_query = operation_query.filter(created_at__lte=end_time)
    if not start_time and not end_time:
        operation_query = operation_query.filter(created_at__gte=thirty_days_ago)
    operation_log_total = operation_query.count()
    active_user_total = operation_query.exclude(user_id__isnull=True).values('user_id').distinct().count()

    user_activity = list(
        operation_query
        .annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(count=Count('user_id', distinct=True))
        .order_by('date')
    )

    return {
        "summary": {
            "total_research_tasks": total_tasks,
            "total_tokens_consumed": llm_summary['total_tokens'] or 0,
            "total_cost_yuan": float(llm_summary['total_cost'] or 0),
            "avg_latency_ms": round(llm_summary['avg_latency'] or 0, 2),
            "operation_log_total": operation_log_total,
            "active_user_total": active_user_total,
        },
        "type_distribution": type_stats,
        "llm_usage_ranking": llm_usage_ranking,
        "trends": {
            "daily_llm_usage": daily_stats,
            "daily_active_ops": user_activity
        }
    }


def add_favorite(user_id: int, item_type: str, item_id: str, remark: str = "") -> bool:
    """添加收藏"""
    normalized_remark = str(remark or "").strip()
    favorite, created = Favorite.objects.get_or_create(
        user_id=user_id,
        item_type=item_type,
        item_id=str(item_id),
        defaults={"remark": normalized_remark},
    )
    if not created and normalized_remark and favorite.remark != normalized_remark:
        favorite.remark = normalized_remark
        favorite.save(update_fields=["remark"])
    return created


def remove_favorite(user_id: int, item_type: str, item_id: str) -> bool:
    """取消收藏"""
    deleted, _ = Favorite.objects.filter(
        user_id=user_id, item_type=item_type, item_id=str(item_id)
    ).delete()
    return deleted > 0


def remove_favorites_batch(user_id: int, favorite_ids: List) -> Tuple[int, List[str]]:
    """批量取消收藏，返回 (删除数量, 被删除项的目标资源ID列表)。

    仅删除归属当前用户的收藏项，避免越权删除。
    """
    normalized_ids = [str(fid).strip() for fid in (favorite_ids or []) if str(fid).strip().isdigit()]
    if not normalized_ids:
        return 0, []
    query = Favorite.objects.filter(user_id=user_id, pk__in=[int(fid) for fid in normalized_ids])
    target_ids = [str(item_id) for item_id in query.values_list('item_id', flat=True)]
    deleted, _ = query.delete()
    return deleted, target_ids


def list_favorites(user_id: int, item_type: str = None) -> List[dict]:
    """获取收藏列表（含 调研信息 INFO / 报告 REPORT / 大模型 MODEL 三类）"""
    query = Favorite.objects.filter(user_id=user_id)
    if item_type:
        query = query.filter(item_type=item_type)
    return list(query.order_by('-created_at').values('id', 'item_type', 'item_id', 'remark', 'created_at'))


SCHEDULE_DAILY = 'daily'
SCHEDULE_WEEKLY = 'weekly'
VALID_SCHEDULE_RULES = {SCHEDULE_DAILY, SCHEDULE_WEEKLY}
DEFAULT_ALERT_TIME = time(hour=9, minute=0)


def _parse_schedule_time(value: str | None) -> time:
    text = str(value or '').strip()
    if not text:
        return DEFAULT_ALERT_TIME
    try:
        hour_text, minute_text = text.split(':', 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour=hour, minute=minute)
    except (TypeError, ValueError):
        pass
    return DEFAULT_ALERT_TIME


def _schedule_time_text(schedule_time: time) -> str:
    return f"{schedule_time.hour:02d}:{schedule_time.minute:02d}"


def _normalize_schedule_rule(value: str | None) -> str:
    normalized = str(value or SCHEDULE_DAILY).strip().lower()
    return normalized if normalized in VALID_SCHEDULE_RULES else SCHEDULE_DAILY


def _condition_schedule_time(condition: dict | None) -> time:
    return _parse_schedule_time((condition or {}).get('schedule_time'))


def _combine_local(dt_date, schedule_time: time):
    return timezone.make_aware(
        datetime.combine(dt_date, schedule_time),
        timezone.get_current_timezone(),
    )


def next_run_after(
    base_time=None,
    *,
    schedule_rule: str = SCHEDULE_DAILY,
    schedule_time: time | None = None,
):
    """Return the next due time after base_time using local timezone semantics."""
    base = timezone.localtime(base_time or timezone.now())
    target_time = schedule_time or DEFAULT_ALERT_TIME
    normalized_rule = _normalize_schedule_rule(schedule_rule)
    candidate = _combine_local(base.date(), target_time)

    if normalized_rule == SCHEDULE_WEEKLY:
        while candidate <= base:
            candidate += timedelta(days=7)
        return candidate

    if candidate <= base:
        candidate += timedelta(days=1)
    return candidate


def following_run_after(alert: Alert, base_time=None):
    condition = alert.condition or {}
    return next_run_after(
        base_time or timezone.now(),
        schedule_rule=condition.get('schedule_rule'),
        schedule_time=_condition_schedule_time(condition),
    )


def create_alert(user_id: int, object_type: str, object_name: str,
                 condition: dict = None, notify_email: bool = True,
                 notify_in_app: bool = True) -> int:
    """创建动态提醒"""
    normalized_condition = condition or {}
    normalized_condition["schedule_rule"] = _normalize_schedule_rule(normalized_condition.get("schedule_rule"))
    schedule_time = _condition_schedule_time(normalized_condition)
    normalized_condition["schedule_time"] = _schedule_time_text(schedule_time)
    alert = Alert.objects.create(
        user_id=user_id,
        object_type=object_type,
        object_name=object_name,
        condition=normalized_condition,
        notify_email=notify_email,
        notify_in_app=notify_in_app,
        next_run_at=next_run_after(
            timezone.now(),
            schedule_rule=normalized_condition["schedule_rule"],
            schedule_time=schedule_time,
        ),
    )
    return alert.id


from analytics.models.personalization import Favorite, Alert, UserMessage


def create_user_message(user_id: int, title: str, content: str, 
                        msg_type: str = 'ALERT', alert_id: int = None,
                        action_url: str = '') -> int:
    """创建一条站内消息"""
    msg = UserMessage.objects.create(
        user_id=user_id,
        title=title,
        content=content,
        message_type=msg_type,
        source_alert_id=alert_id,
        action_url=action_url or '',
    )
    return msg.id


def list_user_messages(user_id: int, only_unread: bool = False) -> List[dict]:
    """获取用户的站内消息列表"""
    query = UserMessage.objects.filter(user_id=user_id)
    if only_unread:
        query = query.filter(is_read=False)
    
    return list(query.values(
        'id', 'title', 'content', 'message_type', 
        'is_read', 'created_at', 'action_url'
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
        
        alert.last_triggered_at = timezone.now()
        alert.save()


def _alert_title(alert: Alert) -> str:
    return f"{alert.object_name} 定时调研"


def _alert_search_params(alert: Alert) -> dict:
    condition = alert.condition or {}
    search_params = condition.get('search_params')
    if not isinstance(search_params, dict):
        search_params = {}
    return {
        **search_params,
        "time_range": condition.get("time_range", search_params.get("time_range", "30d")),
        "source_authority": condition.get("source_authority", search_params.get("source_authority", "unrestricted")),
        "source_types": condition.get("source_types", search_params.get("source_types", ["official", "data", "research", "news"])),
        "user_source_requirements": {
            **(search_params.get("user_source_requirements") if isinstance(search_params.get("user_source_requirements"), dict) else {}),
            "time_range": condition.get("time_range", "30d"),
            "source_authority": condition.get("source_authority", "unrestricted"),
            "source_types": condition.get("source_types", ["official", "data", "research", "news"]),
            "research_focus": condition.get("research_focus", ["overview"]),
        },
        "auto_advance": True,
        "source_alert_id": alert.id,
        "triggered_by_alert": True,
    }


def trigger_alert_research(alert: Alert, *, triggered_at=None, advance_next_run: bool = True) -> Tuple[bool, str, Optional[int]]:
    """Create a research task for an alert and update its schedule bookkeeping."""
    from research.interface.research_interface import create_research_task

    fired_at = triggered_at or timezone.now()
    condition = alert.condition or {}
    success, message, task_id = create_research_task(
        user_id=alert.user_id,
        title=_alert_title(alert),
        object_name=alert.object_name,
        object_type=alert.object_type,
        model_id=condition.get("model_id") or None,
        search_params=_alert_search_params(alert),
    )
    if not success:
        return False, message or "创建提醒调研任务失败", None

    alert.last_triggered_at = fired_at
    alert.last_task_id = task_id
    if advance_next_run:
        alert.next_run_at = following_run_after(alert, fired_at)
    alert.save(update_fields=['last_triggered_at', 'last_task', 'next_run_at'])

    if alert.notify_in_app:
        task_url = f"/process?task_id={task_id}"
        create_user_message(
            user_id=alert.user_id,
            title=f"{alert.object_name} 定时调研已启动",
            content=f"系统已按提醒设置启动新的调研任务，任务编号：{task_id}。报告生成后会继续通知。",
            msg_type='ALERT',
            alert_id=alert.id,
            action_url=task_url,
        )

    return True, "", task_id


def trigger_alert_now(alert: Alert) -> Tuple[bool, str, Optional[int]]:
    """Trigger immediately, then pin future daily/weekly runs to this time of day."""
    fired_at = timezone.now()
    schedule_time = timezone.localtime(fired_at).time().replace(second=0, microsecond=0)
    condition = {
        **(alert.condition or {}),
        "schedule_rule": _normalize_schedule_rule((alert.condition or {}).get("schedule_rule")),
        "schedule_time": _schedule_time_text(schedule_time),
    }
    alert.condition = condition
    alert.next_run_at = next_run_after(
        fired_at,
        schedule_rule=condition["schedule_rule"],
        schedule_time=schedule_time,
    )
    alert.save(update_fields=['condition', 'next_run_at'])
    return trigger_alert_research(alert, triggered_at=fired_at, advance_next_run=True)


def trigger_due_alerts(now=None) -> int:
    """Run all due active alerts. Intended for Celery beat."""
    due_time = now or timezone.now()
    triggered_count = 0
    due_alerts = (
        Alert.objects
        .filter(is_active=True, next_run_at__lte=due_time)
        .select_related('user')
        .order_by('next_run_at', 'id')
    )
    for alert in due_alerts:
        with transaction.atomic():
            locked = Alert.objects.select_for_update().select_related('user').get(pk=alert.pk)
            if not locked.is_active or not locked.next_run_at or locked.next_run_at > due_time:
                continue
            success, _, _ = trigger_alert_research(locked, triggered_at=due_time, advance_next_run=True)
            if success:
                triggered_count += 1
    return triggered_count


def _report_url(task_id: int, report_id: int) -> str:
    return f"/report?task_id={task_id}&report_id={report_id}"


def _absolute_frontend_url(path: str) -> str:
    base_url = getattr(settings, "FRONTEND_BASE_URL", "") or getattr(settings, "SITE_URL", "")
    return f"{base_url.rstrip('/')}{path}" if base_url else path


def dispatch_alert_report_ready(report) -> int:
    """Notify the alert owner once an alert-triggered task has generated a report."""
    task = getattr(report, 'task', None)
    if task is None:
        return 0
    alert_id = (task.search_params or {}).get("source_alert_id")
    if not alert_id:
        return 0
    alert = Alert.objects.filter(pk=alert_id, user_id=task.user_id).select_related('user').first()
    if not alert:
        return 0

    url = _report_url(task.id, report.id)
    notified = 0
    if alert.notify_in_app:
        create_user_message(
            user_id=alert.user_id,
            title=f"{alert.object_name} 定时调研报告已生成",
            content=f"提醒任务已完成并生成报告《{report.title}》。点击查看报告页面。",
            msg_type='ALERT',
            alert_id=alert.id,
            action_url=url,
        )
        notified += 1

    if alert.notify_email and alert.user.email:
        try:
            send_mail(
                subject=f"8Feet 定时调研报告已生成：{alert.object_name}",
                message=(
                    f"您设置的 {alert.object_name} 定时调研报告已生成。\n\n"
                    f"报告标题：{report.title}\n"
                    f"查看报告：{_absolute_frontend_url(url)}"
                ),
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[alert.user.email],
                fail_silently=False,
            )
            notified += 1
        except Exception:
            create_user_message(
                user_id=alert.user_id,
                title=f"{alert.object_name} 报告邮件发送失败",
                content="报告已生成，但邮件发送失败。请在站内消息中打开报告。",
                msg_type='ALERT',
                alert_id=alert.id,
                action_url=url,
            )
    return notified


def list_alerts(user_id: int) -> List[dict]:
    """获取用户提醒列表"""
    return list(Alert.objects.filter(user_id=user_id).values(
        'id', 'object_type', 'object_name', 'condition',
        'notify_email', 'notify_in_app', 'is_active', 'last_triggered_at',
        'next_run_at', 'last_task_id', 'created_at'
    ))


def get_cost_report(start_date: str = None, end_date: str = None, user=None) -> List[dict]:
    """获取成本审计报表 (按用户维度)
    
    FR-SJGL-0005: 成本审计 (Token 消耗流水、分用户成本报表)
    """
    from django.db.models import Sum, Count
    from llm_manager.models.model_usage import ModelUsage

    query = apply_user_scope(ModelUsage.objects.all(), user)
    if start_date:
        query = query.filter(created_at__date__gte=start_date)
    if end_date:
        query = query.filter(created_at__date__lte=end_date)

    report = list(
        query.values('user__username', 'user__id')
        .annotate(
            total_tokens=Sum('total_tokens'),
            total_cost=Sum('cost'),
            total_calls=Count('id')
        )
        .order_by('-total_cost')
    )
    return report
