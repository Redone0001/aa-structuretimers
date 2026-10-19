"""Preserve legacy outcomes when copying older source schemas."""

# Preserve the historical algorithms against migration-state models, not live signals.
# pylint: disable=too-many-locals,duplicate-code

from datetime import datetime

from django.conf import settings
from django.db import models
from django.utils.dateparse import parse_datetime


def archive_existing_kills(apps, schema_editor):
    """Carry current battle kills into the new persistent destruction status."""
    database = schema_editor.connection.alias
    state_model = apps.get_model("aa_stratcom", "MapTimerState")
    structure_model = apps.get_model("aa_stratcom", "Structure")
    timer_model = apps.get_model("aa_stratcom", "Timer")
    user_model = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    states = state_model.objects.using(database).select_related("timer")
    for state in states.iterator():
        timer = state.timer
        if timer.date is None:
            continue
        observations = [
            event
            for event in state.events
            if event.get("schedule") == timer.date.isoformat()
            and event.get("action") in ("kill", "restore")
        ]
        if not observations or observations[-1]["action"] != "kill":
            continue
        event = observations[-1]
        if timer.structure_id is None:
            existing = (
                timer_model.objects.using(database)
                .filter(
                    structures_structure_id=timer.structures_structure_id,
                    structure__isnull=False,
                )
                .values_list("structure_id", flat=True)
                .first()
                if timer.structures_structure_id
                else None
            )
            if existing is None:
                snapshot = structure_model.objects.using(database).create(
                    pk=(
                        structure_model.objects.using(database).aggregate(
                            value=models.Max("pk")
                        )["value"]
                        or 0
                    )
                    + 1,
                    **{
                        name: getattr(timer, name)
                        for name in (
                            "eve_solar_system_id",
                            "structure_type_id",
                            "structure_name",
                            "owner_corporation_id",
                            "owner_name",
                            "location_details",
                            "objective",
                            "visibility",
                            "is_opsec",
                            "user_id",
                            "eve_character_id",
                            "eve_corporation_id",
                            "eve_alliance_id",
                            "reinforcement_time",
                            "details_notes",
                        )
                    },
                )
                existing = snapshot.pk
            timer_model.objects.using(database).filter(pk=timer.pk).update(
                structure_id=existing
            )
            timer.structure_id = existing
        at = datetime.fromisoformat(event["at"])
        user_id = (
            user_model.objects.using(database)
            .filter(pk=event.get("user"))
            .values_list("pk", flat=True)
            .first()
        )
        structure_model.objects.using(database).filter(pk=timer.structure_id).filter(
            models.Q(destroyed_at__isnull=True) | models.Q(destroyed_at__lt=at)
        ).update(
            destroyed_at=at, destroyed_by_id=user_id, destroyed_by_timer_id=timer.pk
        )


def preserve_existing_outcomes(apps, schema_editor):
    """Include existing map kills, undo events and manual destruction markers."""
    database = schema_editor.connection.alias
    history = apps.get_model("aa_stratcom", "StructureHistory").objects.using(database)
    states = apps.get_model("aa_stratcom", "MapTimerState").objects.using(database)
    structures = apps.get_model("aa_stratcom", "Structure").objects.using(database)
    users = apps.get_model(*settings.AUTH_USER_MODEL.split(".")).objects.using(database)
    for state in states.select_related("timer").iterator():
        timer = state.timer
        if not timer.structure_id:
            continue
        for event in state.events:
            action = {"kill": "destroyed", "restore": "restored"}.get(
                event.get("action")
            )
            try:
                at = parse_datetime(event.get("at") or "")
                scheduled = parse_datetime(event.get("schedule") or "")
            except (ValueError, TypeError):
                continue
            if action and at:
                user_id = (
                    users.filter(pk=event.get("user"))
                    .values_list("pk", flat=True)
                    .first()
                )
                history.get_or_create(
                    structure_id=timer.structure_id,
                    timer_id=timer.pk,
                    action=action,
                    at=at,
                    scheduled_at=scheduled,
                    defaults={"user_id": user_id},
                )
    for structure in structures.exclude(destroyed_at=None).iterator():
        if not history.filter(
            structure_id=structure.pk, action="destroyed", at=structure.destroyed_at
        ).exists():
            history.create(
                structure_id=structure.pk,
                action="destroyed",
                at=structure.destroyed_at,
                user_id=structure.destroyed_by_id,
                timer_id=structure.destroyed_by_timer_id,
            )


def flag_existing_timers(apps, schema_editor):
    """Retain the legacy default for scheduled Discord timers."""
    timer_model = apps.get_model("aa_stratcom", "Timer")
    timer_model.objects.using(schema_editor.connection.alias).exclude(
        timer_type="PL"
    ).update(discord_timerboard=True)
