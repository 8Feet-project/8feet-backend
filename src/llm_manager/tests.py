from django.test import SimpleTestCase

from llm_manager.interface.llm_interface import normalize_provider_base_url


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
