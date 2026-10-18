"""Destruction archives preserve history and remain repairable."""

from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.apps import apps
from django.db import transaction
from django.urls import reverse
from django.utils.timezone import now

from app_utils.testing import NoSocketsTestCase

from structuretimers import destruction
from structuretimers.models import (
    DiscordTimerboard,
    MapTimerState,
    NotificationRule,
    Timer,
)
from structuretimers.regional_map import permitted_timers
from structuretimers.tasks import (
    notify_about_new_timer,
    schedule_notifications_for_timer,
    send_scheduled_notification,
)
from structuretimers.tests.testdata.factory import (
    NotificationRuleFactory,
    ScheduledNotificationFactory,
    StructureFactory,
    TimerFactory,
    UserWithCreateFactory,
)
from structuretimers.timerboard import render_board


class TestDestruction(NoSocketsTestCase):
    def setUp(self):
        for name in (
            "_task_calc_structure_distances",
            "_task_calc_timer_distances_for_all_staging_systems",
            "_task_schedule_notifications_for_timer",
        ):
            task = patch("structuretimers.models." + name, Mock())
            task.start()
            self.addCleanup(task.stop)
        self.user = UserWithCreateFactory()
        self.structure = StructureFactory(
            user=self.user,
            structure_name="Archived Astrahus",
            fitting="Original fitting",
            details_notes="Original notes",
        )
        self.timer = TimerFactory(user=self.user, structure=self.structure)

    def archive(self):
        with transaction.atomic():
            destruction.mark_destroyed(self.structure, self.user)

    def test_archive_preserves_old_timers_and_map_observations_during_housekeeping(
        self,
    ):
        Timer.objects.filter(pk=self.timer.pk).update(date=now() - timedelta(days=100))
        state = MapTimerState.objects.create(
            timer=self.timer, events=[{"action": "kill"}]
        )
        self.archive()
        Timer.objects.delete_obsolete()
        self.assertTrue(Timer.objects.filter(pk=self.timer.pk).exists())
        self.assertTrue(MapTimerState.objects.filter(pk=state.pk).exists())
        self.structure.refresh_from_db()
        self.assertEqual(self.structure.fitting, "Original fitting")
        self.assertEqual(self.structure.details_notes, "Original notes")

    def test_active_lists_and_maps_hide_archived_records(self):
        self.archive()
        self.client.force_login(self.user)
        rows = self.client.get(
            reverse("structuretimers:timer_list_data", args=["current"])
        ).json()
        self.assertNotIn(self.timer.pk, [row["id"] for row in rows])
        for window in ("preliminary", "all"):
            self.assertFalse(
                permitted_timers(
                    self.user, [self.structure.eve_solar_system_id], {"window": window}
                ).exists()
            )
        board = DiscordTimerboard.objects.create(name="Board", channel_id="123")
        content = "\n".join(render_board(board))
        self.assertNotIn(self.structure.structure_name, content)

    def test_new_matching_timer_does_not_overwrite_destroyed_structure(self):
        self.archive()
        timer = TimerFactory(
            structure_name=self.structure.structure_name,
            structure_type=self.structure.structure_type,
            eve_solar_system=self.structure.eve_solar_system,
        )
        self.assertNotEqual(timer.structure_id, self.structure.pk)
        self.structure.refresh_from_db()
        self.assertEqual(self.structure.fitting, "Original fitting")

    @patch("structuretimers.tasks.send_messages_for_webhook")
    @patch("structuretimers.models.Timer.send_notification")
    def test_destroyed_structures_do_not_send_or_schedule_notifications(
        self, send, queue
    ):
        self.archive()
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED
        )
        notification = ScheduledNotificationFactory(
            timer=self.timer, notification_rule=rule, timer_date=self.timer.date
        )
        send_scheduled_notification.run(scheduled_notification_pk=notification.pk)
        notify_about_new_timer.run(self.timer.pk, rule.pk)
        schedule_notifications_for_timer.run(self.timer.pk, is_new=True)
        send.assert_not_called()
        queue.apply_async.assert_not_called()

    def test_admin_can_browse_and_restore_without_deleting(self):
        self.archive()
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        url = reverse("admin:structuretimers_structure_changelist")
        self.assertContains(self.client.get(url), self.structure.structure_name)
        response = self.client.post(
            url,
            {"action": "restore_structures", "_selected_action": [self.structure.pk]},
        )
        self.assertEqual(response.status_code, 302)
        self.structure.refresh_from_db()
        self.assertIsNone(self.structure.destroyed_at)
        self.assertTrue(Timer.objects.filter(pk=self.timer.pk).exists())

    def test_migration_archives_current_kills_but_ignores_restored_or_old_schedules(
        self,
    ):
        migration = import_module(
            "structuretimers.migrations.0024_structure_destruction"
        )
        event = {
            "action": "kill",
            "schedule": self.timer.date.isoformat(),
            "at": now().isoformat(),
            "user": self.user.pk,
        }
        MapTimerState.objects.create(timer=self.timer, events=[event])
        restored = TimerFactory(user=self.user)
        stale = TimerFactory(user=self.user)
        MapTimerState.objects.create(
            timer=restored,
            events=[
                {**event, "schedule": restored.date.isoformat()},
                {**event, "schedule": restored.date.isoformat(), "action": "restore"},
            ],
        )
        MapTimerState.objects.create(
            timer=stale,
            events=[
                {**event, "schedule": (stale.date - timedelta(days=1)).isoformat()}
            ],
        )
        migration.archive_existing_kills(
            apps, SimpleNamespace(connection=SimpleNamespace(alias="default"))
        )
        self.structure.refresh_from_db()
        self.assertIsNotNone(self.structure.destroyed_at)
        self.assertEqual(self.structure.destroyed_by_timer, self.timer)
        restored.structure.refresh_from_db()
        stale.structure.refresh_from_db()
        self.assertIsNone(restored.structure.destroyed_at)
        self.assertIsNone(stale.structure.destroyed_at)

    def test_history_migration_keeps_old_schedules_undo_and_manual_destruction(self):
        migration = import_module(
            "structuretimers.migrations.0026_reinforcement_history"
        )
        at = now()
        MapTimerState.objects.create(
            timer=self.timer,
            events=[
                {
                    "action": "kill",
                    "at": at.isoformat(),
                    "schedule": (self.timer.date - timedelta(days=1)).isoformat(),
                    "user": self.user.pk,
                },
                {
                    "action": "restore",
                    "at": (at + timedelta(seconds=1)).isoformat(),
                    "schedule": (self.timer.date - timedelta(days=1)).isoformat(),
                    "user": self.user.pk,
                },
            ],
        )
        manual = TimerFactory(user=self.user).structure
        destruction.mark_destroyed(manual, self.user)
        manual.history.all().delete()
        editor = SimpleNamespace(connection=SimpleNamespace(alias="default"))
        migration.preserve_existing_outcomes(apps, editor)
        migration.preserve_existing_outcomes(apps, editor)
        self.assertEqual(
            list(
                self.structure.history.order_by("at").values_list("action", flat=True)
            ),
            ["destroyed", "restored"],
        )
        self.assertEqual(
            self.structure.history.first().scheduled_at,
            self.timer.date - timedelta(days=1),
        )
        self.assertEqual(manual.history.get().action, "destroyed")

    def test_deleted_restricted_timer_history_stays_in_backend_without_exposing_scope(
        self,
    ):
        hidden = TimerFactory(user=self.user, structure=self.structure, is_opsec=True)
        outcome = destruction.record_outcome(
            self.structure, "reinforced", self.user, timer=hidden
        )
        self.assertFalse(
            self.structure.visible_history(self.user).filter(pk=outcome.pk).exists()
        )
        hidden.delete()
        self.assertTrue(self.structure.history.filter(pk=outcome.pk).exists())
        self.assertFalse(
            self.structure.visible_history(self.user).filter(pk=outcome.pk).exists()
        )
