"""
research URL 配置
挂载点: /api/research/
"""
from django.urls import path

from research.api.research_api import (
    cancel_research_task,
    create_task,
    intervene_task,
    task_detail,
    task_followup,
    task_history,
    task_list,
    task_steps,
)

urlpatterns = [
    path('tasks', task_list, name='research-task-list'),
    path('task', create_task, name='research-task-create'),
    path('tasks/<int:task_id>', task_detail, name='research-task-detail'),
    path('tasks/<int:task_id>/cancel', cancel_research_task, name='research-task-cancel'),
    path('tasks/<int:task_id>/workflow', task_steps, name='research-task-workflow'),
    path('tasks/<int:task_id>/history', task_history, name='research-task-history'),
    path('tasks/<int:task_id>/followup', task_followup, name='research-task-followup'),
    path('tasks/<int:task_id>/intervene', intervene_task, name='research-task-intervene'),
]
