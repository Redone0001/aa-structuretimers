"""Read-only bridge to the aa-structures app, when it is installed.

aa-structures already keeps every friendly structure of corporations that added
a token. The Database shows those rows live from its tables instead of copying
them, and timers for those structures link to them instead of creating records.
"""

import math
from typing import Optional

from django.apps import apps
from django.contrib.auth.models import User
from django.db.models import Count
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html

from allianceauth.eveonline.evelinks import dotlan
from eveuniverse.helpers import meters_to_ly
from app_utils.views import link_html

#: Prefix for aa-structures row ids, so they never collide with Database records.
ROW_PREFIX = "s-"
#: Standard Upwell vulnerability window around the reinforce hour, in minutes.
WINDOW_MINUTES = 180


def is_available() -> bool:
    """Return True when aa-structures is installed."""
    return apps.is_installed("structures")


def visible_structures(user: User):
    """Structures this user may see in aa-structures itself, or None."""
    if not is_available():
        return None
    from structures.models import Structure  # pylint: disable=import-outside-toplevel

    return Structure.objects.visible_for_user(user).select_related(
        "eve_solar_system__eve_constellation__eve_region",
        "eve_type",
        "owner__corporation__alliance",
    )


def find_structure_id(timer) -> Optional[int]:
    """Return the aa-structures id of the structure a timer belongs to, if any.

    A structure matches on solar system, type and name, ignoring case. Timers
    that aa-structures creates itself use exactly these values.
    """
    name = (timer.structure_name or "").strip()
    if not is_available() or not name or not timer.structure_type_id:
        return None
    from structures.models import Structure  # pylint: disable=import-outside-toplevel

    return (
        Structure.objects.filter(
            eve_solar_system_id=timer.eve_solar_system_id,
            eve_type_id=timer.structure_type_id,
            name__iexact=name,
        )
        .values_list("id", flat=True)
        .first()
    )


def get_structure(user: User, structure_id: int):
    """Return one structure visible to the user, or None."""
    structures = visible_structures(user)
    if structures is None:
        return None
    return structures.filter(id=structure_id).first()


def display_name(structure) -> str:
    return f'{structure.eve_type.name} "{structure.name}" in {structure.eve_solar_system.name}'


def reinforce_time(structure) -> Optional[str]:
    if structure.reinforce_hour is None:
        return None
    return f"{structure.reinforce_hour:02d}:00"


def database_rows(user: User, staging_system=None, visible_timers=None) -> list:
    """Rows for the Database table, in the same shape as Database records."""
    structures = visible_structures(user)
    if structures is None:
        return []
    structures = list(structures)
    counts = {}
    if visible_timers is not None and structures:
        counts = dict(
            visible_timers.filter(
                structures_structure_id__in=[s.id for s in structures]
            )
            .values("structures_structure_id")
            .annotate(count=Count("id"))
            .values_list("structures_structure_id", "count")
        )
    staging = staging_system.eve_solar_system if staging_system else None
    rows = []
    for structure in structures:
        system = structure.eve_solar_system
        region = system.eve_constellation.eve_region
        corporation = structure.owner.corporation
        light_years = (
            meters_to_ly(staging.distance_to(system)) if staging else None
        )
        distance_text = (
            f"{math.ceil(light_years * 10) / 10} ly" if light_years is not None else "?"
        )
        count = counts.get(structure.id, 0)
        name = format_html(
            '{}<br>{}<br><span class="badge text-bg-info">Structures</span>',
            structure.name,
            corporation.corporation_name,
        )
        if count:
            name = format_html(
                '{} <span class="badge text-bg-secondary">{} timer{}</span>',
                name,
                count,
                "" if count == 1 else "s",
            )
        rows.append(
            {
                "id": f"{ROW_PREFIX}{structure.id}",
                "source": "structures",
                "location": format_html(
                    "{}<br>{}",
                    link_html(dotlan.solar_system_url(system.name), system.name),
                    region.name,
                ),
                "distance": {"display": distance_text, "sort": light_years},
                "structure_details": render_to_string(
                    "structuretimers/partials/structure_box.html",
                    {
                        "type_icon_url": structure.eve_type.icon_url(size=64),
                        "type_name": structure.eve_type.name,
                        "timer_name": "Structures",
                        "timer_style": "info",
                        "reinforcement_time": None,
                    },
                ),
                "name_objective": name,
                "owner": format_html(
                    '<span class="badge text-bg-primary">{}</span>', "friendly"
                ),
                "reinforcement_time": reinforce_time(structure),
                "window_minutes": WINDOW_MINUTES,
                "last_updated_at": (
                    structure.last_updated_at or structure.created_at
                ).isoformat(),
                "actions": format_html(
                    '<a class="btn btn-sm btn-info" href="{}" target="_blank" '
                    'rel="noopener">View in Structures</a>',
                    reverse("structures:index"),
                ),
                "system_name": system.name,
                "region_name": region.name,
                "structure_type_name": structure.eve_type.name,
                "owner_name": corporation.corporation_name,
                "objective_name": "friendly",
                "timer_count": count,
            }
        )
    return rows


def picker_results(user: User, term: str) -> list:
    """Search results for the timer form's Existing structure picker."""
    structures = visible_structures(user)
    if structures is None:
        return []
    from django.db.models import Q  # pylint: disable=import-outside-toplevel

    matches = structures.filter(
        Q(name__icontains=term)
        | Q(eve_solar_system__name__istartswith=term)
        | Q(owner__corporation__corporation_name__icontains=term)
    ).order_by("eve_solar_system__name", "name")[:30]
    return [
        {
            "id": f"{ROW_PREFIX}{structure.id}",
            "text": f"{display_name(structure)} (Structures)",
            "solar_system": {
                "id": structure.eve_solar_system_id,
                "text": structure.eve_solar_system.name,
            },
            "structure_type": {
                "id": structure.eve_type_id,
                "text": structure.eve_type.name,
            },
            "structure_name": structure.name,
            "owner_name": structure.owner.corporation.corporation_name,
            "location_details": "",
            "objective": "FR",
            "reinforcement_time": reinforce_time(structure) or "",
        }
        for structure in matches
    ]
