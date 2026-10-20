"""One Add information page: paste, parse, and save without duplicates."""

from unittest.mock import Mock, patch

from django.urls import reverse
from eveuniverse.tests.testdata.factories_2 import EveTypeFactory

from app_utils.testing import NoSocketsTestCase

from aa_stratcom.constants import EveGroupId, EveTypeId
from aa_stratcom.models import Structure, Timer
from aa_stratcom.tests.testdata.factory import (
    EveSolarSystemLowSecFactory,
    StructureFactory,
    UserWithCreateFactory,
)

from .test_forms import FUTURE, create_form_data, make_owner


@patch("aa_stratcom.models._task_calc_timer_distances_for_all_staging_systems", Mock())
@patch("aa_stratcom.models._task_calc_structure_distances", Mock())
@patch("aa_stratcom.models._task_schedule_notifications_for_timer", Mock())
class TestAddInformation(NoSocketsTestCase):
    def setUp(self):
        self.user = UserWithCreateFactory()
        self.client.force_login(self.user)
        self.system = EveSolarSystemLowSecFactory(name="SVM-3K")
        self.parse_url = reverse("aa_stratcom:add_info_parse")

    def parse(self, text):
        return self.client.post(self.parse_url, {"text": text}).json()

    def test_page_and_single_nav_button(self):
        response = self.client.get(reverse("aa_stratcom:add_info"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="st-add-info"')
        self.assertContains(response, "Add information")
        self.assertNotContains(response, "Quick add timer")
        self.assertNotContains(response, reverse("aa_stratcom:add_recon"))
        fields = list(response.context["form"].fields)
        self.assertEqual(
            fields[:3], ["database_entry_2", "paste_info", "structure_name"]
        )
        self.assertEqual(fields.index("date"), fields.index("timer_type") + 1)
        choices = dict(response.context["form"].fields["timer_type"].widget.choices)
        self.assertNotIn(Timer.Type.HULL, choices)

    def test_timer_paste_is_read_and_matched_to_existing_structure(self):
        record = StructureFactory(
            eve_solar_system=self.system, structure_name="kongbao"
        )
        data = self.parse(
            "SVM-3K - KONGBAO\n17 km\nReinforced until 2030.09.05 03:47:08"
        )
        self.assertEqual(data["found"], ["timer"])
        self.assertEqual(data["values"]["solar_system"]["id"], str(self.system.pk))
        self.assertEqual(data["values"]["structure_name"], "KONGBAO")
        self.assertEqual(data["values"]["date"], "2030-09-05 03:47")
        self.assertEqual(data["values"]["timer_event"], "reinforced")
        self.assertEqual(data["match"]["id"], record.pk)

    def test_fitting_paste_fills_type_name_and_fitting(self):
        record = StructureFactory(structure_name="Home")
        fit = f"[{record.structure_type.name}, Home]\nStandup Point Defense Battery I"
        data = self.parse(fit)
        self.assertEqual(data["found"], ["fitting"])
        self.assertEqual(
            data["values"]["structure_type"]["id"], str(record.structure_type_id)
        )
        self.assertEqual(data["values"]["fitting"], fit)
        self.assertEqual(data["match"]["id"], record.pk)

    def test_imported_name_matches_tooltip_and_saves_to_existing_record(self):
        self.system.name = "L-6W1J"
        self.system.save()
        record = StructureFactory(
            eve_solar_system=self.system,
            structure_name="L-6W1J - K-B8DK Gate (ChuangShi)",
            owner_name="ChuangShi",
        )
        data = self.parse(
            "1. L-6W1J - K-B8DK Gate 2,284 km Reinforced until 2026.10.24 03:49:18"
        )
        self.assertEqual(data["found"], ["timer"])
        self.assertEqual(data["match"]["id"], record.pk)
        self.assertEqual(data["values"]["structure_name"], record.structure_name)
        response = self.client.post(
            reverse("aa_stratcom:add_info"),
            create_form_data(
                database_entry_2=str(data["match"]["id"]),
                eve_solar_system_2=self.system.pk,
                structure_type_2=record.structure_type_id,
                structure_name=data["values"]["structure_name"],
                owner_2=make_owner("ChuangShi"),
                timer_type=Timer.Type.FINAL,
                date=FUTURE,
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Structure.objects.count(), 1)
        record.refresh_from_db()
        self.assertEqual(record.structure_name, "L-6W1J - K-B8DK Gate (ChuangShi)")
        self.assertEqual(record.timers.get().timer_type, Timer.Type.FINAL)

    def test_short_names_do_not_guess_between_owners(self):
        for owner in ("ChuangShi", "Other owner"):
            StructureFactory(
                eve_solar_system=self.system,
                structure_name=f"SVM-3K - Gate ({owner})",
                owner_name=owner,
            )
        data = self.parse("SVM-3K - Gate\nReinforced until 2030.09.05 03:47:08")
        self.assertIsNone(data["match"])

    def test_short_name_does_not_strip_arbitrary_parentheses(self):
        StructureFactory(
            eve_solar_system=self.system,
            structure_name="SVM-3K - Gate (North)",
            owner_name="ChuangShi",
        )
        data = self.parse("SVM-3K - Gate\nReinforced until 2030.09.05 03:47:08")
        self.assertIsNone(data["match"])

    def test_short_name_does_not_match_other_system_or_hidden_record(self):
        StructureFactory(
            structure_name="SVM-3K - Gate (ChuangShi)", owner_name="ChuangShi"
        )
        StructureFactory(
            eve_solar_system=self.system,
            structure_name="SVM-3K - Gate (ChuangShi)",
            owner_name="ChuangShi",
            is_opsec=True,
        )
        data = self.parse("SVM-3K - Gate\nReinforced until 2030.09.05 03:47:08")
        self.assertIsNone(data["match"])

    def test_unreadable_paste_finds_nothing(self):
        data = self.parse("hello there")
        self.assertEqual(data["found"], [])
        self.assertIsNone(data["match"])

    def test_anchoring_paste_identifies_anchoring(self):
        data = self.parse("SVM-3K - Home\nAnchoring until 2030.09.05 03:47:08")
        self.assertEqual(data["values"]["timer_event"], "anchoring")

    def test_structure_rules_include_large_and_faction_citadels(self):
        EveTypeFactory(id=EveTypeId.ASTRAHUS, eve_group__id=EveGroupId.CITADEL)
        faction = EveTypeFactory(id=47512, eve_group__id=EveGroupId.CITADEL)
        refinery = EveTypeFactory(
            id=EveTypeId.ATHANOR, eve_group__id=EveGroupId.REFINERY
        )
        response = self.client.get(reverse("aa_stratcom:add_info"))
        rules = response.context["timer_type_rules"]
        self.assertNotIn(EveTypeId.ASTRAHUS, rules["large"])
        self.assertIn(faction.pk, rules["large"])
        self.assertIn(EveTypeId.AZBEL, rules["large"])
        self.assertIn(refinery.pk, rules["refineries"])

    def test_validation_error_retains_parsed_timer_context(self):
        response = self.client.post(
            reverse("aa_stratcom:add_info"),
            {
                "paste_info": "SVM-3K - Home\nAnchoring until 2030.09.05 03:47:08",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["parsed_timer_event"], "anchoring")

    def test_saving_updates_the_picked_structure_instead_of_duplicating(self):
        record = StructureFactory(
            eve_solar_system=self.system, structure_name="kongbao"
        )
        response = self.client.post(
            reverse("aa_stratcom:add_info"),
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
            UserWithCreateFactory(permissions__=["aa_stratcom.basic_access"])
        )
        self.assertEqual(
            self.client.post(self.parse_url, {"text": "x"}).status_code, 403
        )
