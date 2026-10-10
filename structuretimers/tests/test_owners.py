"""Owners are real player corporations, looked up once and remembered."""

from unittest.mock import Mock, patch

from eveuniverse.models import EveEntity
from app_utils.testing import NoSocketsTestCase
from structuretimers import owners
from structuretimers.models import Organization, Timer
from structuretimers.tests.testdata.factory import TimerFactory

MODULE = "structuretimers.owners"


@patch(
    "structuretimers.models._task_calc_timer_distances_for_all_staging_systems", Mock()
)
class TestOwners(NoSocketsTestCase):
    def test_known_owner_needs_no_esi(self):
        known = Organization.objects.create(
            id=1, name="Known Corp", category=Organization.Category.CORPORATION
        )
        with patch(MODULE + ".EveEntity.objects.fetch_by_names_esi") as fetch:
            self.assertEqual(owners.resolve_corporation("known corp"), known)
            fetch.assert_not_called()

    @patch(MODULE + "._esi_get", return_value={"alliance_id": 99})
    @patch(MODULE + ".EveEntity.objects.resolve_name", return_value="Big Alliance")
    def test_new_owner_is_looked_up_and_remembered_with_alliance(self, *_):
        EveEntity.objects.create(
            id=2, name="New Corp", category=EveEntity.CATEGORY_CORPORATION
        )
        owner = owners.resolve_corporation("lookup:New Corp".removeprefix("lookup:"))
        self.assertEqual(owner.pk, 2)
        self.assertEqual(owner.alliance.name, "Big Alliance")
        self.assertEqual(owner.alliance.category, Organization.Category.ALLIANCE)
        self.assertEqual(owner.display_name, "New Corp [Big Alliance]")

    def test_names_that_are_not_corporations_are_rejected(self):
        EveEntity.objects.create(
            id=3, name="Some Pilot", category=EveEntity.CATEGORY_CHARACTER
        )
        self.assertIsNone(owners.clean_owner_value("lookup:Some Pilot"))
        self.assertIsNone(owners.clean_owner_value(""))

    def test_autocomplete_offers_known_owners_and_a_lookup(self):
        Organization.objects.create(
            id=4, name="Alpha Corp", category=Organization.Category.CORPORATION
        )
        results = owners.autocomplete("alp")
        self.assertEqual(results[0], {"id": "4", "text": "Alpha Corp"})
        self.assertEqual(results[1]["id"], "lookup:alp")
        self.assertEqual(owners.autocomplete("alpha corp")[1:], [])

    def test_timer_owner_name_follows_owner_and_known_names_link(self):
        corp = Organization.objects.create(
            id=5, name="Owner Corp", category=Organization.Category.CORPORATION
        )
        timer = TimerFactory(owner_corporation=corp, owner_name="stale")
        self.assertEqual(timer.owner_name, "Owner Corp")
        from_other_app = TimerFactory(owner_name="owner corp")
        self.assertEqual(from_other_app.owner_corporation, corp)
        self.assertEqual(from_other_app.structure.owner_corporation, corp)
        self.assertEqual(
            Timer.objects.get(pk=from_other_app.pk).owner_name, "Owner Corp"
        )
