"""WSGI entry point for the full LabLink application on Vercel."""

import base64
import json
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lablink.vercel_settings")

# Configure Google Application Default Credentials from Vercel environment variables.
raw_creds = os.environ.get("LABLINK_GOOGLE_CREDENTIALS_JSON") or os.environ.get("LABLINK_GOOGLE_CREDENTIALS_BASE64")
if raw_creds:
    creds_text = raw_creds.strip()
    # Support base64-encoded credentials to avoid whitespace/newline mangling
    if not creds_text.startswith("{"):
        try:
            creds_text = base64.b64decode(creds_text).decode("utf-8")
        except Exception:
            pass

    # Ensure private key literal \n escapes are resolved
    try:
        parsed = json.loads(creds_text)
        if "private_key" in parsed and "\\n" in parsed["private_key"]:
            parsed["private_key"] = parsed["private_key"].replace("\\n", "\n")
        creds_text = json.dumps(parsed)
    except Exception:
        pass

    cred_file = NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False)
    cred_file.write(creds_text)
    cred_file.flush()
    cred_file.close()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_file.name

if project_id := os.environ.get("FIREBASE_PROJECT_ID"):
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)

from django.core.wsgi import get_wsgi_application

app = get_wsgi_application()
