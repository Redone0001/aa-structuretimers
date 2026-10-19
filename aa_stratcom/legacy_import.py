"""One-time copies from legacy tables; never modify the source application."""

import uuid
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth.models import Permission
from django.core.management.base import CommandError, no_style
from django.db import connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.models import F, Max
from django.utils.timezone import now

from . import legacy_selection
from .models import Structure, StructureTimersImport, Timer

SOURCE = "structuretimers"


def import_legacy(
    *, dry_run=False, recon_only=False, include_expired=False, replace=False
):
    """Copy into an empty StratCom namespace, retaining primary/foreign keys.

    Keep both applications stopped while copying. Constraint deferral permits the
    Timer/Structure history cycle; validate every target table before committing.
    Raw saves deliberately avoid jobs, notifications and replication side effects.
    """
    if replace and not recon_only:
        raise CommandError("--replace-stratcom-data requires --recon-only.")
    if recon_only and include_expired:
        raise CommandError("--recon-only cannot be combined with --include-expired.")
    tables = set(connection.introspection.table_names())
    if "structuretimers_timer" not in tables:
        raise CommandError("No structuretimers tables found in this database.")
    models = [
        model
        for model in apps.get_app_config("aa_stratcom").get_models(
            include_auto_created=True
        )
        if model._meta.managed and model is not StructureTimersImport
    ]
    with connection.constraint_checks_disabled(), transaction.atomic():
        cleared = legacy_selection.clear_stratcom() if replace else 0
        (
            marker,
            _created,
        ) = StructureTimersImport.objects.select_for_update().get_or_create(
            source=SOURCE
        )
        if marker.completed_at:
            return marker.counts, True
        # A default identity is configuration, not user data. All domain tables
        # must be empty so imports cannot overwrite records or mis-map relations.
        for model in models:
            if (
                model._meta.model_name != "timersyncconfig"
                and model._base_manager.exists()
            ):
                raise CommandError(
                    f"StratCom must be empty before copying ({model._meta.model_name} contains data)."
                )
        counts = _copy_selected(models, tables, recon_only, include_expired)
        _pause_integrations(tables)
        _copy_permissions()
        if not dry_run:
            with connection.cursor() as cursor:
                for sql in connection.ops.sequence_reset_sql(no_style(), models):
                    cursor.execute(sql)
        connection.check_constraints()
        if replace:
            counts["cleared_rows"] = cleared
        marker.counts = counts
        marker.completed_at = now()
        marker.save(update_fields=["counts", "completed_at"])
        if dry_run:
            transaction.set_rollback(True)
        return counts, False


def _copy_table(model, source_table, *, where="", params=(), prepare=None):
    """Convert backend-native source values using the target field converters."""
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        columns = legacy_selection.source_columns(source_table)
        fields = [
            field for field in model._meta.concrete_fields if field.column in columns
        ]
        clause = f" WHERE {where}" if where else ""
        cursor.execute(
            f"SELECT {', '.join(quote(field.column) for field in fields)} FROM {quote(source_table)}{clause}",
            params,
        )
        copied = 0
        while rows := cursor.fetchmany(500):
            for row in rows:
                instance = model(**_row_values(fields, row, source_table))
                if model._meta.model_name == "discordtimerboardmessage":
                    instance.legacy_message_id = instance.message_id
                    instance.message_id = ""
                for field in model._meta.concrete_fields:
                    if field not in fields and (
                        getattr(field, "auto_now", False)
                        or getattr(field, "auto_now_add", False)
                    ):
                        setattr(instance, field.attname, now())
                if prepare:
                    prepare(instance)
                instance.save_base(raw=True, force_insert=True)
                copied += 1
    return copied


def _derive_structures():
    """Old releases stored Database details only on their Timer rows."""
    fields = [
        field.attname
        for field in Structure._meta.concrete_fields
        if field.name
        not in {
            "id",
            "created_at",
            "fitting",
            "destroyed_at",
            "destroyed_by",
            "destroyed_by_timer",
        }
    ]
    next_pk = (Structure.objects.aggregate(value=Max("pk"))["value"] or 0) + 1
    for timer in Timer.objects.filter(
        structure__isnull=True, structures_structure_id__isnull=True
    ):
        record = Structure(
            pk=next_pk,
            **{name: getattr(timer, name) for name in fields},
            created_at=now(),
        )
        next_pk += 1
        record.save_base(raw=True, force_insert=True)
        Timer.objects.filter(pk=timer.pk).update(structure=record)


