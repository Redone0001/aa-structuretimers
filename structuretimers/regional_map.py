"""Permission-aware adapter between SDE geography and structure timer overlays."""

import math
from collections import defaultdict
from datetime import timedelta

from django.apps import apps
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Count, Exists, OuterRef
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.timezone import now
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .models import Timer
from .distance_ranges import JUMP_RANGES

LIGHT_YEAR = 9_460_000_000_000_000
RANGES = {key: limit for key, limit, _, _ in JUMP_RANGES}
RELATIONSHIPS = {"FR": "friendly", "NE": "neutral", "HO": "hostile", "UN": "undefined"}


def finite(*values):
    """Zero is a valid coordinate; missing/non-finite coordinates are not."""
    return all(v is not None and math.isfinite(v) for v in values)


def distance_ly(source, target):
    """Use geographic metres only, never schematic coordinates."""
    a, b = [source.x, source.y, source.z], [target.x, target.y, target.z]
    return math.dist(a, b) / LIGHT_YEAR if finite(*a, *b) else None


def sde_models():
    return tuple(
        apps.get_model("eve_sde", name)
        for name in ("Region", "SolarSystem", "Stargate")
    )


def permitted_timers(user, systems, params):
    """Apply all timer conditions to the same visible record (AND)."""
    qs = Timer.objects.visible_to_user(user).filter(eve_solar_system_id__in=systems)
    relation = params.get("relationship", "all")
    if relation != "all":
        qs = qs.filter(objective=relation)
    window = params.get("window", "preliminary")
    if window == "preliminary":
        qs = qs.filter(timer_type=Timer.Type.PRELIMINARY)
    else:
        instant = now()
        qs = qs.exclude(timer_type=Timer.Type.PRELIMINARY).filter(date__gte=instant)
        if window in ("4", "24"):
            qs = qs.filter(date__lte=instant + timedelta(hours=int(window)))
    return qs.select_related("structure_type", "user").order_by(
        "objective", "structure_type_id", "pk"
    )


def validate(params):
    if params.get("relationship", "all") not in {"all", *RELATIONSHIPS}:
        raise ValueError("Unknown relationship filter.")
    if params.get("window", "preliminary") not in {"preliminary", "4", "24", "all"}:
        raise ValueError("Unknown timer window.")
    if params.get("range", "none") not in {"none", *RANGES}:
        raise ValueError("Unknown range preset.")


