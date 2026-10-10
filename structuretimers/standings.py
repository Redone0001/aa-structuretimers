"""Owner standings: synced from our alliance's contacts, overridable by coordinators."""

from typing import Iterable, Optional

import requests
from eveuniverse.models import EveEntity

from django.db import transaction
from django.utils.timezone import now

from .models import Organization, StandingsSource
from .owners import ESI_URL, USER_AGENT

CONTACTS_SCOPE = "esi-alliances.read_contacts.v1"
STANDING_VALUES = tuple(value for value, _ in Organization.Standing.choices)


def bucket(value: float) -> int:
    """Round an in-game standing (-10..10) to the nearest of the five groups."""
    return min(STANDING_VALUES, key=lambda option: (abs(option - value), -option))


def effective_standing(organization: Optional[Organization]) -> Optional[int]:
    """Standing of an owner: its own (override first), then its alliance's."""
    return None if organization is None else organization.effective_standing


def refresh_objectives() -> None:
    """Re-derive friend or foe on every owned timer after standings changed."""
    from .models import Timer  # pylint: disable=import-outside-toplevel

    owner_ids = (
        Timer.objects.filter(owner_corporation__isnull=False)
        .values_list("owner_corporation_id", flat=True)
        .distinct()
    )
    by_objective = {}
    for owner in Organization.objects.filter(pk__in=owner_ids).select_related(
        "alliance"
    ):
        objective = Organization.objective_for_standing(owner.effective_standing)
        by_objective.setdefault(objective, []).append(owner.pk)
    for objective, ids in by_objective.items():
        Timer.objects.filter(owner_corporation_id__in=ids).exclude(
            objective=objective
        ).update(objective=objective)


def standing_label(value: Optional[int]) -> str:
    if value is None:
        return "?"
    return f"+{value}" if value > 0 else str(value)


def _get_pages(path: str, token) -> list:
    results, page, pages = [], 1, 1
    while page <= pages:
        response = requests.get(
            f"{ESI_URL}{path}",
            params={"page": page},
            headers={
                "User-Agent": USER_AGENT,
                "Authorization": f"Bearer {token.valid_access_token()}",
            },
            timeout=(5, 30),
        )
        response.raise_for_status()
        results += response.json()
        pages = int(response.headers.get("X-Pages", 1))
        page += 1
    return results


def _remember(ids_and_categories: Iterable[tuple]) -> None:
    """Make sure every contact exists as an Organization with its name."""
    ids_and_categories = list(ids_and_categories)
    resolver = EveEntity.objects.bulk_resolve_names([i for i, _ in ids_and_categories])
    for entity_id, category in ids_and_categories:
        Organization.objects.get_or_create(
            id=entity_id,
            defaults={"name": resolver.to_name(entity_id), "category": category},
        )


def sync_source(source: StandingsSource) -> int:
    """Pull one alliance's contacts and store them as automatic standings."""
    contacts = _get_pages(f"/alliances/{source.alliance_id}/contacts/", source.token)
    groups = {
        contact["contact_id"]: (
            Organization.Category.CORPORATION
            if contact["contact_type"] == "corporation"
            else Organization.Category.ALLIANCE,
            bucket(float(contact["standing"])),
        )
        for contact in contacts
        if contact.get("contact_type") in ("corporation", "alliance")
    }
    # Our own alliance is always the best standing.
    groups[source.alliance_id] = (Organization.Category.ALLIANCE, 10)
    _remember((entity_id, category) for entity_id, (category, _) in groups.items())
    with transaction.atomic():
        Organization.objects.filter(standing_auto__isnull=False).exclude(
            id__in=groups
        ).update(standing_auto=None)
        for entity_id, (_, value) in groups.items():
            Organization.objects.filter(id=entity_id).update(standing_auto=value)
        source.last_sync_at = now()
        source.last_error = ""
        source.save(update_fields=["last_sync_at", "last_error"])
    return len(groups)


def sync_all() -> None:
    for source in StandingsSource.objects.select_related("token"):
        try:
            sync_source(source)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            source.last_error = str(ex)[:254]
            source.save(update_fields=["last_error"])
    refresh_objectives()
