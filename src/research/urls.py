"""
research URL 配置
挂载点: /api/research/
"""
from django.urls import path
from research.api.research_api import (
    analyze_task,
    cancel_research_task,
    create_task,
    cross_validation_result,
    history_detail,
    history_list,
    history_reload,
    task_detail,
    task_events,
    task_facts,
    task_intervention_entry,
    task_intervention_detail,
    task_intervention_submit,
    task_list,
    task_status,
    task_steps,
    tasks_collection,
    trigger_cross_validation,
    retry_task_analysis,
)

urlpatterns = [
    path('tasks', tasks_collection, name='research-task-collection'),
    path('task', create_task, name='research-task-create'),
    path('tasks/<int:task_id>', task_detail, name='research-task-detail'),
    path('tasks/<int:task_id>/status', task_status, name='research-task-status'),
    path('tasks/<int:task_id>/cancel', cancel_research_task, name='research-task-cancel'),
    path('tasks/<int:task_id>/workflow', task_steps, name='research-task-workflow'),
    path('tasks/<int:task_id>/events', task_events, name='research-task-events'),
    path('tasks/<int:task_id>/facts', task_facts, name='research-task-facts'),
    path('tasks/<int:task_id>/analyze', analyze_task, name='research-task-analyze'),
    path('tasks/<int:task_id>/retry-analysis', retry_task_analysis, name='research-task-retry-analysis'),
    path('tasks/<int:task_id>/cross-validation', trigger_cross_validation, name='research-task-cross-validation'),
    path('tasks/<int:task_id>/cross-validation/result', cross_validation_result, name='research-task-cross-validation-result'),
    path('tasks/<int:task_id>/interventions/<int:node_id>', task_intervention_entry, name='research-task-intervention-entry'),
    path('tasks/<int:task_id>/interventions/<int:node_id>/submit', task_intervention_submit, name='research-task-intervention-submit'),
    path('history', history_list, name='research-history-list'),
    path('history/<int:task_id>', history_detail, name='research-history-detail'),
    path('history/<int:task_id>/reload', history_reload, name='research-history-reload'),
]
