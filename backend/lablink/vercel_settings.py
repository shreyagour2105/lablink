import os as _os

from .settings import *  # noqa: F403

# Vercel serves the static build directly, without a Django static manifest.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MIDDLEWARE = [
    middleware for middleware in MIDDLEWARE  # noqa: F405
    if middleware != "whitenoise.middleware.WhiteNoiseMiddleware"
]

# Trust only the exact hostnames Vercel assigns to this project and deployment.
ALLOWED_HOSTS = list(dict.fromkeys([
    *ALLOWED_HOSTS,  # noqa: F405
    "shreyagour2105-lablink-yjmg.vercel.app",
    "shreyagour2105-lablink-yjmg-shreya-3f2c.vercel.app",
    *(hostname for name in ("VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_URL")
      if (hostname := _os.environ.get(name))),
]))
