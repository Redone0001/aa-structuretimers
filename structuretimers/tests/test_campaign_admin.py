"""Admin management keeps campaign completion and deletion behavior consistent."""

from unittest.mock import Mock, patch

from django.urls import reverse
from django.utils.timezone import now
from app_utils.testdata_factories import UserFactory
from app_utils.testing import NoSocketsTestCase
from eveuniverse.tests.testdata.factories_2 import EveSolarSystemFactory

from structuretimers.models import ReconCampaign, ReconCampaignSystem, Timer
from structuretimers.tests.testdata.factory import TimerFactory


class TestCampaignAdmin(NoSocketsTestCase):
    def setUp(self):
        self.user = UserFactory(is_staff=True, is_superuser=True)
        self.client.force_login(self.user)
        self.campaign = ReconCampaign.objects.create(
            name="Admin campaign", created_by=self.user
        )
        self.entry = ReconCampaignSystem.objects.create(
            campaign=self.campaign, solar_system=EveSolarSystemFactory()
        )
        self.list_url = reverse("admin:structuretimers_reconcampaign_changelist")
        self.change_url = reverse(
            "admin:structuretimers_reconcampaign_change", args=[self.campaign.pk]
        )

    def data(self, completed=False, delete=False):
        data = {
            "name": "Updated campaign",
            "_save": "Save",
            "systems-TOTAL_FORMS": "1",
            "systems-INITIAL_FORMS": "1",
            "systems-MIN_NUM_FORMS": "0",
            "systems-MAX_NUM_FORMS": "1000",
            "systems-0-id": self.entry.pk,
            "systems-0-campaign": self.campaign.pk,
            "systems-0-solar_system": self.entry.solar_system_id,
            "systems-0-reserved_by": self.user.pk,
        }
        if completed:
            data["systems-0-completed"] = "on"
        if delete:
            data["systems-0-DELETE"] = "on"
        return data

    def test_campaign_visible_and_searchable(self):
        self.assertContains(self.client.get(reverse("admin:index")), "Recon campaigns")
        response = self.client.get(self.list_url, {"q": "Admin campaign"})
        self.assertContains(response, "Admin campaign")
        self.assertContains(response, "0 / 1")
        self.assertContains(self.client.get(self.change_url), "Open campaign")
        self.assertEqual(str(self.campaign), "Admin campaign")

    def test_inline_changes_update_completion_and_audit(self):
        response = self.client.post(self.change_url, self.data(completed=True))
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertEqual(self.campaign.name, "Updated campaign")
        self.assertIsNotNone(self.campaign.finished_at)
        self.assertIsNotNone(self.entry.completed_at)
        self.assertEqual(self.entry.completed_by, self.user)
        self.assertEqual(self.entry.reserved_by, self.user)
        self.assertEqual(
            self.client.post(self.change_url, self.data()).status_code, 302
        )
        self.campaign.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertIsNone(self.campaign.finished_at)
        self.assertIsNone(self.entry.completed_at)
        self.assertIsNone(self.entry.completed_by)

    def test_deleting_last_system_does_not_mark_empty_campaign_finished(self):
        self.campaign.finished_at = now()
        self.campaign.save()
        self.assertEqual(
            self.client.post(self.change_url, self.data(delete=True)).status_code, 302
        )
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.systems.count(), 0)
        self.assertIsNone(self.campaign.finished_at)

    @patch(
        "structuretimers.models._task_calc_timer_distances_for_all_staging_systems",
        Mock(),
    )
    def test_campaign_delete_keeps_timers_and_universe(self):
        timer = TimerFactory(
            eve_solar_system=self.entry.solar_system, timer_type=Timer.Type.PRELIMINARY
        )
        system = self.entry.solar_system
        url = reverse(
            "admin:structuretimers_reconcampaign_delete", args=[self.campaign.pk]
        )
        self.assertContains(self.client.get(url), "Admin campaign")
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 302)
        self.assertFalse(ReconCampaign.objects.filter(pk=self.campaign.pk).exists())
        self.assertFalse(ReconCampaignSystem.objects.filter(pk=self.entry.pk).exists())
        self.assertTrue(Timer.objects.filter(pk=timer.pk).exists())
        system.refresh_from_db()

    def test_failed_import_retry_action(self):
        self.campaign.import_status = "failed"
        self.campaign.save()
        with patch(
            "structuretimers.campaign_jobs.prepare_campaign.apply_async"
        ) as queue:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    self.list_url,
                    {
                        "action": "retry_failed_imports",
                        "_selected_action": [self.campaign.pk],
                    },
                )
        self.assertEqual(response.status_code, 302)
        queue.assert_called_once_with(
            args=[self.campaign.pk, "systems", False], retry=False
        )
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.import_status, "pending")

    def test_staff_without_model_permission_cannot_manage_campaign(self):
        self.client.force_login(UserFactory(is_staff=True))
        self.assertEqual(self.client.get(self.list_url).status_code, 403)
        self.assertEqual(
            self.client.post(self.change_url, self.data(completed=True)).status_code,
            403,
        )
        self.entry.refresh_from_db()
        self.assertIsNone(self.entry.completed_at)

    def test_pending_import_systems_are_read_only(self):
        self.campaign.import_status = "pending"
        self.campaign.save()
        self.assertEqual(self.client.get(self.change_url).status_code, 200)
        self.assertEqual(
            self.client.post(self.change_url, self.data(completed=True)).status_code,
            302,
        )
        self.entry.refresh_from_db()
        self.assertIsNone(self.entry.completed_at)
        self.assertIsNone(self.entry.reserved_by)
