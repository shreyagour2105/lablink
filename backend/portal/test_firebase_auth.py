from datetime import timedelta
from unittest.mock import patch, sentinel

from django.test import SimpleTestCase
from firebase_admin import auth
from google.auth.exceptions import DefaultCredentialsError

from . import firebase_auth


class FirebaseAuthTests(SimpleTestCase):
    @patch("portal.firebase_auth.firebase_app", return_value=sentinel.app)
    def test_verification_keeps_revocation_checks_enabled(self, _):
        with (
            patch.object(
                auth, "verify_id_token", return_value=sentinel.identity
            ) as verify_id,
            patch.object(
                auth, "verify_session_cookie", return_value=sentinel.session
            ) as verify_session,
            patch.object(
                auth, "create_session_cookie", return_value=sentinel.cookie
            ) as create_cookie,
        ):
            lifetime = timedelta(hours=8)
            self.assertIs(firebase_auth.verify_id_token("token"), sentinel.identity)
            self.assertIs(firebase_auth.verify_cookie("cookie"), sentinel.session)
            self.assertIs(
                firebase_auth.create_cookie("token", lifetime), sentinel.cookie
            )
        verify_id.assert_called_once_with("token", app=sentinel.app, check_revoked=True)
        verify_session.assert_called_once_with(
            "cookie", app=sentinel.app, check_revoked=True
        )
        create_cookie.assert_called_once_with(
            "token", expires_in=lifetime, app=sentinel.app
        )

    @patch("portal.firebase_auth.firebase_app", return_value=sentinel.app)
    def test_invalid_credentials_and_service_errors_remain_distinct(self, _):
        for sdk_method, operation, args, provider_error, expected_error in (
            (
                "verify_id_token",
                firebase_auth.verify_id_token,
                ("token",),
                auth.RevokedIdTokenError("revoked"),
                firebase_auth.InvalidCredential,
            ),
            (
                "verify_session_cookie",
                firebase_auth.verify_cookie,
                ("cookie",),
                auth.RevokedSessionCookieError("revoked"),
                firebase_auth.InvalidCredential,
            ),
            (
                "create_session_cookie",
                firebase_auth.create_cookie,
                ("token", timedelta(hours=8)),
                auth.UserDisabledError("disabled"),
                firebase_auth.InvalidCredential,
            ),
            (
                "verify_id_token",
                firebase_auth.verify_id_token,
                ("token",),
                DefaultCredentialsError("unavailable"),
                firebase_auth.AuthUnavailable,
            ),
        ):
            with (
                self.subTest(operation=sdk_method, error=type(provider_error).__name__),
                patch.object(auth, sdk_method, side_effect=provider_error),
                self.assertRaises(expected_error) as caught,
            ):
                operation(*args)
            self.assertIs(caught.exception.__cause__, provider_error)
