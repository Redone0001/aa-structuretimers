"""aa-structures data shows in the Database without being copied."""

from unittest import skipUnless
from unittest.mock import Mock, patch

from django.urls import reverse
from eveuniverse.models import EveSolarSystem, EveType
from allianceauth.eveonline.models import EveCorporationInfo
from app_utils.testing import NoSocketsTestCase
from structuretimers import structures_bridge
from structuretimers.forms import TimerForm
from structuretimers.models import Timer
from structuretimers.tests.testdata.factory import (
    StructureFactory,
    TimerFactory,
    UserMainFactory,
    UserWithCreateFactory,
)

from .test_forms import FUTURE, create_form_data


@skipUnless(structures_bridge.is_available(), "aa-structures is not installed")
@patch(
    "structuretimers.models._task_calc_timer_distances_for_all_staging_systems", Mock()
)
class TestStructuresBridge(NoSocketsTestCase):
    @classmethod
    def setUpTestData(cls):
        from structures.models import Owner, Structure

        cls.home = StructureFactory()
        corporation = EveCorporationInfo.objects.create(
            corporation_id=2001,
            corporation_name="Friendly Corp",
            corporation_ticker="FRND",
            member_count=10,
        )
        owner = Owner.objects.create(corporation=corporation)
        cls.structure = Structure.objects.create(
            id=1000000000001,
            owner=owner,
            eve_solar_system=EveSolarSystem.objects.get(
                pk=cls.home.eve_solar_system_id
            ),
            eve_type=EveType.objects.get(pk=cls.home.structure_type_id),
            name="Friendly Keep",
            reinforce_hour=19,
            state=0,
        )

    def viewer(self, *extra):
        return UserWithCreateFactory(
            permissions__=[
                "structuretimers.basic_access",
                "structuretimers.recon_member",
                "structuretimers.create_timer",
                *extra,
            ]
        )

    def test_matching_timer_links_to_structure_without_new_record(self):
        records_before = Timer.objects.filter(timer_type="PL").count()
        timer = TimerFactory(
            timer_type=Timer.Type.ARMOR,
            eve_solar_system_id=self.structure.eve_solar_system_id,
            structure_type_id=self.structure.eve_type_id,
            structure_name="friendly keep",
        )
        self.assertEqual(timer.structures_structure_id, self.structure.id)
        self.assertIsNone(timer.structure)
        self.assertEqual(Timer.objects.filter(timer_type="PL").count(), records_before)

    def test_database_lists_structures_only_for_allowed_users(self):
        TimerFactory(
            timer_type=Timer.Type.HULL,
            eve_solar_system_id=self.structure.eve_solar_system_id,
            structure_type_id=self.structure.eve_type_id,
            structure_name="Friendly Keep",
        )
        url = reverse("structuretimers:recon_data")
        self.client.force_login(self.viewer())
        ids = [row["id"] for row in self.client.get(url).json()]
        self.assertNotIn(f"s-{self.structure.id}", ids)

        self.client.force_login(self.viewer("structures.view_all_structures"))
        rows = {row["id"]: row for row in self.client.get(url).json()}
        row = rows[f"s-{self.structure.id}"]
        self.assertEqual(row["reinforcement_time"], "19:00")
        self.assertEqual(row["objective_name"], "+10")
        self.assertEqual(row["owner_name"], "Friendly Corp")
        self.assertEqual(row["timer_count"], 1)
        self.assertIn(self.home.pk, rows)

    def test_picker_offers_and_links_structures(self):
        user = self.viewer("structures.view_all_structures")
        self.client.force_login(user)
        results = self.client.get(
            reverse("structuretimers:select2_database_entries"), {"term": "friendly"}
        ).json()["results"]
        self.assertEqual(results[0]["id"], f"s-{self.structure.id}")

        form = TimerForm(
            user=user,
            data=create_form_data(
                database_entry_2=f"s-{self.structure.id}",
                eve_solar_system_2=self.structure.eve_solar_system_id,
                structure_type_2=self.structure.eve_type_id,
                structure_name="Renamed",
                timer_type=Timer.Type.ARMOR,
                date=FUTURE,
            ),
        )
        self.assertTrue(form.is_valid(), form.errors)
        timer = form.save()
        self.assertEqual(timer.structures_structure_id, self.structure.id)
        self.assertIsNone(timer.structure)

    def test_picker_rejects_structures_the_user_cannot_see(self):
        form = TimerForm(
            user=self.viewer(),
            data=create_form_data(
                database_entry_2=f"s-{self.structure.id}",
                eve_solar_system_2=self.structure.eve_solar_system_id,
                structure_type_2=self.structure.eve_type_id,
                date=FUTURE,
            ),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("database_entry_2", form.errors)


class TestStructuresBridgeMissing(NoSocketsTestCase):
    @patch("structuretimers.structures_bridge.is_available", return_value=False)
    def test_without_aa_structures_nothing_changes(self, _):
        user = UserMainFactory(permissions__=["structuretimers.basic_access"])
        self.assertEqual(structures_bridge.database_rows(user), [])
        self.assertEqual(structures_bridge.picker_results(user, "any"), [])
        self.assertIsNone(structures_bridge.find_structure_id(Timer()))
