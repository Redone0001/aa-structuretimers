"""Version-one payload schema and transactional, idempotent receiving."""

# JSON booleans must not pass integer validation through Python's bool subclass.
# pylint: disable=unidiomatic-typecheck

import hashlib
import json
import uuid
from datetime import timezone

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware, now
from eveuniverse.models import EveSolarSystem, EveType

from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo

from .models import Structure, Timer
from .sync_models import (
    TimerSyncAudit,
    TimerSyncConfig,
    TimerSyncIdentity,
    TimerSyncStructure,
)
from .sync_policy import receive_write


class SyncConflict(Exception):
    """An origin tried to overwrite a different event or locally owned record."""


class SyncTimerForm(forms.Form):
    """A whitelist, not arbitrary model deserialization or remote ORM commands."""

    system_id = forms.IntegerField(min_value=30000000, max_value=32999999)
    structure_type_id = forms.IntegerField(
        min_value=1, max_value=2147483647, required=False
    )
    structure_ref = forms.RegexField(r"^[A-Za-z0-9:_-]{1,80}$")
    structure_name = forms.CharField(max_length=254, required=False)
    location_details = forms.CharField(max_length=254, required=False)
    owner_name = forms.CharField(max_length=254, required=False)
    objective = forms.ChoiceField(choices=Timer.Objective.choices)
    visibility = forms.ChoiceField(choices=Timer.Visibility.choices)
    corporation_id = forms.IntegerField(min_value=1, required=False)
    alliance_id = forms.IntegerField(min_value=1, required=False)
    date = forms.CharField(max_length=40)
    timer_type = forms.ChoiceField(choices=Timer.Type.choices_for_notification_rules())
    details_notes = forms.CharField(max_length=5000, required=False)
    destroyed_at = forms.CharField(max_length=40, required=False)

    def clean_date(self):
        """Normalize aware timestamps without accepting impossible dates."""
        try:
            date = parse_datetime(self.cleaned_data["date"])
        except ValueError as exc:
            raise forms.ValidationError("Enter a valid timer date.") from exc
        if date is None or not is_aware(date):
            raise forms.ValidationError("An ISO-8601 date with a timezone is required.")
        return date.astimezone(timezone.utc).replace(microsecond=0)

    def clean_destroyed_at(self):
        """Archive timestamps are optional, but must include a timezone when set."""
        value = self.cleaned_data["destroyed_at"]
        if not value:
            return None
        try:
            value = parse_datetime(value)
        except ValueError as exc:
            raise forms.ValidationError("Invalid destruction timestamp.") from exc
        if value is None or not is_aware(value):
            raise forms.ValidationError("Destruction timestamps need a timezone.")
        return value.astimezone(timezone.utc).replace(microsecond=0)


