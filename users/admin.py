from django.contrib import admin
from django.contrib.auth import get_user_model
from users.models.user_profile import UserProfile

User = get_user_model()


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False


class UserAdmin(admin.ModelAdmin):
    inlines = [UserProfileInline]
    list_display = ['username', 'email', 'is_staff', 'is_active']
    search_fields = ['username', 'email']


# 取消注册默认的 UserAdmin，替换为自定义版本
admin.site.unregister(User)
admin.site.register(User, UserAdmin)
