"""Operation roles, selection boundaries, concurrent edits and shared timer state."""

import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import Client
from django.urls import reverse
from django.utils.timezone import now

from app_utils.testing import NoSocketsTestCase

from structuretimers.models import MapTimerState, Operation, Timer
from structuretimers.tests.testdata.factory import TimerFactory, UserMainFactory


class TestOperations(NoSocketsTestCase):
    def setUp(self):
        for name in (
            "_task_calc_structure_distances",
            "_task_calc_timer_distances_for_all_staging_systems",
            "_task_schedule_notifications_for_timer",
        ):
            mocked = patch("structuretimers.models." + name, Mock())
            mocked.start()
            self.addCleanup(mocked.stop)
        self.coordinator = self.user("op_coordinator")
        self.viewer = self.user("op_viewer")
        self.outsider = self.user()
        self.timer = TimerFactory(
            user=self.outsider,
            date=now() - timedelta(minutes=5),
            timer_type=Timer.Type.ARMOR,
        )
        self.other = TimerFactory(user=self.outsider, date=now() + timedelta(hours=1))
        self.hidden = TimerFactory(user=self.outsider, is_opsec=True)
        self.operation = Operation.objects.create(
            name="Shared op",
            visibility=Operation.Visibility.ALL,
            created_by=self.coordinator,
        )
        self.operation.timers.add(self.timer)
        self.client.force_login(self.coordinator)

    @staticmethod
    def user(*roles):
        return UserMainFactory(
            permissions__=[
                "structuretimers.basic_access",
                *["structuretimers." + role for role in roles],
            ]
        )

    def url(self, action, operation=None):
        return reverse(
            "structuretimers:operation_" + action,
            kwargs={"pk": (operation or self.operation).pk},
        )

    def data(self, **changes):
        return {
            "name": "Updated op",
            "description": "Form up",
            "visibility": "all",
            "timers": [self.timer.pk],
            "version": self.operation.revision,
            **changes,
        }

    def mutate(self, client=None, **values):
        body = {
            "operation": self.operation.pk,
            "id": self.timer.pk,
            "revision": 0,
            "action": "pause",
            **values,
        }
        return (client or self.client).post(
            reverse("structuretimers:battle_map_data", args=["timer"]),
            json.dumps(body),
            content_type="application/json",
        )

    def test_tab_order_and_role_visibility(self):
        user = self.user("recon_member", "recon_coordinator", "op_coordinator")
        self.client.force_login(user)
        html = self.client.get(reverse("structuretimers:timer_list")).content.decode()
        positions = [
            html.index('id="tab-' + name + '"')
            for name in (
                "preliminary",
                "current",
                "past",
                "standings",
                "recon-campaigns",
                "ops",
            )
        ]
        self.assertEqual(positions, sorted(positions))
        self.client.force_login(self.outsider)
        response = self.client.get(
            reverse("structuretimers:timer_list"), {"tab": "ops"}
        )
        self.assertNotContains(response, 'id="tab-ops"')
        self.assertEqual(response.context["tab"], "current")
        for action in ("detail", "data", "live", "edit", "picker"):
            self.assertEqual(self.client.get(self.url(action)).status_code, 403)

    def test_viewers_only_see_shared_operations(self):
        private = Operation.objects.create(name="SECRET operation")
        self.client.force_login(self.viewer)
        page = self.client.get(reverse("structuretimers:timer_list"), {"tab": "ops"})
        self.assertContains(page, self.operation.name)
        self.assertNotContains(page, private.name)
        self.assertEqual(self.client.get(self.url("detail")).status_code, 200)
        self.assertEqual(self.client.get(self.url("detail", private)).status_code, 404)
        self.assertEqual(self.client.get(self.url("edit")).status_code, 403)
        self.assertEqual(
            self.client.post(self.url("edit"), self.data()).status_code, 403
        )
        self.assertEqual(
            self.client.get(reverse("structuretimers:operation_create")).status_code,
            403,
        )

    def test_coordinator_can_create_and_modify_an_operation(self):
        response = self.client.post(
            reverse("structuretimers:operation_create"),
            self.data(version=0, timers=[self.timer.pk, self.other.pk]),
        )
        self.assertEqual(response.status_code, 302)
        operation = Operation.objects.exclude(pk=self.operation.pk).get()
        self.assertEqual(operation.created_by, self.coordinator)
        self.assertSetEqual(
            set(operation.timers.values_list("pk", flat=True)),
            {self.timer.pk, self.other.pk},
        )
        response = self.client.post(
            self.url("edit", operation),
            self.data(
                version=operation.revision,
                name="Changed",
                visibility="coordinators",
                timers=[self.other.pk],
            ),
        )
        self.assertEqual(response.status_code, 302)
        operation.refresh_from_db()
        self.assertEqual(operation.name, "Changed")
        self.assertEqual(operation.visibility, "coordinators")
        self.assertEqual(operation.revision, 2)
        self.assertEqual(list(operation.timers.all()), [self.other])

    def test_selection_requires_current_visible_timers_and_at_least_one(self):
        old = TimerFactory(date=now() - timedelta(days=3))
        for ids in ([], [self.hidden.pk], [old.pk]):
            response = self.client.post(
                reverse("structuretimers:operation_create"),
                self.data(version=0, timers=ids),
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["operation_form"].errors)
        self.assertEqual(Operation.objects.count(), 1)

    def test_picker_reuses_current_and_retains_selected_history(self):
        old = TimerFactory(date=now() - timedelta(days=3))
        self.operation.timers.add(old)
        data = self.client.get(self.url("picker")).json()
        self.assertSetEqual(
            {row["id"] for row in data}, {self.timer.pk, self.other.pk, old.pk}
        )
        self.assertEqual(
            self.client.get(self.url("edit")).context["operation_selected_ids"],
            [self.timer.pk, old.pk],
        )
        response = self.client.post(
            self.url("edit"), self.data(timers=[self.timer.pk, old.pk])
        )
        self.assertEqual(response.status_code, 302)

    def test_operation_data_only_returns_selected_and_permitted_timers(self):
        self.operation.timers.add(self.hidden)
        self.client.force_login(self.viewer)
        rows = self.client.get(self.url("data")).json()
        self.assertEqual([row["id"] for row in rows], [self.timer.pk])
        self.assertFalse(rows[0]["battle"]["can_edit"])
        self.assertEqual(rows[0]["battle"]["duration"], 900)
        self.assertNotIn("timer-assignee", rows[0]["assignment"])

    def test_private_access_is_rechecked_before_conditional_live_response(self):
        self.client.force_login(self.viewer)
        response = self.client.get(self.url("live"))
        self.operation.visibility = Operation.Visibility.COORDINATORS
        self.operation.save()
        self.assertEqual(
            self.client.get(
                self.url("live"), HTTP_IF_NONE_MATCH=response["ETag"]
            ).status_code,
            404,
        )

    def test_pause_is_shared_with_viewer_and_changes_etag_without_moving_date(self):
        viewer = Client()
        viewer.force_login(self.viewer)
        before = viewer.get(self.url("live"))
        self.assertEqual(
            viewer.get(self.url("live"), HTTP_IF_NONE_MATCH=before["ETag"]).status_code,
            304,
        )
        original_date = self.timer.date
        self.assertEqual(self.mutate().status_code, 200)
        updated = viewer.get(self.url("live"), HTTP_IF_NONE_MATCH=before["ETag"])
        self.assertEqual(updated.status_code, 200)
        self.assertNotEqual(updated["ETag"], before["ETag"])
        self.assertEqual(updated.json()["timers"][0]["events"][-1]["action"], "pause")
        self.assertFalse(updated.json()["timers"][0]["can_edit"])
        self.timer.refresh_from_db()
        self.assertEqual(self.timer.date, original_date)
        self.assertEqual(self.mutate(action="resume", revision=1).status_code, 200)
        self.assertEqual(
            viewer.get(self.url("live")).json()["timers"][0]["events"][-1]["action"],
            "resume",
        )

    def test_operation_coordination_does_not_grant_global_or_unselected_edits(self):
        self.assertEqual(self.mutate(operation=None).status_code, 403)
        self.assertEqual(self.mutate(id=self.other.pk).status_code, 404)
        self.assertEqual(self.mutate().status_code, 200)
        self.client.force_login(self.viewer)
        self.assertEqual(self.mutate(action="resume", revision=1).status_code, 403)
        self.assertEqual(MapTimerState.objects.get(timer=self.timer).revision, 1)

    def test_stale_operation_editor_cannot_overwrite_another_coordinator(self):
        self.assertEqual(
            self.client.post(
                self.url("edit"), self.data(name="First update")
            ).status_code,
            302,
        )
        response = self.client.post(
            self.url("edit"), self.data(name="Lost update", timers=[self.other.pk])
        )
        self.assertEqual(response.status_code, 409)
        self.operation.refresh_from_db()
        self.assertEqual(self.operation.name, "First update")
        self.assertEqual(list(self.operation.timers.all()), [self.timer])

    def test_hidden_membership_is_not_exposed_or_dropped_by_an_editor(self):
        self.operation.timers.add(self.hidden)
        page = self.client.get(self.url("edit"))
        self.assertNotIn(self.hidden.pk, page.context["operation_selected_ids"])
        self.assertNotIn(
            self.hidden.pk, page.context["operation_form"].initial["timers"]
        )
        self.assertEqual(
            self.client.post(self.url("edit"), self.data()).status_code, 302
        )
        self.assertTrue(self.operation.timers.filter(pk=self.hidden.pk).exists())

    def test_coordinator_can_record_armor_and_viewer_cannot_use_prompt_or_action(self):
        prompt_url = reverse("structuretimers:battle_map_data", args=["reinforcement"])
        params = {"operation": self.operation.pk, "id": self.timer.pk}
        self.assertEqual(self.client.get(prompt_url, params).status_code, 200)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(prompt_url, params).status_code, 403)
        self.assertEqual(self.mutate(action="reinforce").status_code, 403)
        self.client.force_login(self.coordinator)
        response = self.mutate(
            action="reinforce",
            reinforcement_date=(now() + timedelta(days=2)).isoformat(),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.timer.structure.history.get().action, "reinforced")
        self.assertEqual(
            self.client.get(self.url("live")).json()["timers"][0]["events"][0][
                "action"
            ],
            "reinforce",
        )

    def test_selected_destroyed_timers_remain_in_operation_history(self):
        Timer.objects.filter(pk=self.timer.pk).update(timer_type=Timer.Type.FINAL)
        self.assertEqual(self.mutate(action="kill").status_code, 200)
        self.client.force_login(self.viewer)
        self.assertEqual(
            [row["id"] for row in self.client.get(self.url("data")).json()],
            [self.timer.pk],
        )
        self.assertIsNotNone(
            self.client.get(self.url("live")).json()["timers"][0]["destroyed_at"]
        )

    def test_operation_edits_and_observations_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.coordinator)
        self.assertEqual(client.post(self.url("edit"), self.data()).status_code, 403)
        self.assertEqual(self.mutate(client=client).status_code, 403)

    def test_live_index_only_lists_permitted_operations_and_detects_new_ones(self):
        private = Operation.objects.create(name="Private operation")
        self.client.force_login(self.viewer)
        url = reverse("structuretimers:operation_index_live")
        response = self.client.get(url)
        self.assertEqual(
            [row["name"] for row in response.json()["operations"]],
            [self.operation.name],
        )
        self.assertFalse(response.json()["can_edit"])
        self.assertIsNone(response.json()["operations"][0]["edit_url"])
        self.assertEqual(
            self.client.get(url, HTTP_IF_NONE_MATCH=response["ETag"]).status_code, 304
        )
        Operation.objects.create(name="New shared operation", visibility="all")
        updated = self.client.get(url, HTTP_IF_NONE_MATCH=response["ETag"])
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(len(updated.json()["operations"]), 2)
        self.client.force_login(self.coordinator)
        self.assertIn(
            private.name,
            [row["name"] for row in self.client.get(url).json()["operations"]],
        )
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_editor_selection_is_retained_after_validation_error(self):
        response = self.client.post(
            reverse("structuretimers:operation_create"),
            self.data(name="", version=0, timers=[self.timer.pk, self.other.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["operation_selected_ids"], [self.timer.pk, self.other.pk]
        )
