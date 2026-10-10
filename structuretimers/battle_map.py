"""Experimental regional battle timeline and shared fleet intelligence."""

import json
from datetime import datetime, time, timedelta, timezone

from django import forms
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from .alliance_directory import lookup_alliances, resolve_alliance

from .models import MapFleetToken, MapTimerState, Timer
from .regional_map import RELATIONSHIPS, sde_models

DURATIONS = {"AR": 900, "HL": 1800, "FI": 900, "AN": 900, "UA": 0}


def timer_events(timer):
    try:
        state = timer.map_state
    except MapTimerState.DoesNotExist:
        return [], 0
    return [
        e for e in state.events if e.get("schedule") == timer.date.isoformat()
    ], state.revision


def timer_clock(timer, events, instant):
    """Replay observations at an instant without moving the original timer date."""
    duration = DURATIONS.get(timer.timer_type)
    elapsed = (instant - timer.date).total_seconds()
    paused = None
    killed = False
    for event in events:
        at = datetime.fromisoformat(event["at"])
        if at > instant:
            break
        if event["action"] == "pause":
            paused = at
        elif event["action"] == "resume" and paused:
            elapsed -= (at - paused).total_seconds()
            paused = None
        elif event["action"] == "adjust":
            elapsed -= event["seconds"]
        elif event["action"] == "kill":
            killed = True
        elif event["action"] == "restore":
            killed = False
    if paused:
        elapsed -= (instant - paused).total_seconds()
    remaining = None if duration is None else max(0, duration - elapsed)
    return elapsed, paused, killed, remaining


def timer_status(timer, events, instant):
    if timer.date is None:
        return "unscheduled"
    elapsed, paused, killed, remaining = timer_clock(timer, events, instant)
    if killed:
        return "killed"
    if elapsed < 0:
        return "upcoming"
    if paused:
        return "paused"
    if remaining is None:
        return "occurred"
    return "open" if remaining > 0 else "repaired"


def visible_tokens(user):
    # Access to this endpoint already requires regional-map basic_access.
    return MapFleetToken.objects.all()


class FleetForm(forms.ModelForm):
    class Meta:
        model = MapFleetToken
        fields = [
            "system_id",
            "alliance_name",
            "ship_name",
            "dps",
            "logi",
            "mobility",
            "stance",
            "dscan",
            "note",
        ]


def token_payload(token, user):
    fields = [*FleetForm.Meta.fields, "id", "alliance_id", "ship_id", "revision"]
    return {
        **{f: getattr(token, f) for f in fields},
        "can_edit": token.user_can_edit(user),
        "updated_at": token.updated_at.isoformat(),
    }


def lookup(kind, term):
    if kind == "alliance":
        return lookup_alliances(term)
    # The complete installed SDE supplies ships, even if eveuniverse has not loaded them.
    from django.apps import apps

    type_model = apps.get_model("eve_sde", "ItemType")
    return list(
        type_model.objects.filter(group__category_id=6, name__icontains=term)
        .order_by("name")
        .values("id", "name")[:30]
    )


