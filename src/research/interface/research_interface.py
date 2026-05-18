"""
调研任务业务逻辑 — interface 层
负责任务创建、状态查询、会话历史、继续追问与用户介入。
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.utils import timezone

from llm_manager.interface.llm_interface import (
    normalize_object_type,
    resolve_user_model_config,
)
from research.models import (
    ResearchConversation,
    ResearchConversationMessage,
    ResearchTask,
    SESSION_STATUS_CANCELLED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_WAITING_USER,
)
from research.models.task_step_log import TaskStepLog


logger = logging.getLogger(__name__)

AUTO_OBJECT_TYPE_VALUES = {"", "auto", "自动识别", "自动"}

STOCK_CODE_RE = re.compile(
    r"^\s*(?:"
    r"\d{6}(?:\.(?:SH|SZ|BJ))?"
    r"|[A-Z]{1,5}(?:\.(?:US|O|N|NYSE|NASDAQ))?"
    r"|\d{4,5}(?:\.HK)?"
    r")\s*$",
    re.IGNORECASE,
)

PRODUCT_KEYWORDS = {
    "商品",
    "期货",
    "原油",
    "天然气",
    "黄金",
    "白银",
    "铜",
    "铝",
    "锌",
    "镍",
    "螺纹钢",
    "铁矿石",
    "焦煤",
    "焦炭",
    "动力煤",
    "大豆",
    "豆粕",
    "玉米",
    "白糖",
    "棉花",
    "橡胶",
    "PTA",
    "PVC",
}

STOCK_KEYWORDS = {
    "股票",
    "证券",
    "A股",
    "港股",
    "美股",
    "股价",
    "行情",
    "财报",
    "年报",
    "季报",
}


def _fallback_system_message() -> str:
    return "8Feet research runtime is not available in this deployment."


def _research_runtime():
    from research.interface import research_runtime

    return research_runtime


def _safe_research_runtime():
    try:
        return (_research_runtime(), None)
    except ModuleNotFoundError as exc:
        message = f"调研运行时依赖未安装: {exc.name}"
        logger.warning(message)
        return (None, message)


def _frontend_status(status: str) -> str:
    return (status or STATUS_PENDING).lower()


def _frontend_object_type(object_type: str) -> str:
    return {
        "COMPANY": "company",
        "STOCK": "stock",
        "PRODUCT": "commodity",
        "COMMODITY": "commodity",
    }.get(object_type or "", (object_type or "company").lower())


def infer_object_type(object_name: str, object_type: str | None = None) -> str:
    """Infer a supported research object type for "auto detect" task creation."""
    normalized = normalize_object_type(object_type)
    raw_type = str(object_type or "").strip()
    is_auto_type = raw_type.lower() in AUTO_OBJECT_TYPE_VALUES
    if normalized and not is_auto_type:
        return normalized

    name = str(object_name or "").strip()
    upper_name = name.upper()
    if STOCK_CODE_RE.match(name) or any(keyword in upper_name for keyword in STOCK_KEYWORDS):
        return "STOCK"
    if any(keyword.upper() in upper_name for keyword in PRODUCT_KEYWORDS):
        return "PRODUCT"
    return "COMPANY"


def create_research_task(
    user_id: int,
    title: str,
    object_name: str,
    object_type: str,
    llm_config_id: int = None,
    model_id: str = None,
    search_params: dict = None,
) -> Tuple[bool, Optional[str], Optional[int]]:
    """创建调研任务并异步触发 efeet 执行。"""
    User = get_user_model()
    user = User.objects.filter(pk=user_id).first()
    if not user:
        return (False, "用户不存在", None)

    if not title or not object_name:
        return (False, "title/object_name 不能为空", None)

    normalized_object_type = infer_object_type(object_name, object_type)
    if not normalized_object_type:
        return (False, "object_type 无效", None)

    selected_model_id = llm_config_id or model_id
    ok, message, config, _ = resolve_user_model_config(
        user,
        model_id=selected_model_id,
        object_type=normalized_object_type,
    )
    if not ok:
        return (False, message, None)

    task = ResearchTask.objects.create(
        user=user,
        title=title,
        object_name=object_name,
        object_type=normalized_object_type,
        llm_config=config,
        search_params=search_params or {},
        status=STATUS_PENDING,
        progress={
            "searching": 0,
            "analyzing": 0,
            "report": 0,
            "stage": STATUS_PENDING,
            "event_count": 0,
        },
    )

    runtime, runtime_error = _safe_research_runtime()

    ResearchConversation.objects.create(
        task=task,
        thread_id=str(uuid4()),
        system_message=(
            runtime.build_research_system_message_for_user(user)
            if runtime else _fallback_system_message()
        ),
    )

    TaskStepLog.objects.create(
        task=task,
        step_name="任务已创建",
        step_status="COMPLETED",
        detail={"message": f"调研任务 [{title}] 创建成功，准备启动调研链路"},
    )

    if runtime is None:
        TaskStepLog.objects.create(
            task=task,
            step_name="调研运行时未启动",
            step_status="FAILED",
            detail={
                "error": runtime_error or "调研运行时不可用",
                "message": "任务已保存，但当前环境缺少 research runtime 依赖，未启动异步调研。",
            },
        )
        task.progress = {
            **(task.progress or {}),
            "stage": STATUS_PENDING,
            "runtime_error": runtime_error,
        }
        task.save(update_fields=['progress', 'updated_at'])
        return (True, None, task.id)

    success, message = runtime.enqueue_task_run(
        task.id,
        prompt=runtime.build_initial_prompt(task),
        create_report=True,
        queued_step_name="开始执行调研",
    )
    if not success:
        task.status = STATUS_FAILED
        task.progress = {
            **(task.progress or {}),
            "stage": STATUS_FAILED,
        }
        task.save(update_fields=['status', 'progress', 'updated_at'])
        TaskStepLog.objects.create(
            task=task,
            step_name="任务启动失败",
            step_status="FAILED",
            detail={"error": message or "未知错误"},
        )
        return (False, message, None)

    return (True, None, task.id)


def get_task_detail(task_id: int, user_id: int) -> Optional[dict]:
    """获取调研任务详情与会话摘要。"""
    task = (
        ResearchTask.objects
        .select_related('conversation', 'llm_config')
        .filter(pk=task_id, user_id=user_id)
        .first()
    )
    if not task:
        return None

    latest_analysis = task.analysis_results.order_by('-created_at').first()
    latest_report = task.reports.filter(is_latest=True).first()
    conversation = _get_conversation(task)

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
        "conversation": _serialize_conversation(conversation),
        "latest_analysis": (
            {
                "id": latest_analysis.id,
                "analysis_type": latest_analysis.analysis_type,
                "conclusion": latest_analysis.conclusion,
                "created_at": latest_analysis.created_at.isoformat(),
            }
            if latest_analysis else None
        ),
        "latest_report": (
            {
                "id": latest_report.id,
                "title": latest_report.title,
                "summary": latest_report.summary,
                "version": latest_report.version,
                "created_at": latest_report.created_at.isoformat(),
            }
            if latest_report else None
        ),
    }


def list_user_tasks(user_id: int) -> List[dict]:
    """获取用户的所有调研任务列表。"""
    tasks = (
        ResearchTask.objects
        .filter(user_id=user_id, parent_task__isnull=True)
        .select_related('conversation')
        .order_by('-created_at')
    )
    results: list[dict] = []
    for task in tasks:
        conversation = _get_conversation(task)
        results.append(
            {
                "id": task.id,
                "task_id": str(task.id),
                "title": task.title,
                "object_name": task.object_name,
                "object_type": _frontend_object_type(task.object_type),
                "status": _frontend_status(task.status),
                "progress": task.progress,
                "created_at": task.created_at.isoformat(),
                "updated_at": task.updated_at.isoformat(),
                "conversation_status": conversation.status if conversation else None,
            }
        )
    return results


def cancel_task(task_id: int, user_id: int) -> Tuple[bool, Optional[str]]:
    """取消调研任务。"""
    task = (
        ResearchTask.objects
        .select_related('conversation')
        .filter(pk=task_id, user_id=user_id)
        .first()
    )
    if not task:
        return (False, "任务不存在或无权操作")
    if task.status in (STATUS_CANCELLED, STATUS_COMPLETED):
        return (False, "任务已完成或已取消")

    task.status = STATUS_CANCELLED
    task.progress = {
        **(task.progress or {}),
        "stage": STATUS_CANCELLED,
        "last_event_type": "cancelled",
    }
    task.save(update_fields=['status', 'progress', 'updated_at'])

    conversation = _get_conversation(task)
    if conversation is not None:
        conversation.status = SESSION_STATUS_CANCELLED
        conversation.last_finished_at = timezone.now()
        conversation.save(update_fields=['status', 'last_finished_at', 'updated_at'])

    TaskStepLog.objects.create(
        task=task,
        step_name="任务已取消",
        step_status="COMPLETED",
        detail={"message": "用户手动取消了调研任务"},
    )
    return (True, None)


def get_task_step_logs(task_id: int, user_id: int) -> List[dict]:
    """获取任务步骤日志。"""
    logs = (
        TaskStepLog.objects
        .filter(task_id=task_id, task__user_id=user_id)
        .values(
            'id',
            'step_name',
            'step_status',
            'detail',
            'is_interactive',
            'user_response',
            'created_at',
        )
    )
    return [
        {
            **log,
            "created_at": log["created_at"].isoformat(),
        }
        for log in logs
    ]


def pause_research_task(task_id: int, step_name: str, detail: dict) -> Tuple[bool, Optional[str]]:
    """Agent 请求暂停以等待用户介入。"""
    task = ResearchTask.objects.filter(pk=task_id).first()
    if not task:
        return (False, "任务不存在")
    if task.status in (STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED):
        return (False, "无效的任务状态")

    from research.models.research_task import STATUS_WAITING_USER

    task.status = STATUS_WAITING_USER
    task.save(update_fields=['status', 'updated_at'])

    TaskStepLog.objects.create(
        task=task,
        step_name=step_name,
        step_status='PAUSED',
        detail=detail,
        is_interactive=True,
    )
    return (True, None)


def respond_to_step(
    task_id: int,
    user_id: int,
    step_id: int,
    action: str,
    response_data: dict = None,
) -> Tuple[bool, Optional[str]]:
    """用户提供反馈，继续或终止任务。"""
    task = (
        ResearchTask.objects
        .select_related('conversation')
        .filter(pk=task_id, user_id=user_id)
        .first()
    )
    if not task:
        return (False, "任务不存在或无权操作")

    if task.status != STATUS_WAITING_USER:
        return (False, "当前任务未处于等待介入状态")

    step = TaskStepLog.objects.filter(
        pk=step_id,
        task_id=task_id,
        is_interactive=True,
    ).first()
    if not step:
        return (False, "无效的介入步骤")

    action_text = _step_response_text(action, response_data or {})
    step.user_response = {
        "action": action,
        "data": {**(response_data or {}), "response": action_text},
    }
    step.step_status = 'COMPLETED'
    step.save(update_fields=['user_response', 'step_status'])

    if action == 'CANCEL':
        task.status = STATUS_CANCELLED
        task.save(update_fields=['status', 'updated_at'])
        TaskStepLog.objects.create(
            task=task,
            step_name="任务中止",
            step_status="COMPLETED",
            detail={"message": "用户在介入时选择中止任务"},
        )
    else:
        TaskStepLog.objects.create(
            task=task,
            step_name="恢复运行",
            step_status="RUNNING",
            detail={"message": action_text, "action": action},
        )

        runtime, runtime_error = _safe_research_runtime()
        if runtime is None:
            return (False, runtime_error or "调研运行时不可用")

        success, error_message = runtime.enqueue_task_run(
            task.id,
            prompt=runtime._approval_response_prompt(action_text),
            create_report=True,
            queued_step_name="继续执行审批后的调研",
            run_metadata={
                "approval_step_id": step.id,
                "approval_action": action,
            },
        )
        if not success:
            task.status = STATUS_WAITING_USER
            task.save(update_fields=['status', 'updated_at'])
            return (False, error_message or "恢复调研失败")

    return (True, None)


def _step_response_text(action: str, response_data: dict) -> str:
    normalized = str(action or "").strip().lower()
    if normalized in {"accept", "confirm_continue", "accepted"}:
        return "接受"
    if normalized in {"replan", "update_rules", "replan_requested"}:
        comment = str(response_data.get("comment") or response_data.get("reason") or "").strip()
        return f"重新计划: {comment}" if comment else "重新计划"
    if normalized in {"reject", "skip_intervention", "rejected"}:
        reason = str(response_data.get("comment") or response_data.get("reason") or "").strip()
        return f"拒绝，因为{reason}" if reason else "拒绝"
    comment = str(response_data.get("comment") or response_data.get("reason") or "").strip()
    return comment or str(action or "").strip() or "接受"


def get_task_conversation_history(task_id: int, user_id: int) -> Optional[dict]:
    """获取任务会话历史。"""
    conversation = (
        ResearchConversation.objects
        .select_related('task')
        .filter(task_id=task_id, task__user_id=user_id)
        .first()
    )
    if not conversation:
        return None

    messages = list(
        ResearchConversationMessage.objects
        .filter(conversation=conversation)
        .values(
            'message_index',
            'run_number',
            'role',
            'message_type',
            'content',
            'payload',
            'created_at',
        )
    )
    return {
        "task_id": task_id,
        "conversation": _serialize_conversation(conversation),
        "messages": [
            {
                **message,
                "created_at": message["created_at"].isoformat(),
            }
            for message in messages
        ],
    }


def continue_task_conversation(
    task_id: int,
    user_id: int,
    message: str,
    run_metadata: dict = None,
) -> Tuple[bool, Optional[str]]:
    """基于已保存会话继续追问。"""
    if not message or not message.strip():
        return (False, "message 不能为空")

    task = (
        ResearchTask.objects
        .select_related('conversation')
        .filter(pk=task_id, user_id=user_id)
        .first()
    )
    if not task:
        return (False, "任务不存在或无权操作")
    if task.status == STATUS_CANCELLED:
        return (False, "任务已取消，无法继续追问")
    if _get_conversation(task) is None:
        return (False, "任务尚未初始化会话")

    runtime, runtime_error = _safe_research_runtime()
    if runtime is None:
        return (False, runtime_error or "调研运行时不可用")

    success, error_message = runtime.enqueue_task_run(
        task.id,
        prompt=runtime.build_followup_prompt(message),
        create_report=False,
        queued_step_name="开始处理追问",
        run_metadata=run_metadata or {},
    )
    if not success:
        return (False, error_message)
    return (True, None)


def _serialize_conversation(conversation: ResearchConversation | None) -> Optional[dict]:
    if conversation is None:
        return None
    return {
        "thread_id": conversation.thread_id,
        "status": conversation.status,
        "run_count": conversation.run_count,
        "latest_user_message": conversation.latest_user_message,
        "latest_assistant_message": conversation.latest_assistant_message,
        "last_error": conversation.last_error,
        "last_started_at": (
            conversation.last_started_at.isoformat()
            if conversation.last_started_at else None
        ),
        "last_finished_at": (
            conversation.last_finished_at.isoformat()
            if conversation.last_finished_at else None
        ),
        "updated_at": conversation.updated_at.isoformat(),
    }


def _get_conversation(task: ResearchTask) -> ResearchConversation | None:
    try:
        return task.conversation
    except ResearchConversation.DoesNotExist:
        return None
