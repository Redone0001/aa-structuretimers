"""Compatibility imports for installations predating AA StratCom.

Submodules resolve to the canonical module objects, never duplicate Django models.
"""

# pylint: disable=invalid-name,unused-argument

import importlib
import importlib.abc
import importlib.util
import sys

from aa_stratcom import __title__, __version__  # noqa: F401

default_app_config = "aa_stratcom.apps.StratComConfig"


class _LegacyLoader(importlib.abc.Loader):
    def create_module(self, spec):
        return importlib.import_module(
            "aa_stratcom" + spec.name[len("structuretimers") :]
        )

    def exec_module(self, module):
        """Canonical module is already initialized."""


class _LegacyFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        """Resolve old imports to the new package without reloading models."""
        if fullname.startswith("structuretimers."):
            canonical = "aa_stratcom" + fullname[len("structuretimers") :]
            found = importlib.util.find_spec(canonical)
            if found is not None:
                return importlib.util.spec_from_loader(
                    fullname,
                    _LegacyLoader(),
                    is_package=found.submodule_search_locations is not None,
                )
        return None


sys.meta_path.insert(0, _LegacyFinder())
