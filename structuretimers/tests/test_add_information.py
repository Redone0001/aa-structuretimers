"""One Add information page: paste, parse, and save without duplicates."""

from unittest.mock import Mock, patch

from django.urls import reverse
from app_utils.testing import NoSocketsTestCase
from structuretimers.models import Structure, Timer
from structuretimers.tests.testdata.factory import (
    EveSolarSystemLowSecFactory,
    StructureFactory,
    UserWithCreateFactory,
)

from .test_forms import FUTURE, create_form_data, make_owner


@patch(
    "structuretimers.models._task_calc_timer_distances_for_all_staging_systems", Mock()
)
@patch("structuretimers.models._task_calc_structure_distances", Mock())
class TestAddInformation(NoSocketsTestCase):
    def setUp(self):
        self.user = UserWithCreateFactory()
        self.client.force_login(self.user)
        self.system = EveSolarSystemLowSecFactory(name="SVM-3K")
        self.parse_url = reverse("structuretimers:add_info_parse")

    def parse(self, text):
        return self.client.post(self.parse_url, {"text": text}).json()

    def test_page_and_single_nav_button(self):
        response = self.client.get(reverse("structuretimers:add_info"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="st-add-info"')
        self.assertContains(response, "Add information")
        self.assertNotContains(response, "Quick add timer")
        self.assertNotContains(response, reverse("structuretimers:add_recon"))
        fields = list(response.context["form"].fields)
        self.assertEqual(fields[:3], ["database_entry_2", "paste_info", "structure_name"])

    def test_timer_paste_is_read_and_matched_to_existing_structure(self):
        record = StructureFactory(eve_solar_system=self.system, structure_name="kongbao")
        data = self.parse("SVM-3K - KONGBAO\n17 km\nReinforced until 2030.09.05 03:47:08")
        self.assertEqual(data["found"], ["timer"])
        self.assertEqual(data["values"]["solar_system"]["id"], str(self.system.pk))
        self.assertEqual(data["values"]["structure_name"], "KONGBAO")
        self.assertEqual(data["values"]["date"], "2030-09-05 03:47")
        self.assertEqual(data["match"]["id"], record.pk)

    def test_fitting_paste_fills_type_name_and_fitting(self):
        record = StructureFactory(structure_name="Home")
        fit = f"[{record.structure_type.name}, Home]\nStandup Point Defense Battery I"
        data = self.parse(fit)
        self.assertEqual(data["found"], ["fitting"])
        self.assertEqual(data["values"]["structure_type"]["id"], str(record.structure_type_id))
        self.assertEqual(data["values"]["fitting"], fit)
        self.assertEqual(data["match"]["id"], record.pk)

    def test_unreadable_paste_finds_nothing(self):
        data = self.parse("hello there")
        self.assertEqual(data["found"], [])
        self.assertIsNone(data["match"])

    def test_saving_updates_the_picked_structure_instead_of_duplicating(self):
        record = StructureFactory(eve_solar_system=self.system, structure_name="kongbao")
        response = self.client.post(
            reverse("structuretimers:add_info"),
            create_form_data(
                database_entry_2=str(record.pk),
                eve_solar_system_2=self.system.pk,
                structure_type_2=record.structure_type_id,
                structure_name="kongbao",
                owner_2=make_owner("Owner corp", 98000010),
                timer_type=Timer.Type.ARMOR,
                date=FUTURE,
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Structure.objects.count(), 1)
        record.refresh_from_db()
        self.assertEqual(record.owner_name, "Owner corp")
        self.assertEqual(record.timers.get().timer_type, Timer.Type.ARMOR)

    def test_parse_needs_create_permission(self):
        self.client.force_login(
            UserWithCreateFactory(permissions__=["structuretimers.basic_access"])
        )
        self.assertEqual(self.client.post(self.parse_url, {"text": "x"}).status_code, 403)
