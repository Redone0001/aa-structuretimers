"""Explicit standings configuration and admin recovery tools."""

from unittest.mock import Mock, patch

from django.contrib.messages import get_messages
from django.urls import reverse
from esi.models import Scope, Token

from app_utils.testdata_factories import UserFactory
from app_utils.testing import NoSocketsTestCase

from aa_stratcom import standings
from aa_stratcom.models import Organization, StandingsSource, Timer
from aa_stratcom.tests.testdata.factory import TimerFactory


@patch("aa_stratcom.models._task_calc_structure_distances", Mock())
class TestStandingsAdmin(NoSocketsTestCase):
    def setUp(self):
        self.user = UserFactory(is_staff=True, is_superuser=True)
        self.client.force_login(self.user)
        (self.token,) = Token.objects.bulk_create(
            [
                Token(
                    character_id=7,
                    character_name="Chosen Diplomat",
                    character_owner_hash="x",
                )
            ]
        )
        self.token.scopes.add(Scope.objects.create(name=standings.CONTACTS_SCOPE))
        self.list_url = reverse("admin:aa_stratcom_standingssource_changelist")
        self.add_url = reverse("admin:aa_stratcom_standingssource_add")

    def source(self):
        return StandingsSource.objects.create(
            token=self.token, alliance_id=999, alliance_name="Our Alliance"
        )

    def test_admin_lists_source_health_without_exposing_secrets(self):
        source = self.source()
        source.last_error = "Contacts unavailable"
        source.save()
        response = self.client.get(self.list_url)
        self.assertContains(response, "Chosen Diplomat")
        self.assertContains(response, "Contacts unavailable")
        response = self.client.get(
            reverse("admin:aa_stratcom_standingssource_change", args=[source.pk])
        )
        self.assertContains(response, "Last sync at")
        self.assertNotContains(response, 'name="access_token"')
        self.assertNotContains(response, 'name="refresh_token"')

    @patch("aa_stratcom.admin.standings.sync_source")
    @patch(
        "aa_stratcom.admin.EveEntity.objects.resolve_name",
        return_value="Correct Alliance",
    )
    @patch("aa_stratcom.admin.requests.get")
    def test_explicit_token_derives_alliance_and_syncs(self, get, resolve, sync):
        get.return_value.json.return_value = {"alliance_id": 123}
        response = self.client.post(
            self.add_url, {"token": self.token.pk, "_save": "Save"}
        )
        self.assertEqual(response.status_code, 302)
        source = StandingsSource.objects.get()
        self.assertEqual(source.token, self.token)
        self.assertEqual(source.alliance_id, 123)
        self.assertEqual(source.alliance_name, "Correct Alliance")
        self.assertEqual(source.added_by, self.user)
        sync.assert_called_once()
        self.assertEqual(self.client.get(self.add_url).status_code, 403)

    def test_token_without_scope_is_rejected(self):
        self.token.scopes.clear()
        response = self.client.post(self.add_url, {"token": self.token.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StandingsSource.objects.exists())

    @patch("aa_stratcom.admin.requests.get")
    def test_character_without_alliance_is_rejected(self, get):
        get.return_value.json.return_value = {}
        response = self.client.post(self.add_url, {"token": self.token.pk})
        self.assertContains(response, "not in an alliance")
        self.assertFalse(StandingsSource.objects.exists())

    @patch(
        "aa_stratcom.admin.standings.sync_source",
        side_effect=RuntimeError("Token revoked"),
    )
    def test_failed_sync_is_saved_and_reported(self, sync):
        source = self.source()
        response = self.client.post(
            self.list_url, {"action": "sync_now", "_selected_action": [source.pk]}
        )
        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        self.assertEqual(source.last_error, "Token revoked")
        self.assertTrue(
            any(message.level == 40 for message in get_messages(response.wsgi_request))
        )

    @patch("aa_stratcom.models._task_calc_timer_distances_for_all_staging_systems")
    def test_override_save_and_source_delete_recalculate_objectives(self, task):
        source = self.source()
        corp = Organization.objects.create(
            id=100, name="Corp", category="corporation", standing_auto=-5
        )
        timer = TimerFactory(owner_corporation=corp)
        response = self.client.post(
            reverse("admin:aa_stratcom_organization_change", args=[corp.pk]),
            {
                "id": corp.pk,
                "name": corp.name,
                "category": corp.category,
                "alliance": "",
                "standing_override": "5",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        timer.refresh_from_db()
        self.assertEqual(timer.objective, Timer.Objective.FRIENDLY)
        other = Organization.objects.create(
            id=101, name="Other", category="corporation", standing_auto=-5
        )
        other_timer = TimerFactory(owner_corporation=other)
        response = self.client.post(
            self.list_url,
            {
                "action": "delete_selected",
                "_selected_action": [source.pk],
                "post": "yes",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(StandingsSource.objects.exists())
        corp.refresh_from_db()
        self.assertIsNone(corp.standing_auto)
        self.assertEqual(corp.standing_override, 5)
        other_timer.refresh_from_db()
        self.assertEqual(other_timer.objective, Timer.Objective.NEUTRAL)
        standings.sync_all()
        self.assertFalse(StandingsSource.objects.exists())

    def test_non_staff_cannot_access_admin(self):
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.get(self.list_url).status_code, 302)

    @patch("aa_stratcom.admin.standings.sync_source")
    @patch(
        "aa_stratcom.admin.EveEntity.objects.resolve_name",
        return_value="New Alliance",
    )
    @patch("aa_stratcom.admin.requests.get")
    def test_existing_source_can_be_replaced(self, get, resolve, sync):
        source = self.source()
        (token,) = Token.objects.bulk_create(
            [
                Token(
                    character_id=8,
                    character_name="New Diplomat",
                    character_owner_hash="y",
                )
            ]
        )
        token.scopes.add(Scope.objects.get(name=standings.CONTACTS_SCOPE))
        get.return_value.json.return_value = {"alliance_id": 456}
        response = self.client.post(
            reverse("admin:aa_stratcom_standingssource_change", args=[source.pk]),
            {
                "token": token.pk,
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        self.assertEqual(source.token, token)
        self.assertEqual(source.alliance_id, 456)
        self.assertEqual(StandingsSource.objects.count(), 1)
        sync.assert_called_once()

    def test_individual_source_delete_clears_cached_standings(self):
        source = self.source()
        org = Organization.objects.create(
            id=100, name="Corp", category="corporation", standing_auto=-5
        )
        response = self.client.post(
            reverse("admin:aa_stratcom_standingssource_delete", args=[source.pk]),
            {"post": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        org.refresh_from_db()
        self.assertIsNone(org.standing_auto)
        self.assertTrue(Token.objects.filter(pk=self.token.pk).exists())
