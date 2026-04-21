"""
报告异步任务
"""
from celery import shared_task

from reports.interface.report_interface import run_export_job


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def export_report_task(self, export_id: int):
    """异步执行报告导出。"""
    return run_export_job(export_id)
