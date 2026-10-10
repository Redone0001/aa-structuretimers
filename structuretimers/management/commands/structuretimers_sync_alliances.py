"""Populate fleet autocomplete from CCP's public alliance directory."""

from django.core.management.base import BaseCommand, CommandError

from structuretimers.alliance_directory import sync_alliances


class Command(BaseCommand):
    help = "Load public alliance names for regional-map fleet autocomplete."

    def handle(self, *args, **options):
        try:
            count = sync_alliances()
        except Exception as exc:
            raise CommandError(f"Alliance directory refresh failed: {exc}") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Alliance directory ready: {count} active alliances resolved."
            )
        )
