from django.core.management.base import BaseCommand, CommandError
from django.db.models import QuerySet
from django.utils.timezone import now
from eveuniverse.models import EveSolarSystem

from allianceauth.timerboard.models import Timer as AuthTimer
from app_utils.datetime import DATETIME_FORMAT
from app_utils.django import app_labels

from aa_stratcom.constants import EveTypeId
from aa_stratcom.models import Timer

_OBJECTIVE_MAP = {
    AuthTimer.Objective.FRIENDLY: Timer.Objective.FRIENDLY,
    AuthTimer.Objective.HOSTILE: Timer.Objective.HOSTILE,
    AuthTimer.Objective.NEUTRAL: Timer.Objective.NEUTRAL,
}


_STRUCTURE_MAP = {
    AuthTimer.Structure.ANSIBLEX: EveTypeId.ANSIBLEX,
    AuthTimer.Structure.ASTRAHUS: EveTypeId.ASTRAHUS,
    AuthTimer.Structure.ATHANOR: EveTypeId.ATHANOR,
    AuthTimer.Structure.AZBEL: EveTypeId.AZBEL,
    AuthTimer.Structure.FORTIZAR: EveTypeId.FORTIZAR,
    AuthTimer.Structure.IHUB: EveTypeId.IHUB,
    AuthTimer.Structure.KEEPSTAR: EveTypeId.KEEPSTAR,
    AuthTimer.Structure.MERCDEN: EveTypeId.MERCENARY_DEN,
    AuthTimer.Structure.METENOX: EveTypeId.METENOX_MOON_DRILL,
    AuthTimer.Structure.MOONPOP: EveTypeId.ATHANOR,
    AuthTimer.Structure.ORBITALSKYHOOK: EveTypeId.ORBITAL_SKYHOOK,
    AuthTimer.Structure.PHAROLUX: EveTypeId.PHAROLUX,
    AuthTimer.Structure.POCO: EveTypeId.CUSTOMS_OFFICE,
    AuthTimer.Structure.POSL: EveTypeId.CALDARI_CONTROL_TOWER,
    AuthTimer.Structure.POSM: EveTypeId.CALDARI_CONTROL_TOWER_MEDIUM,
    AuthTimer.Structure.POSS: EveTypeId.CALDARI_CONTROL_TOWER_SMALL,
    AuthTimer.Structure.RAITARU: EveTypeId.RAITARU,
    AuthTimer.Structure.SOTIYO: EveTypeId.SOTIYO,
    AuthTimer.Structure.TATARA: EveTypeId.TATARA,
    AuthTimer.Structure.TENEBREX: EveTypeId.TENEBREX,
}

_TIMER_TYPE_MAP = {
    AuthTimer.TimerType.ANCHORING: Timer.Type.ANCHORING,
    AuthTimer.TimerType.ARMOR: Timer.Type.ARMOR,
    AuthTimer.TimerType.FINAL: Timer.Type.FINAL,
    AuthTimer.TimerType.HULL: Timer.Type.HULL,
    AuthTimer.TimerType.THEFT: Timer.Type.THEFT,
    AuthTimer.TimerType.UNANCHORING: Timer.Type.UNANCHORING,
    AuthTimer.TimerType.UNSPECIFIED: Timer.Type.NONE,
}


