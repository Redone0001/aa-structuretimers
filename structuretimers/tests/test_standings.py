"""Owner standings: five groups, synced from contacts, overridden by coordinators."""

from unittest.mock import Mock, patch

from django.urls import reverse
from esi.models import Token
from eveuniverse.helpers import EveEntityNameResolver
from app_utils.testing import NoSocketsTestCase
from structuretimers import standings
from structuretimers.models import Organization, StandingsSource
from structuretimers.tests.testdata.factory import (
    UserMainFactory,
    UserWithAccessFactory,
)

CORP = Organization.Category.CORPORATION
ALLIANCE = Organization.Category.ALLIANCE


class TestStandingRules(NoSocketsTestCase):
    def test_contacts_round_to_the_five_groups(self):
        for value, expected in [
            (10, 10), (7.6, 10), (6.7, 5), (2.6, 5), (2.4, 0), (0, 0),
            (-2.4, 0), (-2.6, -5), (-7.4, -5), (-7.6, -10), (-10, -10),
        ]:
            with self.subTest(value=value):
                self.assertEqual(standings.bucket(value), expected)

    def test_corporation_wins_over_alliance_and_override_wins_over_auto(self):
        alliance = Organization.objects.create(
            id=1, name="A", category=ALLIANCE, standing_auto=-10
        )
        corp = Organization.objects.create(
            id=2, name="C", category=CORP, alliance=alliance
        )
        self.assertEqual(standings.effective_standing(corp), -10)
        corp.standing_auto = 5
        self.assertEqual(standings.effective_standing(corp), 5)
        corp.standing_override = 0
        self.assertEqual(standings.effective_standing(corp), 0)
        alone = Organization(id=3, name="X", category=CORP)
        self.assertEqual(standings.effective_standing(alone), 0)
        self.assertIsNone(standings.effective_standing(None))
        self.assertEqual(standings.standing_label(5), "+5")
        self.assertEqual(standings.standing_label(-10), "-10")


class TestStandingsSync(NoSocketsTestCase):
    @patch("structuretimers.standings.EveEntity.objects.bulk_resolve_names")
    @patch("structuretimers.standings._get_pages")
    def test_sync_sets_auto_standings_and_clears_old_ones(self, get_pages, resolve):
        get_pages.return_value = [
            {"contact_id": 100, "contact_type": "alliance", "standing": -10.0},
            {"contact_id": 200, "contact_type": "corporation", "standing": 6.0},
            {"contact_id": 300, "contact_type": "character", "standing": 10.0},
        ]
        resolve.return_value = EveEntityNameResolver(
            {100: "Enemy Alliance", 200: "Friendly Corp", 999: "Our Alliance"}
        )
        stale = Organization.objects.create(
            id=400, name="Old", category=CORP, standing_auto=5, standing_override=-5
        )
        user = UserMainFactory()
        # bulk_create skips the token's own save, which would call ESI.
        (token,) = Token.objects.bulk_create(
            [
                Token(
                    user=user,
                    character_id=1,
                    character_name="Scout",
                    character_owner_hash="x",
                )
            ]
        )
        source = StandingsSource.objects.create(token=token, alliance_id=999)

        self.assertEqual(standings.sync_source(source), 3)

        self.assertEqual(Organization.objects.get(pk=100).standing_auto, -10)
        self.assertEqual(Organization.objects.get(pk=200).standing_auto, 5)
        self.assertEqual(Organization.objects.get(pk=200).category, CORP)
        self.assertEqual(Organization.objects.get(pk=999).standing_auto, 10)
        self.assertFalse(Organization.objects.filter(pk=300).exists())
        stale.refresh_from_db()
        self.assertIsNone(stale.standing_auto)
        self.assertEqual(stale.standing_override, -5)
        source.refresh_from_db()
        self.assertIsNotNone(source.last_sync_at)


class TestStandingsTab(NoSocketsTestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            id=1, name="Some Corp", category=CORP
        )
        self.coordinator = UserMainFactory(
            permissions__=[
                "structuretimers.basic_access",
                "structuretimers.recon_member",
                "structuretimers.recon_coordinator",
            ]
        )

    def test_only_coordinators_see_and_change_standings(self):
        url = reverse("structuretimers:standing_set", args=[self.organization.pk])
        self.client.force_login(UserWithAccessFactory())
        page = self.client.get(reverse("structuretimers:timer_list"))
        self.assertNotContains(page, 'id="tab-standings"')
        self.assertEqual(self.client.post(url, {"standing": "-10"}).status_code, 403)

        self.client.force_login(self.coordinator)
        page = self.client.get(reverse("structuretimers:timer_list") + "?tab=standings")
        self.assertContains(page, 'id="tab-standings"')
        self.assertContains(page, "Some Corp")
        self.assertEqual(page.context["tab"], "standings")
        self.client.post(url, {"standing": "-10"})
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.standing_override, -10)
        self.client.post(url, {"standing": ""})
        self.organization.refresh_from_db()
        self.assertIsNone(self.organization.standing_override)
        self.assertEqual(self.client.post(url, {"standing": "3"}).status_code, 400)

    @patch("structuretimers.views.standings.sync_all", Mock())
    def test_sync_now(self):
        self.client.force_login(self.coordinator)
        response = self.client.post(reverse("structuretimers:standings_sync"))
        self.assertEqual(response.status_code, 302)
