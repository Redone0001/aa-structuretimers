"""Operation roster isolation, user membership, role permissions and validation."""

import json

from django.contrib import admin
from django.contrib.auth.models import Permission
from django.urls import reverse

from app_utils.testing import NoSocketsTestCase

from structuretimers.models import Operation, OperationRole, OperationRoleTemplate
from structuretimers.operation_roster import roster_payload
from structuretimers.tests import test_operations
from structuretimers.tests.testdata.factory import (
    EveSolarSystemFactory,
    UserMainFactory,
)


class TestOperationRoster(NoSocketsTestCase):
    """Exercise the same coordinator/viewer boundary with role snapshots."""

    user = staticmethod(test_operations.TestOperations.user)
    url = test_operations.TestOperations.url
    data = test_operations.TestOperations.data

    def setUp(self):
        test_operations.TestOperations.setUp(self)
        self.fc = OperationRoleTemplate.objects.create(name="FC")
        self.recon = OperationRoleTemplate.objects.create(name="Recon coord")
        self.system = EveSolarSystemFactory()
        self.recon.systems.add(self.system)
        self.person = UserMainFactory()
        self.other_person = UserMainFactory()

    def roster(self, **changes):
        return {
            "participants": [self.other_person.pk],
            "roles": [
                {
                    "name": "FC",
                    "systems": [],
                    "users": [self.person.pk],
                    "template_id": self.fc.pk,
                },
                {
                    "name": "Recon coord",
                    "systems": [self.system.name],
                    "users": [self.person.pk],
                    "template_id": self.recon.pk,
                },
                {
                    "name": "Temporary cyno",
                    "systems": ["Unknown op-only system"],
                    "users": [],
                },
            ],
            **changes,
        }

    def save(self, roster=None, **changes):
        return self.client.post(
            self.url("edit"),
            self.data(
                roster=json.dumps(roster if roster is not None else self.roster()),
                **changes,
            ),
        )

    def test_role_first_roster_has_unfilled_multiple_roles_and_unroled_participant(
        self,
    ):
        before = set(self.person.user_permissions.values_list("pk", flat=True))
        self.assertEqual(self.save().status_code, 302)
        roles = list(self.operation.roles.all())
        self.assertEqual(
            [role.name for role in roles], ["FC", "Recon coord", "Temporary cyno"]
        )
        self.assertEqual(list(roles[0].users.all()), [self.person])
        self.assertEqual(list(roles[1].users.all()), [self.person])
        self.assertFalse(roles[2].users.exists())
        self.assertEqual(
            set(self.operation.participants.all()), {self.person, self.other_person}
        )
        self.assertEqual(
            set(self.person.user_permissions.values_list("pk", flat=True)), before
        )
        self.assertFalse(self.person.groups.exists())
        payload = self.client.get(self.url("live")).json()["operation"]["roster"]
        self.assertEqual(payload["roles"][2]["systems"], ["Unknown op-only system"])
        self.assertEqual(payload["roles"][2]["users"], [])

    def test_new_operation_saves_roster_during_creation(self):
        response = self.client.post(
            reverse("structuretimers:operation_create"),
            self.data(
                name="New roster op", version=0, roster=json.dumps(self.roster())
            ),
        )
        self.assertEqual(response.status_code, 302, response.content)
        operation = Operation.objects.get(name="New roster op")
        self.assertEqual(operation.roles.count(), 3)
        self.assertEqual(
            set(operation.participants.all()), {self.person, self.other_person}
        )
        self.assertEqual(list(operation.timers.all()), [self.timer])
        self.assertFalse(self.operation.roles.exists())

    def test_template_and_other_operation_are_never_mutated_by_local_edits(self):
        from eveuniverse.models import EveSolarSystem

        count = EveSolarSystem.objects.count()
        self.assertEqual(self.save().status_code, 302)
        template_count = OperationRoleTemplate.objects.count()
        self.operation.refresh_from_db()
        data = self.roster()
        data["roles"][0].update(name="FC for this op", systems=["New temporary place"])
        self.assertEqual(
            self.save(data, version=self.operation.revision).status_code, 302
        )
        self.fc.refresh_from_db()
        self.assertEqual(self.fc.name, "FC")
        self.assertFalse(self.fc.systems.exists())
        self.assertEqual(OperationRoleTemplate.objects.count(), template_count)
        self.assertEqual(EveSolarSystem.objects.count(), count)
        other = Operation.objects.create(name="Other op")
        role = OperationRole.objects.create(
            operation=other, template=self.fc, name=self.fc.name
        )
        self.fc.name = "Future FC template"
        self.fc.save()
        self.assertEqual(self.operation.roles.first().name, "FC for this op")
        role.refresh_from_db()
        self.assertEqual(role.name, "FC")
        self.fc.delete()
        self.assertTrue(
            self.operation.roles.filter(name="FC for this op", template=None).exists()
        )

    def test_viewer_can_read_shared_roster_but_cannot_edit_or_lookup_users(self):
        self.save()
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.url("detail")).status_code, 200)
        self.assertEqual(
            self.client.get(self.url("live")).json()["operation"]["roster"]["roles"][0][
                "name"
            ],
            "FC",
        )
        self.assertEqual(self.save().status_code, 403)
        for kind in ("users", "systems"):
            self.assertEqual(
                self.client.get(
                    reverse("structuretimers:operation_roster_lookup", args=[kind])
                ).status_code,
                403,
            )
        self.operation.visibility = "coordinators"
        self.operation.save()
        self.assertEqual(self.client.get(self.url("live")).status_code, 404)
        self.client.force_login(self.person)
        self.assertEqual(self.client.get(self.url("detail")).status_code, 403)

    def test_invalid_roster_is_atomic_and_duplicate_names_rejected(self):
        for roster in (
            self.roster(roles=[{"name": "FC", "systems": [], "users": [999999]}]),
            self.roster(roles=[{"name": "FC"}, {"name": "fc"}]),
            self.roster(roles=[{"name": "bad", "systems": [False]}]),
            self.roster(participants=[True]),
            {"roles": "invalid", "participants": []},
        ):
            response = self.save(roster)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertTrue(response.context["operation_form"].errors)
            self.assertFalse(self.operation.roles.exists())
            self.operation.refresh_from_db()
            self.assertEqual(self.operation.revision, 1)

    def test_inactive_new_users_rejected_but_existing_participants_preserved(self):
        self.person.is_active = False
        self.person.save()
        self.assertTrue(self.save().context["operation_form"].errors)
        self.operation.participants.add(self.person)
        self.assertEqual(self.save().status_code, 302)

    def test_empty_roster_clears_only_this_operation_and_stale_edit_cannot_replace_it(
        self,
    ):
        self.assertEqual(self.save().status_code, 302)
        self.assertEqual(
            self.save(self.roster(roles=[], participants=[]), version=1).status_code,
            409,
        )
        self.assertEqual(self.operation.roles.count(), 3)
        self.operation.refresh_from_db()
        self.assertEqual(
            self.save(
                self.roster(roles=[], participants=[]), version=self.operation.revision
            ).status_code,
            302,
        )
        self.assertFalse(self.operation.roles.exists())
        self.assertFalse(self.operation.participants.exists())
        self.assertEqual(OperationRoleTemplate.objects.count(), 2)

    def test_live_roster_etag_changes_without_refresh_or_extra_auth_permissions(self):
        before = self.client.get(self.url("live"))
        self.save()
        after = self.client.get(self.url("live"), HTTP_IF_NONE_MATCH=before["ETag"])
        self.assertEqual(after.status_code, 200)
        self.assertNotEqual(after["ETag"], before["ETag"])
        self.assertEqual(
            self.client.get(
                self.url("live"), HTTP_IF_NONE_MATCH=after["ETag"]
            ).status_code,
            304,
        )
        self.assertEqual(len(roster_payload(self.operation)["roles"]), 3)

    def test_editor_receives_templates_and_admin_can_manage_global_roles(self):
        page = self.client.get(self.url("edit"))
        self.assertEqual(
            [role["name"] for role in page.context["operation_role_templates"]],
            ["FC", "Recon coord"],
        )
        self.assertContains(page, "Add all roles")
        self.assertContains(page, "Add temporary role")
        self.assertIn(OperationRoleTemplate, admin.site._registry)
        self.assertTrue(
            Permission.objects.filter(codename="change_operationroletemplate").exists()
        )
