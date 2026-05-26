"""
research URL 配置
挂载点: /api/research/
"""
from django.urls import path

from research.api.research_api import (
    analyze_task,
    cancel_research_task,
    create_task,
    cross_validation,
    intervene_task,
    research_history_detail,
    research_history_list,
    retry_analysis,
    task_auto_advance,
    task_collection,
    task_detail,
    task_events,
    task_facts,
    task_followup,
    task_history,
    task_intervention,
    task_list,
    task_steps,
    task_status,
)

urlpatterns = [
    path('history', research_history_list, name='research-history-list'),
    path('history/', research_history_list, name='research-history-list-slash'),
    path('history/<int:task_id>', research_history_detail, name='research-history-detail'),
    path('history/<int:task_id>/', research_history_detail, name='research-history-detail-slash'),
    path('tasks', task_collection, name='research-task-collection'),
    path('task', create_task, name='research-task-create'),
    path('tasks/<int:task_id>', task_detail, name='research-task-detail'),
    path('tasks/<int:task_id>/status', task_status, name='research-task-status'),
    path('tasks/<int:task_id>/auto-advance', task_auto_advance, name='research-task-auto-advance'),
    path('tasks/<int:task_id>/cancel', cancel_research_task, name='research-task-cancel'),
    path('tasks/<int:task_id>/workflow', task_steps, name='research-task-workflow'),
    path('tasks/<int:task_id>/facts', task_facts, name='research-task-facts'),
    path('tasks/<int:task_id>/analyze', analyze_task, name='research-task-analyze'),
    path('tasks/<int:task_id>/retry-analysis', retry_analysis, name='research-task-retry-analysis'),
    path('tasks/<int:task_id>/cross-validation', cross_validation, name='research-task-cross-validation'),
    path('tasks/<int:task_id>/cross-validation/result', cross_validation, name='research-task-cross-validation-result'),
    path('tasks/<int:task_id>/events', task_events, name='research-task-events'),
    path('tasks/<int:task_id>/interventions/<str:node_id>', task_intervention, name='research-task-intervention-detail'),
    path('tasks/<int:task_id>/history', task_history, name='research-task-history'),
    path('tasks/<int:task_id>/followup', task_followup, name='research-task-followup'),
    path('tasks/<int:task_id>/intervene', intervene_task, name='research-task-intervene'),
]
