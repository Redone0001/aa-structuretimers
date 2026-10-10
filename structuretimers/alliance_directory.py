"""Public alliance names for battle intel, independent of Auth membership records."""

import requests

from allianceauth.eveonline.models import EveAllianceInfo
from eveuniverse.models import EveEntity

from . import __version__


def sync_alliances():
    """Import active alliance IDs; Eve Universe batches and caches name resolution."""
    response = requests.get(
        "https://esi.evetech.net/v1/alliances/",
        headers={
            "User-Agent": (
                f"aa-structuretimers/{__version__} "
                "(+https://github.com/Redone0001/aa-structuretimers)"
            )
        },
        timeout=(5, 60),
    )
    response.raise_for_status()
    ids = response.json()
    if not isinstance(ids, list) or any(
        type(item) is not int or item <= 0 for item in ids
    ):
        raise ValueError("ESI returned an invalid alliance list")
    # Names are immutable in EVE. Only missing names need resolution on later runs.
    # Keep historical entries: existing fleet reports may refer to closed alliances.
    EveEntity.objects.bulk_resolve_ids(ids)
    return (
        EveEntity.objects.filter(id__in=ids, category=EveEntity.CATEGORY_ALLIANCE)
        .exclude(name="")
        .count()
    )


def lookup_alliances(term):
    """Merge the public name cache with Auth's existing alliance records."""
    matches = {
        row["alliance_id"]: row
        for row in EveAllianceInfo.objects.filter(alliance_name__icontains=term)
        .order_by("alliance_name")
        .values("alliance_id", "alliance_name")[:30]
    }
    for entity in EveEntity.objects.filter(
        category=EveEntity.CATEGORY_ALLIANCE, name__icontains=term
    ).order_by("name", "id")[:30]:
        matches[entity.id] = {"alliance_id": entity.id, "alliance_name": entity.name}
    return sorted(
        matches.values(),
        key=lambda row: (row["alliance_name"].casefold(), row["alliance_id"]),
    )[:30]


def resolve_alliance(name):
    if not name:
        return None
    return (
        EveEntity.objects.filter(
            category=EveEntity.CATEGORY_ALLIANCE, name__iexact=name
        )
        .values_list("id", flat=True)
        .first()
        or EveAllianceInfo.objects.filter(alliance_name__iexact=name)
        .values_list("alliance_id", flat=True)
        .first()
    )
