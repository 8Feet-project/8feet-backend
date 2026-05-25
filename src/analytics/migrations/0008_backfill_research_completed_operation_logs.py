from django.db import migrations


def backfill_research_completed_operation_logs(apps, schema_editor):
    OperationLog = apps.get_model("analytics", "OperationLog")
    ResearchTask = apps.get_model("research", "ResearchTask")
    TaskStepLog = apps.get_model("research", "TaskStepLog")

    completed_steps = (
        TaskStepLog.objects
        .filter(step_name="调研完成", step_status="COMPLETED")
        .select_related("task", "task__llm_config")
        .order_by("created_at", "id")
    )
    existing_task_ids = set(
        OperationLog.objects
        .filter(action_type="RESEARCH_COMPLETED", target_module="research.task")
        .values_list("target_id", flat=True)
    )
    created_task_ids = set()
    rows = []
    for step in completed_steps:
        task = step.task
        if task is None or task.id in existing_task_ids or task.id in created_task_ids:
            continue
        detail = step.detail if isinstance(step.detail, dict) else {}
        rows.append(
            OperationLog(
                user_id=task.user_id,
                action_type="RESEARCH_COMPLETED",
                target_module="research.task",
                target_id=task.id,
                detail={
                    "level": "info",
                    "object_type": task.object_type,
                    "model_id": str(task.llm_config_id or ""),
                    "model_name": getattr(task.llm_config, "name", "") if task.llm_config_id else "",
                    "action_summary": f"调研任务完成：{task.object_name}",
                    "user_action": "完成调研任务",
                    "search_intent": task.title,
                    "response_raw": str(detail.get("message") or "")[:4000],
                    "agent_trace": [{"step": "research", "detail": "历史调研完成记录回填"}],
                    "backfilled_from": f"taskstep-{step.id}",
                },
                created_at=step.created_at,
            )
        )
        created_task_ids.add(task.id)

    if rows:
        OperationLog.objects.bulk_create(rows)

    completed_without_steps = (
        ResearchTask.objects
        .filter(status="COMPLETED")
        .exclude(id__in=existing_task_ids)
        .exclude(id__in=created_task_ids)
        .select_related("llm_config")
    )
    fallback_rows = []
    for task in completed_without_steps:
        fallback_rows.append(
            OperationLog(
                user_id=task.user_id,
                action_type="RESEARCH_COMPLETED",
                target_module="research.task",
                target_id=task.id,
                detail={
                    "level": "info",
                    "object_type": task.object_type,
                    "model_id": str(task.llm_config_id or ""),
                    "model_name": getattr(task.llm_config, "name", "") if task.llm_config_id else "",
                    "action_summary": f"调研任务完成：{task.object_name}",
                    "user_action": "完成调研任务",
                    "search_intent": task.title,
                    "agent_trace": [{"step": "research", "detail": "历史已完成任务回填"}],
                    "backfilled_from": "research_task",
                },
                created_at=task.updated_at,
            )
        )
    if fallback_rows:
        OperationLog.objects.bulk_create(fallback_rows)


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0007_alert_schedule_and_delivery"),
        ("research", "0005_alter_researchtask_search_params"),
    ]

    operations = [
        migrations.RunPython(backfill_research_completed_operation_logs, migrations.RunPython.noop),
    ]
