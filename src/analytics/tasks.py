"""
提醒调度异步任务
"""
from celery import shared_task

from analytics.interface.analytics_interface import trigger_due_alerts


@shared_task(bind=True)
def dispatch_due_alerts_task(self):
    """扫描到期提醒并启动对应调研任务。"""
    return trigger_due_alerts()
