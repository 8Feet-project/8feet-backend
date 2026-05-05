"""
调研任务异步执行与日志包装器
"""
from __future__ import annotations

import threading
import traceback
import uuid
from contextlib import contextmanager
from typing import Optional

from django.db import close_old_connections
from django.db.models import Max
from django.utils import timezone

from research.models.research_task import (
    DISPATCH_FAILED,
    DISPATCH_FINISHED,
    DISPATCH_PENDING,
    DISPATCH_QUEUED,
    DISPATCH_RUNNING,
    DISPATCH_STARTING,
    STATUS_ANALYZING,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SEARCHING,
    ResearchTask,
)
from research.models.task_step_log import TaskStepLog


def _next_sequence(task_id: int) -> int:
    current = TaskStepLog.objects.filter(task_id=task_id).aggregate(max_sequence=Max('sequence'))['max_sequence']
    return (current or 0) + 1


def append_task_log(
    task: ResearchTask,
    step_name: str,
    step_status: str,
    *,
    step_code: Optional[str] = None,
    detail: Optional[dict] = None,
    error_message: Optional[str] = None,
    is_interactive: bool = False,
) -> TaskStepLog:
    now = timezone.now()
    finished_at = now if step_status in ('COMPLETED', 'FAILED', 'SKIPPED', 'PAUSED') else None
    return TaskStepLog.objects.create(
        task=task,
        step_code=step_code,
        sequence=_next_sequence(task.id),
        step_name=step_name,
        step_status=step_status,
        detail=detail or {},
        error_message=error_message,
        is_interactive=is_interactive,
        started_at=now,
        finished_at=finished_at,
    )


@contextmanager
def logged_task_step(task: ResearchTask, step_code: str, step_name: str, detail: Optional[dict] = None):
    step = append_task_log(task, step_name, 'RUNNING', step_code=step_code, detail=detail or {})
    started_at = step.started_at
    try:
        yield step
        step.step_status = 'COMPLETED'
        step.finished_at = timezone.now()
        payload = dict(step.detail or {})
        payload.setdefault('updated_at', step.finished_at.isoformat())
        payload.setdefault('finished_at', step.finished_at.isoformat())
        payload.setdefault('duration_ms', max(int((step.finished_at - started_at).total_seconds() * 1000), 0))
        step.detail = payload
        step.save(update_fields=['step_status', 'finished_at', 'detail'])
    except Exception as exc:
        step.step_status = 'FAILED'
        step.finished_at = timezone.now()
        payload = dict(step.detail or {})
        payload['updated_at'] = step.finished_at.isoformat()
        payload['finished_at'] = step.finished_at.isoformat()
        payload['duration_ms'] = max(int((step.finished_at - started_at).total_seconds() * 1000), 0)
        payload['traceback'] = traceback.format_exc()
        step.detail = payload
        step.error_message = str(exc)
        step.save(update_fields=['step_status', 'finished_at', 'detail', 'error_message'])
        raise


