"""
research URL 配置
挂载点: /api/research/
"""
from django.urls import path
from research.api.research_api import (
    cancel_research_task,
    create_task,
    task_detail,
    task_events,
    task_intervention_detail,
    task_intervention_submit,
    task_list,
    task_status,
    task_steps,
)

urlpatterns = [
    path('tasks', task_list, name='research-task-list'),
    path('task', create_task, name='research-task-create'),
    path('tasks/<int:task_id>', task_detail, name='research-task-detail'),
    path('tasks/<int:task_id>/status', task_status, name='research-task-status'),
    path('tasks/<int:task_id>/cancel', cancel_research_task, name='research-task-cancel'),
    path('tasks/<int:task_id>/workflow', task_steps, name='research-task-workflow'),
    path('tasks/<int:task_id>/events', task_events, name='research-task-events'),
    path('tasks/<int:task_id>/interventions/<int:node_id>', task_intervention_detail, name='research-task-intervention-detail'),
    path('tasks/<int:task_id>/interventions/<int:node_id>/submit', task_intervention_submit, name='research-task-intervention-submit'),
]
