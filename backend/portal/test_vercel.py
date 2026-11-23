import base64
import json
from typing import ClassVar

from django.test import SimpleTestCase
from lablink import vercel_settings
from lablink.credentials import CredentialConfigurationError, normalize_service_account


class VercelHostTests(SimpleTestCase):
    def test_production_alias_is_accepted_and_unrelated_hosts_are_rejected(self):
        with self.settings(ALLOWED_HOSTS=vercel_settings.ALLOWED_HOSTS):
            for host, expected in (
                ("shreyagour2105-lablink-yjmg-shreya-3f2c.vercel.app", 200),
                ("unrelated.vercel.app", 400),
                ("shreyagour2105-lablink-yjmg-shreya-3f2c.vercel.app.example.com", 400),
            ):
                with self.subTest(host=host):
                    response = self.client.get("/", secure=True, HTTP_HOST=host)
                    self.assertEqual(response.status_code, expected)


class CredentialConfigurationTests(SimpleTestCase):
    credentials: ClassVar[dict[str, str]] = {
        "type": "service_account",
        "project_id": "fixture-project",
        "client_email": "fixture@example.invalid",
        "private_key": "fixture-line-one\\nfixture-line-two",
    }

    def test_accepts_json_and_strict_base64_for_expected_project(self):
        raw = json.dumps(self.credentials)
        encoded = base64.b64encode(raw.encode()).decode()
        for value in (raw, encoded):
            with self.subTest(encoded=value == encoded):
                normalized = json.loads(
                    normalize_service_account(
                        value, expected_project_id="fixture-project"
                    )
                )
                self.assertEqual(
                    normalized["private_key"], "fixture-line-one\nfixture-line-two"
                )

    def test_rejects_malformed_incomplete_and_wrong_project_credentials(self):
        cases = (
            "",
            "not valid base64",
            "{invalid json",
            "e30=",
            json.dumps({**self.credentials, "type": "authorized_user"}),
            json.dumps({**self.credentials, "client_email": ""}),
            json.dumps({**self.credentials, "project_id": "another-project"}),
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(
                CredentialConfigurationError
            ):
                normalize_service_account(value, expected_project_id="fixture-project")
