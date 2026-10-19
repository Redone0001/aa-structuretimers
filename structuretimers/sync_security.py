"""Small security boundary: scoped bearer keys, encryption and pinned HTTPS."""

import hashlib
import hmac
import ipaddress
import json
import re
import socket
import time
import uuid
from urllib.parse import urlsplit

import urllib3
from cryptography.fernet import Fernet, InvalidToken

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils.timezone import now

TOKEN = re.compile(r"stsync_([0-9a-f]{32})\.([A-Za-z0-9_-]{43})\Z")


def token_parts(raw):
    """Only the fixed token format is accepted; keys in query strings never work."""
    match = TOKEN.fullmatch(raw) if isinstance(raw, str) else None
    if not match:
        raise ValidationError("Enter a generated timer sync API key.")
    return uuid.UUID(hex=match[1])


def _cipher():
    secret = getattr(settings, "STRUCTURETIMERS_SYNC_ENCRYPTION_KEY", None)
    if not secret:
        raise ValidationError(
            "Configure STRUCTURETIMERS_SYNC_ENCRYPTION_KEY before saving outbound credentials."
        )
    try:
        return Fernet(secret.encode() if isinstance(secret, str) else secret)
    except (ValueError, TypeError) as exc:
        raise ValidationError(
            "The timer sync encryption key is not a valid Fernet key."
        ) from exc


def encrypt_secret(raw):
    """Use an installation-managed encryption key kept outside the database."""
    return _cipher().encrypt(raw.encode()).decode()


def decrypt_secret(credential):
    """Changing the destination invalidates a credential until explicitly re-entered."""
    actual = hashlib.sha256(credential.destination.endpoint.encode()).hexdigest()
    if not hmac.compare_digest(actual, credential.endpoint_digest):
        raise ValidationError(
            "Destination changed; a key manager must re-enter its credential."
        )
    try:
        return _cipher().decrypt(credential.ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValidationError(
            "Outbound credential cannot be decrypted; re-enter it."
        ) from exc


def authenticate(header):
    """Authenticate without session fallback or secret-bearing diagnostic output."""
    from .sync_models import TimerSyncKey

    if not header.startswith("Bearer "):
        return None
    raw = header[7:]
    try:
        identifier = token_parts(raw)
    except ValidationError:
        return None
    key = TimerSyncKey.objects.filter(
        pk=identifier, enabled=True, expires_at__gt=now()
    ).first()
    expected = key.digest if key else "0" * 64
    valid = hmac.compare_digest(hashlib.sha256(raw.encode()).hexdigest(), expected)
    return key if valid else None


def within_rate_limit(identifier, limit):
    """Atomic cache counters work across workers; no raw keys or IPs in cache keys."""
    bucket = int(now().timestamp()) // 60
    digest = hashlib.sha256(identifier.encode()).hexdigest()
    name = f"st-sync-rate:{bucket}:{digest}"
    if cache.add(name, 1, timeout=70):
        return True
    return cache.incr(name) <= limit


def validate_endpoint(url):
    """Deployment allowlist plus a deliberately narrow HTTPS URL grammar."""
    try:
        parsed = urlsplit(url)
        allowed = getattr(settings, "STRUCTURETIMERS_SYNC_ALLOWED_HOSTS", [])
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in allowed
            and parsed.netloc == parsed.hostname
            and not parsed.query
            and not parsed.fragment
            and parsed.path.endswith("/structuretimers/api/v1/sync/timers/")
            and re.fullmatch(r"/[A-Za-z0-9_/-]+", parsed.path)
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValidationError(
            "Use an HTTPS timer sync endpoint on STRUCTURETIMERS_SYNC_ALLOWED_HOSTS (host names only; no wildcards)."
        )
    return parsed


def post_payload(destination, secret, payload):
    """Resolve once, reject private networks, and pin the actual TLS connection.

    The original host remains the SNI/certificate identity. No redirects, proxy
    environment variables or second DNS resolution can change the recipient.
    """
    parsed = validate_endpoint(destination.endpoint)
    addresses = {
        entry[4][0]
        for entry in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    }
    if not addresses or any(
        not ipaddress.ip_address(address).is_global for address in addresses
    ):
        raise ValidationError(
            "Sync destinations must resolve only to public IP addresses."
        )
    address = sorted(addresses)[0]
    with urllib3.HTTPSConnectionPool(
        address,
        port=443,
        assert_hostname=parsed.hostname,
        server_hostname=parsed.hostname,
        cert_reqs="CERT_REQUIRED",
        timeout=urllib3.Timeout(connect=5, read=10, total=15),
        retries=False,
    ) as pool:
        response = pool.urlopen(
            "POST",
            parsed.path,
            body=json.dumps(
                payload, separators=(",", ":"), ensure_ascii=False
            ).encode(),
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
                "Accept-Encoding": "identity",
                "Host": parsed.hostname,
            },
            redirect=False,
            preload_content=False,
            retries=False,
        )
        try:
            if response.status != 200:
                raise ValidationError(f"Receiver returned HTTP {response.status}.")
            chunks, size = [], 0
            deadline = time.monotonic() + 20
            while True:
                chunk = response.read1(4097 - size, decode_content=False)
                if time.monotonic() > deadline:
                    raise ValidationError("Receiver acknowledgement timed out.")
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > 4096:
                    raise ValidationError("Receiver acknowledgement is too large.")
            data = json.loads(b"".join(chunks))
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise ValidationError("Receiver did not acknowledge the timer.")
        finally:
            response.close()
