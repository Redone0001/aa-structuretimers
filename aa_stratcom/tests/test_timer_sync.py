"""Federation permissions, replay safety, SSRF defense and replica enforcement."""

import copy
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet

from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.timezone import now

from aa_stratcom.models import Structure, Timer
from aa_stratcom.sync_models import (
    TimerSyncAudit,
    TimerSyncConfig,
    TimerSyncCredential,
    TimerSyncDelivery,
    TimerSyncDestination,
    TimerSyncIdentity,
    TimerSyncKey,
)
from aa_stratcom.sync_outbox import backfill, changed
from aa_stratcom.sync_protocol import serialize_timer
from aa_stratcom.sync_security import (
    authenticate,
    decrypt_secret,
    post_payload,
    validate_endpoint,
)
from aa_stratcom.tests.testdata.factory import TimerFactory, UserWithCreateFactory

HOST = "timers.example.org"
ENDPOINT = f"https://{HOST}/aa_stratcom/api/v1/sync/timers/"
ENCRYPTION_KEY = Fernet.generate_key().decode()


def grant(user, codename):
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label="aa_stratcom", codename=codename)
    )
    for name in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
        user.__dict__.pop(name, None)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    AA_STRATCOM_SYNC_ALLOWED_HOSTS=[HOST],
    AA_STRATCOM_SYNC_ENCRYPTION_KEY=ENCRYPTION_KEY,
)
class TimerSyncTests(TestCase):
    def setUp(self):
        cache.clear()
        for name in (
            "_task_calc_structure_distances",
            "_task_calc_timer_distances_for_all_staging_systems",
            "_task_schedule_notifications_for_timer",
        ):
            mock = patch("aa_stratcom.models." + name)
            mock.start()
            self.addCleanup(mock.stop)
        self.actor = UserWithCreateFactory(is_staff=True)
        self.source = uuid.uuid4()
        self.key = TimerSyncKey(
            name="Incoming partner", source_instance=self.source, created_by=self.actor
        )
        self.secret = self.key.generate()
        self.key.save()
        self.local = TimerFactory(
            user=self.actor,
            structure_name="Local citadel",
            date=(now() + timedelta(days=2)).replace(microsecond=0),
            timer_type="FI",
        )
        self.payload = {
            "schema": 1,
            "source": str(self.source),
            "timer_id": str(uuid.uuid4()),
            "revision": 1,
            "action": "upsert",
            "timer": serialize_timer(self.local),
        }
        self.payload["timer"]["structure_name"] = "Partner structure"
        self.url = reverse("aa_stratcom:timer_sync_api")

    def post(self, payload=None, secret=None, **kwargs):
        return self.client.post(
            self.url,
            json.dumps(payload if payload is not None else self.payload),
            content_type="application/json",
            secure=True,
            HTTP_AUTHORIZATION="Bearer " + (self.secret if secret is None else secret),
            **kwargs,
        )

    def destination(self, **kwargs):
        grant(self.actor, "manage_timer_sharing")
        return TimerSyncDestination.objects.create(
            name="Partner",
            endpoint=ENDPOINT,
            enabled=True,
            authorized_by=self.actor,
            **kwargs,
        )

    def imported(self):
        return TimerSyncIdentity.objects.get(source_instance=self.source).timer

    def test_key_is_hashed_and_revocation_expiry_rotation_are_enforced(self):
        self.assertNotEqual(self.key.digest, self.secret)
        self.assertEqual(len(self.key.digest), 64)
        self.assertEqual(authenticate("Bearer " + self.secret), self.key)
        self.assertIsNone(authenticate("Bearer " + self.secret[:-1] + "!"))
        self.assertIsNone(authenticate("Basic " + self.secret))
        old = self.secret
        self.secret = self.key.generate()
        self.key.save()
        self.assertIsNone(authenticate("Bearer " + old))
        self.assertEqual(self.post(secret=old).status_code, 401)
        self.key.enabled = False
        self.key.save()
        self.assertEqual(self.post().status_code, 401)
        self.key.enabled = True
        self.key.expires_at = now() - timedelta(seconds=1)
        self.key.save()
        self.assertEqual(self.post().status_code, 401)

    def test_https_bearer_only_no_cookie_or_query_auth_and_post_only(self):
        self.client.force_login(self.actor)
        self.assertEqual(
            self.client.post(
                self.url, "{}", content_type="application/json", secure=True
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                self.url + "?api_key=" + self.secret,
                "{}",
                content_type="application/json",
                secure=True,
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                self.url,
                "{}",
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + self.secret,
            ).status_code,
            400,
        )
        self.assertEqual(self.client.get(self.url, secure=True).status_code, 405)
        self.assertIn("no-store", self.post().headers["Cache-Control"])

    def test_receive_create_update_delete_and_duplicate_or_out_of_order_retries(self):
        self.assertEqual(self.post().status_code, 200)
        created = self.imported()
        self.assertEqual(created.structure_name, "Partner structure")
        self.assertIsNone(created.user_id)
        self.assertEqual(self.post().json()["result"], "unchanged")
        self.assertEqual(Timer.objects.count(), 2)
        self.payload["revision"] = 2
        self.payload["timer"]["date"] = (created.date + timedelta(hours=1)).isoformat()
        self.assertEqual(self.post().status_code, 200)
        created.refresh_from_db()
        self.assertEqual(created.date.isoformat(), self.payload["timer"]["date"])
        old = copy.deepcopy(self.payload)
        old["revision"] = 1
        self.assertEqual(self.post(old).json()["result"], "stale")
        self.payload.update(revision=3, action="delete", timer=None)
        self.assertEqual(self.post().json()["result"], "deleted")
        self.assertFalse(Timer.objects.filter(pk=created.pk).exists())
        self.assertTrue(Structure.objects.filter(pk=created.structure_id).exists())
        self.assertEqual(self.post().json()["result"], "unchanged")
        self.assertEqual(self.post(old).json()["result"], "stale")
        self.assertFalse(TimerSyncDelivery.objects.exists())

    def test_same_revision_with_different_content_is_a_conflict(self):
        self.assertEqual(self.post().status_code, 200)
        self.payload["timer"]["structure_name"] = "Tampered replay"
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(self.imported().structure_name, "Partner structure")

    def test_destroyed_state_is_replicated_and_can_be_restored(self):
        self.payload["timer"]["destroyed_at"] = now().replace(microsecond=0).isoformat()
        self.assertEqual(self.post().status_code, 200)
        self.assertIsNotNone(self.imported().structure.destroyed_at)
        self.payload["revision"] = 2
        self.payload["timer"]["destroyed_at"] = None
        self.assertEqual(self.post().status_code, 200)
        self.assertIsNone(self.imported().structure.destroyed_at)

    def test_stale_backfill_object_cannot_replace_newer_source_data(self):
        self.destination()
        stale = Timer.objects.get(pk=self.local.pk)
        self.local.date += timedelta(hours=2)
        self.local.save()
        changed(stale)
        self.assertEqual(
            TimerSyncDelivery.objects.get().payload["timer"]["date"],
            self.local.date.isoformat(),
        )

    def test_local_dedup_alias_does_not_stop_publishing_locally_owned_timer(self):
        self.payload["timer"] = serialize_timer(self.local)
        self.assertEqual(self.post().json()["result"], "deduplicated")
        self.destination()
        changed(self.local)
        self.assertTrue(TimerSyncDelivery.objects.exists())

    def test_deletion_while_destination_disabled_stays_queued_as_a_tombstone(self):
        target = self.destination()
        changed(self.local)
        target.enabled = False
        target.save()
        self.local.delete()
        row = TimerSyncDelivery.objects.get()
        self.assertEqual(row.payload["action"], "delete")
        self.assertIsNone(row.identity.timer_id)

    def test_changing_a_scheduled_timer_to_recon_retracts_it(self):
        self.destination()
        changed(self.local)
        self.local.date = None
        self.local.timer_type = "PL"
        self.local.save()
        self.assertEqual(TimerSyncDelivery.objects.get().payload["action"], "delete")

    @patch(
        "aa_stratcom.sync_jobs.deliver_timer_sync.delay",
        side_effect=RuntimeError("broker down"),
    )
    def test_broker_failure_keeps_durable_delivery_for_periodic_recovery(self, _delay):
        self.destination()
        with self.captureOnCommitCallbacks(execute=True):
            changed(self.local)
        self.assertEqual(TimerSyncDelivery.objects.count(), 1)

    def test_replica_does_not_offer_copy_or_battle_edits_even_to_coordinator(self):
        from aa_stratcom.battle_map import observation_payload

        config = TimerSyncConfig.current()
        config.replica, config.master_source = True, self.source
        config.save()
        grant(self.actor, "op_coordinator")
        self.assertFalse(
            observation_payload(self.local, self.actor, can_edit=True)["can_edit"]
        )
        self.client.force_login(self.actor)
        self.assertEqual(
            self.client.get(
                reverse("aa_stratcom:copy", args=[self.local.pk])
            ).status_code,
            403,
        )
        response = self.client.post(
            reverse("aa_stratcom:battle_map_data", args=["timer"]),
            json.dumps({"id": self.local.pk, "revision": 0, "action": "kill"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_forged_origin_unknown_fields_wrong_types_and_huge_payload_fail_closed(
        self,
    ):
        for edit in (
            lambda p: p.update(source=str(uuid.uuid4())),
            lambda p: p.update(schema=2),
            lambda p: p.update(revision=True),
            lambda p: p.update(action="execute"),
            lambda p: p["timer"].update(user_id=self.actor.pk),
            lambda p: p["timer"].update(system_id=True),
            lambda p: p["timer"].update(structure_name={"html": "x"}),
            lambda p: p["timer"].update(date="not a date"),
            lambda p: p["timer"].update(is_opsec="false"),
        ):
            payload = copy.deepcopy(self.payload)
            edit(payload)
            self.assertEqual(self.post(payload).status_code, 422, payload)
        self.assertEqual(self.post({"oversized": "x" * 33000}).status_code, 400)
        self.assertEqual(Timer.objects.count(), 1)

    def test_opsec_requires_explicit_receiving_key_opt_in(self):
        self.payload["timer"]["is_opsec"] = True
        self.assertEqual(self.post().status_code, 422)
        self.key.allow_opsec = True
        self.key.save()
        self.assertEqual(self.post().status_code, 200)
        self.assertTrue(self.imported().is_opsec)

    def test_missing_corporation_restriction_is_not_silently_made_public(self):
        self.payload["timer"].update(visibility="CO", corporation_id=99999999)
        self.assertEqual(self.post().status_code, 422)
        self.assertEqual(Timer.objects.count(), 1)

    def test_fingerprint_deduplicates_but_different_structure_or_type_does_not(self):
        self.payload["timer"] = serialize_timer(self.local)
        self.assertEqual(self.post().json()["result"], "deduplicated")
        self.assertEqual(self.imported().pk, self.local.pk)
        self.assertEqual(Timer.objects.count(), 1)
        self.payload["revision"] = 2
        self.payload["timer"]["date"] = (
            self.local.date + timedelta(hours=1)
        ).isoformat()
        self.assertEqual(self.post().status_code, 409)
        self.payload["timer_id"] = str(uuid.uuid4())
        self.payload["timer"]["structure_name"] = "Another structure at the same time"
        self.payload["timer"]["date"] = self.local.date.isoformat()
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(Timer.objects.count(), 2)

    def test_replica_blocks_other_sources_and_adopts_matching_local_timer(self):
        config = TimerSyncConfig.current()
        config.replica, config.master_source = True, uuid.uuid4()
        config.save()
        self.assertEqual(self.post().status_code, 422)
        config.master_source = self.source
        config.save()
        self.payload["timer"] = serialize_timer(self.local)
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(Timer.objects.count(), 1)
        self.assertTrue(
            TimerSyncIdentity.objects.get(source_instance=self.source).owns_timer
        )
        self.payload["revision"] = 2
        self.payload["timer"]["date"] = (
            self.local.date + timedelta(hours=1)
        ).isoformat()
        self.assertEqual(self.post().status_code, 200)
        self.local.refresh_from_db()
        self.assertEqual(self.local.date.isoformat(), self.payload["timer"]["date"])

    def test_switching_peer_to_replica_adopts_an_existing_dedup_alias(self):
        self.payload["timer"] = serialize_timer(self.local)
        self.assertEqual(self.post().json()["result"], "deduplicated")
        config = TimerSyncConfig.current()
        config.replica, config.master_source = True, self.source
        config.save()
        self.payload["revision"] = 2
        self.payload["timer"]["date"] = (
            self.local.date + timedelta(hours=1)
        ).isoformat()
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(Timer.objects.count(), 1)
        self.assertTrue(
            TimerSyncIdentity.objects.get(source_instance=self.source).owns_timer
        )

    def test_replica_protects_direct_and_bulk_writes_and_creation_views(self):
        config = TimerSyncConfig.current()
        config.replica, config.master_source = True, self.source
        config.save()
        self.client.force_login(self.actor)
        for url_name in ("add", "add_fast", "add_info", "add_recon"):
            self.assertEqual(
                self.client.get(reverse("aa_stratcom:" + url_name)).status_code, 403
            )
        self.assertFalse(self.local.user_can_edit(self.actor))
        for mutation in (
            lambda: self.local.save(),
            lambda: self.local.delete(),
            lambda: Timer.objects.filter(pk=self.local.pk).delete(),
            lambda: Timer.objects.filter(pk=self.local.pk).update(timer_type="AR"),
            lambda: Timer.objects.bulk_create([Timer(date=now())]),
            lambda: self.local.structure.save(),
            lambda: Structure.objects.filter(pk=self.local.structure_id).update(
                structure_name="bad"
            ),
        ):
            with self.assertRaises(PermissionDenied):
                mutation()
        self.assertEqual(Timer.objects.delete_obsolete(), 0)

    @patch("aa_stratcom.sync_security.cache.add", side_effect=RuntimeError("down"))
    def test_rate_limit_failure_does_not_disable_protection(self, _mock):
        self.assertEqual(self.post().status_code, 503)
        self.assertEqual(Timer.objects.count(), 1)

    @patch("aa_stratcom.sync_api.within_rate_limit", return_value=False)
    def test_rate_limit_is_enforced(self, _mock):
        response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "60")

    def test_write_only_credentials_are_encrypted_and_bound_to_destination(self):
        target = self.destination()
        credential = TimerSyncCredential(destination=target)
        credential.set_secret(self.secret)
        credential.save()
        credential.refresh_from_db()
        self.assertNotIn(self.secret, credential.ciphertext)
        self.assertEqual(decrypt_secret(credential), self.secret)
        target.endpoint = (
            "https://elsewhere.example.org/aa_stratcom/api/v1/sync/timers/"
        )
        target.save()
        credential.destination = target
        with self.assertRaisesMessage(ValidationError, "Destination changed"):
            decrypt_secret(credential)

    def test_endpoint_allowlist_rejects_credentials_redirect_parameters_ports_and_http(
        self,
    ):
        self.assertEqual(validate_endpoint(ENDPOINT).hostname, HOST)
        for url in (
            ENDPOINT.replace("https:", "http:"),
            ENDPOINT + "?next=1",
            ENDPOINT.replace(HOST, "evil.example.org"),
            ENDPOINT.replace(HOST, "user@" + HOST),
            ENDPOINT.replace(HOST, HOST + ":443"),
            "https://127.0.0.1/aa_stratcom/api/v1/sync/timers/",
        ):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                validate_endpoint(url)

    @patch("aa_stratcom.sync_security.socket.getaddrinfo")
    @patch("aa_stratcom.sync_security.urllib3.HTTPSConnectionPool")
    def test_private_or_mixed_dns_addresses_never_receive_a_credential(
        self, pool, resolve
    ):
        target = self.destination()
        for addresses in (
            ("127.0.0.1",),
            ("10.0.0.1",),
            ("169.254.169.254",),
            ("::1",),
            ("1.1.1.1", "192.168.1.1"),
        ):
            resolve.return_value = [
                (None, None, None, None, (address, 443)) for address in addresses
            ]
            with self.assertRaises(ValidationError):
                post_payload(target, self.secret, self.payload)
        pool.assert_not_called()

    @patch("aa_stratcom.sync_security.socket.getaddrinfo")
    @patch("aa_stratcom.sync_security.urllib3.HTTPSConnectionPool")
    def test_https_pins_public_ip_preserves_sni_and_never_follows_redirect(
        self, pool, resolve
    ):
        resolve.return_value = [(None, None, None, None, ("1.1.1.1", 443))]
        connection = pool.return_value.__enter__.return_value
        response = connection.urlopen.return_value
        response.status = 200
        response.read1.side_effect = [b'{"ok":true}', b""]
        post_payload(self.destination(), self.secret, self.payload)
        self.assertEqual(pool.call_args.args[0], "1.1.1.1")
        self.assertEqual(pool.call_args.kwargs["server_hostname"], HOST)
        self.assertEqual(pool.call_args.kwargs["assert_hostname"], HOST)
        self.assertFalse(connection.urlopen.call_args.kwargs["redirect"])
        response.status = 302
        with self.assertRaises(ValidationError):
            post_payload(self.destination(), self.secret, self.payload)

    def test_outbox_scope_change_retracts_opsec_and_remote_records_never_loop(self):
        target = self.destination()
        backfill(target)
        delivery = TimerSyncDelivery.objects.get(destination=target)
        self.assertEqual(delivery.payload["action"], "upsert")
        self.local.is_opsec = True
        self.local.save()
        delivery.refresh_from_db()
        self.assertEqual(delivery.payload["action"], "delete")
        self.assertIsNone(delivery.payload["timer"])
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(TimerSyncDelivery.objects.count(), 1)

    def test_outbox_all_scope_requires_authorizer_opsec_and_revoked_role_stops_it(self):
        target = self.destination(scope="all")
        changed(self.local)
        self.assertFalse(TimerSyncDelivery.objects.exists())
        grant(self.actor, "opsec_access")
        changed(self.local)
        self.assertTrue(TimerSyncDelivery.objects.exists())
        self.actor.user_permissions.remove(
            Permission.objects.get(
                content_type__app_label="aa_stratcom",
                codename="manage_timer_sharing",
            )
        )
        from aa_stratcom.sync_outbox import authorized_destination

        target.refresh_from_db()
        self.assertFalse(authorized_destination(target))

    @patch("aa_stratcom.sync_jobs.post_payload")
    def test_delivery_retry_retains_row_and_ack_cannot_erase_a_newer_event(self, send):
        from aa_stratcom.sync_jobs import _deliver

        target = self.destination()
        credential = TimerSyncCredential(destination=target)
        credential.set_secret(self.secret)
        credential.save()
        backfill(target)
        row = TimerSyncDelivery.objects.get()
        send.side_effect = RuntimeError("request might include secrets")
        _deliver(row.pk)
        row.refresh_from_db()
        self.assertEqual(row.attempts, 1)
        self.assertEqual(row.delivered_revision, 0)
        self.assertNotIn(self.secret, row.last_error)

        def newer(*args):
            changed(self.local)

        send.side_effect = newer
        _deliver(row.pk)
        row.refresh_from_db()
        self.assertEqual(row.delivered_revision, 0)
        send.side_effect = None
        _deliver(row.pk)
        row.refresh_from_db()
        self.assertEqual(row.delivered_revision, row.revision)
        self.assertEqual(row.last_error, "")

    def test_admin_key_and_sharing_permissions_are_independent(self):
        self.client.force_login(self.actor)
        keys = reverse("admin:aa_stratcom_timersynckey_changelist")
        targets = reverse("admin:aa_stratcom_timersyncdestination_changelist")
        self.assertEqual(self.client.get(keys).status_code, 403)
        self.assertEqual(self.client.get(targets).status_code, 403)
        grant(self.actor, "manage_sync_keys")
        self.assertEqual(self.client.get(keys).status_code, 200)
        self.assertEqual(self.client.get(targets).status_code, 403)
        key_page = self.client.get(
            reverse("admin:aa_stratcom_timersynckey_change", args=[self.key.pk])
        )
        self.assertNotContains(key_page, self.secret)
        other = UserWithCreateFactory(is_staff=True)
        grant(other, "manage_timer_sharing")
        self.client.force_login(other)
        self.assertEqual(self.client.get(keys).status_code, 403)
        self.assertEqual(self.client.get(targets).status_code, 200)
        credential_url = reverse("admin:aa_stratcom_timersynccredential_add")
        self.assertEqual(self.client.get(credential_url).status_code, 403)

    def test_admin_sharing_page_never_contains_saved_credential(self):
        target = self.destination()
        credential = TimerSyncCredential(destination=target)
        credential.set_secret(self.secret)
        credential.save()
        self.client.force_login(self.actor)
        page = self.client.get(
            reverse("admin:aa_stratcom_timersyncdestination_change", args=[target.pk])
        )
        self.assertNotContains(page, self.secret)
        self.assertNotContains(page, credential.ciphertext)
        self.assertTrue(TimerSyncAudit.objects.count() == 0)
