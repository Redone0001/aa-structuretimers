"""Exercise SDE imports and the fields consumed by maps and fleet intelligence."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase

from aa_stratcom import battle_map, dscan, regional_map


@skipUnless(apps.is_installed("eve_sde"), "Requires installed eve_sde")
class SdeCompatibilityTests(TestCase):
    def test_imported_geography_and_ships_feed_consumers(self):
        from eve_sde.models import (
            Constellation,
            ItemCategory,
            ItemGroup,
            ItemType,
            Region,
            SolarSystem,
            Stargate,
        )

        fixtures = {
            "_sde": [{"buildNumber": 42, "releaseDate": "2026-11-24T00:00:00Z"}],
            "categories": [{"_key": 6, "name": {"en": "Ship"}}],
            "groups": [
                {
                    "_key": 25,
                    "name": {"en": "Frigate"},
                    "categoryID": 6,
                    "anchorable": False,
                    "anchored": False,
                    "fittableNonSingleton": False,
                    "published": True,
                    "useBasePrice": False,
                }
            ],
            "types": [
                {
                    "_key": 587,
                    "name": {"en": "Rifter"},
                    "groupID": 25,
                    "published": True,
                }
            ],
            "mapRegions": [{"_key": 10000001, "name": {"en": "Region"}}],
            "mapConstellations": [
                {
                    "_key": 20000001,
                    "name": {"en": "Constellation"},
                    "regionID": 10000001,
                }
            ],
            "mapSolarSystems": [
                {
                    "_key": system_id,
                    "name": {"en": name},
                    "constellationID": 20000001,
                    "position": {"x": x, "y": 0, "z": 0},
                    "position2D": {"x": x_2d, "y": 0},
                }
                for system_id, name, x, x_2d in (
                    (30000001, "ALPHA", 0, 0),
                    (30000002, "BETA", regional_map.LIGHT_YEAR, 100),
                )
            ],
            "mapStargates": [
                {
                    "_key": 50000001,
                    "solarSystemID": 30000001,
                    "destination": {"solarSystemID": 30000002},
                }
            ],
        }
        with TemporaryDirectory() as folder:
            for name, rows in fixtures.items():
                Path(folder, f"{name}.jsonl").write_text(
                    "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
                )
            # Supplementary type attributes are unrelated to these consumers.
            with patch.object(ItemType, "load_extra", return_value={}):
                for model in (
                    ItemCategory,
                    ItemGroup,
                    ItemType,
                    Region,
                    Constellation,
                    SolarSystem,
                    Stargate,
                ):
                    model.load_from_sde(folder)

        systems = list(
            SolarSystem.objects.filter(constellation__region_id=10000001)
            .select_related("constellation")
            .order_by("pk")
        )
        self.assertEqual([system.name for system in systems], ["ALPHA", "BETA"])
        geography = regional_map.geography_payload(
            Region.objects.get(pk=10000001), systems, Stargate
        )
        self.assertEqual(len(geography["nodes"]), 2)
        self.assertTrue(
            all(node["position"] is not None for node in geography["nodes"])
        )
        self.assertEqual(len(geography["edges"]), 1)
        self.assertEqual(regional_map.distance_ly(*systems), 1)
        self.assertEqual(
            battle_map.lookup("system", "ALPHA"),
            [{"id": 30000001, "name": "ALPHA", "region": "Region"}],
        )
        self.assertEqual(
            battle_map.lookup("ship", "Rifter"), [{"id": 587, "name": "Rifter"}]
        )
        self.assertEqual(
            dscan.parse_paste("587\tPlayer ship\tRifter\t1 km"),
            {"ship_name": "Rifter", "dps": 1, "logi": 0},
        )
