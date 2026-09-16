"""Recon creation, validation and subsequent access."""

from datetime import time
from unittest.mock import Mock, patch

from django.test import override_settings
from django.urls import reverse

from app_utils.testing import NoSocketsTestCase

from structuretimers.forms import ReconForm
from structuretimers.models import ScheduledNotification, Timer
from structuretimers.tests.testdata.factory import (
    EveSolarSystemLowSecFactory,
    UserWithAccessFactory,
    UserWithCreateFactory,
)


@patch(
    "structuretimers.models._task_calc_timer_distances_for_all_staging_systems", Mock()
)
@override_settings(CELERY_ALWAYS_EAGER=True, CELERY_EAGER_PROPAGATES_EXCEPTIONS=True)
class TestRecon(NoSocketsTestCase):
    def setUp(self):
        self.user = UserWithCreateFactory()
        self.system = EveSolarSystemLowSecFactory()
        self.client.force_login(self.user)
        self.url = reverse("structuretimers:add_recon")
        self.data = {
            "eve_solar_system_2": str(self.system.pk),
            "structure_name": "Recon target",
            "reinforcement_time": "23:45",
        }

    def test_form_fields_and_defaults(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(tuple(form.fields), ReconForm.recon_fields)
        self.assertTrue(form.fields["timer_type"].widget.is_hidden)
        self.assertEqual(form["objective"].value(), Timer.Objective.NEUTRAL)
        self.assertContains(response, 'value="00:00"')

    def test_create_list_detail_and_edit(self):
        response = self.client.post(self.url, self.data)
        self.assertRedirects(
            response, reverse("structuretimers:timer_list") + "?tab=preliminary"
        )
        timer = Timer.objects.get(structure_name="Recon target")
        self.assertEqual(timer.timer_type, Timer.Type.PRELIMINARY)
        self.assertEqual(timer.objective, Timer.Objective.NEUTRAL)
        self.assertEqual(timer.reinforcement_time, time(23, 45))
        self.assertEqual(timer.user, self.user)
        self.assertIsNone(timer.date)
        self.assertIsNone(timer.structure_type)
        self.assertFalse(ScheduledNotification.objects.filter(timer=timer).exists())
        response = self.client.get(
            reverse("structuretimers:timer_list_data", args=["preliminary"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recon target")
        response = self.client.get(reverse("structuretimers:detail", args=[timer.pk]))
        self.assertContains(response, "23:45")
        edit_url = reverse("structuretimers:edit", args=[timer.pk])
        response = self.client.get(edit_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"]["reinforcement_time"].value(), time(23, 45)
        )
        response = self.client.post(
            edit_url, {**self.data, "reinforcement_time": "06:30"}
        )
        self.assertEqual(response.status_code, 302)
        timer.refresh_from_db()
        self.assertEqual(timer.reinforcement_time, time(6, 30))

    def test_ignores_injected_timer_fields(self):
        form = ReconForm(
            data={
                **self.data,
                "timer_type": Timer.Type.HULL,
                "date": "2026-10-01 12:00",
                "hours_left": "2",
            },
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        timer = form.save()
        self.assertEqual(timer.timer_type, Timer.Type.PRELIMINARY)
        self.assertIsNone(timer.date)

    def test_optional_fields_can_be_blank(self):
        form = ReconForm(data={**self.data, "reinforcement_time": ""}, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.save().reinforcement_time)

    def test_required_fields(self):
        for field in ("eve_solar_system_2", "structure_name"):
            with self.subTest(field=field):
                form = ReconForm(data={**self.data, field: ""}, user=self.user)
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_optional_details_and_objective_are_saved(self):
        form = ReconForm(
            data={
                **self.data,
                "location_details": "Moon 2",
                "owner_name": "Owner corp",
                "objective": Timer.Objective.FRIENDLY,
                "details_notes": "Scout report",
            },
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        timer = form.save()
        timer.refresh_from_db()
        self.assertEqual(timer.location_details, "Moon 2")
        self.assertEqual(timer.owner_name, "Owner corp")
        self.assertEqual(timer.objective, Timer.Objective.FRIENDLY)
        self.assertEqual(timer.details_notes, "Scout report")

    def test_valid_boundary_hours(self):
        for value in ("00:00", "23:59"):
            with self.subTest(value=value):
                form = ReconForm(
                    data={**self.data, "reinforcement_time": value}, user=self.user
                )
                self.assertTrue(form.is_valid(), form.errors)

    def test_invalid_hours(self):
        for value in ("24:00", "12:60", "12:00:30", "noon", "1:2"):
            with self.subTest(value=value):
                form = ReconForm(
                    data={**self.data, "reinforcement_time": value}, user=self.user
                )
                self.assertFalse(form.is_valid())
                self.assertIn("reinforcement_time", form.errors)

    def test_create_permission_required(self):
        self.client.force_login(UserWithAccessFactory())
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
        self.assertFalse(Timer.objects.filter(structure_name="Recon target").exists())
