from django.apps import AppConfig

from . import __version__


class StratComConfig(AppConfig):
    name = "aa_stratcom"
    label = "aa_stratcom"
    verbose_name = f"AA StratCom v{__version__}"

    def ready(self):
        from . import sync_signals  # noqa: F401
