"""Copy an early timer-only schema without installing the source Python module."""

from datetime import timedelta
from io import StringIO
from unittest import skipIf
from unittest.mock import patch

from django.apps import apps
from django.apps.registry import Apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, models
from django.test import TransactionTestCase
from django.utils.timezone import now

from aa_stratcom.models import Operation, Structure, StructureTimersImport, Timer
from aa_stratcom.sync_models import TimerSyncConfig


class EarlyTimer(models.Model):
    id = models.IntegerField(primary_key=True)
    date = models.DateTimeField(null=True)
    timer_type = models.CharField(max_length=2, default="NO")
    details_notes = models.TextField()
    structure_name = models.CharField(max_length=254)
    visibility = models.CharField(max_length=2)
    is_opsec = models.BooleanField(default=False)

    class Meta:
        apps = Apps()
        app_label = "legacy_copy_test_fixture"
        db_table = "structuretimers_timer"


@skipIf(
    apps.is_installed("structuretimers"),
    "uses a minimal source fixture instead of the real old app",
)
class SparseLegacyImportTests(TransactionTestCase):
    def setUp(self):
        with connection.schema_editor() as editor:
            editor.create_model(EarlyTimer)

    def tearDown(self):
        with connection.schema_editor() as editor:
            editor.delete_model(EarlyTimer)
        super().tearDown()

    def test_missing_new_columns_get_defaults_and_structure_is_derived(self):
        original = EarlyTimer.objects.create(
            pk=41,
            date=now() + timedelta(days=1),
            details_notes="Keep early notes",
            structure_name="Early private record",
            visibility="CO",
            is_opsec=True,
        )
        call_command("stratcom_import_structuretimers", stdout=StringIO())
        copied = Timer.objects.get(pk=41)
        self.assertEqual(copied.date, original.date)
        self.assertEqual(copied.details_notes, "Keep early notes")
        self.assertEqual(copied.visibility, "CO")
        self.assertTrue(copied.is_opsec)
        self.assertTrue(copied.discord_timerboard)
        self.assertEqual(Structure.objects.count(), 1)
        self.assertEqual(copied.structure.visibility, "CO")
        self.assertTrue(copied.structure.is_opsec)
        self.assertEqual(
            EarlyTimer.objects.get(pk=41).details_notes, "Keep early notes"
        )
        self.assertEqual(EarlyTimer.objects.count(), 1)

    def seed_mixed(self):
        for pk, kind, date, name in [
            (41, "PL", None, "Actual recon"),
            (42, "FI", now() - timedelta(days=300), "Expired noise"),
            (43, "FI", now() + timedelta(days=1), "Future timer"),
        ]:
            EarlyTimer.objects.create(
                pk=pk,
                date=date,
                timer_type=kind,
                details_notes=name,
                structure_name=name,
                visibility="CO",
                is_opsec=True,
            )

    def test_default_excludes_expired_before_deriving_database_records(self):
        self.seed_mixed()
        call_command("stratcom_import_structuretimers", stdout=StringIO())
        self.assertEqual(set(Timer.objects.values_list("pk", flat=True)), {41, 43})
        self.assertFalse(
            Structure.objects.filter(structure_name="Expired noise").exists()
        )
        self.assertEqual(EarlyTimer.objects.count(), 3)

    def test_expired_history_requires_explicit_option(self):
        self.seed_mixed()
        call_command(
            "stratcom_import_structuretimers", "--include-expired", stdout=StringIO()
        )
        self.assertEqual(Timer.objects.count(), 3)

    def test_replace_reimports_only_recon_and_keeps_shared_users_and_source(self):
        from aa_stratcom.tests.testdata.factory import UserWithAccessFactory

        self.seed_mixed()
        user = UserWithAccessFactory()
        grants = set(user.user_permissions.values_list("pk", flat=True))
        Operation.objects.create(name="Clear this operation")
        original_identity = TimerSyncConfig.current().instance_id
        call_command(
            "stratcom_import_structuretimers",
            "--recon-only",
            "--replace-stratcom-data",
            stdout=StringIO(),
        )
        self.assertEqual(list(Timer.objects.values_list("pk", flat=True)), [41])
        self.assertEqual(
            list(Structure.objects.values_list("structure_name", flat=True)),
            ["Actual recon"],
        )
        self.assertFalse(Operation.objects.exists())
        self.assertNotEqual(TimerSyncConfig.current().instance_id, original_identity)
        self.assertEqual(EarlyTimer.objects.count(), 3)
        user.refresh_from_db()
        self.assertEqual(
            set(user.user_permissions.values_list("pk", flat=True)), grants
        )

    def test_replace_dry_run_preserves_existing_data_and_identity(self):
        self.seed_mixed()
        operation = Operation.objects.create(name="Keep on dry run")
        identity = TimerSyncConfig.current().instance_id
        call_command(
            "stratcom_import_structuretimers",
            "--recon-only",
            "--replace-stratcom-data",
            "--dry-run",
            stdout=StringIO(),
        )
        self.assertTrue(Operation.objects.filter(pk=operation.pk).exists())
        self.assertEqual(TimerSyncConfig.current().instance_id, identity)
        self.assertFalse(Timer.objects.exists())
        self.assertFalse(StructureTimersImport.objects.exists())
        self.assertEqual(EarlyTimer.objects.count(), 3)

    @patch(
        "aa_stratcom.legacy_import._copy_permissions",
        side_effect=RuntimeError("failed copy"),
    )
    def test_failed_replace_restores_every_existing_row(self, _copy):
        self.seed_mixed()
        operation = Operation.objects.create(name="Keep after error")
        identity = TimerSyncConfig.current().instance_id
        with self.assertRaisesMessage(RuntimeError, "failed copy"):
            call_command(
                "stratcom_import_structuretimers",
                "--recon-only",
                "--replace-stratcom-data",
                stdout=StringIO(),
            )
        self.assertTrue(Operation.objects.filter(pk=operation.pk).exists())
        self.assertEqual(TimerSyncConfig.current().instance_id, identity)
        self.assertFalse(Timer.objects.exists())
        self.assertEqual(EarlyTimer.objects.count(), 3)

    def test_replace_requires_recon_only(self):
        self.seed_mixed()
        with self.assertRaisesMessage(CommandError, "requires --recon-only"):
            call_command(
                "stratcom_import_structuretimers",
                "--replace-stratcom-data",
                stdout=StringIO(),
            )
