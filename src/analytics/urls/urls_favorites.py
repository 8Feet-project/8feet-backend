"""
收藏夹相关路由 (挂载于 /api/v1/favorites/)
"""
from django.urls import path
from analytics.api.analytics_api import (
    favorite_add, favorite_remove, favorite_list
)

urlpatterns = [
    path('items', favorite_list, name='favorite-list'), # GET
    path('items', favorite_add, name='favorite-create'), # POST
    path('items/<int:favorite_id>', favorite_remove, name='favorite-delete'), # DELETE
]
