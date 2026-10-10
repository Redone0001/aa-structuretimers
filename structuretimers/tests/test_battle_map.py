"""Battle observations, shared fleets, permission boundaries and lookup validation."""

import json
from datetime import timedelta

from django.test import Client
from django.urls import reverse
from django.utils.timezone import now

from structuretimers.battle_map import timer_status
from structuretimers.models import MapFleetToken, MapTimerState, Timer
from structuretimers.tests.test_regional_map import RegionalMapTests
from structuretimers.tests.testdata.factory import (
    UserWithAccessFactory,
    UserNoAccessFactory,
)


class BattleMapTests(RegionalMapTests):
    def url(self, layer):
        return reverse("structuretimers:battle_map_data", args=[layer])

    def snapshot(self, **params):
        return self.client.get(
            self.url("snapshot"),
            {"region": self.region.pk, "day": now().date().isoformat(), **params},
        )

    def post(self, layer, **body):
        return self.client.post(
            self.url(layer), json.dumps(body), content_type="application/json"
        )

    def active_timer(self, **params):
        timer = self.timer(**params)
        Timer.objects.filter(pk=timer.pk).update(
            date=now() - timedelta(minutes=5), timer_type="AR"
        )
        timer.refresh_from_db()
        return timer

    def test_snapshot_permission_filtering_and_durations(self):
        self.active_timer(structure_name="Visible")
        self.active_timer(structure_name="SECRET", is_opsec=True)
        self.active_timer(
            structure_name="CORPSECRET", visibility=Timer.Visibility.CORPORATION
        )
        self.client.force_login(UserWithAccessFactory())
        response = self.snapshot()
        self.assertNotContains(response, "SECRET")
        self.assertEqual(len(response.json()["timers"]), 1)
        self.assertEqual(response.json()["timers"][0]["duration"], 900)
        self.assertFalse(response.json()["timers"][0]["can_edit"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_pause_resume_kill_restore_and_conflict(self):
        timer = self.active_timer(objective="HO")
        original = timer.date
        for revision, action in enumerate(["pause", "resume", "kill", "restore"]):
            response = self.post("timer", id=timer.pk, revision=revision, action=action)
            self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            self.post("timer", id=timer.pk, revision=0, action="kill").status_code, 409
        )
        timer.refresh_from_db()
        self.assertEqual(timer.date, original)
        self.assertEqual(len(timer.map_state.events), 4)
        self.assertEqual(timer_status(timer, timer.map_state.events, now()), "open")

    def test_readers_and_hidden_timer_mutation(self):
        timer = self.active_timer()
        hidden = self.active_timer(is_opsec=True)
        self.client.force_login(UserWithAccessFactory())
        self.assertEqual(
            self.post("timer", id=timer.pk, revision=0, action="kill").status_code, 403
        )
        self.assertEqual(
            self.post("timer", id=hidden.pk, revision=0, action="kill").status_code, 404
        )

    def test_expired_timer_cannot_pause_and_schedule_change_discards_observations(self):
        timer = self.active_timer()
        Timer.objects.filter(pk=timer.pk).update(date=now() - timedelta(hours=1))
        self.assertEqual(
            self.post("timer", id=timer.pk, revision=0, action="pause").status_code, 400
        )
        self.assertEqual(
            self.post("timer", id=timer.pk, revision=0, action="kill").status_code, 200
        )
        Timer.objects.filter(pk=timer.pk).update(date=now())
        self.assertEqual(self.snapshot().json()["timers"][0]["events"], [])

    def test_long_paused_timer_is_included_on_later_day(self):
        timer = self.active_timer()
        date = now() - timedelta(days=5)
        Timer.objects.filter(pk=timer.pk).update(date=date)
        MapTimerState.objects.create(
            timer=timer,
            events=[
                {
                    "schedule": date.isoformat(),
                    "action": "pause",
                    "at": (date + timedelta(minutes=3)).isoformat(),
                }
            ],
        )
        self.assertEqual(len(self.snapshot().json()["timers"]), 1)

    def test_shared_tokens_optional_fields_validation_crud(self):
        result = self.post("fleet", system_id=self.a.pk)
        self.assertEqual(result.status_code, 200, result.content)
        token = result.json()["token"]
        self.assertEqual(token["stance"], "foe")
        self.assertEqual(token["mobility"], "gate")
        self.assertIsNone(token["dps"])
        self.client.force_login(UserWithAccessFactory())
        self.assertEqual(len(self.snapshot().json()["tokens"]), 1)
        self.assertFalse(self.snapshot().json()["tokens"][0]["can_edit"])
        self.assertEqual(
            self.post("fleet", id=token["id"], revision=1, action="delete").status_code,
            403,
        )
        self.client.force_login(self.user)
        result = self.post(
            "fleet",
            id=token["id"],
            revision=1,
            system_id=self.b.pk,
            alliance_name="Uncertain alliance?",
            ship_name="Unknown ship",
            dps=0,
            logi=5,
            note="<script>text only</script>",
            dscan="unparsed\nscan",
        )
        self.assertEqual(result.status_code, 200, result.content)
        self.assertIsNone(result.json()["token"]["alliance_id"])
        self.assertEqual(result.json()["token"]["dps"], 0)
        self.assertEqual(
            self.post("fleet", id=token["id"], revision=1, action="delete").status_code,
            409,
        )
        self.assertEqual(
            self.post("fleet", id=token["id"], revision=2, action="delete").status_code,
            200,
        )
        self.assertFalse(MapFleetToken.objects.exists())
        for body in [
            {"dps": -1},
            {"logi": 1.5},
            {"stance": "bad"},
            {"mobility": "bad"},
            {"note": "x" * 5001},
        ]:
            self.assertEqual(
                self.post("fleet", system_id=self.a.pk, **body).status_code, 400
            )

    def test_ship_autocomplete_and_icon_resolve(self):
        from eve_sde.models import ItemCategory, ItemGroup, ItemType

        category = ItemCategory.objects.create(id=6, name="Ship")
        group = ItemGroup.objects.create(id=25, name="Frigate", category=category)
        ship = ItemType.objects.create(id=587, name="Rifter", group=group)
        data = self.client.get(self.url("lookup"), {"kind": "ship", "q": "rif"}).json()
        self.assertEqual(data["results"], [{"id": ship.pk, "name": "Rifter"}])
        result = self.post("fleet", system_id=self.a.pk, ship_name="Rifter")
        self.assertEqual(result.json()["token"]["ship_id"], ship.pk)

    def test_csrf_access_and_bad_inputs(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(
            client.post(
                self.url("fleet"), "{}", content_type="application/json"
            ).status_code,
            403,
        )
        self.assertEqual(self.snapshot(day="invalid").status_code, 400)
        self.assertEqual(
            self.client.post(
                self.url("fleet"), "[]", content_type="application/json"
            ).status_code,
            400,
        )
        self.client.force_login(UserNoAccessFactory())
        self.assertEqual(self.snapshot().status_code, 403)
        self.client.logout()
        self.assertEqual(self.snapshot().status_code, 302)
