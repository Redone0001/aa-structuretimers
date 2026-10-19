# Timerboard federation API v1

Each board can send its locally owned timers to several receiving boards. A
destination chooses **All non-OPSEC timers** or **All timers, including OPSEC**.
Sharing starts disabled. Enabling a destination queues the existing scheduled
timers and subsequently pushes creates, edits and deletions. Preliminary recon
records are not timers and are not exported.

A receiver can remain an independent peer or become a **read-only replica** of
one master instance UUID. In replica mode, only that master may submit events.
Local timer creation, editing and deletion are blocked in forms, views, model
saves, queryset updates/deletes and supported bulk writes. This includes battle
timer outcomes and creating follow-up timers. Existing local timers remain in
the database. A matching local timer is adopted by the master on a replica;
independent peers deduplicate a match without granting the sender ownership of
the local record. Replica mode also pauses outbound sharing.

Imported timers are not forwarded to other destinations. This prevents sync
loops; v1 supports direct peer links and a master with several direct replicas,
not cascaded relay chains.

## Install and configure

Upgrade both boards, then run `python manage.py migrate` and
`python manage.py collectstatic --noinput`. Restart Auth and Celery workers/beat.
The new migration is `0030_timer_federation`.

On every receiving board, add this to `local.py`, preserving any existing entries:

```python
APPS_WITH_PUBLIC_VIEWS = [
    *globals().get("APPS_WITH_PUBLIC_VIEWS", []),
    "structuretimers",
]
```

This uses Alliance Auth's supported URL-hook exemption for
`structuretimers.sync_api.timer_sync` only. The rest of the app still requires AA
login and its normal permissions. The exempt endpoint requires its own bearer
key, not an anonymous session. Without this setting, AA redirects machine
requests to browser login and replication cannot work.

On sending boards, explicitly allow the receiving host names (no wildcards,
ports, schemes, paths or IP addresses):

```python
STRUCTURETIMERS_SYNC_ALLOWED_HOSTS = ["timers.partner.example"]
```

Generate a dedicated encryption key once in the Auth virtualenv:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Keep it in the service's secret store/environment, separate from database
backups. Read it in `local.py`:

```python
import os

STRUCTURETIMERS_SYNC_ENCRYPTION_KEY = os.environ["TIMER_SYNC_ENCRYPTION_KEY"]
```

This setting is required only for boards storing outbound credentials. Keep it
stable; changing or losing it requires re-entering all outbound API keys. Do not
put the encryption key in source control. Back it up securely if credentials
must remain recoverable after a server restore.

Use a real HTTPS deployment with a valid public certificate. The receiving view
requires `request.is_secure()`. If TLS terminates at a trusted reverse proxy,
configure Django's secure-proxy header according to that deployment; the proxy
must strip client-supplied forwarding headers and set the trusted value itself.
Do not trust arbitrary client `X-Forwarded-Proto` values. Ensure the proxy forwards
`Authorization` and does not log it. Set a small request-body limit at the proxy
as well; the application accepts at most 32 KiB per event.

Add a Celery Beat task on sending boards. Timer changes also trigger delivery
after transaction commit; this task recovers failed deliveries and broker or
worker outages:

```python
CELERYBEAT_SCHEDULE["structuretimers_sync_delivery"] = {
    "task": "structuretimers.sync_jobs.deliver_timer_sync",
    "schedule": crontab(minute="*"),
}
```

## Roles and initial connection

Both admin roles require normal Django admin staff access. Assign them through
AA groups independently; neither permission automatically grants the other:

| Permission | Allows |
| --- | --- |
| `structuretimers.manage_sync_keys` | Generate/revoke/rotate receiving keys, set encrypted outbound credentials |
| `structuretimers.manage_timer_sharing` | Configure destinations and sharing scope; configure replica mode and master UUID |

Sharing OPSEC timers additionally requires `structuretimers.opsec_access` on the
user authorizing that destination. The delivery worker checks that the user
remains active and retains these permissions before every attempt. A receiver
must separately opt a receiving key into OPSEC imports. Superusers retain their
normal Django authority over both roles.

1. A sharing administrator saves **Timer sync configuration** on each board.
   Note each instance UUID. Key administrators can also see their board's UUID
   on the receiving-key form. Preserve the UUID through upgrades; do not clone a
   configured production database into a second active sending board without
   establishing a separate instance identity.
2. On the receiver, a key administrator adds a **Timer sync key**, entering the
   sender's instance UUID. Choose OPSEC permission and expiry. Copy the secret
   from the one-time creation response. Only its digest remains in the database.
3. On the sender, a sharing administrator creates a disabled **Timer sync
   destination**, with the full endpoint, for example
   `https://timers.partner.example/structuretimers/api/v1/sync/timers/`.
4. On the sender, a key administrator creates its **Timer sync credential** and
   pastes the receiver's secret. The credential is write-only and encrypted at
   rest. This does not enable sharing.
5. The sharing administrator chooses the scope and enables the destination.
   Existing timers are queued in the background, followed by new changes.
6. For a replica, its sharing administrator enables **Replica** and enters the
   master's instance UUID. Install a receiving key bound to that UUID first.
   Other incoming sources are rejected even if they have a valid key.

Keys default to a 90-day expiry. A key administrator can revoke or rotate them at
any time; rotation immediately invalidates the old token, which must then be
replaced on the sender. Changing a key's source UUID also rotates the secret.
Expiration is not extended automatically by rotation.

Changing a destination URL invalidates its credential binding. A key
administrator must verify the new endpoint and re-enter the credential. This
prevents a sharing administrator from redirecting a saved key to another server.
The independent deployment host allowlist must also approve the new host.

