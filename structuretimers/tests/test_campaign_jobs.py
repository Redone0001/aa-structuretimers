"""Creation latency, atomic imports, retry behavior and read-only status APIs."""

from unittest.mock import patch

from django.core.cache import cache
from django.urls import reverse
from app_utils.testing import NoSocketsTestCase
from eveuniverse.tests.testdata.factories_2 import EveSolarSystemFactory

from structuretimers.campaign_jobs import prepare_campaign, enqueue_campaign_job
from structuretimers.models import ReconCampaign, ReconCampaignSystem
from structuretimers.tests.testdata.factory import UserWithAccessFactory


class TestCampaignJobs(NoSocketsTestCase):
    def setUp(self):
        cache.clear()
        self.user = UserWithAccessFactory()
        self.client.force_login(self.user)
        self.system = EveSolarSystemFactory()
        self.region = self.system.eve_constellation.eve_region
        self.campaign = ReconCampaign.objects.create(
            name="Background import",
            created_by=self.user,
            region_ids=[self.region.pk],
            import_status="pending",
            gates_status="pending",
        )

    def test_import_deduplicates_and_reuses_region_cache(self):
        ReconCampaignSystem.objects.create(
            campaign=self.campaign, solar_system=self.system
        )
        with (
            patch(
                "structuretimers.campaign_jobs.EveRegion.objects.update_or_create_esi"
            ) as importer,
            patch(
                "structuretimers.campaign_jobs.prepare_campaign.apply_async"
            ) as queue,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                prepare_campaign(self.campaign.pk, "systems")
            importer.assert_called_once_with(id=self.region.pk, include_children=True)
            queue.assert_called_once_with(
                args=[self.campaign.pk, "gates", False], retry=False
            )
            prepare_campaign(self.campaign.pk, "systems")  # redelivery is harmless
            self.assertEqual(importer.call_count, 1)
            second = ReconCampaign.objects.create(
                name="Cached",
                region_ids=[self.region.pk],
                import_status="pending",
                gates_status="pending",
            )
            prepare_campaign(second.pk, "systems")
            self.assertEqual(importer.call_count, 1)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.import_status, "ready")
        self.assertEqual(self.campaign.systems.count(), 1)
        self.assertEqual(second.systems.count(), 1)

    def test_failure_keeps_membership_atomic_and_retry_succeeds(self):
        with patch(
            "structuretimers.campaign_jobs.EveRegion.objects.update_or_create_esi",
            side_effect=RuntimeError("ESI unavailable"),
        ):
            prepare_campaign(self.campaign.pk, "systems")
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.import_status, "failed")
        self.assertEqual(self.campaign.systems.count(), 0)
        self.campaign.import_status = self.campaign.gates_status = "pending"
        self.campaign.save()
        with patch(
            "structuretimers.campaign_jobs.EveRegion.objects.update_or_create_esi"
        ):
            prepare_campaign(self.campaign.pk, "systems")
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.import_status, "ready")
        self.assertEqual(self.campaign.systems.count(), 1)

    def test_gate_job_reuses_existing_data_and_refresh_is_explicit(self):
        ReconCampaignSystem.objects.create(
            campaign=self.campaign, solar_system=self.system
        )
        with patch(
            "structuretimers.campaign_jobs.EveSolarSystem.objects.get_or_create_esi"
        ) as cached:
            prepare_campaign(self.campaign.pk, "gates")
        cached.assert_called_once_with(
            id=self.system.pk, include_children=True, enabled_sections=["stargates"]
        )
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.gates_status, "ready")
        self.campaign.gates_status = "pending"
        self.campaign.save()
        with patch(
            "structuretimers.campaign_jobs.EveSolarSystem.objects.update_or_create_esi",
            side_effect=RuntimeError(),
        ):
            prepare_campaign(self.campaign.pk, "gates", True)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.gates_status, "failed")

    def test_broker_failure_is_retryable(self):
        with patch(
            "structuretimers.campaign_jobs.prepare_campaign.apply_async",
            side_effect=RuntimeError("broker unavailable"),
        ):
            enqueue_campaign_job(self.campaign.pk, "systems")
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.import_status, "failed")

    def test_pending_campaign_cannot_be_completed(self):
        entry = ReconCampaignSystem.objects.create(
            campaign=self.campaign, solar_system=self.system, reserved_by=self.user
        )
        response = self.client.post(
            self.campaign.get_absolute_url(),
            {"action": "complete", "systems": [entry.pk]},
        )
        self.assertEqual(response.status_code, 400)
        entry.refresh_from_db()
        self.assertIsNone(entry.completed_at)
        status = self.client.get(
            reverse("structuretimers:campaign_status", args=[self.campaign.pk])
        ).json()
        self.assertEqual(
            status, {"import_status": "pending", "gates_status": "pending"}
        )

    def test_detail_does_not_build_map_payload(self):
        with patch("structuretimers.campaigns.campaign_map_data") as build:
            response = self.client.get(self.campaign.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        build.assert_not_called()
        self.assertNotContains(response, '"positions":')

    def test_system_only_creation_is_immediately_usable_without_esi(self):
        from django.contrib.auth.models import Permission

        self.user.user_permissions.add(
            Permission.objects.get(codename="recon_coordinator")
        )
        with (
            patch(
                "structuretimers.campaign_jobs.prepare_campaign.apply_async"
            ) as queue,
            patch(
                "structuretimers.campaigns.EveSolarSystem.objects.update_or_create_esi"
            ) as importer,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("structuretimers:campaign_create"),
                    {"name": "Instant", "systems": self.system.name},
                )
        self.assertEqual(response.status_code, 302)
        created = ReconCampaign.objects.get(name="Instant")
        self.assertEqual(created.import_status, "ready")
        self.assertEqual(created.systems.count(), 1)
        importer.assert_not_called()
        queue.assert_called_once_with(args=[created.pk, "gates", False], retry=False)

    def test_retry_requires_coordinator_and_does_not_duplicate_queue(self):
        from django.contrib.auth.models import Permission

        self.campaign.import_status = "failed"
        self.campaign.save()
        url = self.campaign.get_absolute_url()
        self.assertEqual(
            self.client.post(url, {"action": "retry_import"}).status_code, 403
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename="recon_coordinator")
        )
        with patch(
            "structuretimers.campaign_jobs.prepare_campaign.apply_async"
        ) as queue:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(url, {"action": "retry_import"})
                self.client.post(url, {"action": "retry_import"})
        queue.assert_called_once_with(
            args=[self.campaign.pk, "systems", False], retry=False
        )

    def test_system_validation_uses_one_query(self):
        from structuretimers.campaigns import CampaignForm

        systems = [EveSolarSystemFactory() for _ in range(20)]
        form = CampaignForm(
            {"name": "Batch", "systems": ",".join(s.name for s in systems)}
        )
        with self.assertNumQueries(1):
            self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(form.cleaned_data["resolved_systems"]), 20)

    def test_map_and_status_endpoints_require_access(self):
        from structuretimers.tests.testdata.factory import UserNoAccessFactory

        self.client.force_login(UserNoAccessFactory())
        for route in ["campaign_map_data", "campaign_status"]:
            self.assertEqual(
                self.client.get(
                    reverse("structuretimers:" + route, args=[self.campaign.pk])
                ).status_code,
                403,
            )
