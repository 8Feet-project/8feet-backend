import jwt

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase

from analytics.models.logs import SystemLog
from shared.permissions import ROLE_PERMISSIONS, setup_groups, to_product_permission_codes
from users.models.user_profile import ROLE_SUPER_ADMIN, UserProfile


class AdminLogPermissionTests(TestCase):
    def test_role_permission_configuration_points_to_existing_permissions(self):
        configured_codes = sorted({
            code
            for permission_codes in ROLE_PERMISSIONS.values()
            for code in permission_codes
        })
        existing_codes = {
            f"{permission.content_type.app_label}.{permission.codename}"
            for permission in Permission.objects.select_related("content_type")
        }

        missing_codes = [code for code in configured_codes if code not in existing_codes]

        self.assertEqual(missing_codes, [])

    def test_super_admin_group_maps_to_admin_log_product_permissions(self):
        setup_groups()
        user = get_user_model().objects.create_user(
            username="super-admin",
            password="test-pass-123",
        )
        UserProfile.objects.create(user=user, role=ROLE_SUPER_ADMIN)

        permissions = to_product_permission_codes(user.get_all_permissions())

        self.assertIn("admin:logs:read", permissions)
        self.assertIn("admin:logs:export", permissions)

    def test_admin_log_api_uses_log_permission_instead_of_dashboard_permission(self):
        user = get_user_model().objects.create_user(
            username="log-reader",
            password="test-pass-123",
        )
        view_log_permission = Permission.objects.get(
            content_type__app_label="analytics",
            codename="view_audit_log",
        )
        user.user_permissions.add(view_log_permission)
        SystemLog.objects.create(
            level="INFO",
            module="permissions",
            message="log permission smoke test",
        )
        token = jwt.encode(
            {"user_id": user.id, "type": "access_token"},
            settings.SECRET_KEY,
            algorithm="HS256",
        )

        response = Client(HTTP_AUTHORIZATION=f"Bearer {token}").get(
            "/api/v1/admin/logs/",
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["total"], 1)