@login_required
@permission_required("structuretimers.basic_access", raise_exception=True)
@require_http_methods(["GET", "POST"])
@never_cache
def battle_data(request, layer):
    try:
        if request.method == "POST":
            body = json.loads(request.body)
            if not isinstance(body, dict):
                raise ValueError("Expected an object.")
            if layer == "timer":
                return update_timer(request.user, body)
            if layer == "fleet":
                return update_fleet(request.user, body)
            return JsonResponse({"error": "Unknown action."}, status=404)
        if layer == "lookup":
            kind = request.GET.get("kind")
            if kind not in ("alliance", "ship"):
                raise ValueError("Unknown lookup.")
            term = request.GET.get("q", "").strip()[:200]
            return JsonResponse(
                {"results": lookup(kind, term) if len(term) >= 2 else []}
            )
        if layer != "snapshot":
            return JsonResponse({"error": "Unknown layer."}, status=404)
        _, system_model, _ = sde_models()
        region_id = int(request.GET.get("region", "0"))
        systems = dict(
            system_model.objects.filter(constellation__region_id=region_id).values_list(
                "id", "name"
            )
        )
        day = datetime.strptime(
            request.GET.get("day", now().date().isoformat()), "%Y-%m-%d"
        ).date()
        start = datetime.combine(day, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        timers = (
            Timer.objects.visible_to_user(request.user)
            .filter(eve_solar_system_id__in=systems, date__isnull=False)
            .exclude(timer_type=Timer.Type.PRELIMINARY)
            .filter(
                Q(date__gte=start - timedelta(days=1), date__lt=end)
                | Q(map_state__isnull=False)
            )
            .select_related("map_state", "structure_type", "user")
            .order_by("date", "pk")
        )
        records = []
        for timer in timers:
            events, revision = timer_events(timer)
            if timer.date >= end or (
                timer.date < start - timedelta(days=1)
                and timer_status(timer, events, start) not in ("paused", "open")
            ):
                continue
            records.append(
                {
                    "id": timer.pk,
                    "system_id": timer.eve_solar_system_id,
                    "system": systems[timer.eve_solar_system_id],
                    "name": timer.structure_name or "Unnamed structure",
                    "type": (
                        timer.structure_type.name
                        if timer.structure_type
                        else "Unknown structure"
                    ),
                    "type_id": timer.structure_type_id,
                    "timer_type": timer.timer_type,
                    "timer_label": timer.get_timer_type_display(),
                    "relationship": RELATIONSHIPS[timer.objective],
                    "date": timer.date.isoformat(),
                    "duration": DURATIONS.get(timer.timer_type),
                    "events": [
                        {k: e[k] for k in ("action", "at", "seconds") if k in e}
                        for e in events
                    ],
                    "revision": revision,
                    "can_edit": timer.user_can_edit(request.user),
                }
            )
        return JsonResponse(
            {
                "timers": records,
                "tokens": [
                    token_payload(t, request.user)
                    for t in visible_tokens(request.user).filter(system_id__in=systems)
                ],
                "server_time": now().isoformat(),
                "day": day.isoformat(),
                "scope": "all map users",
            }
        )
    except (ValueError, TypeError, OverflowError) as ex:
        return JsonResponse({"error": str(ex) or "Invalid input."}, status=400)
    except LookupError:
        return JsonResponse(
            {"error": "Import the EVE SDE to use the battle map."}, status=503
        )


@transaction.atomic
def update_timer(user, body):
    timer = get_object_or_404(
        Timer.objects.filter(
            pk__in=Timer.objects.visible_to_user(user).values("pk")
        ).select_for_update(),
        pk=int(body.get("id", 0)),
    )
    if not timer.user_can_edit(user):
        return JsonResponse({"error": "You cannot edit this timer."}, status=403)
    if timer.date is None:
        raise ValueError("Only scheduled timers support battle observations.")
    state, _ = MapTimerState.objects.get_or_create(timer=timer)
    if body.get("revision") != state.revision:
        return JsonResponse(
            {"error": "Timer changed. Refresh before editing."}, status=409
        )
    events, _ = timer_events(timer)
    instant = now()
    status = timer_status(timer, events, instant)
    action = body.get("action")
    allowed = {
        "pause": status == "open",
        "resume": status == "paused",
        "adjust": status == "paused",
        "kill": status != "killed",
        "restore": status == "killed",
    }
    if not allowed.get(action):
        raise ValueError(
            "This action is unavailable for the timer's current live state."
        )
    adjustment = {}
    if action == "adjust":
        seconds = body.get("seconds")
        if type(seconds) is not int or seconds not in (-60, 60):
            raise ValueError("Adjust remaining time by exactly one minute.")
        remaining = timer_clock(timer, events, instant)[3]
        if (
            remaining is None
            or not 0 <= remaining + seconds <= DURATIONS[timer.timer_type]
        ):
            raise ValueError(
                "Remaining time must stay within the timer's repair window."
            )
        adjustment = {"seconds": seconds}
    state.events = [
        *state.events,
        {
            **adjustment,
            "action": action,
            "at": instant.isoformat(),
            "schedule": timer.date.isoformat(),
            "user": user.pk,
        },
    ]
    state.revision += 1
    state.save(update_fields=["events", "revision"])
    return JsonResponse({"ok": True})


@transaction.atomic
def update_fleet(user, body):
    token_id = body.get("id")
    token = (
        get_object_or_404(visible_tokens(user).select_for_update(), pk=int(token_id))
        if token_id
        else MapFleetToken(creator=user)
    )
    if not token.user_can_edit(user):
        return JsonResponse({"error": "You cannot edit this token."}, status=403)
    if token_id and body.get("revision") != token.revision:
        return JsonResponse(
            {"error": "Token changed. Refresh before editing."}, status=409
        )
    if body.get("action") == "delete":
        if not token_id:
            raise ValueError("Choose a token to delete.")
        token.delete()
        return JsonResponse({"ok": True})
    if body.get("action") == "move":
        if not token_id:
            raise ValueError("Choose a token to move.")
        _, system_model, _ = sde_models()
        destination = get_object_or_404(system_model, pk=int(body.get("system_id", 0)))
        token.system_id = destination.pk
        token.revision += 1
        token.save(update_fields=["system_id", "revision", "updated_at"])
        return JsonResponse({"token": token_payload(token, user)})
    form = FleetForm({"mobility": "gate", "stance": "foe", **body}, instance=token)
    if not form.is_valid():
        return JsonResponse(
            {
                "error": "; ".join(
                    f"{k}: {', '.join(v)}" for k, v in form.errors.items()
                )
            },
            status=400,
        )
    _, system_model, _ = sde_models()
    get_object_or_404(system_model, pk=form.cleaned_data["system_id"])
    token = form.save(commit=False)
    # Resolve images from names on the server; arbitrary IDs cannot spoof a match.
    token.alliance_id = resolve_alliance(token.alliance_name)
    token.ship_id = None
    if token.ship_name:
        from django.apps import apps

        type_model = apps.get_model("eve_sde", "ItemType")
        token.ship_id = (
            type_model.objects.filter(
                group__category_id=6, name__iexact=token.ship_name
            )
            .values_list("pk", flat=True)
            .first()
        )
    token.revision += 1
    token.save()
    return JsonResponse({"token": token_payload(token, user)})
