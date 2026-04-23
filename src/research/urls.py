"""
research URL 配置
挂载点: /api/research/
"""
from django.urls import path
from research.api.research_api import (
    create_task, task_detail, task_list,
    cancel_research_task, task_steps,
    task_followup, task_history,
)

urlpatterns = [
    path('task', create_task, name='research-create-task'),
    path('task/detail', task_detail, name='research-task-detail'),
    path('task/list', task_list, name='research-task-list'),
    path('task/cancel', cancel_research_task, name='research-task-cancel'),
    path('task/steps', task_steps, name='research-task-steps'),
    path('task/history', task_history, name='research-task-history'),
    path('task/followup', task_followup, name='research-task-followup'),
]