def geography_payload(region, systems, gate_model):
    """Normalize the complete SDE region identically for every map consumer."""
    ids = {s.id for s in systems}
    positioned = [s for s in systems if finite(s.x_2d, s.y_2d)]
    # Determined from the full region, never from filters or the selected system.
    ox = min((s.x_2d for s in positioned), default=0)
    oy = max((s.y_2d for s in positioned), default=0)
    nearest = []
    for s in positioned:
        distances = [
            math.hypot(s.x_2d - t.x_2d, s.y_2d - t.y_2d)
            for t in positioned
            if t.pk != s.pk
        ]
        positive = [d for d in distances if d > 0]
        if positive:
            nearest.append(min(positive))
    scale = sorted(nearest)[len(nearest) // 2] / 150 if nearest else 1
    pairs = sorted(
        {
            tuple(sorted((a, b)))
            for a, b in gate_model.objects.filter(
                solar_system_id__in=ids, destination_id__in=ids
            ).values_list("solar_system_id", "destination_id")
            if a != b
        }
    )
    return {
        "region": {"id": region.pk, "name": region.name},
        "origin": [ox, oy],
        "scale": scale,
        "nodes": [
            {
                "id": s.pk,
                "name": s.name,
                "constellation": (s.constellation.name if s.constellation else ""),
                "position": (
                    [(s.x_2d - ox) / scale, -(s.y_2d - oy) / scale]
                    if finite(s.x_2d, s.y_2d)
                    else None
                ),
            }
            for s in systems
        ],
        "edges": [
            {
                "id": f"gate-{a}-{b}",
                "source": a,
                "target": b,
                "kind": "gate",
                "directed": False,
            }
            for a, b in pairs
        ],
    }


def structure_payload(user, ids, params):
    """Aggregate only visible matching timer records for either map controller."""
    groups = defaultdict(lambda: defaultdict(int))
    grouped = (
        permitted_timers(user, ids, params)
        .values(
            "eve_solar_system_id",
            "objective",
            "structure_type_id",
            "structure_type__name",
        )
        .order_by("eve_solar_system_id", "objective", "structure_type_id")
        .annotate(count=Count("pk"))
    )
    for record in grouped:
        groups[record["eve_solar_system_id"]][
            (
                record["objective"],
                record["structure_type_id"],
                record["structure_type__name"] or "Unknown structure",
            )
        ] = record["count"]
    return {
        "systems": [
            {
                "id": sid,
                "indicators": [
                    {
                        "id": f"structure-{sid}-{relation}-{type_id or 'unknown'}",
                        "category": RELATIONSHIPS[relation],
                        "label": name,
                        "type_id": type_id,
                        "count": count,
                        "symbol": {
                            "FR": "F",
                            "NE": "N",
                            "HO": "H",
                            "UN": "?",
                        }[relation],
                        "tooltip": f"{name} · {RELATIONSHIPS[relation]} · {count} timer record(s)",
                    }
                    for (relation, type_id, name), count in values.items()
                ],
            }
            for sid, values in groups.items()
        ]
    }


@login_required
@permission_required(
    ("structuretimers.basic_access", "structuretimers.recon_member"),
    raise_exception=True,
)
@require_GET
@never_cache
def map_page(request):
    return render(
        request,
        "structuretimers/regional_map.html",
        {"title": "Regional map", "jump_ranges": JUMP_RANGES},
    )


@login_required
@permission_required(
    ("structuretimers.basic_access", "structuretimers.recon_member"),
    raise_exception=True,
)
@require_GET
@never_cache
def map_data(request, layer):
    """Separate geography, timer overlay and detail requests; no shared private cache."""
    try:
        region_model, system_model, gate_model = sde_models()
    except LookupError:
        return JsonResponse(
            {
                "error": "Enable eve_sde, migrate and import the SDE to use the regional map."
            },
            status=503,
        )
    try:
        validate(request.GET)
        if layer == "regions":
            # SDE system ID namespaces distinguish known space from wormholes
            # (31m) and Abyssal space (32m), without name or size heuristics.
            # Missing schematic coordinates do not remove otherwise valid regions.
            return JsonResponse(
                {
                    "regions": list(
                        region_model.objects.exclude(
                            name__in=(
                                []
                                if request.GET.get("include_test") == "1"
                                else ["A821-A", "UUA-F4", "J7HZ-F"]
                            )
                        )
                        .filter(
                            Exists(
                                system_model.objects.filter(
                                    constellation__region_id=OuterRef("pk"),
                                    id__gte=30_000_000,
                                    id__lt=31_000_000,
                                )
                            )
                        )
                        .order_by("name")
                        .values("id", "name")
                    )
                }
            )
        if layer == "search":
            term = request.GET.get("q", "").strip()[:100]
            qs = (
                system_model.objects.filter(name__icontains=term)
                if len(term) >= 2
                else system_model.objects.none()
            )
            return JsonResponse(
                {"systems": list(qs.order_by("name").values("id", "name")[:30])}
            )
        region = get_object_or_404(region_model, pk=int(request.GET.get("region", "0")))
        systems = list(
            system_model.objects.filter(constellation__region=region)
            .select_related("constellation")
            .order_by("id")
        )
        ids = {s.id for s in systems}
        if layer == "geography":
            return JsonResponse(geography_payload(region, systems, gate_model))
        if layer == "structures":
            return JsonResponse(structure_payload(request.user, ids, request.GET))
        if layer == "range":
            preset = request.GET.get("range", "none")
            if preset == "none":
                return JsonResponse({"systems": []})
            source = get_object_or_404(
                system_model, pk=int(request.GET.get("source", "0"))
            )
            return JsonResponse(
                {
                    "limit_ly": RANGES[preset],
                    "source": {"id": source.id, "name": source.name},
                    "systems": [
                        {"id": s.pk, "distance_ly": distance_ly(source, s)}
                        for s in systems
                    ],
                }
            )
        if layer == "details":
            sid = int(request.GET.get("system", "0"))
            if sid not in ids:
                return JsonResponse(
                    {"error": "System is outside this region."}, status=404
                )
            timers = permitted_timers(request.user, [sid], request.GET)
            return JsonResponse(
                {
                    "timers": [
                        {
                            "id": t.pk,
                            "name": t.structure_name or "Unnamed structure",
                            "type": (
                                t.structure_type.name
                                if t.structure_type
                                else "Unknown structure"
                            ),
                            "relationship": RELATIONSHIPS[t.objective],
                            "timer_type": t.get_timer_type_display(),
                            "date": t.date.isoformat() if t.date else None,
                            "owner": t.owner_name,
                            "location": t.location_details,
                            "edit_url": (
                                reverse("structuretimers:edit", args=[t.pk])
                                if t.user_can_edit(request.user)
                                else None
                            ),
                            "delete_url": (
                                reverse("structuretimers:delete", args=[t.pk])
                                if t.user_can_edit(request.user)
                                else None
                            ),
                            "refresh_url": (
                                reverse(
                                    "structuretimers:recon_action",
                                    args=[t.pk, "refresh"],
                                )
                                if t.timer_type == Timer.Type.PRELIMINARY
                                and t.user_can_edit(request.user)
                                else None
                            ),
                        }
                        for t in timers
                    ]
                }
            )
        return JsonResponse({"error": "Unknown map layer."}, status=404)
    except (ValueError, TypeError):
        return JsonResponse(
            {"error": "Invalid map filters or system/region ID."}, status=400
        )
