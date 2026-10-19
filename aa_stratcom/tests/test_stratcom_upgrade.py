"""Upgrade compatibility and non-destructive classic timerboard copying."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils.timezone import now
from eveuniverse.tests.testdata.factories_2 import EveTypeFactory

from aa_stratcom.models import Structure, Timer, TimerboardImport
from aa_stratcom.sync_models import TimerSyncConfig
from aa_stratcom.tests.testdata.factory import (
    AuthTimerFactory,
    EveSolarSystemLowSecFactory,
    TimerFactory,
    UserWithAccessFactory,
)


class StratComUpgradeTests(TestCase):
    def setUp(self):
        self.output = StringIO()
        self.system = EveSolarSystemLowSecFactory(name="Import System")
        self.user = UserWithAccessFactory()
        self.source = AuthTimerFactory(
            system=self.system.name,
            user=self.user,
            eve_time=now() - timedelta(days=10),
            details="Historical intel",
            planet_moon="Moon 1",
            corp_timer=True,
            important=True,
        )
        from aa_stratcom.management.commands.aa_stratcom_migrate_timers import (
            _STRUCTURE_MAP,
        )

        self.type = EveTypeFactory(id=_STRUCTURE_MAP[self.source.structure])

    def run_import(self, *args):
        call_command("stratcom_import_timerboard", *args, stdout=self.output)

    @patch("aa_stratcom.models._task_calc_structure_distances")
    @patch("aa_stratcom.models._task_calc_timer_distances_for_all_staging_systems")
    @patch("aa_stratcom.models._task_schedule_notifications_for_timer")
    @patch("aa_stratcom.sync_signals.changed")
    def test_copy_past_timer_and_privacy_without_side_effects(self, *jobs):
        before = self.source.__class__.objects.values().get(pk=self.source.pk)
        self.run_import()
        timer = TimerboardImport.objects.get(source_id=self.source.pk).timer
        self.assertEqual(timer.date, self.source.eve_time)
        self.assertEqual(timer.details_notes, self.source.details)
        self.assertEqual(timer.eve_corporation_id, self.source.eve_corp_id)
        self.assertEqual(timer.eve_alliance_id, self.source.eve_corp.alliance_id)
        self.assertEqual(timer.eve_character_id, self.source.eve_character_id)
        self.assertEqual(timer.user_id, self.user.pk)
        self.assertEqual(timer.visibility, Timer.Visibility.CORPORATION)
        self.assertEqual(timer.structure.visibility, Timer.Visibility.CORPORATION)
        self.assertEqual(timer.structure.eve_corporation_id, self.source.eve_corp_id)
        self.assertTrue(timer.is_important)
        self.assertEqual(timer.location_details, "Moon 1")
        self.assertEqual(
            self.source.__class__.objects.values().get(pk=self.source.pk), before
        )
        for job in jobs:
            job.assert_not_called()

    def test_repeated_import_and_future_timer_do_not_merge_distinct_records(self):
        self.source.eve_time = now() + timedelta(days=1)
        self.source.save()
        other = AuthTimerFactory(
            system=self.source.system,
            eve_time=self.source.eve_time,
            structure=self.source.structure,
            planet_moon="Different structure",
        )
        self.run_import()
        self.run_import()
        self.assertEqual(TimerboardImport.objects.count(), 2)
        self.assertEqual(Timer.objects.count(), 2)
        self.assertNotEqual(
            TimerboardImport.objects.get(source_id=self.source.pk).timer.structure_id,
            TimerboardImport.objects.get(source_id=other.pk).timer.structure_id,
        )

    def test_unknown_fields_are_archived_and_fixable(self):
        self.source.system = "Unloaded System"
        self.source.structure = "Other"
        self.source.timer_type = "Abandoned"
        self.source.save()
        self.run_import()
        record = TimerboardImport.objects.get()
        self.assertEqual(record.source_data["system"], "Unloaded System")
        self.assertEqual(record.source_data["structure"], "Other")
        self.assertEqual(record.source_data["timer_type"], "Abandoned")
        self.assertIsNone(record.timer.eve_solar_system_id)
        self.assertIsNone(record.timer.structure_type_id)
        self.assertEqual(record.timer.timer_type, Timer.Type.NONE)

    def test_dry_run_rolls_back_records_and_permissions(self):
        source_permission = Permission.objects.get(
            content_type__app_label="auth", codename="timer_management"
        )
        self.user.user_permissions.add(source_permission)
        original_permissions = set(
            self.user.user_permissions.values_list("pk", flat=True)
        )
        self.run_import("--dry-run")
        self.assertFalse(TimerboardImport.objects.exists())
        self.assertFalse(Timer.objects.exists())
        self.assertFalse(Structure.objects.exists())
        self.assertEqual(
            set(self.user.user_permissions.values_list("pk", flat=True)),
            original_permissions,
        )
        self.assertTrue(
            self.source.__class__.objects.filter(pk=self.source.pk).exists()
        )

    def test_permission_grants_are_additive_and_viewers_gain_no_management(self):
        viewer = UserWithAccessFactory()
        viewer.user_permissions.clear()
        source_view = Permission.objects.get(
            content_type__app_label="auth", codename="timer_view"
        )
        source_manage = Permission.objects.get(
            content_type__app_label="auth", codename="timer_management"
        )
        viewer.user_permissions.add(source_view)
        self.user.user_permissions.add(source_manage)
        group = Group.objects.create(name="Timerboard managers")
        group.permissions.add(source_manage)
        original_id = Permission.objects.get(
            content_type__app_label="aa_stratcom", codename="basic_access"
        ).pk
        self.run_import()
        self.assertTrue(viewer.has_perm("aa_stratcom.basic_access"))
        self.assertFalse(viewer.has_perm("aa_stratcom.create_timer"))
        self.assertFalse(viewer.has_perm("aa_stratcom.manage_timer"))
        self.assertTrue(self.user.has_perm("aa_stratcom.create_timer"))
        self.assertTrue(self.user.has_perm("aa_stratcom.manage_timer"))
        self.assertTrue(group.permissions.filter(pk=source_manage.pk).exists())
        self.assertTrue(
            group.permissions.filter(
                codename="manage_timer", content_type__app_label="aa_stratcom"
            ).exists()
        )
        self.assertTrue(viewer.user_permissions.filter(pk=source_view.pk).exists())
        self.assertEqual(
            Permission.objects.get(
                content_type__app_label="aa_stratcom", codename="basic_access"
            ).pk,
            original_id,
        )

    def test_existing_stratcom_timer_is_untouched(self):
        existing = TimerFactory(eve_solar_system=self.system, details_notes="Keep this")
        original_pk = existing.pk
        self.run_import()
        existing.refresh_from_db()
        self.assertEqual(existing.pk, original_pk)
        self.assertEqual(existing.details_notes, "Keep this")
        self.assertEqual(Timer.objects.count(), 2)

    def test_import_is_blocked_on_replica(self):
        TimerSyncConfig.objects.filter(pk=1).update(replica=True)
        with self.assertRaisesMessage(CommandError, "read-only"):
            self.run_import()
        self.assertFalse(TimerboardImport.objects.exists())

    @patch(
        "aa_stratcom.models.TimerboardImport.objects.create",
        side_effect=RuntimeError("failed archive"),
    )
    def test_failed_import_rolls_back_copies(self, _create):
        with self.assertRaisesMessage(RuntimeError, "failed archive"):
            self.run_import()
        self.assertFalse(Timer.objects.exists())
        self.assertFalse(Structure.objects.exists())
        self.assertTrue(
            self.source.__class__.objects.filter(pk=self.source.pk).exists()
        )

    def test_missing_optional_timerboard_produces_clear_error(self):
        from django.conf import settings

        installed = [
            app for app in settings.INSTALLED_APPS if app != "allianceauth.timerboard"
        ]
        with override_settings(INSTALLED_APPS=installed):
            with self.assertRaisesMessage(
                CommandError, "Keep allianceauth.timerboard installed"
            ):
                self.run_import()

    def test_admin_links_to_structure_repair_fields(self):
        from django.contrib import admin
        from django.urls import reverse

        from aa_stratcom.admin import StructureAdmin, TimerboardImportAdmin

        self.run_import()
        record = TimerboardImport.objects.get()
        archive_admin = TimerboardImportAdmin(TimerboardImport, admin.site)
        self.assertIn(
            reverse(
                "admin:aa_stratcom_structure_change",
                args=[record.timer.structure_id],
            ),
            archive_admin.structure_record(record),
        )
        self.assertIn("eve_solar_system", StructureAdmin.fields)
        self.assertIn("structure_type", StructureAdmin.fields)
