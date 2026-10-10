"""Every scheduled timer points to a structure record in the database."""

from datetime import time, timedelta
from unittest.mock import Mock, patch

from django.urls import reverse
from django.utils.timezone import now
from app_utils.testing import NoSocketsTestCase
from structuretimers.forms import TimerForm
from structuretimers.models import Timer
from structuretimers.tests.testdata.factory import (
    TimerFactory,
    UserMainFactory,
    UserWithCreateFactory,
)

from .test_forms import FUTURE, create_form_data


@patch(
    "structuretimers.models._task_calc_timer_distances_for_all_staging_systems", Mock()
)
class TestDatabaseEntry(NoSocketsTestCase):
    def test_new_timer_creates_record_from_its_details(self):
        timer = TimerFactory(
            timer_type=Timer.Type.ARMOR,
            structure_name="Home",
            owner_name="Owner corp",
            objective=Timer.Objective.FRIENDLY,
            reinforcement_time=time(18, 0),
        )
        record = timer.database_entry
        self.assertEqual(record.timer_type, Timer.Type.PRELIMINARY)
        self.assertIsNone(record.date)
        self.assertFalse(record.discord_timerboard)
        for field in ("eve_solar_system", "structure_type", "structure_name"):
            self.assertEqual(getattr(record, field), getattr(timer, field))
        self.assertEqual(record.owner_name, "Owner corp")
        self.assertEqual(record.objective, Timer.Objective.FRIENDLY)
        self.assertEqual(record.reinforcement_time, time(18, 0))
        self.assertIsNone(record.database_entry)

    def test_matching_record_is_reused_and_filled_in(self):
        record = TimerFactory(
            timer_type=Timer.Type.PRELIMINARY,
            date=None,
            structure_name="Home",
            owner_name="",
            reinforcement_time=None,
        )
        Timer.objects.filter(pk=record.pk).update(
            last_updated_at=now() - timedelta(days=60)
        )
        timer = TimerFactory(
            timer_type=Timer.Type.HULL,
            eve_solar_system=record.eve_solar_system,
            structure_type=record.structure_type,
            structure_name="HOME",
            owner_name="Owner corp",
            reinforcement_time=time(3, 30),
        )
        self.assertEqual(timer.database_entry, record)
        record.refresh_from_db()
        self.assertEqual(record.owner_name, "Owner corp")
        self.assertEqual(record.reinforcement_time, time(3, 30))
        self.assertGreater(record.last_updated_at, now() - timedelta(minutes=1))
        self.assertEqual(Timer.objects.filter(timer_type="PL").count(), 1)

    def test_unnamed_or_different_structures_get_their_own_record(self):
        record = TimerFactory(
            timer_type=Timer.Type.PRELIMINARY, date=None, structure_name=""
        )
        unnamed = TimerFactory(
            eve_solar_system=record.eve_solar_system,
            structure_type=record.structure_type,
            structure_name="",
        )
        other_system = TimerFactory(
            structure_type=record.structure_type, structure_name="Home"
        )
        self.assertNotIn(
            record, [unnamed.database_entry, other_system.database_entry]
        )
        self.assertNotEqual(unnamed.database_entry, other_system.database_entry)

    def test_records_never_link_to_records(self):
        record = TimerFactory(timer_type=Timer.Type.PRELIMINARY, date=None)
        self.assertIsNone(record.database_entry)

    def test_scheduling_a_record_keeps_it_and_adds_a_timer(self):
        user = UserWithCreateFactory()
        record = TimerFactory(
            timer_type=Timer.Type.PRELIMINARY,
            date=None,
            user=user,
            structure_name="Home",
        )
        form = TimerForm(
            user=user,
            instance=record,
            data=create_form_data(
                eve_solar_system_2=record.eve_solar_system_id,
                structure_type_2=record.structure_type_id,
                structure_name="Home",
                timer_type=Timer.Type.ARMOR,
                date=FUTURE,
            ),
        )
        record_pk = record.pk
        self.assertTrue(form.is_valid(), form.errors)
        timer = form.save()
        record = Timer.objects.get(pk=record_pk)
        self.assertEqual(record.timer_type, Timer.Type.PRELIMINARY)
        self.assertIsNone(record.date)
        self.assertNotEqual(timer.pk, record.pk)
        self.assertEqual(timer.timer_type, Timer.Type.ARMOR)
        self.assertEqual(timer.database_entry, record)

    def test_picked_record_is_linked_even_if_details_differ(self):
        user = UserWithCreateFactory()
        record = TimerFactory(
            timer_type=Timer.Type.PRELIMINARY, date=None, structure_name="Home"
        )
        form = TimerForm(
            user=user,
            data=create_form_data(
                database_entry_2=str(record.pk),
                eve_solar_system_2=record.eve_solar_system_id,
                structure_type_2=record.structure_type_id,
                structure_name="Renamed since",
                timer_type=Timer.Type.ARMOR,
                date=FUTURE,
            ),
        )
        self.assertTrue(form.is_valid(), form.errors)
        timer = form.save()
        self.assertEqual(timer.database_entry, record)

    def test_picker_rejects_unknown_records_and_needs_recon_member(self):
        user = UserWithCreateFactory()
        not_a_record = TimerFactory(timer_type=Timer.Type.ARMOR)
        form = TimerForm(
            user=user,
            data=create_form_data(
                database_entry_2=str(not_a_record.pk),
                eve_solar_system_2=not_a_record.eve_solar_system_id,
                structure_type_2=not_a_record.structure_type_id,
                date=FUTURE,
            ),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("database_entry_2", form.errors)
        basic = UserMainFactory(
            permissions__=[
                "structuretimers.basic_access",
                "structuretimers.create_timer",
            ]
        )
        self.assertNotIn("database_entry_2", TimerForm(user=basic).fields)

    def test_picker_search_returns_visible_records(self):
        user = UserWithCreateFactory()
        record = TimerFactory(
            timer_type=Timer.Type.PRELIMINARY, date=None, structure_name="Home base"
        )
        TimerFactory(
            timer_type=Timer.Type.PRELIMINARY,
            date=None,
            structure_name="Home secret",
            is_opsec=True,
        )
        self.client.force_login(user)
        url = reverse("structuretimers:select2_database_entries")
        results = self.client.get(url, {"term": "home"}).json()["results"]
        self.assertEqual([row["id"] for row in results], [record.pk])
        self.assertEqual(results[0]["structure_name"], "Home base")
