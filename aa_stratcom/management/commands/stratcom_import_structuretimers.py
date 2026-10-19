"""Copy the independent old structuretimers app into an empty StratCom app."""

from django.core.management.base import BaseCommand

from aa_stratcom.legacy_import import import_legacy


class Command(BaseCommand):
    help = (
        "Copy legacy structuretimers data and permissions without changing originals."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--recon-only",
            action="store_true",
            help="Copy only preliminary/Database recon records.",
        )
        parser.add_argument(
            "--include-expired",
            action="store_true",
            help="Include expired scheduled timers (excluded by default).",
        )
        parser.add_argument(
            "--replace-stratcom-data",
            action="store_true",
            help="Clear ALL StratCom data/configuration first; requires --recon-only. Source and Auth users remain untouched.",
        )

    def handle(self, *args, **options):
        counts, already_done = import_legacy(
            dry_run=options["dry_run"],
            recon_only=options["recon_only"],
            include_expired=options["include_expired"],
            replace=options["replace_stratcom_data"],
        )
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
