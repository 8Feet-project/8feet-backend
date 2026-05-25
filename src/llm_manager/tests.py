from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from llm_manager.interface.llm_interface import (
    create_or_update_llm_config,
    get_provider_runtime_config,
    normalize_provider_base_url,
    serialize_admin_model_item,
    serialize_model_permission_options,
    set_default_summary_model,
)
from llm_manager.models import LLMConfig, ModelObjectMapping, ModelPermission
from llm_manager.models.model_permission import USAGE_TYPE_SUMMARIZE


class ProviderBaseUrlTests(SimpleTestCase):
    def test_meteor_root_url_uses_openai_compatible_v1_base(self):
        self.assertEqual(
            normalize_provider_base_url("MeteorAPI", "https://meteor041.com"),
            "https://meteor041.com/v1",
        )

    def test_meteor_explicit_api_path_is_preserved(self):
        self.assertEqual(
            normalize_provider_base_url("MeteorAPI", "https://meteor041.com/v1"),
            "https://meteor041.com/v1",
        )

    def test_other_providers_are_not_rewritten(self):
        self.assertEqual(
            normalize_provider_base_url("OpenAI", "https://api.openai.com/v1"),
            "https://api.openai.com/v1",
        )


class CreateOrUpdateLLMConfigTests(SimpleTestCase):
    @patch("llm_manager.interface.llm_interface.LLMConfig.objects")
    def test_create_uses_boolean_coercion_without_runtime_error(self, objects):
        objects.filter.return_value.order_by.return_value.first.return_value = None
        created = MagicMock(id=7)
        objects.create.return_value = created

        success, message, config_id = create_or_update_llm_config(
            name="gpt-4o",
            provider="OpenAI",
            api_endpoint="https://api.openai.com/v1",
            api_key="secret",
            is_enabled="true",
            is_online="false",
        )

        self.assertTrue(success)
        self.assertIsNone(message)
        self.assertEqual(config_id, 7)
        objects.create.assert_called_once()
        _, kwargs = objects.create.call_args
        self.assertIs(kwargs["is_enabled"], True)
        self.assertIs(kwargs["is_online"], False)


class DefaultSummaryModelTests(TestCase):
    def test_set_default_summary_model_replaces_existing_defaults(self):
        previous = LLMConfig.objects.create(
            name="previous",
            provider="OpenAI",
            api_endpoint="https://example.com/v1",
            api_key_encrypted="previous-key",
        )
        selected = LLMConfig.objects.create(
            name="selected",
            provider="OpenAI",
            api_endpoint="https://example.com/v1",
            api_key_encrypted="selected-key",
        )
        ModelObjectMapping.objects.create(
            llm_config=previous,
            object_type="COMPANY",
            usage_type=USAGE_TYPE_SUMMARIZE,
            priority=100,
            is_default=True,
        )

        success, message, payload = set_default_summary_model(selected.id, True)

        self.assertTrue(success, message)
        self.assertEqual(payload["model_id"], str(selected.id))
        self.assertFalse(
            ModelObjectMapping.objects.filter(
                llm_config=previous,
                usage_type=USAGE_TYPE_SUMMARIZE,
                is_default=True,
            ).exists()
        )
        self.assertEqual(
            ModelObjectMapping.objects.filter(
                llm_config=selected,
                usage_type=USAGE_TYPE_SUMMARIZE,
                is_default=True,
            ).count(),
            3,
        )
        serialized = serialize_admin_model_item(selected)
        self.assertTrue(serialized["is_default_summary_model"])
        self.assertIn("COMPANY", serialized["default_summary_object_types"])


class AdminModelPermissionSerializationTests(TestCase):
    def test_admin_model_item_includes_permission_subject_details(self):
        user = get_user_model().objects.create_user(
            username="authorized-user",
            email="authorized@example.com",
            password="test-pass-123",
        )
        config = LLMConfig.objects.create(
            name="permission-model",
            provider="OpenAI",
            api_endpoint="https://example.com/v1",
            api_key_encrypted="secret",
        )
        ModelPermission.objects.create(llm_config=config, user=user, is_active=True)
        ModelPermission.objects.create(llm_config=config, role="admin", is_active=True)

        serialized = serialize_admin_model_item(config)

        self.assertEqual(serialized["permission_user_ids"], [user.id])
        self.assertEqual(serialized["permission_users"][0]["username"], "authorized-user")
        self.assertEqual(serialized["permission_group_ids"], ["group-admin"])
        self.assertEqual(serialized["permission_groups"][0]["label"], "管理员")
        self.assertIn("authorized-user", serialized["granted_scope_summary"])
        self.assertIn("管理员", serialized["granted_scope_summary"])

    def test_permission_options_include_users_and_supported_groups(self):
        user = get_user_model().objects.create_user(
            username="option-user",
            email="option@example.com",
            password="test-pass-123",
        )

        options = serialize_model_permission_options()

        self.assertIn(user.id, [item["user_id"] for item in options["users"]])
        self.assertIn("group-admin", [item["group_id"] for item in options["groups"]])
        self.assertIn("group-user", [item["group_id"] for item in options["groups"]])


class ProviderRuntimeConfigTests(SimpleTestCase):
    def test_runtime_config_defaults_to_streaming_enabled(self):
        config = MagicMock(
            name="gpt-5.4",
            provider="OpenAI",
            api_endpoint="https://example.com/v1",
            api_key_encrypted="secret",
            params={},
        )
        config.name = "gpt-5.4"

        success, message, runtime = get_provider_runtime_config(config)

        self.assertTrue(success)
        self.assertEqual(runtime["provider"], "OpenAI")
        self.assertIsNone(message)
        self.assertIs(runtime["streaming"], True)

    def test_runtime_config_allows_stream_flag_to_disable_streaming(self):
        config = MagicMock(
            name="gpt-5.4",
            provider="OpenAI",
            api_endpoint="https://example.com/v1",
            api_key_encrypted="secret",
            params={"stream": "false", "debug_provider_http": "false"},
        )
        config.name = "gpt-5.4"

        success, _message, runtime = get_provider_runtime_config(config)

        self.assertTrue(success)
        self.assertIs(runtime["streaming"], False)
        self.assertIs(runtime["debug_provider_http"], False)
