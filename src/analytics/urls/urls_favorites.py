"""
收藏相关路由 (挂载于 /api/v1/favorites/)
"""
from django.urls import path
from analytics.api.analytics_api import (
    favorite_items,
    favorite_remove,
    favorite_batch_remove,
)

urlpatterns = [
    path('items', favorite_items, name='favorite-items'),
    path('items/', favorite_items, name='favorite-items-slash'),
    path('items/batch-delete', favorite_batch_remove, name='favorite-batch-delete'), # POST
    path('items/batch-delete/', favorite_batch_remove, name='favorite-batch-delete-slash'), # POST
    path('items/<int:favorite_id>', favorite_remove, name='favorite-delete'), # DELETE
    path('items/<int:favorite_id>/', favorite_remove, name='favorite-delete-slash'), # DELETE
]
