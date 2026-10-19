"""Selectors."""

from django.db.models import QuerySet
from eveuniverse.models import EveType

from aa_stratcom.constants import EveCategoryId, EveGroupId, EveTypeId


def timer_type_rules() -> dict:
    """Structure families used to narrow the Add information timer choices."""
    large = {
        EveTypeId.AZBEL,
        EveTypeId.FORTIZAR,
        EveTypeId.KEEPSTAR,
        EveTypeId.SOTIYO,
        EveTypeId.TATARA,
    }
    refineries, skyhooks = set(), {EveTypeId.ORBITAL_SKYHOOK}
    for type_id, group_id in EveType.objects.filter(
        eve_group_id__in=(EveGroupId.CITADEL, EveGroupId.REFINERY, EveGroupId.SKYHOOK)
    ).values_list("pk", "eve_group_id"):
        if group_id == EveGroupId.CITADEL and type_id != EveTypeId.ASTRAHUS:
            # Includes the faction Fortizars and Palatine Keepstar.
            large.add(type_id)
        if group_id == EveGroupId.REFINERY:
            refineries.add(type_id)
        if group_id == EveGroupId.SKYHOOK:
            skyhooks.add(type_id)
    return {
        "large": sorted(large),
        "refineries": sorted(refineries),
        "skyhooks": sorted(skyhooks),
    }


def supported_eve_types() -> QuerySet[EveType]:
    """Return query for all supported types."""
    structures = EveType.objects.filter(
        eve_group__eve_category_id=EveCategoryId.STRUCTURE,
        published=True,
    )
    groups_published = EveType.objects.filter(
        eve_group_id__in=[
            EveGroupId.CONTROL_TOWER,
            EveGroupId.MOBILE_DEPOT,
            EveGroupId.MERCENARY_DEN,
        ],
        published=True,
    )
    groups_unpublished = EveType.objects.filter(
        eve_group_id__in=[EveGroupId.PIRATE_FORWARD_OPERATING_BASE]
    )
    types = EveType.objects.filter(
        id__in=[
            EveTypeId.CUSTOMS_OFFICE,
            EveTypeId.ORBITAL_SKYHOOK,
            EveTypeId.IHUB,
            EveTypeId.TCU,
        ]
    )
    qs = (structures | groups_published | groups_unpublished | types).distinct()
    return qs