class Command(BaseCommand):
    help = "Migrate pending timers from Alliance Auth's Structure Timers app."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Perform's a dry run. Does not actually migrate, but will show potential issues.",
        )

    def _migrate_timers(
        self, auth_timers_qs: QuerySet[AuthTimer], is_dry_run: bool
    ) -> None:
        migrated_count = 0
        issues_count = 0
        skipped_count = 0
        for auth_timer in auth_timers_qs:
            try:
                eve_solar_system = EveSolarSystem.objects.get(name=auth_timer.system)
            except EveSolarSystem.DoesNotExist:
                self.stdout.write(
                    self.style.WARNING(
                        f"Can't migrate timer with unrecognized solar system: {_auth_timer_str(auth_timer)}"
                    )
                )
                issues_count += 1
                continue

            if auth_timer.structure not in _STRUCTURE_MAP:
                self.stdout.write(
                    self.style.WARNING(
                        f"Can't migrate timer with unrecognized structure: {_auth_timer_str(auth_timer)}"
                    )
                )
                issues_count += 1
                continue

            structure_type_id = _STRUCTURE_MAP[auth_timer.structure]

            if auth_timer.objective not in _OBJECTIVE_MAP:
                self.stdout.write(
                    self.style.NOTICE(
                        f"Unrecognized objective for timer: {_auth_timer_str(auth_timer)}"
                    )
                )
                objective = Timer.Objective.UNDEFINED
            else:
                objective = _OBJECTIVE_MAP[auth_timer.objective]

            if auth_timer.timer_type not in _TIMER_TYPE_MAP:
                self.stdout.write(
                    self.style.NOTICE(
                        f"Unrecognized type for timer: {_auth_timer_str(auth_timer)}"
                    )
                )
                timer_type = Timer.Type.NONE
            else:
                timer_type = _TIMER_TYPE_MAP[auth_timer.timer_type]

            if (
                auth_timer.structure == AuthTimer.Structure.MOONPOP
                and auth_timer.timer_type == AuthTimer.TimerType.UNSPECIFIED
            ):
                timer_type = Timer.Type.MOONMINING

            if auth_timer.corp_timer:
                visibility = Timer.Visibility.CORPORATION
            else:
                visibility = Timer.Visibility.UNRESTRICTED

            try:
                Timer.objects.get(
                    date=auth_timer.eve_time,
                    eve_solar_system=eve_solar_system,
                    structure_type_id=structure_type_id,
                    timer_type=timer_type,
                )

            except Timer.DoesNotExist:
                if not is_dry_run:
                    Timer.objects.create(
                        date=auth_timer.eve_time,
                        details_notes=auth_timer.details,
                        eve_alliance=auth_timer.eve_corp.alliance,
                        eve_character=auth_timer.eve_character,
                        eve_corporation=auth_timer.eve_corp,
                        eve_solar_system=eve_solar_system,
                        is_important=auth_timer.important,
                        location_details=auth_timer.planet_moon,
                        objective=objective,
                        structure_type_id=structure_type_id,
                        timer_type=timer_type,
                        user=auth_timer.user,
                        visibility=visibility,
                    )
                migrated_count += 1

            else:
                self.stdout.write(
                    self.style.NOTICE(
                        f"Skipping already existing timer: {_auth_timer_str(auth_timer)}"
                    )
                )
                skipped_count += 1

        total = migrated_count + issues_count + skipped_count
        self.stdout.write(
            f"Results: Migrated: {migrated_count} - Issues: {issues_count} "
            f"- Total: {total}"
        )

    def handle(self, *args, **options):
        if "timerboard" not in app_labels():
            raise CommandError("The Alliance Auth timerboard app is not installed.")

        auth_timers_qs = AuthTimer.objects.filter(eve_time__gt=now())
        is_dry_run = options["dry_run"]
        count = auth_timers_qs.count()
        topic = "dry run" if is_dry_run else "migration"
        self.stdout.write(f"Started {topic} for {count} timers")
        self._migrate_timers(auth_timers_qs, is_dry_run=is_dry_run)
        self.stdout.write(self.style.SUCCESS(f"{topic.capitalize()} completed!"))


def _auth_timer_str(timer: AuthTimer) -> str:
    eve_time = timer.eve_time.strftime(DATETIME_FORMAT)
    timer_type = AuthTimer.TimerType(timer.timer_type).label
    location = timer.system
    if timer.planet_moon:
        location += f" - {timer.planet_moon}"
    return f"{timer_type} in {location} @ {eve_time}"
