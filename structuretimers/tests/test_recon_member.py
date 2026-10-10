"""Basic access alone only reaches the current and past timer tabs."""

from datetime import time
from http import HTTPStatus
from unittest.mock import Mock, patch

from django.urls import reverse
from app_utils.testing import NoSocketsTestCase
from structuretimers.models import Timer
from structuretimers.tests.testdata.factory import (
    StructureFactory,
    TimerFactory,
    UserMainFactory,
    UserWithAccessFactory,
)


@patch(
    "structuretimers.models._task_calc_timer_distances_for_all_staging_systems", Mock()
)
class TestReconMember(NoSocketsTestCase):
    def setUp(self):
        self.user = UserMainFactory(
            permissions__=[
                "structuretimers.basic_access",
                "structuretimers.create_timer",
            ]
        )
        self.client.force_login(self.user)
        self.recon = StructureFactory(
            user=self.user,
            reinforcement_time=time(12, 0),
        )

    def test_timer_list_hides_recon_tabs(self):
        response = self.client.get(
            reverse("structuretimers:timer_list") + "?tab=preliminary"
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.context["tab"], "current")
        self.assertNotContains(response, 'id="tab-preliminary"')
        self.assertNotContains(response, 'id="tab-recon-campaigns"')
        # The map section is open to everyone who can see timers.
        self.assertContains(response, 'id="st-map-panel"')
        self.assertNotContains(response, reverse("structuretimers:add_recon"))
        self.assertNotContains(response, 'id="recon-dashboard"')

    def test_recon_pages_and_endpoints_are_forbidden(self):
        for url in [
            reverse("structuretimers:recon_data"),
            reverse("structuretimers:campaign_list"),
            reverse("structuretimers:add_recon"),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, HTTPStatus.FORBIDDEN)
        response = self.client.post(
            reverse("structuretimers:recon_action", args=[self.recon.pk, "refresh"])
        )
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_recon_timers_are_invisible(self):
        response = self.client.get(
            reverse("structuretimers:structure_detail", args=[self.recon.pk])
        )
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_recon_member_restores_everything(self):
        user = UserWithAccessFactory()
        self.client.force_login(user)
        response = self.client.get(reverse("structuretimers:timer_list"))
        self.assertContains(response, 'id="tab-preliminary"')
        self.assertEqual(
            self.client.get(reverse("structuretimers:recon_data")).status_code,
            HTTPStatus.OK,
        )

    def test_map_is_open_to_basic_access_but_shows_no_records(self):
        self.assertRedirects(
            self.client.get(reverse("structuretimers:regional_map")),
            reverse("structuretimers:timer_list") + "?tab=current",
        )
        response = self.client.get(
            reverse("structuretimers:battle_map_data", args=["snapshot"])
        )
        self.assertNotEqual(response.status_code, HTTPStatus.FORBIDDEN)
        response = self.client.get(
            reverse("structuretimers:regional_map_data", args=["structures"]),
            {"region": self.recon.eve_solar_system.eve_constellation.eve_region_id},
        )
        self.assertNotEqual(response.status_code, HTTPStatus.FORBIDDEN)
        self.assertNotIn(str(self.recon.eve_solar_system_id), response.content.decode())
