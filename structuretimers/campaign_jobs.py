"""Background universe imports; web requests only persist work and enqueue it."""

import logging

from celery import shared_task
from django.core.cache import cache
from django.db import transaction
from eveuniverse.models import EveRegion, EveSolarSystem

from .models import ReconCampaign, ReconCampaignSystem

logger = logging.getLogger(__name__)


def enqueue_campaign_job(pk, kind, refresh=False):
    """Broker errors leave a visible retryable state, never a broken create response."""
    try:
        prepare_campaign.apply_async(args=[pk, kind, refresh], retry=False)
    except Exception:
        logger.exception("Could not queue campaign %s %s import", pk, kind)
        changes = (
            {"import_status": "failed", "gates_status": "failed"}
            if kind == "systems"
            else {"gates_status": "failed"}
        )
        ReconCampaign.objects.filter(
            pk=pk,
            **{("import_status" if kind == "systems" else "gates_status"): "pending"},
        ).update(**changes)


@shared_task(
    soft_time_limit=600, time_limit=660, acks_late=True, reject_on_worker_lost=True
)
def prepare_campaign(pk, kind, refresh=False):
    campaign = ReconCampaign.objects.filter(pk=pk).first()
    if campaign is None:
        return
    status_field = "import_status" if kind == "systems" else "gates_status"
    if getattr(campaign, status_field) != "pending":
        return
    try:
        if kind == "systems":
            system_ids = set()
            for region_id in campaign.region_ids:
                key = f"structuretimers:campaign-region:{region_id}"
                cached_ids = cache.get(key)
                if cached_ids and EveSolarSystem.objects.filter(
                    pk__in=cached_ids
                ).count() == len(cached_ids):
                    system_ids.update(cached_ids)
                    continue
                # Gate imports are separate so the campaign becomes usable sooner.
                EveRegion.objects.update_or_create_esi(
                    id=region_id, include_children=True
                )
                ids = list(
                    EveSolarSystem.objects.filter(
                        eve_constellation__eve_region_id=region_id
                    ).values_list("pk", flat=True)
                )
                if not ids:
                    raise ValueError("Region imported without systems")
                cache.set(key, ids, timeout=3600)
                system_ids.update(ids)
            with transaction.atomic():
                current = ReconCampaign.objects.select_for_update().get(pk=pk)
                if current.import_status != "pending":
                    return
                existing = set(
                    current.systems.values_list("solar_system_id", flat=True)
                )
                ReconCampaignSystem.objects.bulk_create(
                    [
                        ReconCampaignSystem(campaign=current, solar_system_id=system_id)
                        for system_id in system_ids - existing
                    ]
                )
                current.import_status = "ready"
                current.save(update_fields=["import_status"])
                transaction.on_commit(lambda: enqueue_campaign_job(pk, "gates"))
        else:
            importer = (
                EveSolarSystem.objects.update_or_create_esi
                if refresh
                else EveSolarSystem.objects.get_or_create_esi
            )
            for system_id in campaign.systems.values_list("solar_system_id", flat=True):
                importer(
                    id=system_id,
                    include_children=True,
                    enabled_sections=[EveSolarSystem.Section.STARGATES],
                )
            ReconCampaign.objects.filter(pk=pk).update(gates_status="ready")
    except Exception:
        logger.exception("Campaign %s %s import failed", pk, kind)
        changes = (
            {"import_status": "failed", "gates_status": "failed"}
            if kind == "systems"
            else {"gates_status": "failed"}
        )
        ReconCampaign.objects.filter(
            pk=pk,
            **{("import_status" if kind == "systems" else "gates_status"): "pending"},
        ).update(**changes)
