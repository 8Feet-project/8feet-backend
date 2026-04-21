"""
模型路由推荐接口 (挂载于 /api/v1/model-routing/)
"""
from django.urls import path
from llm_manager.api.llm_api import routing_recommendation

urlpatterns = [
    path('recommendation', routing_recommendation, name='model-routing-recommendation'),
]
