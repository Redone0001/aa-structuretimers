"""Management access, refresh and deletion behavior."""

from datetime import time, timedelta
from unittest.mock import Mock, patch

from django.test import Client
from django.urls import reverse
from django.utils.timezone import now
from app_utils.testing import NoSocketsTestCase
from structuretimers.constants import EveTypeId
from structuretimers.forms import ReconForm
from structuretimers.models import Timer
from structuretimers.tests.testdata.factory import (
    CitadelTypeFactory,
    TimerFactory,
    UserWithAccessFactory,
    UserWithCreateFactory,
    UserWithManageFactory,
)


@patch(
    "structuretimers.models._task_calc_timer_distances_for_all_staging_systems", Mock()
)
class TestManageRecon(NoSocketsTestCase):
    def setUp(self):
        self.user = UserWithCreateFactory()
        self.client.force_login(self.user)
        self.timer = TimerFactory(
            user=self.user,
            timer_type=Timer.Type.PRELIMINARY,
            date=None,
            reinforcement_time=time(12, 0),
        )
        self.url = reverse("structuretimers:recon_data")

    def action_url(self, action, timer=None):
        return reverse(
            "structuretimers:recon_action", args=[(timer or self.timer).pk, action]
        )

    def test_data_contains_only_visible_preliminary_timers(self):
        current = TimerFactory(timer_type=Timer.Type.HULL)
        hidden = TimerFactory(timer_type=Timer.Type.PRELIMINARY, is_opsec=True)
        data = self.client.get(self.url).json()
        self.assertEqual([row["id"] for row in data], [self.timer.pk])
        self.assertNotIn(current.pk, [row["id"] for row in data])
        self.assertNotIn(hidden.pk, [row["id"] for row in data])
        self.assertIn("Still there", data[0]["actions"])
        self.assertIn("Destroyed", data[0]["actions"])
        self.assertEqual(data[0]["reinforcement_time"], "12:00")
        self.assertEqual(data[0]["window_minutes"], 180)

    def test_special_types_have_half_hour_radius(self):
        for type_id in [EveTypeId.ANSIBLEX, EveTypeId.METENOX_MOON_DRILL]:
            with self.subTest(type_id=type_id):
                self.timer.structure_type = CitadelTypeFactory(id=type_id)
                self.timer.save()
                self.assertEqual(
                    self.client.get(self.url).json()[0]["window_minutes"], 30
                )

    def test_refresh_updates_only_timestamp(self):
        old_date = now() - timedelta(days=40)
        Timer.objects.filter(pk=self.timer.pk).update(last_updated_at=old_date)
        before = now()
        with patch(
            "structuretimers.models._task_schedule_notifications_for_timer"
        ) as schedule:
            response = self.client.post(self.action_url("refresh"))
        self.assertEqual(response.status_code, 200)
        self.timer.refresh_from_db()
        self.assertGreaterEqual(self.timer.last_updated_at, before)
        self.assertEqual(self.timer.reinforcement_time, time(12, 0))
        self.assertEqual(self.timer.timer_type, Timer.Type.PRELIMINARY)
        self.assertIsNone(self.timer.date)
        schedule.assert_not_called()

    def test_destroy_removes_timer(self):
        self.assertEqual(self.client.post(self.action_url("destroy")).status_code, 200)
        self.assertFalse(Timer.objects.filter(pk=self.timer.pk).exists())

    def test_mutations_require_post_and_csrf(self):
        self.assertEqual(self.client.get(self.action_url("refresh")).status_code, 405)
        self.assertEqual(self.client.get(self.action_url("destroy")).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.action_url("destroy")).status_code, 403)
        self.assertTrue(Timer.objects.filter(pk=self.timer.pk).exists())

    def test_read_only_user_can_view_but_not_change(self):
        self.client.force_login(UserWithAccessFactory())
        actions = self.client.get(self.url).json()[0]["actions"]
        self.assertIn("View", actions)
        self.assertNotIn("Still there", actions)
        for action in ["refresh", "destroy"]:
            self.assertEqual(self.client.post(self.action_url(action)).status_code, 403)

    def test_manager_can_refresh_other_users_recon(self):
        self.client.force_login(UserWithManageFactory())
        self.assertEqual(self.client.post(self.action_url("refresh")).status_code, 200)

    def test_hidden_and_nonpreliminary_timers_cannot_be_changed(self):
        for timer in [
            TimerFactory(timer_type=Timer.Type.HULL),
            TimerFactory(timer_type=Timer.Type.PRELIMINARY, is_opsec=True),
        ]:
            for action in ["refresh", "destroy"]:
                self.assertEqual(
                    self.client.post(self.action_url(action, timer)).status_code, 404
                )
                self.assertTrue(Timer.objects.filter(pk=timer.pk).exists())

    def test_recon_type_can_be_saved_and_missing_type_is_supported(self):
        structure_type = CitadelTypeFactory(id=EveTypeId.ANSIBLEX)
        form = ReconForm(
            data={
                "structure_type_2": structure_type.pk,
                "eve_solar_system_2": self.timer.eve_solar_system_id,
                "structure_name": "Ansiblex recon",
                "reinforcement_time": "00:00",
            },
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        recon = form.save()
        recon.refresh_from_db()
        self.assertEqual(recon.structure_type_id, structure_type.pk)
        self.assertEqual(recon.timer_type, Timer.Type.PRELIMINARY)
        self.timer.structure_type = None
        self.timer.reinforcement_time = None
        self.timer.save()
        row = next(
            row
            for row in self.client.get(self.url).json()
            if row["id"] == self.timer.pk
        )
        self.assertIsNone(row["reinforcement_time"])
        self.assertEqual(row["window_minutes"], 180)

    def test_edit_returns_to_manage_recon(self):
        self.timer.structure_type = None
        self.timer.save()
        url = (
            reverse("structuretimers:edit", args=[self.timer.pk]) + "?tab=manage-recon"
        )
        response = self.client.post(
            url,
            {
                "eve_solar_system_2": self.timer.eve_solar_system_id,
                "structure_name": "Updated recon",
            },
        )
        self.assertRedirects(
            response, reverse("structuretimers:timer_list") + "?tab=manage-recon"
        )
