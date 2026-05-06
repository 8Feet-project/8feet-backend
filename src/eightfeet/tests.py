from unittest.mock import patch

from django.test import SimpleTestCase, override_settings


@override_settings(SECURE_SSL_REDIRECT=True, ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class HealthEndpointTests(SimpleTestCase):
    def test_healthz_is_not_redirected_when_ssl_redirect_enabled(self):
        response = self.client.get("/healthz", secure=False)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], 0)

    @patch("eightfeet.health._check_db", return_value=False)
    @patch("eightfeet.health._check_redis", return_value=True)
    @patch("eightfeet.health._check_minio", return_value=True)
    def test_readyz_returns_503_when_dependency_check_fails(self, *_mocks):
        response = self.client.get("/readyz", secure=False)

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["code"], 503)
        self.assertEqual(payload["data"]["status"], "not_ready")
