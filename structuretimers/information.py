"""Read pasted EVE text for the Add information form, and find what it is about.

Understands the timer text from a structure's tooltip (including Orbital
Skyhooks) and fittings in EFT format ("[Astrahus, Name]" on the first line).
Whatever it finds is returned as form values; the matching Database record is
returned too, so a paste updates that record instead of creating a duplicate.
"""

import re

from eveuniverse.models import EveSolarSystem, EveType

from . import owners, structures_bridge
from .constants import EveCategoryId
from .forms import parse_eve_timer_text
from .models import Structure

#: First line of an EFT fitting: "[Type, Name]".
EFT_HEADER = re.compile(r"^\s*\[(?P<type>[^,\]]+),\s*(?P<name>[^\]]*)\]\s*$")


def _structure_type(name: str):
    return (
        EveType.objects.filter(
            name__iexact=name.strip(), eve_group__eve_category_id=EveCategoryId.STRUCTURE
        ).first()
        or EveType.objects.filter(name__iexact=name.strip()).first()
    )


def _option(obj, text=None):
    return {"id": str(obj.pk), "text": text or obj.name} if obj else None


def parse(text: str, user) -> dict:
    """Return the form values found in a paste, plus the matching structure."""
    text = (text or "").strip()
    values, found = {}, []
    lines = [line for line in text.splitlines() if line.strip()]
    eft = EFT_HEADER.match(lines[0]) if lines else None
    if eft:
        structure_type = _structure_type(eft.group("type"))
        if structure_type:
            values["structure_type"] = _option(structure_type)
        if eft.group("name").strip():
            values["structure_name"] = eft.group("name").strip()
        values["fitting"] = text
        found.append("fitting")
    else:
        try:
            parsed = parse_eve_timer_text(text)
        except ValueError:
            parsed = None
        if parsed:
            system = EveSolarSystem.objects.filter(
                name__iexact=parsed.solar_system_name
            ).first()
            if system:
                values["solar_system"] = _option(system)
            values["structure_name"] = parsed.structure_name
            values["date"] = parsed.date.strftime("%Y-%m-%d %H:%M")
            if parsed.location_details:
                values["location_details"] = parsed.location_details
            if parsed.owner_name:
                known = owners.Organization.objects.filter(
                    category=owners.Organization.Category.CORPORATION,
                    name__iexact=parsed.owner_name,
                ).first()
                values["owner"] = (
                    {"id": str(known.pk), "text": known.display_name}
                    if known
                    else {
                        "id": owners.LOOKUP_PREFIX + parsed.owner_name,
                        "text": f'Look up corporation "{parsed.owner_name}" on ESI',
                    }
                )
            if parsed.structure_type_name:
                structure_type = _structure_type(parsed.structure_type_name)
                if structure_type:
                    values["structure_type"] = _option(structure_type)
            found.append("timer")
    return {"found": found, "values": values, "match": find_match(user, values)}


def find_match(user, values):
    """The one Database or aa-structures record these values describe, if any."""
    name = values.get("structure_name")
    if not name:
        return None
    system_id = (values.get("solar_system") or {}).get("id")
    type_id = (values.get("structure_type") or {}).get("id")
    if user.has_perm("structuretimers.recon_member"):
        qs = Structure.objects.visible_to_user(user).filter(structure_name__iexact=name)
        if system_id:
            qs = qs.filter(eve_solar_system_id=system_id)
        if type_id:
            qs = qs.filter(structure_type_id=type_id)
        matches = list(qs.select_related("eve_solar_system", "structure_type", "owner_corporation__alliance")[:2])
        if len(matches) == 1:
            return picker_entry(matches[0])
    if structures_bridge.is_available() and system_id:
        term = name
        for row in structures_bridge.picker_results(user, term):
            if row["structure_name"].casefold() == name.casefold() and str(
                row["solar_system"]["id"]
            ) == str(system_id):
                return row
    return None


def picker_entry(entry) -> dict:
    """A Database record in the shape the Existing structure picker uses."""
    return {
        "id": entry.pk,
        "text": entry.structure_display_name,
        "solar_system": {"id": entry.eve_solar_system_id, "text": entry.eve_solar_system.name},
        "structure_type": (
            {"id": entry.structure_type_id, "text": entry.structure_type.name}
            if entry.structure_type
            else None
        ),
        "structure_name": entry.structure_name,
        "owner_name": entry.owner_name or "",
        "owner": (
            {"id": str(entry.owner_corporation_id), "text": entry.owner_corporation.display_name}
            if entry.owner_corporation_id
            else (
                {"id": owners.LOOKUP_PREFIX + entry.owner_name, "text": entry.owner_name}
                if entry.owner_name
                else None
            )
        ),
        "location_details": entry.location_details,
        "objective": entry.objective,
        "reinforcement_time": (
            entry.reinforcement_time.strftime("%H:%M") if entry.reinforcement_time else ""
        ),
    }
