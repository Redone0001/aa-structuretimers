"""Copy the independent old structuretimers app into an empty StratCom app."""

from django.core.management.base import BaseCommand

from aa_stratcom.legacy_import import import_legacy


class Command(BaseCommand):
    help = (
        "Copy legacy structuretimers data and permissions without changing originals."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        counts, already_done = import_legacy(dry_run=options["dry_run"])
        if already_done:
            self.stdout.write(
                "Already copied; no data changed. This is a one-time copy, not ongoing sync."
            )
            return
        for model, count in sorted(counts.items()):
            self.stdout.write(f"{model}: {count}")
        self.stdout.write(
            "Dry run rolled back; source unchanged."
            if options["dry_run"]
            else "Copy complete; source unchanged. Copied Discord/notification/federation integrations are disabled."
        )
