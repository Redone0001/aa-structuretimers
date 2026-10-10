"""Every timer is a reinforcement of a structure in the Database."""

from datetime import time, timedelta
from unittest.mock import Mock, patch

from django.urls import reverse
from django.utils.timezone import now
from app_utils.testing import NoSocketsTestCase
from structuretimers.forms import ReconForm, TimerForm
from structuretimers.models import Structure, Timer
from structuretimers.tests.testdata.factory import (
    StructureFactory,
    TimerFactory,
    UserMainFactory,
    UserWithCreateFactory,
)

from .test_forms import FUTURE, create_form_data


@patch(
    "structuretimers.models._task_calc_timer_distances_for_all_staging_systems", Mock()
)
@patch("structuretimers.models._task_calc_structure_distances", Mock())
class TestDatabaseEntry(NoSocketsTestCase):
    def test_new_timer_creates_record_from_its_details(self):
        timer = TimerFactory(
            timer_type=Timer.Type.ARMOR,
            structure_name="Home",
            owner_name="Owner corp",
            objective=Timer.Objective.FRIENDLY,
            reinforcement_time=time(18, 0),
        )
        record = timer.structure
        self.assertIsInstance(record, Structure)
        for field in ("eve_solar_system", "structure_type", "structure_name"):
            self.assertEqual(getattr(record, field), getattr(timer, field))
        self.assertEqual(record.owner_name, "Owner corp")
        self.assertEqual(record.objective, Timer.Objective.FRIENDLY)
        self.assertEqual(record.reinforcement_time, time(18, 0))
        self.assertEqual(list(record.timers.all()), [timer])

    def test_matching_record_is_reused_and_filled_in(self):
        record = StructureFactory(
            structure_name="Home", owner_name="", reinforcement_time=None
        )
        Structure.objects.filter(pk=record.pk).update(
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
        self.assertEqual(timer.structure, record)
        record.refresh_from_db()
        self.assertEqual(record.owner_name, "Owner corp")
        self.assertEqual(record.reinforcement_time, time(3, 30))
        self.assertGreater(record.last_updated_at, now() - timedelta(minutes=1))
        self.assertEqual(Structure.objects.count(), 1)
        # The timer takes the record's spelling of the name.
        self.assertEqual(timer.structure_name, "Home")

    def test_unnamed_or_different_structures_get_their_own_record(self):
        record = StructureFactory(structure_name="")
        unnamed = TimerFactory(
            eve_solar_system=record.eve_solar_system,
            structure_type=record.structure_type,
            structure_name="",
        )
        other_system = TimerFactory(
            structure_type=record.structure_type, structure_name="Home"
        )
        self.assertNotIn(record, [unnamed.structure, other_system.structure])
        self.assertNotEqual(unnamed.structure, other_system.structure)

    def test_editing_a_record_updates_its_timers(self):
        timer = TimerFactory(structure_name="Old name")
        record = timer.structure
        record.structure_name = "New name"
        record.save()
        timer.refresh_from_db()
        self.assertEqual(timer.structure_name, "New name")

    def test_record_with_a_date_adds_a_timer_to_its_history(self):
        user = UserWithCreateFactory()
        record = StructureFactory(user=user, structure_name="Home")
        TimerFactory(structure=record, date=now() - timedelta(days=30))
        form = ReconForm(
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
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(), record)
        self.assertEqual(record.timers.count(), 2)
        latest = record.timers.order_by("-date").first()
        self.assertEqual(latest.timer_type, Timer.Type.ARMOR)
        self.assertEqual(latest.user, user)

    def test_editing_a_timer_needs_its_date(self):
        user = UserWithCreateFactory()
        timer = TimerFactory(user=user, structure_name="Home")
        form = TimerForm(
            user=user,
            instance=timer,
            data=create_form_data(
                eve_solar_system_2=timer.eve_solar_system_id,
                structure_type_2=timer.structure_type_id,
            ),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("date", form.errors)

    def test_picked_record_is_linked_even_if_details_differ(self):
        user = UserWithCreateFactory()
        record = StructureFactory(structure_name="Home")
        form = TimerForm(
            user=user,
            data=create_form_data(
                database_entry_2=str(record.pk),
                eve_solar_system_2=record.eve_solar_system_id,
                structure_type_2=record.structure_type_id,
                structure_name="Renamed since",
                timer_type=Timer.Type.ARMOR,
                date=FUTURE,
                fitting="[Astrahus, Home]",
            ),
        )
        self.assertTrue(form.is_valid(), form.errors)
        timer = form.save()
        self.assertEqual(timer.structure, record)
        record.refresh_from_db()
        self.assertEqual(record.structure_name, "Renamed since")
        self.assertEqual(record.fitting, "[Astrahus, Home]")

    def test_picker_rejects_unknown_records_and_needs_recon_member(self):
        user = UserWithCreateFactory()
        record = StructureFactory()
        form = TimerForm(
            user=user,
            data=create_form_data(
                database_entry_2="999999",
                eve_solar_system_2=record.eve_solar_system_id,
                structure_type_2=record.structure_type_id,
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
        record = StructureFactory(structure_name="Home base")
        StructureFactory(structure_name="Home secret", is_opsec=True)
        self.client.force_login(user)
        url = reverse("structuretimers:select2_database_entries")
        results = self.client.get(url, {"term": "home"}).json()["results"]
        self.assertEqual([row["id"] for row in results], [record.pk])
        self.assertEqual(results[0]["structure_name"], "Home base")

    def test_current_timers_offer_view_and_copy_fit(self):
        user = UserWithCreateFactory()
        timer = TimerFactory(user=user)
        Structure.objects.filter(pk=timer.structure_id).update(fitting="[Raitaru, X]")
        other = TimerFactory()
        self.client.force_login(user)
        rows = {
            row["id"]: row
            for row in self.client.get(
                reverse("structuretimers:timer_list_data", args=["current"])
            ).json()
        }
        self.assertIn("st-view-fit", rows[timer.pk]["actions"])
        self.assertIn("st-copy-fit", rows[timer.pk]["actions"])
        self.assertIn("[Raitaru, X]", rows[timer.pk]["actions"])
        self.assertNotIn("st-view-fit", rows[other.pk]["actions"])
