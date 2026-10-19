"""Select legacy recon and explicitly reset only StratCom-owned tables."""

from django.apps import apps
from django.core.management.base import CommandError
from django.db import connection
from django.utils.timezone import now

from .models import Organization, Structure, Timer
from .sync_policy import receive_write


def clear_stratcom():
    """No cascades or migrations: delete rows exclusively in this app's tables."""
    models = [
        model
        for model in apps.get_app_config("aa_stratcom").get_models(
            include_auto_created=True
        )
        if model._meta.managed
    ]
    counts = {}
    with connection.cursor() as cursor:
        for model in models:
            table = model._meta.db_table
            if not table.startswith("aa_stratcom_"):
                raise CommandError("Refusing to clear a table outside StratCom.")
            counts[model._meta.model_name] = model._base_manager.count()
            cursor.execute(f"DELETE FROM {connection.ops.quote_name(table)}")
    return sum(counts.values())


def copy_recon(tables, copy_table):
    """PL is recon in upstream; later forks store the Database in Structure."""
    counts = {}
    if "structuretimers_organization" in tables:
        counts["organization"] = copy_table(
            Organization, "structuretimers_organization"
        )
    if "structuretimers_structure" in tables:
        counts["structure"] = copy_table(
            Structure,
            "structuretimers_structure",
            prepare=_structure_without_timer,
        )
    columns = source_columns("structuretimers_timer")
    if "timer_type" not in columns:
        raise CommandError(
            "The source has no timer_type column; preliminary records cannot be identified safely."
        )
    counts["timer"] = copy_table(
        Timer, "structuretimers_timer", where="timer_type = %s", params=["PL"]
    )
    return counts


def _structure_without_timer(record):
    """Scheduled timers are excluded; keep the destruction timestamp itself."""
    record.destroyed_by_timer_id = None


def remove_expired():
    """Prune before deriving recon, so expired upstream timers don't seed Database."""
    expired = Timer.objects.exclude(timer_type=Timer.Type.PRELIMINARY).filter(
        date__lt=now()
    )
    count = expired.count()
    with receive_write():
        expired.delete()
    return count


def source_columns(table):
    """Read legacy schemas without importing their Python models."""
    with connection.cursor() as cursor:
        return {
            column.name
            for column in connection.introspection.get_table_description(cursor, table)
        }
