"""Regional adapter security, filtering, geographic distance and stable placement."""

from datetime import timedelta
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.apps import apps
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import now

from structuretimers.models import Timer
from structuretimers.regional_map import LIGHT_YEAR, distance_ly
from structuretimers.tests.testdata.factory import (
    StructureFactory,
    TimerFactory,
    UserNoAccessFactory,
    UserWithAccessFactory,
    UserWithCreateFactory,
)


@skipUnless(apps.is_installed("eve_sde"), "Requires installed eve_sde")
class RegionalMapTests(TestCase):
    def setUp(self):
        from eve_sde.models import Constellation, Region, SolarSystem, Stargate

        self.region = Region.objects.create(id=10000001, name="Region")
        constellation = Constellation.objects.create(
            id=20000001, name="Constellation", region=self.region
        )
        self.a = SolarSystem.objects.create(
            id=30000001,
            name="ALPHA",
            constellation=constellation,
            x_2d=0,
            y_2d=0,
            x=0,
            y=0,
            z=0,
        )
        self.b = SolarSystem.objects.create(
            id=30000002,
            name="BETA",
            constellation=constellation,
            x_2d=100,
            y_2d=100,
            x=6 * LIGHT_YEAR,
            y=0,
            z=0,
        )
        self.missing = SolarSystem.objects.create(
            id=30000003, name="Missing", constellation=constellation
        )
        Stargate.objects.create(
            id=1, name="A to B", solar_system=self.a, destination=self.b
        )
        Stargate.objects.create(
            id=2, name="B to A", solar_system=self.b, destination=self.a
        )
        self.user = UserWithCreateFactory()
        self.client.force_login(self.user)
        self.patch = patch(
            "structuretimers.models._task_calc_timer_distances_for_all_staging_systems",
            Mock(),
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def get(self, layer, **params):
        return self.client.get(
            reverse("structuretimers:regional_map_data", args=[layer]),
            {"region": self.region.id, **params},
        )

    def timer(self, **kwargs):
        from eveuniverse.models import EveSolarSystem
        from eveuniverse.tests.testdata.factories_2 import EveSolarSystemFactory

        system = EveSolarSystem.objects.filter(
            pk=self.a.pk
        ).first() or EveSolarSystemFactory(id=self.a.pk)
        return StructureFactory(
            user=self.user,
            eve_solar_system=system,
            **kwargs,
        )

    def test_geography_stable_y_inverted_and_gates_deduplicated(self):
        data = self.get("geography").json()
        self.assertEqual(len(data["edges"]), 1)
        self.assertEqual(data["edges"][0]["id"], "gate-30000001-30000002")
        self.assertGreater(
            data["nodes"][0]["position"][1], data["nodes"][1]["position"][1]
        )
        self.assertIsNone(data["nodes"][2]["position"])
        self.assertEqual(
            data, self.get("geography", relationship="HO", window="24").json()
        )
        self.assertNotIn("timers", str(data))

    def test_hidden_timers_excluded_from_counts_and_details(self):
        self.timer(structure_name="Visible", objective="FR")
        self.timer(structure_name="SECRET OPSEC", objective="FR", is_opsec=True)
        hidden = self.timer(
            structure_name="SECRET CORPORATION",
            objective="FR",
            visibility=Timer.Visibility.CORPORATION,
        )
        reader = UserWithAccessFactory()
        self.client.force_login(reader)
        structures = self.get("structures").json()
        self.assertEqual(
            sum(i["count"] for s in structures["systems"] for i in s["indicators"]), 1
        )
        detail = self.get("details", system=self.a.pk)
        self.assertNotContains(detail, "SECRET")
        record = detail.json()["timers"][0]
        self.assertIsNone(record["edit_url"])
        self.assertIsNone(record["refresh_url"])
        self.assertEqual(
            self.client.post(
                reverse("structuretimers:recon_action", args=[hidden.pk, "refresh"])
            ).status_code,
            404,
        )

    def test_same_record_and_filters_aggregation_and_actions(self):
        first = self.timer(objective="FR")
        self.timer(objective="FR", structure_type=first.structure_type)
        self.timer(objective="HO")
        self.assertEqual(
            self.get("structures", relationship="FR").json()["systems"][0][
                "indicators"
            ][0]["count"],
            2,
        )
        self.assertEqual(
            self.get("structures", relationship="FR", window="4").json()["systems"], []
        )
        # Reinforcing the structure adds a timer to its history.
        first = TimerFactory(
            structure=first,
            user=self.user,
            timer_type=Timer.Type.ARMOR,
            date=now() + timedelta(hours=3),
        )
        self.assertEqual(
            len(
                self.get(
                    "details", system=self.a.pk, relationship="FR", window="4"
                ).json()["timers"]
            ),
            1,
        )
        self.assertTrue(
            self.get("details", system=self.a.pk, relationship="FR", window="4").json()[
                "timers"
            ][0]["edit_url"]
        )
        Timer.objects.filter(pk=first.pk).update(date=now() + timedelta(hours=8))
        self.assertEqual(
            self.get("structures", relationship="FR", window="4").json()["systems"], []
        )
        self.assertEqual(
            len(
                self.get("structures", relationship="FR", window="24").json()["systems"]
            ),
            1,
        )

    def test_blops_range_matches_shared_badges(self):
        from structuretimers.distance_ranges import JUMP_RANGES, distance_range
        from structuretimers.regional_map import RANGES

        self.assertEqual(RANGES, {key: limit for key, limit, _, _ in JUMP_RANGES})
        self.assertEqual(RANGES["blops"], 8.0)
        self.assertEqual(str(distance_range(7.9)[0]), "Blops")
        self.assertIsNone(distance_range(8.0))

    def test_range_uses_geographic_coordinates_and_external_origin(self):
        from eve_sde.models import SolarSystem

        source = SolarSystem.objects.create(
            id=30000999, name="External", x=0, y=0, z=0, x_2d=999999, y_2d=999999
        )
        data = self.get("range", range="super", source=source.pk).json()
        self.assertEqual(data["limit_ly"], 6)
        self.assertEqual(data["systems"][1]["distance_ly"], 6)
        self.assertIsNone(data["systems"][2]["distance_ly"])
        self.assertEqual(distance_ly(self.a, self.b), 6)
        self.assertEqual(
            self.get("search", q="External").json()["systems"][0]["id"], source.pk
        )

    def test_access_validation_and_empty_region(self):
        self.assertEqual(self.get("structures", relationship="bogus").status_code, 400)
        self.assertEqual(self.get("details", system=9999).status_code, 404)
        self.assertIn("no-store", self.get("structures")["Cache-Control"])
        self.client.force_login(UserNoAccessFactory())
        for layer in [
            "regions",
            "geography",
            "structures",
            "details",
            "range",
            "search",
        ]:
            self.assertEqual(self.get(layer).status_code, 403)
        self.client.logout()
        self.assertEqual(self.get("regions").status_code, 302)

    def test_region_picker_uses_space_type_not_name_size_or_coordinates(self):
        from eve_sde.models import Constellation, Region, SolarSystem

        for index, (name, system_id) in enumerate(
            [
                ("A-R00001", 31_000_001),
                ("C-R00001", 31_000_002),
                ("Abyssal", 32_000_001),
                ("Empty", None),
                ("Numeric region 123", 30_000_100),
                ("Pochven", 30_000_101),
            ],
            start=1,
        ):
            region = Region.objects.create(id=10_000_100 + index, name=name)
            constellation = Constellation.objects.create(
                id=20_000_100 + index, name=name, region=region
            )
            if system_id:
                SolarSystem.objects.create(
                    id=system_id, name=name, constellation=constellation
                )
        names = {r["name"] for r in self.get("regions").json()["regions"]}
        self.assertEqual(names, {"Region", "Numeric region 123", "Pochven"})
        # Region discovery is not a new access restriction or a search filter.
        self.assertEqual(
            self.get("search", q="A-R00001").json()["systems"][0]["id"], 31_000_001
        )

    def test_ccp_test_regions_hidden_by_default_but_can_be_shown(self):
        from eve_sde.models import Constellation, Region, SolarSystem

        for i, name in enumerate(["A821-A", "UUA-F4", "J7HZ-F"]):
            region = Region.objects.create(id=10000900 + i, name=name)
            constellation = Constellation.objects.create(
                id=20000900 + i, name=name, region=region
            )
            SolarSystem.objects.create(
                id=30000900 + i, name=name, constellation=constellation
            )
        self.assertEqual(
            [r["name"] for r in self.get("regions").json()["regions"]], ["Region"]
        )
        self.assertEqual(
            len(self.get("regions", include_test="1").json()["regions"]), 4
        )
