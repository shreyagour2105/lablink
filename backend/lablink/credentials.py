"""Validation for service-account credentials supplied through environment variables."""

import base64
import binascii
import json


class CredentialConfigurationError(ValueError):
    """Raised when server credentials cannot be safely loaded."""


REQUIRED_FIELDS = ("client_email", "private_key", "project_id")


def normalize_service_account(raw: str, *, expected_project_id: str = "") -> str:
    """Return validated service-account JSON from raw JSON or strict base64."""
    text = raw.strip()
    if not text:
        raise CredentialConfigurationError("Service-account credentials are empty.")
    if not text.startswith("{"):
        try:
            text = base64.b64decode(text, validate=True).decode("utf-8")
        except (
            binascii.Error,
            UnicodeDecodeError,
            UnicodeEncodeError,
            ValueError,
        ) as error:
            raise CredentialConfigurationError(
                "Service-account credentials must be JSON or valid base64-encoded JSON."
            ) from error
    try:
        credentials = json.loads(text)
    except json.JSONDecodeError as error:
        raise CredentialConfigurationError(
            "Service-account credentials contain invalid JSON."
        ) from error
    if (
        not isinstance(credentials, dict)
        or credentials.get("type") != "service_account"
    ):
        raise CredentialConfigurationError(
            "Credentials must describe a service account."
        )
    if any(
        not isinstance(credentials.get(field), str) or not credentials[field].strip()
        for field in REQUIRED_FIELDS
    ):
        raise CredentialConfigurationError(
            "Service-account credentials are missing a required field."
        )
    if expected_project_id and credentials["project_id"] != expected_project_id:
        raise CredentialConfigurationError(
            "Service-account credentials belong to a different Firebase project."
        )
    credentials["private_key"] = credentials["private_key"].replace("\\n", "\n")
    return json.dumps(credentials)
