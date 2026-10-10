"""Structure owners: real player corporations, resolved once and remembered."""

from typing import Optional

import requests
from eveuniverse.models import EveEntity

from django.db.models import Q

from . import __version__
from .models import Organization

#: Select2 value prefix for "look this name up on ESI".
LOOKUP_PREFIX = "lookup:"
ESI_URL = "https://esi.evetech.net/latest"
USER_AGENT = (
    f"aa-structuretimers/{__version__} "
    "(+https://github.com/Redone0001/aa-structuretimers)"
)


def _esi_get(path: str) -> dict:
    response = requests.get(
        f"{ESI_URL}{path}", headers={"User-Agent": USER_AGENT}, timeout=(5, 30)
    )
    response.raise_for_status()
    return response.json()


def _alliance_for_corporation(corporation_id: int) -> Optional[Organization]:
    alliance_id = _esi_get(f"/corporations/{corporation_id}/").get("alliance_id")
    if not alliance_id:
        return None
    alliance, _ = Organization.objects.update_or_create(
        id=alliance_id,
        defaults={
            "name": EveEntity.objects.resolve_name(alliance_id),
            "category": Organization.Category.ALLIANCE,
        },
    )
    return alliance


def add_corporation(corporation_id: int, name: str) -> Organization:
    """Remember a corporation as an owner, with its current alliance."""
    organization, _ = Organization.objects.update_or_create(
        id=corporation_id,
        defaults={
            "name": name,
            "category": Organization.Category.CORPORATION,
            "alliance": _alliance_for_corporation(corporation_id),
        },
    )
    return organization


def resolve_corporation(name: str) -> Optional[Organization]:
    """Return the player corporation with this exact name, looking it up on ESI
    the first time it is seen. Returns None when no such corporation exists.
    """
    name = (name or "").strip()
    if not name:
        return None
    known = Organization.objects.filter(
        category=Organization.Category.CORPORATION, name__iexact=name
    ).first()
    if known:
        return known
    # ESI matches names regardless of case, but the returned queryset filters on
    # the exact spelling, so look the stored entity up case-insensitively.
    EveEntity.objects.fetch_by_names_esi([name])
    entity = EveEntity.objects.filter(
        category=EveEntity.CATEGORY_CORPORATION, name__iexact=name
    ).first()
    if not entity:
        return None
    return add_corporation(entity.id, entity.name)


def known_owner(value) -> Optional[Organization]:
    """Return a remembered owner corporation by its ID, or None."""
    try:
        pk = int(value)
    except (TypeError, ValueError):
        return None
    return Organization.objects.filter(
        pk=pk, category=Organization.Category.CORPORATION
    ).first()


def clean_owner_value(value) -> Optional[Organization]:
    """Turn an Owner field value (ID or lookup) into a corporation, or None."""
    value = str(value or "").strip()
    if value.startswith(LOOKUP_PREFIX):
        return resolve_corporation(value[len(LOOKUP_PREFIX) :])
    if not value:
        return None
    return known_owner(value) or resolve_corporation(value)


def autocomplete(term: str) -> list:
    """Remembered owners matching the term, plus an ESI lookup for new names."""
    term = (term or "").strip()
    if len(term) < 2:
        return []
    matches = (
        Organization.objects.filter(category=Organization.Category.CORPORATION)
        .filter(Q(name__icontains=term) | Q(alliance__name__icontains=term))
        .select_related("alliance")
        .order_by("name")[:30]
    )
    results = [{"id": str(org.pk), "text": org.display_name} for org in matches]
    if len(term) >= 3 and not any(
        org.name.casefold() == term.casefold() for org in matches
    ):
        results.append(
            {
                "id": f"{LOOKUP_PREFIX}{term}",
                "text": f'Look up corporation "{term}" on ESI',
            }
        )
    return results
