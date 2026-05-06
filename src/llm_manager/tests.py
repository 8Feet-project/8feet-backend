from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from llm_manager.interface.llm_interface import (
    create_or_update_llm_config,
    normalize_provider_base_url,
)


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
