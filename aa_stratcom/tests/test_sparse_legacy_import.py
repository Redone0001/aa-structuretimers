"""Copy an early timer-only schema without installing the source Python module."""

from io import StringIO
from unittest import skipIf

from django.apps import apps
from django.apps.registry import Apps
from django.core.management import call_command
from django.db import connection, models
from django.test import TransactionTestCase
from django.utils.timezone import now

from aa_stratcom.models import Structure, Timer


class EarlyTimer(models.Model):
    id = models.IntegerField(primary_key=True)
    date = models.DateTimeField()
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
            date=now(),
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
