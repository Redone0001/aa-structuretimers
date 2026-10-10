"""Public fleet directory stays separate from Auth membership records."""

from unittest.mock import patch

import requests
from django.test import TestCase
from allianceauth.eveonline.models import EveAllianceInfo
from eveuniverse.models import EveEntity

from structuretimers.alliance_directory import (
    lookup_alliances,
    resolve_alliance,
    sync_alliances,
)


class AllianceDirectoryTests(TestCase):
    def test_public_names_and_auth_fallback_are_deduplicated(self):
        EveEntity.objects.create(
            id=99000001, name="Public Alliance", category="alliance"
        )
        EveEntity.objects.create(
            id=99000002, name="Public Corporation", category="corporation"
        )
        EveAllianceInfo.objects.create(
            alliance_id=99000001, alliance_name="Public Alliance", alliance_ticker="PUB"
        )
        EveAllianceInfo.objects.create(
            alliance_id=99000003, alliance_name="Public Legacy", alliance_ticker="OLD"
        )
        self.assertEqual(
            lookup_alliances("public"),
            [
                {"alliance_id": 99000001, "alliance_name": "Public Alliance"},
                {"alliance_id": 99000003, "alliance_name": "Public Legacy"},
            ],
        )
        self.assertEqual(resolve_alliance("PUBLIC ALLIANCE"), 99000001)
        self.assertEqual(resolve_alliance("Public Legacy"), 99000003)
        self.assertIsNone(resolve_alliance("Public Corporation"))
        self.assertIsNone(resolve_alliance("Uncertain report"))
        self.assertIsNone(resolve_alliance(""))

    @patch("structuretimers.alliance_directory.requests.get")
    @patch.object(EveEntity.objects, "bulk_resolve_ids")
    def test_sync_resolves_public_list_without_creating_auth_records(
        self, resolve, get
    ):
        get.return_value.json.return_value = [99000001]

        def populate(ids):
            EveEntity.objects.create(
                id=ids[0], name="New Alliance", category="alliance"
            )

        resolve.side_effect = populate
        self.assertEqual(sync_alliances(), 1)
        resolve.assert_called_once_with([99000001])
        self.assertFalse(EveAllianceInfo.objects.exists())
        get.return_value.raise_for_status.assert_called_once()

    @patch("structuretimers.alliance_directory.requests.get")
    def test_outage_preserves_cached_names(self, get):
        EveEntity.objects.create(
            id=99000001, name="Known Alliance", category="alliance"
        )
        get.side_effect = requests.Timeout()
        with self.assertRaises(requests.Timeout):
            sync_alliances()
        self.assertEqual(resolve_alliance("Known Alliance"), 99000001)

    @patch("structuretimers.alliance_directory.requests.get")
    @patch.object(EveEntity.objects, "bulk_resolve_ids")
    def test_invalid_response_is_not_imported(self, resolve, get):
        for payload in [{"error": "unavailable"}, [True], ["99000001"], [-1]]:
            get.return_value.json.return_value = payload
            with self.assertRaises(ValueError):
                sync_alliances()
        resolve.assert_not_called()

    def test_suggestions_are_bounded(self):
        EveEntity.objects.bulk_create(
            [
                EveEntity(id=99000000 + i, name=f"Alliance {i:02}", category="alliance")
                for i in range(40)
            ]
        )
        self.assertEqual(len(lookup_alliances("Alliance")), 30)
