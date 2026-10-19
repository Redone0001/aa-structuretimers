from django.apps import AppConfig

from . import __version__


class StratComConfig(AppConfig):
    name = "aa_stratcom"
    label = "structuretimers"
    verbose_name = f"AA StratCom v{__version__}"

    def ready(self):
        from . import sync_signals  # noqa: F401


def __getattr__(name):
    """Support explicit AppConfig paths used by older installations."""
    if name == "StructureTimersConfig":
        return StratComConfig
    raise AttributeError(name)
