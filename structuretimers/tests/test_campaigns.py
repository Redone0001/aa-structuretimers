"""Campaign permissions, region expansion and reservation workflow."""

from unittest.mock import Mock, patch

from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from app_utils.testing import NoSocketsTestCase
from eveuniverse.tests.testdata.factories_2 import EveSolarSystemFactory

from structuretimers.models import ReconCampaign, ReconCampaignSystem, Timer
from structuretimers.tests.testdata.factory import UserWithAccessFactory, TimerFactory


@patch(
    "structuretimers.models._task_calc_timer_distances_for_all_staging_systems", Mock()
)
class TestCampaigns(NoSocketsTestCase):
    def setUp(self):
        self.user = UserWithAccessFactory()
        self.client.force_login(self.user)
        self.campaign = ReconCampaign.objects.create(
            name="Scout north", created_by=self.user
        )
        self.entry = ReconCampaignSystem.objects.create(
            campaign=self.campaign, solar_system=EveSolarSystemFactory()
        )
        self.url = self.campaign.get_absolute_url()

    def coordinator(self):
        self.user.user_permissions.add(
            Permission.objects.get(codename="recon_coordinator")
        )

    def act(self, action, entries=None):
        return self.client.post(
            self.url,
            {"action": action, "systems": [e.pk for e in (entries or [self.entry])]},
        )

    def test_reserve_complete_reopen(self):
        self.assertEqual(self.act("reserve").status_code, 302)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.reserved_by, self.user)
        self.assertEqual(self.act("complete").status_code, 302)
        self.campaign.refresh_from_db()
        self.assertIsNotNone(self.campaign.finished_at)
        self.assertContains(self.client.get(self.url), "Finished")
        self.assertEqual(self.act("reopen").status_code, 403)
        self.coordinator()
        self.assertEqual(self.act("reopen").status_code, 302)
        self.campaign.refresh_from_db()
        self.assertIsNone(self.campaign.finished_at)

    def test_bulk_conflict_does_not_partially_reserve(self):
        other = UserWithAccessFactory()
        second = ReconCampaignSystem.objects.create(
            campaign=self.campaign,
            solar_system=EveSolarSystemFactory(),
            reserved_by=other,
        )
        self.act("reserve", [self.entry, second])
        self.entry.refresh_from_db()
        self.assertIsNone(self.entry.reserved_by)
        self.assertEqual(self.act("complete", [second]).status_code, 403)

    def test_all_systems_must_be_complete(self):
        second = ReconCampaignSystem.objects.create(
            campaign=self.campaign, solar_system=EveSolarSystemFactory()
        )
        self.act("reserve", [self.entry, second])
        self.act("complete")
        self.campaign.refresh_from_db()
        self.assertIsNone(self.campaign.finished_at)
        self.act("complete", [second])
        self.campaign.refresh_from_db()
        self.assertIsNotNone(self.campaign.finished_at)

    def test_creation_requires_coordinator_and_region_deduplicates(self):
        url = reverse("structuretimers:campaign_create")
        self.assertEqual(self.client.get(url).status_code, 403)
        self.coordinator()
        self.assertEqual(self.client.get(url).status_code, 200)
        region = self.entry.solar_system.eve_constellation.eve_region
        with patch(
            "structuretimers.campaigns.EveRegion.objects.update_or_create_esi"
        ) as importer:
            response = self.client.post(
                url,
                {
                    "name": "Region campaign",
                    "systems": self.entry.solar_system.name,
                    "regions": [region.pk],
                },
            )
        self.assertEqual(response.status_code, 302)
        importer.assert_called_once_with(id=region.pk, include_children=True)
        campaign = ReconCampaign.objects.get(name="Region campaign")
        self.assertEqual(campaign.systems.count(), 1)
        with patch(
            "structuretimers.campaigns.EveRegion.objects.update_or_create_esi",
            side_effect=RuntimeError(),
        ):
            self.client.post(url, {"name": "Failed import", "regions": [region.pk]})
        self.assertFalse(ReconCampaign.objects.filter(name="Failed import").exists())

    def test_reserved_user_can_manage_existing_recon_but_not_hidden(self):
        timer = TimerFactory(
            eve_solar_system=self.entry.solar_system,
            timer_type=Timer.Type.PRELIMINARY,
            date=None,
        )
        url = reverse(
            "structuretimers:campaign_recon_edit",
            args=[self.campaign.pk, self.entry.pk, timer.pk],
        )
        self.assertEqual(self.client.post(url, {"action": "refresh"}).status_code, 403)
        self.act("reserve")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(
            self.client.post(url, {"structure_name": "Scouted structure"}).status_code,
            302,
        )
        timer.refresh_from_db()
        self.assertEqual(timer.structure_name, "Scouted structure")
        self.assertEqual(timer.eve_solar_system_id, self.entry.solar_system_id)
        self.assertEqual(self.client.post(url, {"action": "refresh"}).status_code, 302)
        Timer.objects.filter(pk=timer.pk).update(is_opsec=True)
        self.assertEqual(self.client.post(url, {"action": "destroy"}).status_code, 404)
        self.assertNotContains(self.client.get(self.url), "Scouted structure")
        Timer.objects.filter(pk=timer.pk).update(is_opsec=False)
        self.assertEqual(self.client.post(url, {"action": "destroy"}).status_code, 302)
        self.assertFalse(Timer.objects.filter(pk=timer.pk).exists())

    def test_add_is_locked_to_reserved_system_and_completed_is_read_only(self):
        self.act("reserve")
        url = reverse(
            "structuretimers:campaign_recon_add", args=[self.campaign.pk, self.entry.pk]
        )
        response = self.client.post(
            url, {"structure_name": "New recon", "eve_solar_system_2": "999999"}
        )
        self.assertEqual(response.status_code, 302)
        timer = Timer.objects.get(structure_name="New recon")
        self.assertEqual(timer.eve_solar_system_id, self.entry.solar_system_id)
        self.assertEqual(timer.timer_type, Timer.Type.PRELIMINARY)
        self.act("complete")
        self.assertEqual(
            self.client.post(url, {"structure_name": "Too late"}).status_code, 403
        )

    def test_reservation_identity_and_csrf(self):
        other = UserWithAccessFactory()
        self.entry.reserved_by = other
        self.entry.save()
        self.assertNotContains(self.client.get(self.url), other.username)
        self.coordinator()
        self.assertContains(self.client.get(self.url), other.username)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(
            client.post(
                self.url, {"action": "reserve", "systems": [self.entry.pk]}
            ).status_code,
            403,
        )
