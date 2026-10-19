"""Copy classic timerboard records without changing the source application."""

import json

from django.apps import apps
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils.timezone import now
from eveuniverse.models import EveSolarSystem, EveType

from aa_stratcom.models import Structure, Timer, TimerboardImport
from aa_stratcom.sync_policy import read_only


class Command(BaseCommand):
    help = "Copy all classic timerboard timers and access grants into AA StratCom."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if not apps.is_installed("allianceauth.timerboard"):
            raise CommandError(
                "Keep allianceauth.timerboard installed for this import."
            )
        if read_only():
            raise CommandError("Import on the master: this instance is read-only.")
        # Optional source app: importing its models before this check breaks sites
        # that have never installed timerboard.
        from .aa_stratcom_migrate_timers import (
            _OBJECTIVE_MAP,
            _STRUCTURE_MAP,
            _TIMER_TYPE_MAP,
        )

        source_model = apps.get_model("timerboard", "Timer")
        copied = skipped = unresolved = 0
        for source in source_model.objects.select_related("eve_corp").order_by("pk"):
            if TimerboardImport.objects.filter(source_id=source.pk).exists():
                skipped += 1
                continue
            system = EveSolarSystem.objects.filter(name__iexact=source.system).first()
            type_id = _STRUCTURE_MAP.get(source.structure)
            if not EveType.objects.filter(pk=type_id).exists():
                type_id = None
            timer_type = _TIMER_TYPE_MAP.get(source.timer_type, Timer.Type.NONE)
            if source.structure == source_model.Structure.MOONPOP:
                if source.timer_type == source_model.TimerType.UNSPECIFIED:
                    timer_type = Timer.Type.MOONMINING
            if not system or not type_id:
                unresolved += 1
                self.stdout.write(
                    f"Source #{source.pk}: unresolved system/type; retained in import archive."
                )
            common = dict(
                details_notes=source.details,
                last_updated_at=now(),
                structure_name=source.structure if not type_id else "",
                eve_alliance_id=(
                    source.eve_corp.alliance_id if source.eve_corp else None
                ),
                eve_corporation_id=source.eve_corp_id,
                eve_character_id=source.eve_character_id,
                eve_solar_system=system,
                location_details=source.planet_moon,
                objective=_OBJECTIVE_MAP.get(
                    source.objective, Timer.Objective.UNDEFINED
                ),
                structure_type_id=type_id,
                user_id=source.user_id,
                visibility=(
                    Timer.Visibility.CORPORATION
                    if source.corp_timer
                    else Timer.Visibility.UNRESTRICTED
                ),
            )
            # A separate structure preserves each source record's privacy and avoids
            # merging unrelated structures merely because system/time coincide.
            structure = Structure(**common, created_at=now())
            structure.save_base(raw=True)
            timer = Timer(
                **common,
                structure=structure,
                date=source.eve_time,
                timer_type=timer_type,
                is_important=source.important,
            )
            # Do not emit notifications, federation exports or distance jobs while
            # copying historical records. The normal UI recalculates as needed.
            timer.save_base(raw=True)
            encoder = DjangoJSONEncoder()
            snapshot = {
                field.attname: getattr(source, field.attname)
                for field in source._meta.fields
            }
            TimerboardImport.objects.create(
                source_id=source.pk,
                timer=timer,
                source_data=json.loads(encoder.encode(snapshot)),
            )
            copied += 1
        self._copy_permissions()
        if options["dry_run"]:
            transaction.set_rollback(True)
        self.stdout.write(
            f"{'Dry run' if options['dry_run'] else 'Import'}: copied {copied}, "
            f"already imported {skipped}, unresolved {unresolved}. Source unchanged."
        )

    @staticmethod
    def _copy_permissions():
        mapping = {
            "timer_view": ("basic_access",),
            "timer_management": ("basic_access", "create_timer", "manage_timer"),
        }
        for source_code, targets in mapping.items():
            for source in Permission.objects.filter(
                content_type__app_label="auth", codename=source_code
            ):
                for code in targets:
                    target = Permission.objects.get(
                        content_type__app_label="aa_stratcom",
                        content_type__model="general",
                        codename=code,
                    )
                    for user in source.user_set.all():
                        user.user_permissions.add(target)
                    for group in source.group_set.all():
                        group.permissions.add(target)
