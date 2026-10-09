"""Pytest configuration for OpenWindow Django tests."""

import os
import sys

# Ensure the openwindow package root is on the path
_openwindow_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _openwindow_root not in sys.path:
    sys.path.insert(0, _openwindow_root)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "openwindow.config.django_settings")

# Configure Django before pytest-django picks it up
from django.conf import settings as django_settings  # noqa: E402
if not django_settings.configured:
    from django import setup as django_setup  # noqa: E402
    django_setup()

# Prevent pytest from treating the tests/ dir as a Python package
collect_ignore_glob = ["conftest.py"]