def payload_digest(payload):
    """Canonical content digest also detects conflicting replays of a revision."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def serialize_timer(timer):
    """No character/user identities, fittings, images, fleet intel or API credentials."""
    reference = (
        f"record:{timer.structure_id}"
        if timer.structure_id
        else f"external:{timer.structures_structure_id or timer.pk}"
    )
    return {
        "system_id": timer.eve_solar_system_id,
        "structure_type_id": timer.structure_type_id,
        "structure_ref": reference,
        "structure_name": timer.structure_name,
        "location_details": timer.location_details,
        "owner_name": timer.owner_name or "",
        "objective": timer.objective,
        "visibility": timer.visibility,
        "corporation_id": timer.eve_corporation_id,
        "alliance_id": timer.eve_alliance_id,
        "date": timer.date.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
        "timer_type": timer.timer_type,
        "is_opsec": timer.is_opsec,
        "details_notes": timer.details_notes,
        "destroyed_at": (
            timer.structure.destroyed_at.replace(microsecond=0).isoformat()
            if timer.structure_id and timer.structure.destroyed_at
            else None
        ),
    }


def validate_payload(payload, key):
    """Reject unknown schema, fields, origins and ambiguous types before writing."""
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "source",
        "timer_id",
        "revision",
        "action",
        "timer",
    }:
        raise ValidationError("Invalid sync envelope.")
    if type(payload["schema"]) is not int or payload["schema"] != 1:
        raise ValidationError("Unsupported sync schema.")
    try:
        source = uuid.UUID(payload["source"])
        identifier = uuid.UUID(payload["timer_id"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Invalid origin identity.") from exc
    if source != key.source_instance:
        raise ValidationError("This key is not authorized for that source instance.")
    revision = payload["revision"]
    if type(revision) is not int or not 1 <= revision <= 9223372036854775807:
        raise ValidationError("Invalid timer revision.")
    action = payload["action"]
    if action not in ("upsert", "delete"):
        raise ValidationError("Invalid sync action.")
    data = payload["timer"]
    if action == "delete":
        if data is not None:
            raise ValidationError("Deletion events cannot contain timer data.")
        return source, identifier, None
    return source, identifier, _timer_values(data, key)


def _timer_values(data, key):
    """Validate the typed timer data independently of the event envelope."""
    allowed = set(SyncTimerForm.base_fields) | {"is_opsec"}
    if (
        not isinstance(data, dict)
        or set(data) != allowed
        or type(data.get("is_opsec")) is not bool
    ):
        raise ValidationError("Invalid timer fields.")
    if data["is_opsec"] and not key.allow_opsec:
        raise ValidationError("This receiving key does not allow OPSEC timers.")
    # Django form coercion must not turn nested values or booleans into data.
    integer_fields = ("system_id", "structure_type_id", "corporation_id", "alliance_id")
    if any(
        value is not None and type(value) is not int
        for name, value in data.items()
        if name in integer_fields
    ):
        raise ValidationError("Invalid EVE identifiers.")
    if any(
        not isinstance(value, str)
        for name, value in data.items()
        if name not in (*integer_fields, "is_opsec")
        and not (name == "destroyed_at" and value is None)
    ):
        raise ValidationError("Timer text fields must be strings.")
    form = SyncTimerForm(data)
    if not form.is_valid():
        raise ValidationError("Invalid timer values: " + ", ".join(form.errors))
    return {**form.cleaned_data, "is_opsec": data["is_opsec"]}


def _relationships(data):
    """Organization restrictions must refer to existing local EVE organizations."""
    corporation = EveCorporationInfo.objects.filter(pk=data["corporation_id"]).first()
    alliance = EveAllianceInfo.objects.filter(pk=data["alliance_id"]).first()
    if (
        data["visibility"] == "CO"
        and not corporation
        or data["visibility"] == "AL"
        and not alliance
    ):
        raise ValidationError(
            "Import the restricted timer's EVE corporation/alliance on the receiver first."
        )
    return corporation, alliance


def _matching_timer(data):
    """Never merge unrelated structures just because their timers coincide."""
    if not data["structure_name"] or not data["structure_type_id"]:
        return None
    matches = list(
        Timer.objects.select_for_update().filter(
            eve_solar_system_id=data["system_id"],
            date=data["date"],
            structure_type_id=data["structure_type_id"],
            structure_name__iexact=data["structure_name"],
            location_details__iexact=data["location_details"],
            timer_type=data["timer_type"],
        )[:2]
    )
    if len(matches) > 1:
        raise SyncConflict(
            "Several local timers match. Resolve the duplicate records before retrying."
        )
    if matches:
        timer = matches[0]
        if timer.is_opsec != data["is_opsec"] or timer.visibility != data["visibility"]:
            raise SyncConflict("A matching timer has different access restrictions.")
        return timer
    return None


def _upsert(identity, data, adopt=False):
    """Only origin-owned timers may be replaced; a local match is an alias."""
    if not identity.timer_id:
        match = _matching_timer(data)
        if match:
            if (
                match.sync_identities.filter(source_instance=identity.source_instance)
                .exclude(pk=identity.pk)
                .exists()
            ):
                raise SyncConflict(
                    "This source already has another identity for that timer."
                )
            identity.timer = match
            identity.owns_timer = adopt
            if not adopt:
                return "deduplicated"
            if (
                match.sync_identities.filter(imported=True, owns_timer=True)
                .exclude(pk=identity.pk)
                .exists()
            ):
                raise SyncConflict("Another source already owns this matching timer.")
        else:
            identity.owns_timer = True
    if adopt and not identity.owns_timer:
        if (
            identity.timer.sync_identities.filter(imported=True, owns_timer=True)
            .exclude(pk=identity.pk)
            .exists()
        ):
            raise SyncConflict("Another source already owns this matching timer.")
        identity.owns_timer = True
    if not identity.owns_timer:
        current = serialize_timer(identity.timer)
        comparison = {
            name: value for name, value in current.items() if name != "structure_ref"
        }
        incoming = {
            name: value for name, value in data.items() if name != "structure_ref"
        }
        incoming["date"] = data["date"].isoformat()
        incoming["destroyed_at"] = (
            data["destroyed_at"].isoformat() if data["destroyed_at"] else None
        )
        if comparison != incoming:
            raise SyncConflict(
                "A local or different-origin timer owns this matching record; it was not overwritten."
            )
        return "deduplicated"
    # Missing public EVE data is resolved only through eveuniverse's ESI client.
    system, _ = EveSolarSystem.objects.get_or_create_esi(id=data["system_id"])
    structure_type = None
    if data["structure_type_id"]:
        structure_type, _ = EveType.objects.get_or_create_esi(
            id=data["structure_type_id"]
        )
    corporation, alliance = _relationships(data)
    mapping, _ = TimerSyncStructure.objects.get_or_create(
        source_instance=identity.source_instance, reference=data["structure_ref"]
    )
    structure = mapping.structure or Structure()
    for name in (
        "structure_name",
        "location_details",
        "owner_name",
        "objective",
        "visibility",
        "is_opsec",
    ):
        setattr(structure, name, data[name])
    structure.eve_solar_system = system
    structure.structure_type = structure_type
    structure.eve_corporation = corporation
    structure.eve_alliance = alliance
    structure.destroyed_at = data["destroyed_at"]
    structure.destroyed_by = None
    structure.destroyed_by_timer = None
    structure.user = (
        None  # Remote creators must not acquire local ownership privileges.
    )
    structure.save()
    mapping.structure = structure
    mapping.save(update_fields=["structure"])
    return _save_timer(identity, data, structure)


def _save_timer(identity, data, structure):
    """Apply scheduling and privacy explicitly; creator privileges never transfer."""
    timer = identity.timer or Timer()
    timer.structure = structure
    timer.date = data["date"]
    timer.timer_type = data["timer_type"]
    timer.details_notes = data["details_notes"]
    timer.visibility = data["visibility"]
    timer.is_opsec = data["is_opsec"]
    timer.eve_corporation = structure.eve_corporation
    timer.eve_alliance = structure.eve_alliance
    timer.user = None
    timer.save()
    identity.timer = timer
    return "updated"


@transaction.atomic
def receive(payload, key):
    """Serialize origin events and dedup checks using the singleton row lock."""
    config = TimerSyncConfig.current()
    config = TimerSyncConfig.objects.select_for_update().get(pk=config.pk)
    # Check revocation/expiry again after acquiring the write lock.
    key = type(key).objects.select_for_update().get(pk=key.pk)
    if not key.enabled or key.expires_at <= now():
        raise ValidationError("Receiving key is no longer active.")
    source, identifier, data = validate_payload(payload, key)
    if (
        source == config.instance_id
        or config.replica
        and source != config.master_source
    ):
        raise ValidationError("This source is not authorized to write to this board.")
    identity, _ = TimerSyncIdentity.objects.get_or_create(
        source_instance=source, timer_uuid=identifier, defaults={"imported": True}
    )
    if not identity.imported:
        raise SyncConflict("Incoming events cannot overwrite a local origin identity.")
    digest = payload_digest(payload)
    revision = payload["revision"]
    if revision < identity.revision:
        result = "stale"
    elif revision == identity.revision:
        if digest != identity.payload_digest:
            raise SyncConflict(
                "The same revision was submitted with different content."
            )
        result = "unchanged"
    else:
        with receive_write():
            if data is None:
                if identity.timer_id and identity.owns_timer:
                    identity.timer.delete()
                identity.timer = None
                result = "deleted"
            else:
                result = _upsert(identity, data, adopt=config.replica)
        identity.revision = revision
        identity.payload_digest = digest
        identity.deleted = data is None
        identity.save()
    type(key).objects.filter(pk=key.pk).update(last_used_at=now())
    TimerSyncAudit.objects.create(
        source_instance=source,
        timer_uuid=identifier,
        key=key,
        action=payload["action"],
        result=result,
    )
    return {
        "ok": True,
        "result": result,
        "instance": str(config.instance_id),
        "revision": identity.revision,
    }
