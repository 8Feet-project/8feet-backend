# Generated manually to align UserProfile with the current frontend-facing contract.

from django.db import migrations, models


def normalize_roles(apps, schema_editor):
    UserProfile = apps.get_model("users", "UserProfile")
    role_map = {
        "ADMIN": "admin",
        "NORMAL": "user",
        "USER": "user",
        "SUPER_ADMIN": "super_admin",
    }
    for profile in UserProfile.objects.all():
        normalized = role_map.get(profile.role, profile.role)
        if normalized != profile.role:
            profile.role = normalized
            profile.save(update_fields=["role"])


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="nickname",
            field=models.CharField(
                blank=True,
                help_text="用户昵称",
                max_length=64,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="email_verified",
            field=models.BooleanField(
                default=False,
                help_text="邮箱是否已验证",
            ),
        ),
        migrations.RunPython(normalize_roles, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="userprofile",
            name="role",
            field=models.CharField(
                choices=[
                    ("super_admin", "超级管理员"),
                    ("admin", "管理员"),
                    ("user", "普通用户"),
                ],
                default="user",
                help_text="用户角色",
                max_length=16,
            ),
        ),
        migrations.RemoveField(
            model_name="userprofile",
            name="organization",
        ),
    ]
