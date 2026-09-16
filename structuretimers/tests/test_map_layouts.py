"""Bundled map positions must work without network access."""

import math
from app_utils.testing import NoSocketsTestCase
from structuretimers.map_layouts import get_region_layout, region_layouts


class TestMapLayouts(NoSocketsTestCase):
    def test_all_bundled_regions_have_valid_unique_positions(self):
        layouts = region_layouts()
        self.assertEqual(len(layouts), 67)
        for name, layout in layouts.items():
            with self.subTest(region=name):
                positions = list(layout["positions"].values())
                self.assertTrue(positions)
                self.assertEqual(len(positions), len(set(map(tuple, positions))))
                for x, y in positions:
                    self.assertTrue(math.isfinite(x) and math.isfinite(y))
                    self.assertTrue(0 <= x <= layout["width"])
                    self.assertTrue(0 <= y <= layout["height"])

    def test_querious_and_domain_labels(self):
        querious = get_region_layout("Querious")["positions"]
        self.assertLess(querious["L-FVHR"][0], querious["K7D-II"][0])
        self.assertLess(querious["V-3U8T"][1], querious["49-U6U"][1])
        self.assertIn("Amarr", get_region_layout("Domain")["positions"])
        self.assertIsNone(get_region_layout("Unknown region"))