def _pause_integrations(tables):
    """Copies must not post to the same Discord messages or share as the old board."""
    from .models import DiscordTimerboard, DiscordWebhook, NotificationRule
    from .sync_models import (
        TimerSyncConfig,
        TimerSyncDelivery,
        TimerSyncDestination,
        TimerSyncIdentity,
        TimerSyncKey,
    )

    for model in (DiscordTimerboard, DiscordWebhook, NotificationRule):
        model.objects.update(is_enabled=False)
    TimerSyncDestination.objects.update(enabled=False, needs_backfill=True)
    TimerSyncKey.objects.update(enabled=False)
    # The new board has its own UUID and starts writable. Credentials/audits are
    # retained but disabled; receiving keys must be reissued for the new endpoint.
    current = TimerSyncConfig.current()
    if "structuretimers_timersyncconfig" in tables:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT instance_id FROM structuretimers_timersyncconfig WHERE id = 1"
            )
            row = cursor.fetchone()
        if row:
            TimerSyncIdentity.objects.filter(
                source_instance=uuid.UUID(str(row[0])), imported=False
            ).update(source_instance=current.instance_id)
    # Do not send old-origin payloads after the new board's first configuration.
    # Payloads remain archived until a deliberate destination backfill replaces them.
    TimerSyncDelivery.objects.update(delivered_revision=F("revision"))


def _copy_permissions():
    """Add equivalent new grants to the same users/groups; keep old grants intact."""
    for source in Permission.objects.filter(
        content_type__app_label=SOURCE
    ).select_related("content_type"):
        target = Permission.objects.filter(
            content_type__app_label="aa_stratcom",
            content_type__model=source.content_type.model,
            codename=source.codename,
        ).first()
        if target:
            for user in source.user_set.all():
                user.user_permissions.add(target)
            for group in source.group_set.all():
                group.permissions.add(target)


def _backfill_older_schemas(tables):
    """Use historical ORM models so old event backfills emit no model signals."""
    state = MigrationLoader(connection).project_state().apps
    editor = SimpleNamespace(connection=connection)
    from .legacy_backfill import (
        archive_existing_kills,
        flag_existing_timers,
        preserve_existing_outcomes,
    )

    columns = legacy_selection.source_columns("structuretimers_timer")
    if "discord_timerboard" not in columns:
        flag_existing_timers(state, editor)
    if "structuretimers_structurehistory" not in tables:
        archive_existing_kills(state, editor)
        preserve_existing_outcomes(state, editor)


def _row_values(fields, row, source_table):
    """Apply exactly the backend/field conversions used by Django SELECT queries."""
    values = {}
    for field, value in zip(fields, row):
        expression = field.get_col(source_table)
        converters = connection.ops.get_db_converters(
            expression
        ) + field.get_db_converters(connection)
        for convert in converters:
            value = convert(value, expression, connection)
        values[field.attname] = value
    return values


def _copy_selected(models, tables, recon_only, include_expired):
    """Select the requested source records before deriving Database entries."""
    counts = {}
    if recon_only:
        counts = legacy_selection.copy_recon(tables, _copy_table)
    else:
        for model in models:
            if model._meta.model_name == "timersyncconfig":
                continue
            source_table = SOURCE + model._meta.db_table[len("aa_stratcom") :]
            if source_table in tables:
                counts[model._meta.model_name] = _copy_table(model, source_table)
        if not include_expired:
            counts["skipped_expired"] = legacy_selection.remove_expired()
    if recon_only or "structuretimers_structure" not in tables:
        _derive_structures()
    if not recon_only:
        _backfill_older_schemas(tables)
    counts["timer"] = Timer.objects.count()
    counts["structure"] = Structure.objects.count()
    return counts
