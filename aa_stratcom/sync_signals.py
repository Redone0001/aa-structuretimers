"""Queue local timer saves/deletions, never received replication events."""

# Django signals always pass sender, even though the registered model is fixed.
# pylint: disable=unused-argument

from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from .models import Timer
from .sync_outbox import changed


@receiver(post_save, sender=Timer, dispatch_uid="asc_sync_timer_saved")
def timer_saved(sender, instance, raw=False, **kwargs):
    """Fixture loading is not a publish operation."""
    if not raw:
        changed(instance)


@receiver(pre_delete, sender=Timer, dispatch_uid="asc_sync_timer_deleted")
def timer_deleted(sender, instance, **kwargs):
    """Keep tombstones and latest delete deliveries after local deletion."""
    from .sync_policy import require_local_write

    require_local_write()
    changed(instance, deleted=True)
