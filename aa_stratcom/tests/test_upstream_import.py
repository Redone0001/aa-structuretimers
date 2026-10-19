"""Compatibility with upstream's preliminary Timer rows, before Structure existed."""

from datetime import timedelta
from io import StringIO
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.apps import apps
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django.utils.timezone import now
from eveuniverse.tests.testdata.factories_2 import EveTypeFactory

from aa_stratcom.models import Operation, Structure, Timer
from aa_stratcom.tests.testdata.factory import EveSolarSystemLowSecFactory

UPSTREAM = (
    apps.is_installed("structuretimers")
    and "structure" not in apps.get_app_config("structuretimers").models
)


@skipUnless(UPSTREAM, "requires the upstream timer-only app")
class UpstreamReconImportTests(TransactionTestCase):
    def test_reset_imports_only_preliminary_and_preserves_all_source_rows(self):
        model = apps.get_model("structuretimers", "Timer")
        system, kind = EveSolarSystemLowSecFactory(), EveTypeFactory()
        for name in (
            "_task_calc_timer_distances_for_all_staging_systems",
            "_task_schedule_notifications_for_timer",
        ):
            patcher = patch("structuretimers.models." + name, Mock())
            patcher.start()
            self.addCleanup(patcher.stop)
        for label, timer_type, date in (
            ("Recon", "PL", None),
            ("Expired", "FI", now() - timedelta(days=100)),
            ("Future", "FI", now() + timedelta(days=1)),
        ):
            model.objects.create(
                structure_name=label,
                timer_type=timer_type,
                date=date,
                eve_solar_system=system,
                structure_type=kind,
                details_notes="Original intel",
                is_opsec=True,
                visibility="CO",
                details_image_url="https://example.org/fitting.png",
            )
        original_age = now() - timedelta(days=60)
        model.objects.update(last_updated_at=original_age)
        before = self.snapshot()
        operation = Operation.objects.create(name="To clear")
        options = ["--recon-only", "--replace-stratcom-data"]
        call_command(
            "stratcom_import_structuretimers", *options, "--dry-run", stdout=StringIO()
        )
        self.assertTrue(Operation.objects.filter(pk=operation.pk).exists())
        self.assertEqual(self.snapshot(), before)
        call_command("stratcom_import_structuretimers", *options, stdout=StringIO())
        self.assertFalse(Operation.objects.exists())
        record = Structure.objects.get()
        timer = Timer.objects.get()
        self.assertEqual(record.structure_name, "Recon")
        self.assertEqual(record.last_updated_at, original_age)
        self.assertEqual(record.details_notes, "Original intel")
        self.assertTrue(record.is_opsec)
        self.assertEqual(record.visibility, "CO")
        self.assertEqual(timer.timer_type, "PL")
        self.assertEqual(timer.details_image_url, "https://example.org/fitting.png")
        self.assertEqual(self.snapshot(), before)

    @staticmethod
    def snapshot():
        result = {}
        with connection.cursor() as cursor:
            for table in connection.introspection.table_names():
                if table.startswith("structuretimers_"):
                    cursor.execute(
                        f"SELECT * FROM {connection.ops.quote_name(table)} ORDER BY 1"
                    )
                    result[table] = cursor.fetchall()
        return result
