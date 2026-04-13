"""
调研任务业务逻辑 — interface 层
当前阶段仅建骨架，具体的 DeepSearch 爬虫/向量检索逻辑后续由其他人员迭代。
"""
from typing import Tuple, Optional, List

from django.contrib.auth import get_user_model

from research.models.research_task import (
    ResearchTask, STATUS_PENDING, STATUS_CANCELLED
)
from research.models.task_step_log import TaskStepLog


def create_research_task(
    user_id: int, title: str, object_name: str,
    object_type: str, llm_config_id: int = None,
    search_params: dict = None
) -> Tuple[bool, Optional[str], Optional[int]]:
    """创建调研任务

    FR-JSDY-0001: 用户发起调研任务
    Returns:
        (成功与否, 错误消息, task_id)
    """
    User = get_user_model()
    user = User.objects.filter(pk=user_id).first()
    if not user:
        return (False, "用户不存在", None)

    if not title or not object_name or not object_type:
        return (False, "title/object_name/object_type 不能为空", None)

    task = ResearchTask.objects.create(
        user=user,
        title=title,
        object_name=object_name,
        object_type=object_type,
        llm_config_id=llm_config_id,
        search_params=search_params or {},
        status=STATUS_PENDING,
        progress={"searching": 0, "analyzing": 0, "report": 0},
    )

    # 记录首条步骤日志
    TaskStepLog.objects.create(
        task=task,
        step_name="任务已创建",
        step_status="COMPLETED",
        detail={"message": f"调研任务 [{title}] 创建成功，等待 DeepSearch 检索"}
    )

    # TODO: 后续迭代 — 触发异步 DeepSearch 任务（Celery Task）
    return (True, None, task.id)


def get_task_detail(task_id: int) -> Optional[dict]:
    """获取调研任务详情"""
    task = ResearchTask.objects.filter(pk=task_id).first()
    if not task:
        return None

    return {
        "id": task.id,
        "title": task.title,
        "object_name": task.object_name,
        "object_type": task.object_type,
        "status": task.status,
        "progress": task.progress,
        "search_params": task.search_params,
        "llm_config_id": task.llm_config_id,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def list_user_tasks(user_id: int) -> List[dict]:
    """获取用户的所有调研任务列表

    FR-GRXX-0001: 历史调研记录管理
    """
    tasks = ResearchTask.objects.filter(user_id=user_id).values(
        'id', 'title', 'object_name', 'object_type',
        'status', 'created_at'
    )
    return list(tasks)


def cancel_task(task_id: int, user_id: int) -> Tuple[bool, Optional[str]]:
    """取消调研任务"""
    task = ResearchTask.objects.filter(pk=task_id, user_id=user_id).first()
    if not task:
        return (False, "任务不存在或无权操作")
    if task.status in (STATUS_CANCELLED, 'COMPLETED'):
        return (False, "任务已完成或已取消")

    task.status = STATUS_CANCELLED
    task.save()

    TaskStepLog.objects.create(
        task=task,
        step_name="任务已取消",
        step_status="COMPLETED",
        detail={"message": "用户手动取消了调研任务"}
    )
    return (True, None)


def get_task_step_logs(task_id: int) -> List[dict]:
    """获取任务步骤日志（全流程监控）

    FR-JSDY-0003: 全流程可视化监控
    """
    logs = TaskStepLog.objects.filter(task_id=task_id).values(
        'id', 'step_name', 'step_status', 'detail', 
        'is_interactive', 'user_response', 'created_at'
    )
    return list(logs)


def pause_research_task(task_id: int, step_name: str, detail: dict) -> Tuple[bool, Optional[str]]:
    """Agent 请求暂停以等待用户介入"""
    task = ResearchTask.objects.filter(pk=task_id).first()
    if not task:
        return (False, "任务不存在")
    if task.status in (STATUS_CANCELLED, 'COMPLETED', 'FAILED'):
        return (False, "无效的任务状态")

    from research.models.research_task import STATUS_WAITING_USER
    task.status = STATUS_WAITING_USER
    task.save()

    # 创建一个需要交互的步骤日志
    TaskStepLog.objects.create(
        task=task,
        step_name=step_name,
        step_status='PAUSED',
        detail=detail,
        is_interactive=True
    )
    return (True, None)


def respond_to_step(task_id: int, user_id: int, step_id: int, action: str, 
                    response_data: dict = None) -> Tuple[bool, Optional[str]]:
    """用户提供反馈，继续或终止任务"""
    task = ResearchTask.objects.filter(pk=task_id, user_id=user_id).first()
    if not task:
        return (False, "任务不存在或无权操作")
        
    from research.models.research_task import STATUS_WAITING_USER
    if task.status != STATUS_WAITING_USER:
        return (False, "当前任务未处于等待介入状态")

    step = TaskStepLog.objects.filter(pk=step_id, task_id=task_id, is_interactive=True).first()
    if not step:
        return (False, "无效的介入步骤")

    # 记录用户的响应
    step.user_response = {
        "action": action,
        "data": response_data or {}
    }
    step.step_status = 'COMPLETED'
    step.save()

    if action == 'CANCEL':
        task.status = STATUS_CANCELLED
        task.save()
        TaskStepLog.objects.create(
            task=task, step_name="任务中止", step_status="COMPLETED",
            detail={"message": "用户在介入时选择中止任务"}
        )
    else:
        # TODO: 根据 action 决定恢复到 SEARCHING 还是 ANALYZING，这里先简单设为 SEARCHING
        task.status = 'SEARCHING' 
        task.save()
        TaskStepLog.objects.create(
            task=task, step_name="恢复运行", step_status="RUNNING",
            detail={"message": "接收到用户反馈，任务继续执行", "action": action}
        )
        # TODO: 真正唤醒 Celery 任务继续往下走

    return (True, None)
