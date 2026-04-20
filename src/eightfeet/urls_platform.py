"""
平台初始化路由 (挂载于 /api/v1/platform/)
"""
from django.urls import path
from django.http import JsonResponse
from django.contrib.auth import get_user_model
from users.models.user_profile import UserProfile, ROLE_SUPER_ADMIN


def init_status(request):
    """获取平台初始化状态"""
    User = get_user_model()
    # 逻辑：只要存在任何用户，即视为已通过基础初始化流程
    has_any_user = User.objects.exists()
    
    # 检查是否存在超级管理员
    has_super_admin = UserProfile.objects.filter(role=ROLE_SUPER_ADMIN).exists()
    
    return JsonResponse({
        "initialized": has_any_user,
        "has_super_admin": has_super_admin
    })


def initialize(request):
    """引导初始化过程
    如果不存在超级管理员，则使用默认凭据一键创建
    """
    User = get_user_model()
    super_admin_profile = UserProfile.objects.filter(role=ROLE_SUPER_ADMIN).first()
    
    if request.method == 'POST' and not super_admin_profile:
        # 直接配置参数
        username = "super_admin"
        password = "super_admin_password"
        email = "23373052@buaa.edu.cn"
        nickname = "SUPERADMIN"
        
        from users.interface.auth_interface import register_user
        success, message, result = register_user(
            username, nickname, password, email, email_verified=True
        )
        
        
        if not success:
            return JsonResponse({
                "initialized": False,
                "super_admin_user_id": -404
            })

    return JsonResponse({
        "initialized": User.objects.exists(),
        "super_admin_user_id": super_admin_profile.user_id if super_admin_profile else None
    })

urlpatterns = [
    path('init-status', init_status, name='platform-init-status'),
    path('initialize', initialize, name='platform-initialize'),
]
