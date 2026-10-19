"""Run against the actual legacy app, not a second copy of StratCom models."""

from io import StringIO
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils.timezone import now
from eveuniverse.tests.testdata.factories_2 import EveTypeFactory

from aa_stratcom.models import (
    DiscordWebhook,
    Operation,
    OperationRole,
    Structure,
    StructureHistory,
    StructureTimersImport,
    Timer,
)
from aa_stratcom.sync_models import TimerSyncConfig
from aa_stratcom.tests.testdata.factory import (
    EveSolarSystemLowSecFactory,
    UserWithAccessFactory,
)


@skipUnless(apps.is_installed("structuretimers"), "requires the real legacy app")
class IndependentImportTests(TransactionTestCase):
    def setUp(self):
        self.user = UserWithAccessFactory()
        self.system = EveSolarSystemLowSecFactory()
        self.ship = EveTypeFactory()
        for name in (
            "_task_calc_structure_distances",
            "_task_calc_timer_distances_for_all_staging_systems",
            "_task_schedule_notifications_for_timer",
        ):
            patcher = patch("structuretimers.models." + name, Mock())
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in (
            "_task_calc_structure_distances",
            "_task_calc_timer_distances_for_all_staging_systems",
            "_task_schedule_notifications_for_timer",
        ):
            patcher = patch("aa_stratcom.models." + name, Mock())
            patcher.start()
            self.addCleanup(patcher.stop)
        old_timer = apps.get_model("structuretimers", "Timer")
        self.original = old_timer.objects.create(
            date=now(),
            eve_solar_system=self.system,
            structure_type=self.ship,
            structure_name="Private original",
            visibility="CO",
            is_opsec=True,
            details_notes="Retain intelligence",
            user=self.user,
        )
        self.old_operation = apps.get_model(
            "structuretimers", "Operation"
        ).objects.create(name="Original op")
        self.old_operation.timers.add(self.original)
        self.old_operation.participants.add(self.user)
        self.old_role = apps.get_model(
            "structuretimers", "OperationRole"
        ).objects.create(operation=self.old_operation, name="FC")
        self.old_role.users.add(self.user)
        self.old_role.systems = [self.system.name]
        self.old_role.save()
        apps.get_model("structuretimers", "StructureHistory").objects.create(
            structure=self.original.structure,
            timer=self.original,
            action="destroyed",
            user=self.user,
        )
        self.webhook = apps.get_model(
            "structuretimers", "DiscordWebhook"
        ).objects.create(
            name="Original webhook",
            url="https://example.org/unused",
            is_enabled=True,
        )
        board = apps.get_model("structuretimers", "DiscordTimerboard").objects.create(
            name="Original board", channel_id="123"
        )
        apps.get_model("structuretimers", "DiscordTimerboardMessage").objects.create(
            board=board,
            channel_id="123",
            message_id="987",
            position=0,
            content="Original content",
        )
        self.old_identity = (
            apps.get_model("structuretimers", "TimerSyncConfig").current().instance_id
        )
        self.group = Group.objects.create(name="Legacy recon")
        self.old_permission = Permission.objects.get(
            content_type__app_label="structuretimers", codename="opsec_access"
        )
        self.group.permissions.add(self.old_permission)
        self.user.user_permissions.add(self.old_permission)

    @staticmethod
    def snapshot_source():
        with connection.cursor() as cursor:
            tables = [
                name
                for name in connection.introspection.table_names()
                if name.startswith("structuretimers_")
            ]
            result = {}
            for name in tables:
                cursor.execute(
                    f"SELECT * FROM {connection.ops.quote_name(name)} ORDER BY 1"
                )
                result[name] = cursor.fetchall()
        return result

    def run_copy(self, *args):
        call_command("stratcom_import_structuretimers", *args, stdout=StringIO())

    def test_atomic_copy_preserves_every_source_row_relationship_and_grant(self):
        before = self.snapshot_source()
        self.run_copy()
        copied = Timer.objects.get(pk=self.original.pk)
        self.assertEqual(copied.details_notes, "Retain intelligence")
        self.assertEqual(copied.structure_id, self.original.structure_id)
        self.assertEqual(copied.visibility, "CO")
        self.assertTrue(copied.is_opsec)
        self.assertEqual(StructureHistory.objects.get().timer_id, copied.pk)
        self.assertEqual(
            list(Operation.objects.get().timers.values_list("pk", flat=True)),
            [copied.pk],
        )
        self.assertEqual(
            list(Operation.objects.get().participants.values_list("pk", flat=True)),
            [self.user.pk],
        )
        self.assertEqual(
            list(OperationRole.objects.get().users.values_list("pk", flat=True)),
            [self.user.pk],
        )
        self.assertEqual(
            OperationRole.objects.get().systems,
            [self.system.name],
        )
        self.assertFalse(DiscordWebhook.objects.get().is_enabled)
        from aa_stratcom.models import DiscordTimerboardMessage

        message = DiscordTimerboardMessage.objects.get()
        self.assertEqual(message.legacy_message_id, "987")
        self.assertEqual(message.message_id, "")
        self.assertEqual(self.snapshot_source(), before)
        self.assertNotEqual(TimerSyncConfig.current().instance_id, self.old_identity)
        new_permission = Permission.objects.get(
            content_type__app_label="aa_stratcom", codename="opsec_access"
        )
        self.assertTrue(
            self.user.user_permissions.filter(pk=new_permission.pk).exists()
        )
        self.assertTrue(self.group.permissions.filter(pk=new_permission.pk).exists())
        self.assertTrue(
            self.user.user_permissions.filter(pk=self.old_permission.pk).exists()
        )
        self.assertTrue(
            self.group.permissions.filter(pk=self.old_permission.pk).exists()
        )
        self.assertEqual(Timer._meta.db_table, "aa_stratcom_timer")
        self.assertEqual(reverse("aa_stratcom:timer_list"), "/aa_stratcom/")
        self.assertEqual(reverse("structuretimers:timer_list"), "/structuretimers/")
        self.run_copy()
        self.assertEqual(Timer.objects.count(), 1)
        self.assertEqual(self.snapshot_source(), before)
        copied.details_notes = "Independent new notes"
        copied.save()
        self.original.refresh_from_db()
        self.assertEqual(self.original.details_notes, "Retain intelligence")
        # Imported IDs must not collide with the next newly created row.
        another = Timer.objects.create(date=now())
        self.assertNotEqual(another.pk, copied.pk)

    def test_dry_run_rolls_back_copies_and_new_grants(self):
        before = self.snapshot_source()
        grants = list(self.user.user_permissions.values_list("pk", flat=True))
        self.run_copy("--dry-run")
        self.assertFalse(Timer.objects.exists())
        self.assertFalse(Structure.objects.exists())
        self.assertFalse(StructureTimersImport.objects.exists())
        self.assertEqual(
            list(self.user.user_permissions.values_list("pk", flat=True)), grants
        )
        self.assertEqual(self.snapshot_source(), before)

    def test_refuses_to_overwrite_nonempty_target(self):
        Timer.objects.create(date=now())
        with self.assertRaisesMessage(CommandError, "must be empty"):
            self.run_copy()
        self.assertEqual(Timer.objects.count(), 1)
        self.assertFalse(StructureTimersImport.objects.exists())

    @patch(
        "aa_stratcom.legacy_import._copy_permissions",
        side_effect=RuntimeError("copy failed"),
    )
    def test_failure_rolls_back_all_target_rows(self, _copy):
        before = self.snapshot_source()
        with self.assertRaisesMessage(RuntimeError, "copy failed"):
            self.run_copy()
        self.assertFalse(Timer.objects.exists())
        self.assertFalse(StructureTimersImport.objects.exists())
        self.assertEqual(self.snapshot_source(), before)

    def test_celery_tasks_are_different_objects_with_distinct_names(self):
        from structuretimers.tasks import housekeeping as old_housekeeping

        from aa_stratcom.tasks import housekeeping

        self.assertEqual(housekeeping.name, "aa_stratcom.tasks.housekeeping")
        self.assertEqual(old_housekeeping.name, "structuretimers.tasks.housekeeping")
        self.assertIsNot(housekeeping, old_housekeeping)

    def test_old_permissions_do_not_implicitly_grant_new_access(self):
        self.user.user_permissions.clear()
        old_basic = Permission.objects.get(
            content_type__app_label="structuretimers", codename="basic_access"
        )
        self.user.user_permissions.add(old_basic)
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(reverse("structuretimers:timer_list")).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse("aa_stratcom:timer_list")).status_code, 403
        )
        self.run_copy()
        self.assertEqual(
            self.client.get(reverse("aa_stratcom:timer_list")).status_code, 200
        )
