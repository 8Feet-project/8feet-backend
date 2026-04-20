"""
llm_manager 模型路由推荐接口
"""
from django.urls import path

from llm_manager.api.llm_api import recommended_model

urlpatterns = [
    path('', recommended_model),
    path('recommend', recommended_model),
]
