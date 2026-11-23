"""WSGI entry point for the full LabLink application on Vercel."""

import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lablink.vercel_settings")

from django.core.exceptions import ImproperlyConfigured
from lablink.credentials import CredentialConfigurationError, normalize_service_account

# Google ADC reads a file lazily. Retain the private temporary file for this worker.
raw_json = os.environ.get("LABLINK_GOOGLE_CREDENTIALS_JSON", "").strip()
raw_base64 = os.environ.get("LABLINK_GOOGLE_CREDENTIALS_BASE64", "").strip()
if raw_json and raw_base64:
    raise ImproperlyConfigured(
        "Set only one LabLink service-account credential variable."
    )
if raw_credentials := raw_json or raw_base64:
    try:
        credentials = normalize_service_account(
            raw_credentials,
            expected_project_id=os.environ.get("FIREBASE_PROJECT_ID", "").strip(),
        )
    except CredentialConfigurationError as error:
        raise ImproperlyConfigured(str(error)) from error
    credential_file = NamedTemporaryFile(  # noqa: SIM115
        mode="w", encoding="utf-8", suffix=".json"
    )
    credential_file.write(credentials)
    credential_file.flush()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credential_file.name

if project_id := os.environ.get("FIREBASE_PROJECT_ID"):
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)

from django.core.wsgi import get_wsgi_application

app = get_wsgi_application()
