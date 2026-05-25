import jwt

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch

from analytics.interface.analytics_interface import create_alert, dispatch_alert_report_ready, trigger_due_alerts
from analytics.models.logs import SystemLog
from analytics.models.personalization import Alert, UserMessage
from llm_manager.models import LLMConfig
from llm_manager.models.model_permission import ModelPermission
from reports.models.report import Report
from research.models import ResearchTask
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
        UserProfile.objects.create(user=user, role=ROLE_SUPER_ADMIN)
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


class AlertScheduleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="alert-user",
            email="alert-user@example.com",
            password="test-pass-123",
        )
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="analytics", codename="create_alert"),
            Permission.objects.get(content_type__app_label="analytics", codename="view_alert"),
        )
        self.model = LLMConfig.objects.create(
            name="alert-model",
            provider="openai",
            is_enabled=True,
            is_online=True,
            api_endpoint="https://example.com",
            api_key_encrypted="test",
        )
        ModelPermission.objects.create(llm_config=self.model, user=self.user, is_active=True)

    def test_create_alert_schedules_without_triggering_task(self):
        alert_id = create_alert(
            self.user.id,
            "COMPANY",
            "腾讯控股",
            {"schedule_rule": "daily", "schedule_time": "10:15"},
            notify_email=False,
        )

        alert = Alert.objects.get(pk=alert_id)
        self.assertEqual(alert.condition["schedule_time"], "10:15")
        self.assertIsNotNone(alert.next_run_at)
        self.assertIsNone(alert.last_triggered_at)
        self.assertEqual(ResearchTask.objects.count(), 0)

    @patch("research.interface.research_interface._safe_research_runtime", return_value=(None, "runtime disabled"))
    def test_status_update_triggers_once_and_pins_future_time(self, _runtime):
        alert_id = create_alert(
            self.user.id,
            "COMPANY",
            "腾讯控股",
            {"schedule_rule": "daily", "schedule_time": "10:15"},
            notify_email=False,
        )
        token = jwt.encode(
            {"user_id": self.user.id, "type": "access_token"},
            settings.SECRET_KEY,
            algorithm="HS256",
        )

        response = Client(HTTP_AUTHORIZATION=f"Bearer {token}").patch(
            f"/api/v1/alerts/{alert_id}",
            data={"status": "enabled"},
            content_type="application/json",
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertTrue(payload["triggered_task_id"])
        alert = Alert.objects.get(pk=alert_id)
        self.assertEqual(alert.last_task_id, int(payload["triggered_task_id"]))
        self.assertEqual(alert.condition["schedule_time"], timezone.localtime(alert.last_triggered_at).strftime("%H:%M"))
        self.assertGreater(alert.next_run_at, alert.last_triggered_at)
        self.assertTrue(UserMessage.objects.filter(source_alert=alert, action_url__contains="/process?task_id=").exists())

    @patch("research.interface.research_interface._safe_research_runtime", return_value=(None, "runtime disabled"))
    def test_due_alert_scan_starts_research_task(self, _runtime):
        alert_id = create_alert(
            self.user.id,
            "STOCK",
            "宁德时代",
            {"schedule_rule": "weekly", "schedule_time": "08:00"},
            notify_email=False,
        )
        alert = Alert.objects.get(pk=alert_id)
        alert.next_run_at = timezone.now() - timedelta(minutes=1)
        alert.save(update_fields=["next_run_at"])

        triggered = trigger_due_alerts()

        alert.refresh_from_db()
        self.assertEqual(triggered, 1)
        self.assertIsNotNone(alert.last_task_id)
        self.assertGreater(alert.next_run_at, timezone.now())

    def test_report_ready_notification_links_report_page(self):
        alert_id = create_alert(
            self.user.id,
            "COMPANY",
            "腾讯控股",
            {"schedule_rule": "daily", "schedule_time": "09:00"},
            notify_email=False,
        )
        task = ResearchTask.objects.create(
            user=self.user,
            title="腾讯控股 定时调研",
            object_name="腾讯控股",
            object_type="COMPANY",
            llm_config=self.model,
            search_params={"source_alert_id": alert_id},
        )
        report = Report.objects.create(
            task=task,
            title="腾讯控股 定时调研",
            summary="摘要",
            content_markdown="# 报告",
        )
        UserMessage.objects.all().delete()

        dispatch_alert_report_ready(report)

        message = UserMessage.objects.get(source_alert_id=alert_id)
        self.assertIn(f"report_id={report.id}", message.action_url)
        self.assertIn(f"task_id={task.id}", message.action_url)