class ResearchTaskRunner:
    """调研任务轻量执行器。"""

    @classmethod
    def enqueue(cls, task_id: int) -> bool:
        task = ResearchTask.objects.filter(pk=task_id).first()
        if not task:
            return False
        if task.dispatch_status in (DISPATCH_QUEUED, DISPATCH_STARTING, DISPATCH_RUNNING):
            return False

        now = timezone.now()
        task.dispatch_status = DISPATCH_QUEUED
        task.queued_at = now
        task.last_error = None
        task.save(update_fields=['dispatch_status', 'queued_at', 'last_error', 'updated_at'])
        append_task_log(
            task,
            '任务已提交调度',
            'COMPLETED',
            step_code='dispatch_queued',
            detail={'message': '任务已提交后台异步调度', 'queued_at': now.isoformat()},
        )

        thread = threading.Thread(target=cls.run_task, args=(task_id,), name=f'research-task-{task_id}', daemon=True)
        thread.start()
        return True

    @classmethod
    def acquire_execution(cls, task_id: int) -> Optional[str]:
        task = ResearchTask.objects.filter(pk=task_id).first()
        if not task:
            return None
        if task.status in (STATUS_COMPLETED, STATUS_CANCELLED):
            return None
        if task.dispatch_status == DISPATCH_RUNNING:
            return None

        token = uuid.uuid4().hex
        updated = ResearchTask.objects.filter(
            pk=task_id,
            dispatch_status__in=[DISPATCH_QUEUED, DISPATCH_PENDING, DISPATCH_FAILED, DISPATCH_STARTING],
        ).update(
            dispatch_status=DISPATCH_STARTING,
            runner_token=token,
            started_at=timezone.now(),
            retry_count=task.retry_count + 1,
            last_error=None,
            updated_at=timezone.now(),
        )
        if not updated:
            return None
        return token

    @classmethod
    def run_task(cls, task_id: int) -> None:
        close_old_connections()
        token = cls.acquire_execution(task_id)
        if not token:
            close_old_connections()
            return

        try:
            task = ResearchTask.objects.get(pk=task_id)
            append_task_log(
                task,
                '异步执行启动',
                'COMPLETED',
                step_code='dispatch_started',
                detail={'message': '后台执行线程已启动', 'runner_token': token},
            )
            task.dispatch_status = DISPATCH_RUNNING
            task.save(update_fields=['dispatch_status', 'updated_at'])

            cls._run_pipeline(task_id, token)
        except Exception as exc:
            task = ResearchTask.objects.filter(pk=task_id).first()
            if task:
                task.status = STATUS_FAILED
                task.dispatch_status = DISPATCH_FAILED
                task.finished_at = timezone.now()
                task.last_error = str(exc)
                task.save(update_fields=['status', 'dispatch_status', 'finished_at', 'last_error', 'updated_at'])
                append_task_log(
                    task,
                    '任务执行失败',
                    'FAILED',
                    step_code='task_failed',
                    detail={'message': '后台执行失败', 'traceback': traceback.format_exc()},
                    error_message=str(exc),
                )
        finally:
            close_old_connections()

    @classmethod
    def _run_pipeline(cls, task_id: int, token: str) -> None:
        task = ResearchTask.objects.get(pk=task_id)
        cls._ensure_token(task, token)

        task.status = STATUS_SEARCHING
        task.progress = {'searching': 0, 'analyzing': 0, 'report': 0}
        task.save(update_fields=['status', 'progress', 'updated_at'])

        with logged_task_step(task, 'search_start', '检索开始', {'message': '开始执行检索阶段'}) as step:
            task.progress = {'searching': 40, 'analyzing': 0, 'report': 0}
            task.save(update_fields=['progress', 'updated_at'])
            step.detail = {'message': '已进入检索阶段', 'metrics': {'progress': 40}}
            step.save(update_fields=['detail'])

        with logged_task_step(task, 'search_finish', '检索完成', {'message': '检索阶段完成'}) as step:
            task.progress = {'searching': 100, 'analyzing': 0, 'report': 0}
            task.save(update_fields=['progress', 'updated_at'])
            step.detail = {'message': '检索阶段完成，待进入分析', 'metrics': {'progress': 100}}
            step.save(update_fields=['detail'])

        cls._ensure_not_cancelled(task_id, token)
        task = ResearchTask.objects.get(pk=task_id)
        task.status = STATUS_ANALYZING
        task.save(update_fields=['status', 'updated_at'])

        with logged_task_step(task, 'analysis_start', '分析开始', {'message': '开始执行分析阶段'}) as step:
            task.progress = {'searching': 100, 'analyzing': 50, 'report': 0}
            task.save(update_fields=['progress', 'updated_at'])
            step.detail = {'message': '分析阶段执行中', 'metrics': {'progress': 50}}
            step.save(update_fields=['detail'])

        with logged_task_step(task, 'analysis_finish', '分析完成', {'message': '分析阶段完成'}) as step:
            task.progress = {'searching': 100, 'analyzing': 100, 'report': 50}
            task.save(update_fields=['progress', 'updated_at'])
            step.detail = {'message': '分析阶段完成，准备生成结果', 'metrics': {'progress': 100}}
            step.save(update_fields=['detail'])

        with logged_task_step(task, 'report_finalize', '结果整理完成', {'message': '已完成占位结果整理'}) as step:
            task.progress = {'searching': 100, 'analyzing': 100, 'report': 100}
            task.save(update_fields=['progress', 'updated_at'])
            step.detail = {'message': '已完成占位报告整理', 'metrics': {'progress': 100}}
            step.save(update_fields=['detail'])

        cls._complete_task(task_id, token)

    @classmethod
    def _complete_task(cls, task_id: int, token: str) -> None:
        task = ResearchTask.objects.get(pk=task_id)
        cls._ensure_token(task, token)
        now = timezone.now()
        task.status = STATUS_COMPLETED
        task.dispatch_status = DISPATCH_FINISHED
        task.finished_at = now
        task.last_error = None
        task.progress = {'searching': 100, 'analyzing': 100, 'report': 100}
        task.save(update_fields=['status', 'dispatch_status', 'finished_at', 'last_error', 'progress', 'updated_at'])
        append_task_log(
            task,
            '任务执行完成',
            'COMPLETED',
            step_code='task_completed',
            detail={'message': '调研任务已完成', 'finished_at': now.isoformat()},
        )

    @classmethod
    def recover_incomplete_tasks(cls) -> int:
        tasks = ResearchTask.objects.filter(
            status__in=[STATUS_PENDING, STATUS_SEARCHING, STATUS_ANALYZING, STATUS_FAILED],
            dispatch_status__in=[DISPATCH_PENDING, DISPATCH_QUEUED, DISPATCH_STARTING, DISPATCH_FAILED],
        ).exclude(status=STATUS_CANCELLED)

        recovered = 0
        for task in tasks:
            if cls.enqueue(task.id):
                recovered += 1
        return recovered

    @staticmethod
    def _ensure_not_cancelled(task_id: int, token: str) -> None:
        task = ResearchTask.objects.get(pk=task_id)
        ResearchTaskRunner._ensure_token(task, token)
        if task.status == STATUS_CANCELLED:
            raise RuntimeError('任务已取消，停止继续执行')

    @staticmethod
    def _ensure_token(task: ResearchTask, token: str) -> None:
        if task.runner_token != token:
            raise RuntimeError('任务执行令牌已失效，疑似被新的执行实例接管')
