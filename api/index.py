"""WSGI entry point for the full LabLink application on Vercel."""

import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lablink.vercel_settings")

# Google ADC reads a file. Keep the Vercel secret in private, ephemeral storage.
if credentials := os.environ.get("LABLINK_GOOGLE_CREDENTIALS_JSON"):
    # ADC opens the credentials lazily. Retain the file until this worker exits.
    credential_file = NamedTemporaryFile(mode="w", encoding="utf-8")  # noqa: SIM115
    credential_file.write(credentials)
    credential_file.flush()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credential_file.name

from django.core.wsgi import get_wsgi_application

app = get_wsgi_application()
