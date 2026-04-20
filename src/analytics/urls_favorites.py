from django.urls import path

from analytics.api.analytics_api import (
    favorite_folder_detail,
    favorite_folders_collection,
    favorite_item_detail,
    favorite_item_move,
    favorite_items_collection,
)

urlpatterns = [
    path('folders', favorite_folders_collection, name='favorites-folders'),
    path('folders/<str:folder_id>', favorite_folder_detail, name='favorites-folder-detail'),
    path('items', favorite_items_collection, name='favorites-items'),
    path('items/<int:favorite_id>', favorite_item_detail, name='favorites-item-detail'),
    path('items/<int:favorite_id>/move', favorite_item_move, name='favorites-item-move'),
]
