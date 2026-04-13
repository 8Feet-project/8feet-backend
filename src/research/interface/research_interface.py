"""
调研任务业务逻辑 — interface 层
当前阶段仅建骨架，具体的 DeepSearch 爬虫/向量检索逻辑后续由其他人员迭代。
"""
from typing import Tuple, Optional, List

from django.contrib.auth import get_user_model

from research.models.research_task import (
    OBJECT_TYPE_COMPANY,
    OBJECT_TYPE_PRODUCT,
    OBJECT_TYPE_STOCK,
    ResearchTask,
    STATUS_ANALYZING,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SEARCHING,
    STATUS_WAITING_USER,
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


def _map_object_type(object_type: Optional[str]) -> str:
    mapping = {
        OBJECT_TYPE_COMPANY: 'company',
        OBJECT_TYPE_STOCK: 'stock',
        OBJECT_TYPE_PRODUCT: 'commodity',
        'COMPANY': 'company',
        'STOCK': 'stock',
        'PRODUCT': 'commodity',
    }
    return mapping.get(object_type or '', 'company')



def _map_task_status(status: Optional[str]) -> str:
    mapping = {
        STATUS_PENDING: 'pending',
        STATUS_SEARCHING: 'searching',
        STATUS_ANALYZING: 'analyzing',
        STATUS_WAITING_USER: 'waiting_user',
        STATUS_COMPLETED: 'completed',
        STATUS_FAILED: 'failed',
        STATUS_CANCELLED: 'cancelled',
        'DATA_READY': 'data_ready',
    }
    return mapping.get(status or '', 'pending')



def _map_step_status(step_status: Optional[str], is_interactive: bool = False) -> str:
    mapping = {
        'RUNNING': 'running',
        'COMPLETED': 'completed',
        'FAILED': 'failed',
        'PAUSED': 'waiting_user' if is_interactive else 'pending',
        'SKIPPED': 'skipped',
    }
    return mapping.get(step_status or '', 'pending')



def _build_node_metrics(detail: Optional[dict]) -> List[dict]:
    if not isinstance(detail, dict):
        return []

    metrics = []
    for key, value in detail.get('metrics', {}).items():
        metrics.append({'label': key, 'value': value})

    if 'processed_count' in detail:
        metrics.append({'label': 'processed_count', 'value': detail['processed_count']})
    if 'duration_ms' in detail:
        metrics.append({'label': 'duration_ms', 'value': detail['duration_ms']})
    return metrics



def _serialize_step_log(log: TaskStepLog) -> dict:
    detail = log.detail if isinstance(log.detail, dict) else {}
    return {
        'node_id': str(log.id),
        'node_name': log.step_name,
        'node_type': detail.get('node_type', 'generic'),
        'node_status': _map_step_status(log.step_status, log.is_interactive),
        'description': detail.get('message') or detail.get('description', ''),
        'summary': detail.get('summary') or detail.get('message', ''),
        'started_at': log.created_at.isoformat(),
        'finished_at': detail.get('finished_at'),
        'updated_at': detail.get('updated_at', log.created_at.isoformat()),
        'duration_ms': detail.get('duration_ms'),
        'can_intervene': log.is_interactive and log.step_status == 'PAUSED',
        'intervention_id': str(log.id) if log.is_interactive else None,
        'metrics': _build_node_metrics(detail),
    }



def list_user_tasks(user_id: int) -> List[dict]:
    """获取用户的所有调研任务列表

    FR-GRXX-0001: 历史调研记录管理
    """
    tasks = ResearchTask.objects.filter(user_id=user_id).order_by('-created_at')
    return [
        {
            'task_id': str(task.id),
            'object_name': task.object_name,
            'object_type': _map_object_type(task.object_type),
            'status': _map_task_status(task.status),
            'created_at': task.created_at.isoformat(),
        }
        for task in tasks
    ]


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


def get_task_step_logs(task_id: int) -> dict:
    """获取任务步骤日志（全流程监控）

    FR-JSDY-0003: 全流程可视化监控
    """
    logs = TaskStepLog.objects.filter(task_id=task_id).order_by('created_at')
    nodes = [_serialize_step_log(log) for log in logs]
    edges = [
        {'from': nodes[index]['node_id'], 'to': nodes[index + 1]['node_id']}
        for index in range(len(nodes) - 1)
    ]
    current_node = next(
        (node['node_id'] for node in nodes if node['node_status'] in ('running', 'waiting_user')),
        nodes[-1]['node_id'] if nodes else '',
    )
    waiting_node = next(
        (node['node_id'] for node in nodes if node['node_status'] == 'waiting_user'),
        None,
    )
    return {
        'task_id': str(task_id),
        'nodes': nodes,
        'edges': edges,
        'current_node': current_node,
        'waiting_intervention_node_id': waiting_node,
    }



def get_task_events(task_id: int) -> List[dict]:
    logs = TaskStepLog.objects.filter(task_id=task_id).order_by('created_at')
    events = []
    for log in logs:
        detail = log.detail if isinstance(log.detail, dict) else {}
        node_status = _map_step_status(log.step_status, log.is_interactive)
        events.append({
            'event_id': str(log.id),
            'task_id': str(task_id),
            'node_id': str(log.id),
            'node_name': log.step_name,
            'node_status': node_status,
            'level': 'warning' if node_status == 'waiting_user' else 'error' if node_status == 'failed' else 'success' if node_status == 'completed' else 'info',
            'title': detail.get('event_title', log.step_name),
            'message': detail.get('message', ''),
            'metrics': detail.get('metrics', {}),
            'timestamp': log.created_at.isoformat(),
        })
    return events



def get_task_status_view(task_id: int) -> Optional[dict]:
    task = ResearchTask.objects.filter(pk=task_id).first()
    if not task:
        return None

    logs = TaskStepLog.objects.filter(task_id=task_id).order_by('created_at')
    current_log = next(
        (log for log in logs if _map_step_status(log.step_status, log.is_interactive) in ('running', 'waiting_user')),
        logs.last() if logs else None,
    )
    current_detail = current_log.detail if current_log and isinstance(current_log.detail, dict) else {}
    progress_payload = task.progress if isinstance(task.progress, dict) else {}
    progress = max([value for value in progress_payload.values() if isinstance(value, (int, float))], default=0)

    return {
        'task_id': str(task.id),
        'status': _map_task_status(task.status),
        'current_stage': current_log.step_name if current_log else '任务初始化',
        'progress': progress,
        'hint': current_detail.get('message', '任务正在处理中'),
        'object_name': task.object_name,
        'object_type': _map_object_type(task.object_type),
        'current_node_id': str(current_log.id) if current_log else None,
        'current_node_name': current_log.step_name if current_log else None,
        'waiting_intervention': task.status == STATUS_WAITING_USER,
        'metrics_summary': _build_node_metrics(current_detail),
        'available_actions': ['cancel'] if task.status not in (STATUS_COMPLETED, STATUS_CANCELLED) else ['view_report'],
    }



def get_task_intervention_detail(task_id: int, user_id: int, node_id: str) -> Optional[dict]:
    task = ResearchTask.objects.filter(pk=task_id, user_id=user_id).first()
    if not task:
        return None

    step = TaskStepLog.objects.filter(pk=node_id, task_id=task_id, is_interactive=True).first()
    if not step:
        return None

    detail = step.detail if isinstance(step.detail, dict) else {}
    return {
        'task_id': str(task_id),
        'node_id': str(step.id),
        'node_name': step.step_name,
        'intervention_type': detail.get('intervention_type', 'manual_review'),
        'status': 'waiting_user' if step.step_status == 'PAUSED' else 'resolved',
        'reason': detail.get('reason') or detail.get('message', ''),
        'suggested_action': detail.get('suggested_action', 'review_and_continue'),
        'current_params': detail.get('current_params', {}),
        'preview_data': detail.get('preview_data', {}),
    }


def pause_research_task(task_id: int, step_name: str, detail: dict) -> Tuple[bool, Optional[str]]:
    """Agent 请求暂停以等待用户介入"""
    task = ResearchTask.objects.filter(pk=task_id).first()
    if not task:
        return (False, "任务不存在")
    if task.status in (STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED):
        return (False, "无效的任务状态")

    task.status = STATUS_WAITING_USER
    task.save(update_fields=['status', 'updated_at'])

    TaskStepLog.objects.create(
        task=task,
        step_name=step_name,
        step_status='PAUSED',
        detail=detail,
        is_interactive=True
    )
    return (True, None)


def respond_to_step(task_id: int, user_id: int, node_id: str, action: str,
                    response_data: dict = None) -> Tuple[bool, Optional[str], Optional[dict]]:
    """用户提供反馈，继续或终止任务"""
    task = ResearchTask.objects.filter(pk=task_id, user_id=user_id).first()
    if not task:
        return (False, "任务不存在或无权操作", None)

    if task.status != STATUS_WAITING_USER:
        return (False, "当前任务未处于等待介入状态", None)

    step = TaskStepLog.objects.filter(pk=node_id, task_id=task_id, is_interactive=True).first()
    if not step:
        return (False, "无效的介入步骤", None)

    normalized_action = {
        'confirm_continue': 'CONTINUE',
        'update_rules': 'MODIFY',
        'skip_intervention': 'SKIP',
        'cancel': 'CANCEL',
    }.get(action, action)

    step.user_response = {
        'action': normalized_action,
        'data': response_data or {}
    }
    step.step_status = 'COMPLETED' if normalized_action != 'SKIP' else 'SKIPPED'
    step.save(update_fields=['user_response', 'step_status'])

    if normalized_action == 'CANCEL':
        task.status = STATUS_CANCELLED
        task.save(update_fields=['status', 'updated_at'])
        TaskStepLog.objects.create(
            task=task,
            step_name='任务中止',
            step_status='COMPLETED',
            detail={'message': '用户在介入时选择中止任务'}
        )
        return (True, None, {
            'task_id': str(task_id),
            'node_id': str(node_id),
            'result': 'accepted:cancel',
            'audit_log_id': f'intervention-{node_id}',
            'task_status': 'cancelled',
            'node_status': 'skipped',
        })

    resume_status = STATUS_SEARCHING
    if '分析' in step.step_name:
        resume_status = STATUS_ANALYZING

    task.status = resume_status
    task.save(update_fields=['status', 'updated_at'])
    TaskStepLog.objects.create(
        task=task,
        step_name='恢复运行',
        step_status='RUNNING',
        detail={
            'message': '接收到用户反馈，任务继续执行',
            'action': normalized_action,
            'source_node_id': str(node_id),
            'metrics': {'intervention': 1},
        }
    )

    return (True, None, {
        'task_id': str(task_id),
        'node_id': str(node_id),
        'result': f'accepted:{action}',
        'audit_log_id': f'intervention-{node_id}',
        'task_status': _map_task_status(task.status),
        'node_status': 'completed' if normalized_action != 'SKIP' else 'skipped',
    })
