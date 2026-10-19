"""Integration settings: requires the real legacy aa-structuretimers package."""

from .local import *  # noqa: F403,F401

INSTALLED_APPS += ["structuretimers"]  # noqa: F405
APPS_WITH_PUBLIC_VIEWS = ["aa_stratcom", "structuretimers"]
