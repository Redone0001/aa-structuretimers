"""Configuration, credentials and durable delivery state for timer federation."""

import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.timezone import now


def key_expiry():
    """Receiving keys expire after 90 days unless explicitly configured otherwise."""
    return now() + timedelta(days=90)


class TimerSyncKey(models.Model):
    """Receiving credential: only a random token's digest is persisted."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    source_instance = models.UUIDField(
        help_text="Instance UUID shown by the sending board."
    )
    digest = models.CharField(max_length=64, editable=False)
    enabled = models.BooleanField(default=True)
    allow_opsec = models.BooleanField(default=False)
    expires_at = models.DateTimeField(default=key_expiry)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, editable=False)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return self.name

    def generate(self):
        """Issue a 256-bit secret; return it once, never persist the plaintext."""
        raw = f"stsync_{self.pk.hex}.{secrets.token_urlsafe(32)}"
        self.digest = hashlib.sha256(raw.encode()).hexdigest()
        return raw


class TimerSyncConfig(models.Model):
    """Singleton identity and incoming master selection."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    instance_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    replica = models.BooleanField(default=False)
    master_source = models.UUIDField(
        null=True,
        blank=True,
        help_text="Only this instance can write when replica mode is enabled.",
    )

    class Meta:
        default_permissions = ()
        verbose_name = "Timer sync configuration"

    def __str__(self):
        return "Timer sync configuration"

    def clean(self):
        super().clean()
        if self.replica and (
            not self.master_source or self.master_source == self.instance_id
        ):
            raise ValidationError(
                "Replica mode requires a different master instance UUID."
            )

    def save(self, *args, **kwargs):
        self.pk = 1
        self.full_clean()
        return super().save(*args, **kwargs)

    @classmethod
    def current(cls):
        """Persist a stable identity before sharing or receiving any timers."""
        return cls.objects.get_or_create(pk=1)[0]


class TimerSyncDestination(models.Model):
    """Sharing administrators choose recipients and scope, never see their keys."""

    class Scope(models.TextChoices):
        """Scope is explicit and checked again when each event is delivered."""

        NON_OPSEC = "non_opsec", "All non-OPSEC timers"
        ALL = "all", "All timers, including OPSEC"

    name = models.CharField(max_length=100)
    endpoint = models.URLField(
        max_length=500, help_text="Full HTTPS API endpoint on the receiving board."
    )
    enabled = models.BooleanField(default=False)
    scope = models.CharField(
        max_length=16, choices=Scope.choices, default=Scope.NON_OPSEC
    )
    authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False
    )
    updated_at = models.DateTimeField(auto_now=True)

    needs_backfill = models.BooleanField(default=True, editable=False)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return self.name

    def clean(self):
        from .sync_security import validate_endpoint

        super().clean()
        validate_endpoint(self.endpoint)


class TimerSyncCredential(models.Model):
    """Only key administrators may set this separate, write-only credential."""

    destination = models.OneToOneField(TimerSyncDestination, on_delete=models.CASCADE)
    ciphertext = models.TextField(editable=False)
    endpoint_digest = models.CharField(max_length=64, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"Credential for {self.destination}"

    def set_secret(self, raw):
        """Encrypt a replacement secret and bind it to this exact endpoint."""
        from .sync_security import encrypt_secret, token_parts

        token_parts(raw)
        self.ciphertext = encrypt_secret(raw)
        self.endpoint_digest = hashlib.sha256(
            self.destination.endpoint.encode()
        ).hexdigest()


class TimerSyncIdentity(models.Model):
    """Keep origin identities and revisions even after a timer is deleted."""

    source_instance = models.UUIDField()
    timer_uuid = models.UUIDField(default=uuid.uuid4)
    timer = models.ForeignKey(
        "structuretimers.Timer",
        on_delete=models.SET_NULL,
        null=True,
        related_name="sync_identities",
    )
    imported = models.BooleanField(default=False)
    owns_timer = models.BooleanField(default=True)
    revision = models.PositiveBigIntegerField(default=0)
    payload_digest = models.CharField(max_length=64, blank=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=("source_instance", "timer_uuid"),
                name="st_sync_origin_timer_unique",
            ),
            models.UniqueConstraint(
                fields=("source_instance", "timer"), name="st_sync_source_local_unique"
            ),
        ]


class TimerSyncStructure(models.Model):
    """Remote structure references never collide with local database IDs."""

    source_instance = models.UUIDField()
    reference = models.CharField(max_length=80)
    structure = models.ForeignKey(
        "structuretimers.Structure", on_delete=models.SET_NULL, null=True
    )

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=("source_instance", "reference"), name="st_sync_structure_unique"
            )
        ]


class TimerSyncDelivery(models.Model):
    """Durable latest-state outbox; broker messages contain only row IDs."""

    destination = models.ForeignKey(TimerSyncDestination, on_delete=models.CASCADE)
    identity = models.ForeignKey(TimerSyncIdentity, on_delete=models.CASCADE)
    payload = models.JSONField()
    revision = models.PositiveBigIntegerField()
    delivered_revision = models.PositiveBigIntegerField(default=0)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt = models.DateTimeField(default=now)
    locked_until = models.DateTimeField(default=now)
    last_error = models.CharField(max_length=200, blank=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=("destination", "identity"), name="st_sync_delivery_unique"
            )
        ]


class TimerSyncAudit(models.Model):
    """Credential-free operational audit, retained independently of timers."""

    at = models.DateTimeField(auto_now_add=True)
    source_instance = models.UUIDField(null=True)
    timer_uuid = models.UUIDField(null=True)
    action = models.CharField(max_length=32)
    result = models.CharField(max_length=32)
    key = models.ForeignKey(TimerSyncKey, on_delete=models.SET_NULL, null=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    destination = models.ForeignKey(
        TimerSyncDestination, on_delete=models.SET_NULL, null=True
    )

    class Meta:
        default_permissions = ()
