import time
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse
from google.api_core.exceptions import PermissionDenied
from google.auth.exceptions import DefaultCredentialsError

from .firebase_auth import AuthUnavailable


class SessionDependencyFailureTests(SimpleTestCase):
    def test_dependency_errors_log_only_stage_and_exception_type(self):
        for mock_target, error, stage, error_type in (
            ("firebase_auth.verify_id_token", DefaultCredentialsError("sensitive detail"),
             "verify_id_token", "DefaultCredentialsError"),
            ("get_store", PermissionDenied("sensitive detail"),
             "firestore_account_lookup", "PermissionDenied"),
            ("firebase_auth.create_cookie", AuthUnavailable("sensitive detail"),
             "create_session_cookie", "AuthUnavailable"),
        ):
            with self.subTest(stage=stage):
                with (
                    patch("portal.views.firebase_auth.verify_id_token", return_value={
                        "uid": "private-uid", "auth_time": time.time(),
                    }),
                    patch("portal.views.get_store") as get_store,
                    patch("portal.views." + mock_target, side_effect=error),
                    self.assertLogs("portal.views", level="WARNING") as logs,
                ):
                    get_store.return_value.account.return_value = {"role": "student"}
                    result = self.client.post(
                        reverse("create-session"), {"id_token": "private-token"}, secure=True,
                    )
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json(), {"error": "Sign-in is temporarily unavailable."})
                self.assertEqual(len(result.cookies), 0)
                self.assertEqual(logs.output, [
                    f"WARNING:portal.views:LabLink sign-in dependency unavailable stage={stage} error_type={error_type}",
                ])
