"""
认证记录模型
用于 JWT refresh_token 的会话追踪
"""
from django.db import models
from django.contrib.auth import get_user_model


class AuthRecord(models.Model):
    """JWT 认证会话记录"""
    user = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE,
        related_name='auth_records'
    )
    login_at = models.DateTimeField(auto_now_add=True)
    expires_by = models.DateTimeField()

    class Meta:
        db_table = 'auth_record'
        default_permissions = ()
        verbose_name = '认证记录'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"AuthRecord({self.user.username}, {self.login_at})"
