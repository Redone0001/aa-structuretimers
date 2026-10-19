"""D-scan import parsing, source priority and atomic replacement/addition."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from django.test import TestCase
from django.urls import reverse

from aa_stratcom import dscan
from aa_stratcom.models import MapFleetToken
from aa_stratcom.tests.test_regional_map import RegionalMapTests
from aa_stratcom.tests.testdata.factory import UserWithAccessFactory

URL = "https://dscan.info/v/03b7e51b4e04"
PAGE = (Path(__file__).parent / "fixtures/dscan/example.html").read_bytes()


def scan_rows(type_id, count):
    return "\n".join(
        f"{type_id}\tPilot\twith tab\tShip type\t100 km" for _ in range(count)
    )


class ScanFetchTests(TestCase):
    @patch("structuretimers.dscan.requests.get")
    def test_real_page_structure_and_example_counts(self, get):
        response = Mock(status_code=200)
        response.iter_content.return_value = [PAGE[:30], PAGE[30:]]
        get.return_value.__enter__.return_value = response
        self.assertEqual(
            dscan.fetch_scan(URL),
            {"ship_name": "Apocalypse Navy Issue", "dps": 9, "logi": 5},
        )
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    @patch("structuretimers.dscan.requests.get")
    def test_url_allowlist_prevents_unrelated_fetches(self, get):
        for url in (
            "http://dscan.info/v/03b7e51b4e04",
            URL + "?redirect=1",
            "https://dscan.info.evil.test/v/03b7e51b4e04",
            "https://user@dscan.info/v/03b7e51b4e04",
            "https://127.0.0.1/v/03b7e51b4e04",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                dscan.fetch_scan(url)
        get.assert_not_called()

    @patch("structuretimers.dscan.requests.get")
    def test_redirect_missing_scan_oversize_and_timeout_are_errors(self, get):
        for status, chunks in (
            (302, []),
            (404, []),
            (200, [b"not a scan"]),
            (200, [b"x" * (dscan.MAX_PAGE + 1)]),
        ):
            response = Mock(status_code=status)
            response.iter_content.return_value = chunks
            get.return_value.__enter__.return_value = response
            with self.subTest(status=status), self.assertRaises(ValueError):
                dscan.fetch_scan(URL)
        get.side_effect = requests.Timeout()
        with self.assertRaisesMessage(ValueError, "Could not load"):
            dscan.fetch_scan(URL)


class ScanImportTests(RegionalMapTests):
    def setUp(self):
        super().setUp()
        from eve_sde.models import ItemCategory, ItemGroup, ItemType

        category, _ = ItemCategory.objects.get_or_create(
            id=6, defaults={"name": "Ship"}
        )
        for group_id, group_name, type_id, name in (
            (27, "Battleship", 17726, "Apocalypse Navy Issue"),
            (27, "Battleship", 642, "Apocalypse"),
            (832, "Logistics", 11987, "Guardian"),
            (1527, "Logistics Frigate", 37457, "Deacon"),
        ):
            group, _ = ItemGroup.objects.get_or_create(
                id=group_id, defaults={"name": group_name, "category": category}
            )
            ItemType.objects.create(id=type_id, name=name, group=group)
        self.scan = "\n".join(
            (
                scan_rows(17726, 7),
                scan_rows(642, 2),
                scan_rows(11987, 5),
                scan_rows(37457, 3),
            )
        )

    def save(self, **body):
        return self.client.post(
            reverse("structuretimers:battle_map_data", args=["fleet"]),
            json.dumps({"system_id": self.a.pk, **body}),
            content_type="application/json",
        )

    def test_paste_counts_groups_not_total_and_uses_larger_logistics_class(self):
        self.assertEqual(
            dscan.parse_paste(self.scan),
            {"dps": 9, "logi": 5, "ship_name": "Apocalypse Navy Issue"},
        )
        parsed = dscan.parse_paste(self.scan + "\n" + scan_rows(37457, 4))
        self.assertEqual(parsed["logi"], 7)
        self.assertEqual(dscan.parse_paste(scan_rows(11987, 3))["dps"], 0)
        self.assertEqual(dscan.parse_paste(scan_rows(642, 2))["logi"], 0)

    @patch("structuretimers.dscan.fetch_scan")
    def test_paste_has_priority_and_bad_paste_does_not_fall_back(self, fetch):
        result = self.save(dscan=self.scan, dscan_url="invalid ignored URL")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["token"]["dps"], 9)
        self.assertEqual(result.json()["token"]["dscan_url"], "")
        self.assertEqual(result.json()["token"]["ship_id"], 17726)
        self.assertEqual(self.save(dscan="bad scan", dscan_url=URL).status_code, 400)
        fetch.assert_not_called()

    def test_invalid_paste_is_not_partially_imported(self):
        for text in (self.scan + "\ninvalid row", "1\tthing\ttype\t-", "x" * 50001):
            with self.subTest(text=text[:20]), self.assertRaises(ValueError):
                dscan.parse_paste(text)

    def test_nonships_are_ignored_and_missing_sde_types_fail_without_partial_counts(
        self,
    ):
        from eve_sde.models import ItemCategory, ItemGroup, ItemType

        category = ItemCategory.objects.create(id=65, name="Structure")
        group = ItemGroup.objects.create(id=1657, name="Citadel", category=category)
        ItemType.objects.create(id=35832, name="Astrahus", group=group)
        result = dscan.parse_paste(self.scan + "\n" + scan_rows(35832, 20))
        self.assertEqual(
            (result["dps"], result["ship_name"]), (9, "Apocalypse Navy Issue")
        )
        with self.assertRaisesMessage(ValueError, "missing from the local EVE SDE"):
            dscan.parse_paste(self.scan + "\n" + scan_rows(99999999, 1))
        tied = dscan.parse_paste(scan_rows(642, 2) + "\n" + scan_rows(17726, 2))
        self.assertEqual(tied["ship_name"], "Apocalypse")

    @patch("structuretimers.dscan.fetch_scan")
    def test_token_changed_during_external_fetch_cannot_be_overwritten_or_added(
        self, fetch
    ):
        token = self.save(dps=20).json()["token"]

        def concurrent_edit(_url):
            MapFleetToken.objects.filter(pk=token["id"]).update(dps=30, revision=2)
            return {"dps": 9, "logi": 5, "ship_name": "Apocalypse Navy Issue"}

        fetch.side_effect = concurrent_edit
        result = self.save(
            id=token["id"],
            revision=token["revision"],
            dscan_url=URL,
            reinforcement=True,
        )
        self.assertEqual(result.status_code, 409)
        self.assertEqual(MapFleetToken.objects.get(pk=token["id"]).dps, 30)

    @patch(
        "structuretimers.dscan.fetch_scan",
        return_value={"dps": 9, "logi": 5, "ship_name": "Apocalypse Navy Issue"},
    )
    def test_url_import_persists_source(self, fetch):
        result = self.save(dscan_url=URL)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["token"]["dscan_url"], URL)
        fetch.assert_called_once_with(URL)

    def test_replace_reinforce_preserve_sources_and_reject_stale_addition(self):
        token = self.save(dps=20, logi=4).json()["token"]
        identity = {"id": token["id"], "revision": token["revision"]}
        result = self.save(**identity, dscan=self.scan, reinforcement=True)
        self.assertEqual(result.status_code, 200, result.content)
        token = result.json()["token"]
        self.assertEqual((token["dps"], token["logi"]), (29, 9))
        self.assertEqual(
            self.save(**identity, dscan=self.scan, reinforcement=True).status_code, 409
        )
        result = self.save(
            id=token["id"],
            revision=token["revision"],
            dps=29,
            logi=9,
            note="Only a note changed",
        )
        token = result.json()["token"]
        self.assertEqual(token["dscan"], self.scan)
        self.assertEqual((token["dps"], token["logi"]), (29, 9))
        result = self.save(id=token["id"], revision=token["revision"], dscan=self.scan)
        self.assertEqual(
            (result.json()["token"]["dps"], result.json()["token"]["logi"]), (9, 5)
        )

    def test_unknown_counts_manual_add_overflow_and_failure_leave_token_unchanged(self):
        token = self.save().json()["token"]
        result = self.save(
            id=token["id"],
            revision=token["revision"],
            reinforcement=True,
            dps=4,
            logi="",
        )
        token = result.json()["token"]
        self.assertEqual((token["dps"], token["logi"]), (4, None))
        for body in ({"dps": 2147483647, "reinforcement": True}, {"dscan": "bad scan"}):
            result = self.save(id=token["id"], revision=token["revision"], **body)
            self.assertEqual(result.status_code, 400, result.content)
        saved = MapFleetToken.objects.get(pk=token["id"])
        self.assertEqual(saved.dps, 4)
        self.assertEqual(saved.revision, token["revision"])

    @patch("structuretimers.dscan.fetch_scan")
    def test_cannot_fetch_or_reinforce_someone_elses_token(self, fetch):
        token = self.save(dps=20).json()["token"]
        self.client.force_login(UserWithAccessFactory())
        result = self.save(
            id=token["id"],
            revision=token["revision"],
            dscan_url=URL,
            reinforcement=True,
        )
        self.assertEqual(result.status_code, 403)
        fetch.assert_not_called()
