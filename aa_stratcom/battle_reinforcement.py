"""Armor outcomes and duplicate-safe registration of the following timer."""

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware, now

from . import destruction
from .models import StructureDetails, StructureHistory, Timer
from .operation_access import get_operation, operation_timers


def editable_timer(user, body, lock=False):
    """Use identical visibility and edit checks for prompts and mutations."""
    timers = Timer.objects.filter(
        pk__in=Timer.objects.visible_to_user(user).values("pk")
    )
    if lock:
        timers = timers.select_for_update()
    timer = get_object_or_404(timers, pk=int(body.get("id", 0)))
    if body.get("operation"):
        operation = get_operation(user, body["operation"])
        get_object_or_404(operation_timers(operation, user), pk=timer.pk)
        permitted = user.has_perm("aa_stratcom.op_coordinator")
    else:
        permitted = timer.user_can_edit(user)
    if not permitted:
        raise PermissionDenied("You cannot edit this timer.")
    return timer


def later_timers(timer):
    """Match the exact local record or external structure ID, never a name guess."""
    identity = Q(pk__in=[])
    if timer.structure_id:
        identity |= Q(structure_id=timer.structure_id)
    if timer.structures_structure_id:
        identity |= Q(structures_structure_id=timer.structures_structure_id)
    return (
        Timer.objects.filter(identity, date__gt=timer.date)
        .exclude(timer_type=Timer.Type.PRELIMINARY)
        .order_by("date", "pk")
    )


def reinforcement_options(user, body):
    """Only disclose whether a follow-up exists, never its restricted details."""
    timer = editable_timer(user, body)
    if timer.timer_type != Timer.Type.ARMOR or timer.date is None:
        raise ValueError("Only Armor timers can be marked as reinforced.")
    return {"already_registered": later_timers(timer).exists()}


def reinforce(timer, structure, user, body, instant):
    """Record armor destruction once, optionally adding the Final timer."""
    if structure.destroyed_at:
        raise ValueError(
            "Restore the destroyed structure before recording reinforcement."
        )
    following = later_timers(timer).first()
    already_registered = following is not None
    supplied = body.get("reinforcement_date")
    if supplied and not following:
        if not isinstance(supplied, str):
            raise ValueError("Enter a valid reinforcement date and time in UTC.")
        date = parse_datetime(supplied)
        if date is None or not is_aware(date) or date <= max(now(), timer.date):
            raise ValueError(
                "The new reinforcement timer must be a future UTC date and time."
            )
        # Preserve the original timer's organization and access restrictions.
        details = {
            field.attname: getattr(timer, field.attname)
            for field in StructureDetails._meta.fields
            if field.name != "last_updated_at"
        }
        following = Timer.objects.create(
            **details,
            structure=structure,
            structures_structure_id=timer.structures_structure_id,
            date=date,
            timer_type=Timer.Type.FINAL,
        )
    destruction.record_outcome(
        structure, StructureHistory.Action.REINFORCED, user, instant, timer, following
    )
    return {
        "already_registered": already_registered,
        "timer_created": bool(supplied and not already_registered),
    }