## Identities, deduplication and ordering

An event carries the sending instance UUID, an origin timer UUID and a positive
monotonic revision. Database uniqueness and receiver row locks make retries
idempotent. Older revisions cannot overwrite newer state. A repeated revision
with different content returns a conflict. Deletion retains the origin identity
and revision, so a delayed older event cannot resurrect the timer.

For an initial event with no known identity, a nonblank structure name and known
type allow a secondary match on **system, structure type, structure name,
location, timer type and exact UTC second**. System plus time alone is unsafe:
several structures can have timers simultaneously. Unnamed/unknown structures
are not merged by guesswork. Ambiguous matches return a conflict. Independent
peers do not allow later remote updates/deletions to overwrite a locally owned
matched record; resolve ownership manually or use deliberate replica mode.

Timer deletion preserves its backend structure record and sync audit. Destroyed
structure state is replicated too. The API transfers scheduling, system/type,
structure name/location, owner name, objective, notes, visibility and OPSEC state.
It does not transfer AA users, characters, API tokens, fleets, operations, fittings,
image URLs, battle pause/repair observations or reinforcement-history entries.
Local standings may still determine the displayed objective through the app's
normal owner-standing behavior.

Corporation/alliance restrictions remain tied to their EVE organization IDs.
For a restricted timer the corresponding organization must already exist in the
receiver's Auth database; otherwise the event fails closed rather than becoming
public. A remote creator is never mapped to a local user or granted local owner
privileges. Missing public EVE system/type records are resolved through the
existing eveuniverse ESI client. Imported data remains subject to local AA view
permissions.

## API contract

`POST /structuretimers/api/v1/sync/timers/`

Headers:

```http
Authorization: Bearer <receiving-board-generated-key>
Content-Type: application/json
```

One event per request; use UTF-8 JSON. Example (UUIDs are illustrative):

```json
{
  "schema": 1,
  "source": "7ae47bf3-0f77-43ae-8a3b-9f874683f03c",
  "timer_id": "cc8e4087-3569-4992-97fc-ae3a376c0047",
  "revision": 1,
  "action": "upsert",
  "timer": {
    "system_id": 30000142,
    "structure_type_id": 35832,
    "structure_ref": "record:42",
    "structure_name": "Example Astrahus",
    "location_details": "Moon 1",
    "owner_name": "Example Corporation",
    "objective": "HO",
    "visibility": "UN",
    "corporation_id": null,
    "alliance_id": null,
    "date": "2026-11-01T19:00:00+00:00",
    "timer_type": "FI",
    "is_opsec": false,
    "details_notes": "",
    "destroyed_at": null
  }
}
```

Deletion uses the same source and timer identity, a newer revision,
`"action": "delete"`, and `"timer": null`. A higher-revision upsert can restore a
previously retracted timer (for example after a sharing classification change).
Missing a timer from a snapshot never deletes it; only an explicit deletion
event does.

Successful events return HTTP 200 with `ok`, `result`, `instance` and `revision`.
Results include `updated`, `deduplicated`, `unchanged`, `stale` and `deleted`.
Errors: 400 malformed/oversized JSON or non-HTTPS, 401 invalid/revoked/expired
key, 405 wrong method, 409 identity/content conflict, 415 wrong content type,
422 invalid data/unauthorized origin or OPSEC scope, 429 rate limit, 503 temporary
dependency failure. API keys in query strings and AA session cookies never
authorize this endpoint. It exposes no read/list API.

## Security and operations

The threat model trusts a configured sender to create/update its own imported
records. A stolen valid key can impersonate that sender until revoked or
expired; keep the scope narrow, rotate promptly after exposure and protect both
servers. Each receiving key is bound to one source UUID and cannot mutate
another origin's records. API-key administration does not grant export
permission; sharing administration does not reveal saved keys.

Receiving tokens contain 256 bits of cryptographic randomness. Only SHA-256
digests are stored and comparisons are constant-time. Outbound secrets use
Fernet authenticated encryption with a separate deployment-managed key. Secrets
are never put in URLs, Celery task arguments, sync payloads, audit rows or error
messages. Receiving secrets appear only in the creation/rotation response with
no-store headers; outbound fields never redisplay the saved secret.

Outbound requests require an explicit exact-host allowlist, public DNS addresses
only, valid HTTPS certificates, the original SNI/certificate hostname and a
connection pinned to the validated IP. Private, loopback, link-local and mixed
public/private resolutions are rejected. Redirects, credentials in URLs, custom
ports, query strings and proxy environment variables are not used. This first
version intentionally does not support private-network-only recipients.

Ingress limits are 300 requests/minute per source IP and 120/minute per receiving
key, using atomic shared-cache counters. A cache/authentication outage fails
closed. Deploy a shared cache such as the existing Redis cache for multi-worker
Auth installations. Add reverse-proxy connection/body/rate limits as well; the
application counter does not replace network-level denial-of-service controls.

The database outbox keeps the latest revision per destination/identity, retries
with exponential backoff up to one hour and claims rows with expiring leases.
Workers send up to 100 pending rows per drain. The periodic task recovers
unprocessed rows after failures. A successful acknowledgement of an older event
cannot mark a newer queued revision delivered. See **Timer sync deliveries** for
status, attempts and safe failure messages; **Timer sync audits** records source
events and administrative changes without credentials. Disabling a destination
stops future attempts but cannot retract information already delivered. Key
revocation cannot retract information a partner already received either.

These choices follow the [OWASP REST security guidance](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html)
and [OWASP SSRF prevention guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).
They are implementation controls and a documented trust boundary, not a claim of
an independent penetration test or security certification.
