"""
Django settings for OpenWindow — the Sentinel Ops Control Desk.

Designed to work alongside the existing FastAPI server in `openwindow/api/app.py`.
Configured for SQLite in dev/test and PostgreSQL in production.
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Keep secret key simple for dev/test
SECRET_KEY = "django-insecure-openwindow-dev-key-change-me"  # noqa: S105

DEBUG = os.getenv("DJANGO_DEBUG", "false").strip().lower() in ("1", "true", "yes")
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]

# Use the incidents app alongside existing modules
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "openwindow.incidents",
]

def _postgres_from_url(url: str) -> dict:
    """Build a Django PostgreSQL database config from a postgres:// URL."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username or "",
        "PASSWORD": parsed.password or "",
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
    }


# SQLite by default (local dev/unit tests); DJANGO_DB_URL switches to PostgreSQL
# (used by the no-mock live integration harness, D12).
DJANGO_DB_URL = os.getenv("DJANGO_DB_URL")
if DJANGO_DB_URL:
    DATABASES = {"default": _postgres_from_url(DJANGO_DB_URL)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("DJANGO_DB", os.path.join(BASE_DIR, "db.sqlite3")),
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

DISABLE_MIGRATION_CHECKS = True

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

ROOT_URLCONF = "openwindow.incidents.urls"

# Templates
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

# Sentinel execution handler URL for the approve callback
SENTINEL_CALLBACK_URL = os.getenv("SENTINEL_CALLBACK_URL", "http://localhost:8001/api/v1/execute")
