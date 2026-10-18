"""Archive destroyed structures without losing their fittings or timer history."""

from datetime import datetime

from django.contrib.auth import get_user_model
from django.utils.timezone import now

from .models import MapTimerState, Structure, Timer


def structure_for_timer(timer):
    """Lock the local record, taking a local snapshot for external timers."""
    if not timer.structure_id:
        structure_id = (
            Timer.objects.filter(
                structures_structure_id=timer.structures_structure_id,
                structure__isnull=False,
            )
            .values_list("structure_id", flat=True)
            .first()
            if timer.structures_structure_id
            else None
        )
        structure = (
            Structure.objects.get(pk=structure_id)
            if structure_id
            else Structure.objects.find_or_create_for_timer(timer)
        )
        Timer.objects.filter(pk=timer.pk).update(structure=structure)
        timer.structure = structure
    return Structure.objects.select_for_update().get(pk=timer.structure_id)


def _set_status(structure, at, user_id=None, timer_id=None):
    fields = {
        "destroyed_at": at,
        "destroyed_by_id": user_id,
        "destroyed_by_timer_id": timer_id,
        "last_updated_at": now(),
    }
    Structure.objects.filter(pk=structure.pk).update(**fields)
    for name, value in fields.items():
        setattr(structure, name, value)


def mark_destroyed(structure, user, at=None, timer=None):
    """Store the destruction marker; a battle event cannot replace a manual mark."""
    if timer and structure.destroyed_at and not structure.destroyed_by_timer_id:
        return
    _set_status(structure, at or now(), user.pk, timer.pk if timer else None)


def _live_kills(structure):
    states = MapTimerState.objects.filter(timer__structure=structure).select_related(
        "timer"
    )
    for state in states:
        if state.timer.date is None:
            continue
        observations = [
            event
            for event in state.events
            if event.get("schedule") == state.timer.date.isoformat()
            and event.get("action") in ("kill", "restore")
        ]
        if observations and observations[-1]["action"] == "kill":
            yield state, observations[-1]


def undo_timer_kill(structure, timer):
    """Undo this timer's marker, retaining other kills or a manual destruction."""
    if structure.destroyed_by_timer_id != timer.pk:
        return
    remaining = list(_live_kills(structure))
    if remaining:
        state, event = max(remaining, key=lambda item: item[1]["at"])
        user_id = (
            get_user_model()
            .objects.filter(pk=event.get("user"))
            .values_list("pk", flat=True)
            .first()
        )
        _set_status(
            structure, datetime.fromisoformat(event["at"]), user_id, state.timer_id
        )
    else:
        _set_status(structure, None)


def restore_structure(structure, user):
    """An explicit recon/admin correction also undoes outstanding map kills."""
    instant = now()
    for state, _ in _live_kills(structure):
        state.events = [
            *state.events,
            {
                "action": "restore",
                "at": instant.isoformat(),
                "schedule": state.timer.date.isoformat(),
                "user": user.pk,
            },
        ]
        state.revision += 1
        state.save(update_fields=["events", "revision"])
    _set_status(structure, None)
