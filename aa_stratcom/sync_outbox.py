"""Record local changes and safely queue the newest state for each destination."""

import logging

from django.db import transaction
from django.db.models import Q
from django.utils.timezone import now

from .sync_models import (
    TimerSyncConfig,
    TimerSyncDelivery,
    TimerSyncDestination,
    TimerSyncIdentity,
)
from .sync_policy import receiving
from .sync_protocol import serialize_timer

logger = logging.getLogger(__name__)


def authorized_destination(destination):
    """Revoking an authorizer's permission also stops its existing outbound link."""
    user = destination.authorized_by
    if TimerSyncConfig.objects.filter(pk=1, replica=True).exists():
        return False
    return (
        user.is_active
        and user.has_perm("aa_stratcom.manage_timer_sharing")
        and (
            destination.scope != TimerSyncDestination.Scope.ALL
            or user.has_perm("aa_stratcom.opsec_access")
        )
    )


def _kick():
    """A broker outage must not lose the database outbox or fail timer creation."""
    from .sync_jobs import deliver_timer_sync

    try:
        deliver_timer_sync.delay()
    except Exception:  # pylint: disable=broad-exception-caught
        # Periodic drain retries the durable records after recovery.
        logger.warning(
            "Timer sync queued; broker unavailable, periodic delivery will retry"
        )


@transaction.atomic
def changed(timer, deleted=False, destination=None, kick=True):
    """Imported timers are not exported again; origin revisions prevent loops/replays."""
    if receiving():
        return
    if (
        not timer.date or timer.timer_type == "PL"
    ) and not timer.sync_identities.filter(imported=False).exists():
        return
    destinations = (
        TimerSyncDestination.objects.filter(
            Q(enabled=True) | Q(timersyncdelivery__identity__timer=timer)
        )
        .select_related("authorized_by")
        .distinct()
    )
    if destination is not None:
        destinations = destinations.filter(pk=destination.pk)
    # Keep already approved links current while delivery is disabled or paused.
    # Workers still recheck authorization before transmitting anything.
    destinations = [
        item
        for item in destinations
        if authorized_destination(item)
        or TimerSyncDelivery.objects.filter(
            destination=item, identity__timer=timer
        ).exists()
    ]
    if not destinations:
        return
    # Backfill iterators can be stale by the time they acquire the origin lock.
    # Read the authoritative timer under lock before assigning a newer revision.
    timer = (
        type(timer)
        .objects.select_for_update()
        .select_related("structure")
        .get(pk=timer.pk)
    )
    if timer.sync_identities.filter(imported=True, owns_timer=True).exists():
        return
    config = TimerSyncConfig.current()
    identity, _ = TimerSyncIdentity.objects.get_or_create(
        timer=timer, imported=False, defaults={"source_instance": config.instance_id}
    )
    identity = TimerSyncIdentity.objects.select_for_update().get(pk=identity.pk)
    identity.revision += 1
    identity.deleted = deleted or not timer.date or timer.timer_type == "PL"
    identity.save(update_fields=["revision", "deleted"])
    queued = False
    for target in destinations:
        remove = identity.deleted or (
            timer.is_opsec and target.scope != TimerSyncDestination.Scope.ALL
        )
        # Retract an already shared timer when its access classification changes.
        if (
            remove
            and not TimerSyncDelivery.objects.filter(
                destination=target, identity=identity
            ).exists()
        ):
            continue
        payload = {
            "schema": 1,
            "source": str(config.instance_id),
            "timer_id": str(identity.timer_uuid),
            "revision": identity.revision,
            "action": "delete" if remove else "upsert",
            "timer": None if remove else serialize_timer(timer),
        }
        TimerSyncDelivery.objects.update_or_create(
            destination=target,
            identity=identity,
            defaults={
                "payload": payload,
                "revision": identity.revision,
                "next_attempt": now(),
                "last_error": "",
            },
        )
        queued = True
    if queued and kick:
        transaction.on_commit(_kick)


def backfill(destination, kick=True):
    """Queue a destination's current source timers after enabling/changing scope."""
    from .models import Timer

    if not destination.enabled or not authorized_destination(destination):
        return
    for timer in (
        Timer.objects.exclude(date=None)
        .exclude(timer_type="PL")
        .iterator(chunk_size=200)
    ):
        changed(timer, destination=destination, kick=False)
    TimerSyncDestination.objects.filter(pk=destination.pk).update(needs_backfill=False)
    if kick:
        transaction.on_commit(_kick)
