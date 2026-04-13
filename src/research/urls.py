"""
research URL 配置
挂载点: /api/research/
"""
from django.urls import path
from research.api.research_api import (
    create_task, task_detail, task_list,
    cancel_research_task, task_steps, intervene_task
)

urlpatterns = [
    path('tasks', task_list, name='research-task-list'),     # GET /api/v1/research/tasks
    path('task', create_task, name='research-task-create'), # POST /api/v1/research/task
    path('tasks/<int:task_id>', task_detail, name='research-task-detail'), # GET
    path('tasks/<int:task_id>/cancel', cancel_research_task, name='research-task-cancel'), # POST
    path('tasks/<int:task_id>/workflow', task_steps, name='research-task-workflow'), # GET
    path('tasks/<int:task_id>/intervene', intervene_task, name='research-task-intervene'), # POST
]
