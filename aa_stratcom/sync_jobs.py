"""Deliver a durable latest-state outbox with bounded retries and no secret logs."""

from datetime import timedelta

from celery import shared_task

from django.core.exceptions import ValidationError
from django.db.models import F
from django.utils.timezone import now

from .sync_models import (
    TimerSyncAudit,
    TimerSyncCredential,
    TimerSyncDelivery,
    TimerSyncDestination,
)
from .sync_outbox import authorized_destination, backfill
from .sync_security import decrypt_secret, post_payload


@shared_task(name="structuretimers.sync_jobs.deliver_timer_sync")
def deliver_timer_sync():
    """Drain up to 100 rows per run; a periodic task recovers broker/worker outages."""
    for destination in TimerSyncDestination.objects.filter(
        enabled=True, needs_backfill=True
    ):
        backfill(destination, kick=False)
    instant = now()
    rows = list(
        TimerSyncDelivery.objects.filter(
            destination__enabled=True,
            next_attempt__lte=instant,
            locked_until__lte=instant,
        )
        .exclude(delivered_revision=F("revision"))
        .values_list("pk", flat=True)[:100]
    )
    for pk in rows:
        claimed = TimerSyncDelivery.objects.filter(
            pk=pk, locked_until__lte=instant
        ).update(locked_until=instant + timedelta(minutes=2))
        if claimed:
            _deliver(pk)


def _deliver(pk):
    row = TimerSyncDelivery.objects.select_related(
        "destination__authorized_by", "identity"
    ).get(pk=pk)
    destination = row.destination
    error = ""
    try:
        if not destination.enabled or not authorized_destination(destination):
            error = "Sharing authorization is inactive."
        elif (
            row.payload["timer"]
            and row.payload["timer"]["is_opsec"]
            and destination.scope != "all"
        ):
            error = (
                "Destination scope no longer allows this event; queue a new snapshot."
            )
        else:
            credential = TimerSyncCredential.objects.select_related("destination").get(
                destination=destination
            )
            post_payload(destination, decrypt_secret(credential), row.payload)
    except ValidationError as exc:
        # These messages are generated locally and contain no remote body/secret.
        error = " ".join(exc.messages)[:200]
    except Exception:  # pylint: disable=broad-exception-caught
        # Never log exception/request repr: it may contain credentials.
        error = "Delivery failed. Check HTTPS, credential, receiver scope and remote status."
    query = TimerSyncDelivery.objects.filter(pk=pk)
    if not error:
        # A newer event may have replaced this row during the HTTPS request.
        query.filter(revision=row.revision).update(
            delivered_revision=row.revision, attempts=0, last_error=""
        )
    else:
        query.filter(revision=row.revision).update(
            attempts=F("attempts") + 1,
            last_error=error,
            next_attempt=now()
            + timedelta(seconds=min(3600, 30 * 2 ** min(row.attempts, 7))),
        )
    query.update(locked_until=now())
    TimerSyncAudit.objects.create(
        destination=destination,
        source_instance=row.identity.source_instance,
        timer_uuid=row.identity.timer_uuid,
        action="push",
        result="failed" if error else "delivered",
    )
