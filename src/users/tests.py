import jwt

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from unittest.mock import Mock, patch

from users.interface.persona_interface import save_user_persona_report, should_prompt_persona
from users.interface.persona_runtime import (
    PERSONA_TOOLS,
    build_persona_system_message,
    run_persona_turn,
)
from llm_manager.models import LLMConfig, ModelPermission
from users.models.persona import UserPersona
from users.models.persona import UserPersonaConversation
from users.models.user_profile import ROLE_SUPER_ADMIN, ROLE_USER, UserProfile


class UserPersonaApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="persona-user",
            password="test-pass-123",
            email="persona@example.com",
        )
        UserProfile.objects.create(user=self.user, role=ROLE_USER, nickname="persona-user")
        token = jwt.encode(
            {"user_id": self.user.id, "type": "access_token"},
            settings.SECRET_KEY,
            algorithm="HS256",
        )
        self.client = Client(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_persona_detail_prompts_when_empty(self):
        response = self.client.get("/api/v1/users/me/persona", secure=True)

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertFalse(payload["has_persona"])
        self.assertTrue(payload["should_prompt_persona"])

    def test_skip_persona_prompt(self):
        response = self.client.post("/api/v1/users/me/persona/skip", secure=True)

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertFalse(payload["should_prompt_persona"])
        self.assertIsNotNone(payload["skipped_at"])

    def test_clear_keeps_prompt_available_for_regular_user(self):
        save_user_persona_report(
            user=self.user,
            content_markdown="# 用户人设\n\n偏好深度财务分析。",
            source_thread_id="thread-1",
            model_id="1",
        )

        response = self.client.post("/api/v1/users/me/persona/clear", secure=True)

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertFalse(payload["has_persona"])
        self.assertEqual(payload["content_markdown"], "")
        self.assertTrue(payload["should_prompt_persona"])

    def test_super_admin_is_not_prompted(self):
        self.user.profile.role = ROLE_SUPER_ADMIN
        self.user.profile.save(update_fields=["role"])

        self.assertFalse(should_prompt_persona(self.user))


class UserPersonaRegistrationTests(TestCase):
    def test_first_registered_super_admin_not_prompted(self):
        ok, message, result = __import__(
            "users.interface.auth_interface",
            fromlist=["register_user"],
        ).register_user(
            "first-admin",
            "First Admin",
            "test-pass-123",
            "first@example.com",
            email_verified=True,
        )

        self.assertTrue(ok, message)
        self.assertEqual(result["role"], ROLE_SUPER_ADMIN)
        self.assertFalse(result["should_prompt_persona"])

        user = get_user_model().objects.get(pk=result["user_id"])
        self.assertTrue(user.has_perm("users.view_user"))
        self.assertTrue(user.has_perm("llm_manager.change_llmconfig"))

    def test_regular_registered_user_is_prompted(self):
        User = get_user_model()
        admin = User.objects.create_user(username="admin", password="test-pass-123")
        UserProfile.objects.create(user=admin, role=ROLE_SUPER_ADMIN)

        ok, message, result = __import__(
            "users.interface.auth_interface",
            fromlist=["register_user"],
        ).register_user(
            "regular",
            "Regular",
            "test-pass-123",
            "regular@example.com",
            email_verified=True,
        )

        self.assertTrue(ok, message)
        self.assertEqual(result["role"], ROLE_USER)
        self.assertTrue(result["should_prompt_persona"])
        self.assertFalse(UserPersona.objects.filter(user__username="regular").exists())


class UserModelPermissionProvisioningTests(TestCase):
    def test_new_profile_grants_all_existing_model_permissions(self):
        first_model = LLMConfig.objects.create(
            name="first-model",
            provider="OpenAI",
            api_endpoint="https://example.com/v1",
            api_key_encrypted="secret",
        )
        second_model = LLMConfig.objects.create(
            name="second-model",
            provider="OpenAI",
            api_endpoint="https://example.com/v1",
            api_key_encrypted="secret",
            is_enabled=False,
        )
        user = get_user_model().objects.create_user(
            username="model-provisioned-user",
            password="test-pass-123",
        )

        profile = UserProfile.objects.create(user=user, role=ROLE_USER, nickname="provisioned")

        granted_model_ids = set(
            ModelPermission.objects.filter(user=user).values_list("llm_config_id", flat=True)
        )
        self.assertEqual(granted_model_ids, {first_model.id, second_model.id})

        profile.nickname = "provisioned-updated"
        profile.save(update_fields=["nickname"])

        self.assertEqual(ModelPermission.objects.filter(user=user).count(), 2)


class UserPersonaRuntimeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="persona-runtime",
            password="test-pass-123",
            email="runtime@example.com",
        )
        UserProfile.objects.create(user=self.user, role=ROLE_USER, nickname="runtime")

    def test_persona_tools_are_limited_to_required_tools(self):
        self.assertEqual(
            [tool.name for tool in PERSONA_TOOLS],
            ["web_search", "web_fetch", "write_file", "present_report"],
        )

    def test_system_message_includes_previous_persona(self):
        system_message = build_persona_system_message("# 旧人设\n\n关注财务质量。")

        self.assertIn("上一次的用户人设", system_message)
        self.assertIn("关注财务质量", system_message)
        self.assertIn("有且仅有 web_search、web_fetch、write_file、present_report", system_message)

    def test_presented_report_overwrites_user_persona(self):
        conversation = UserPersonaConversation.objects.create(
            user=self.user,
            thread_id="persona-thread",
            model_id="1",
            system_message="persona system",
        )
        fake_report = Mock()
        fake_report.path = "/mnt/user-data/outputs/user_persona.md"
        fake_report.content = "# 用户调研人设分析\n\n偏好产业链与风险分析。"
        fake_thread = Mock()
        fake_thread.history = []
        fake_thread.state = {
            "presented_reports": [
                {
                    "path": fake_report.path,
                    "content": fake_report.content,
                    "brief_path": "/mnt/user-data/outputs/user_persona_brief.md",
                    "brief_content": "偏好产业链与风险分析。",
                }
            ]
        }
        fake_thread.max_turns = 30
        fake_thread.stream.return_value = ["已完成人设分析。"]

        with (
            patch("users.interface.persona_runtime._resolve_persona_config", return_value=Mock(id=1)),
            patch("users.interface.persona_runtime._build_model", return_value=Mock()),
            patch("users.interface.persona_runtime.Thread", return_value=fake_thread),
            patch("users.interface.persona_runtime.extract_presented_reports", side_effect=[[], [fake_report]]),
        ):
            success, message, payload = run_persona_turn(conversation, "继续")

        self.assertTrue(success, message)
        self.assertEqual(payload["status"], "completed")
        persona = UserPersona.objects.get(user=self.user)
        self.assertIn("偏好产业链与风险分析", persona.content_markdown)
        self.assertEqual(persona.source_thread_id, "persona-thread")
