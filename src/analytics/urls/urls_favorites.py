"""
收藏夹相关路由 (挂载于 /api/v1/favorites/)
"""
from django.urls import path
from analytics.api.analytics_api import (
    favorite_add,
    favorite_folder_detail,
    favorite_folders,
    favorite_items,
    favorite_list,
    favorite_move,
    favorite_remove,
)

urlpatterns = [
    path('folders', favorite_folders, name='favorite-folder-list'),
    path('folders/', favorite_folders, name='favorite-folder-list-slash'),
    path('folders/<str:folder_id>', favorite_folder_detail, name='favorite-folder-detail'),
    path('folders/<str:folder_id>/', favorite_folder_detail, name='favorite-folder-detail-slash'),
    path('items', favorite_items, name='favorite-items'),
    path('items/', favorite_items, name='favorite-items-slash'),
    path('items/<int:favorite_id>/move', favorite_move, name='favorite-move'),
    path('items/<int:favorite_id>/move/', favorite_move, name='favorite-move-slash'),
    path('items/<int:favorite_id>', favorite_remove, name='favorite-delete'), # DELETE
    path('items/<int:favorite_id>/', favorite_remove, name='favorite-delete-slash'), # DELETE
]
