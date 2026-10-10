"""Forms."""

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Dict

import puremagic
import requests

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from eveuniverse.models import EveSolarSystem, EveType

from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo
from allianceauth.services.hooks import get_extension_logger

from .constants import EveGroupId, EveTypeId
from . import owners, structures_bridge
from .models import Structure, Timer

logger = get_extension_logger(__name__)

DATETIME_FORMAT = "%Y-%m-%d %H:%M"

EVE_TIMER_DATE_FORMAT = "%Y.%m.%d %H:%M:%S"
EVE_TIMER_UNTIL_PATTERN = re.compile(
    r"^(?:Reinforced|Anchoring) until\s+"
    r"(?P<date>\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2})\s*$",
    re.IGNORECASE,
)
EVE_SKYHOOK_HEADER_PATTERN = re.compile(
    r"^Orbital Skyhook\s+\((?P<solar_system>.+)\s+"
    r"(?P<location>[^\s]+)\)\s+\[(?P<owner>.+)\]$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedEveTimer:
    """Data extracted from an EVE Online structure timer copy/paste."""

    solar_system_name: str
    structure_name: str
    date: dt.datetime
    location_details: str = ""
    owner_name: str = ""
    structure_type_name: str = ""


def parse_eve_timer_text(value: str) -> ParsedEveTimer:
    """Parse text copied from a supported EVE Online structure timer tooltip."""

    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError(
            _(
                "Paste the EVE timer with 'System - Structure name' on the first "
                "line (or use the 'Orbital Skyhook (System Planet) [Owner]' "
                "format) and include a supported timer date below it."
            )
        )

    skyhook_match = EVE_SKYHOOK_HEADER_PATTERN.match(lines[0])
    if skyhook_match:
        solar_system_name = skyhook_match.group("solar_system").strip()
        location_details = skyhook_match.group("location").strip()
        structure_name = location_details
        owner_name = skyhook_match.group("owner").strip()
        structure_type_name = "Orbital Skyhook"
    elif " - " in lines[0]:
        solar_system_name, structure_name = (
            part.strip() for part in lines[0].split(" - ", maxsplit=1)
        )
        location_details = ""
        owner_name = ""
        structure_type_name = ""
    else:
        raise ValueError(
            _(
                "Paste the EVE timer with 'System - Structure name' on the first "
                "line, or use the Orbital Skyhook format."
            )
        )

    if not solar_system_name or not structure_name:
        raise ValueError(_("The solar system and structure name cannot be empty."))
    if skyhook_match and (not location_details or not owner_name):
        raise ValueError(_("The Orbital Skyhook location and owner must not be empty."))

    until_match = next(
        (match for line in lines[1:] if (match := EVE_TIMER_UNTIL_PATTERN.match(line))),
        None,
    )
    if not until_match:
        raise ValueError(
            _(
                "Could not find a supported 'Reinforced until' or 'Anchoring "
                "until' timer in the text."
            )
        )

    try:
        timer_date = dt.datetime.strptime(
            until_match.group("date"), EVE_TIMER_DATE_FORMAT
        ).replace(tzinfo=dt.timezone.utc)
    except ValueError as ex:
        raise ValueError(_("The timer date or time is invalid.")) from ex

    return ParsedEveTimer(
        solar_system_name=solar_system_name,
        structure_name=structure_name,
        date=timer_date,
        location_details=location_details,
        owner_name=owner_name,
        structure_type_name=structure_type_name,
    )


class AssigneeChoiceField(forms.ModelChoiceField):
    """Identify users by their main character rather than their login name."""

    def label_from_instance(self, obj):
        return obj.profile.main_character.character_name


class TimerForm(forms.ModelForm):
    """Form for timers."""

    ASTERISK_HTML = mark_safe('<i class="fas fa-asterisk"></i>')
    database_entry_2 = forms.CharField(
        required=False,
        label="Existing structure",
        help_text=(
            "Optional. Pick a structure from the Database to link this timer to it "
            "and fill in its details. Leave empty to match on solar system, type "
            "and name, or add a new Database record."
        ),
        widget=forms.Select(attrs={"class": "select2-database-entries"}),
    )
    paste_info = forms.CharField(
        required=False,
        label="Paste new info",
        help_text=(
            "Paste a timer or a fitting from EVE and press Parse: everything it "
            "can read is filled into the boxes below."
        ),
        widget=forms.Textarea(attrs={"rows": 3, "class": "st-paste-info"}),
    )
    eve_solar_system_2 = forms.CharField(
        required=True,
        label=format_html("{} {}", _("Solar System"), ASTERISK_HTML),
        widget=forms.Select(attrs={"class": "select2-solar-systems"}),
    )
    structure_type_2 = forms.CharField(
        required=True,
        label=format_html("{} {}", _("Structure Type"), ASTERISK_HTML),
        widget=forms.Select(attrs={"class": "select2-structure-types"}),
    )
    owner_2 = forms.CharField(
        required=True,
        label=format_html("{} {}", _("Owner"), ASTERISK_HTML),
        help_text=(
            "Player corporation that owns the structure. Its alliance is filled "
            "in automatically."
        ),
        widget=forms.Select(attrs={"class": "select2-owners"}),
    )
    fitting = forms.CharField(
        required=False,
        label="Fitting",
        help_text="Paste the structure's fitting, e.g. copied from EVE.",
        widget=forms.Textarea(attrs={"rows": 6, "class": "font-monospace"}),
    )
    assigned_to = AssigneeChoiceField(
        queryset=get_user_model().objects.filter(
            is_active=True, profile__main_character__isnull=False
        ).select_related("profile__main_character").order_by(
            "profile__main_character__character_name", "pk"
        ),
        required=False,
        label=_("Assigned to"),
        empty_label=_("Unassigned"),
        widget=forms.Select(attrs={"class": "select2-render"}),
    )
    timer_type = forms.ChoiceField(
        required=False,
        label=_("Timer Type"),
        choices=[("", "Not reinforced")] + Timer.Type.choices,
        help_text="Leave empty and leave the date empty if it is not reinforced.",
        widget=forms.Select(attrs={"class": "select2-render"}),
    )
    visibility = forms.ChoiceField(
        choices=Timer.Visibility.choices,
        widget=forms.Select(attrs={"class": "select2-render"}),
    )
    date = forms.DateTimeField(
        required=False,
        label=_("Date"),
        widget=forms.DateTimeInput(
            attrs={"id": "timer-date-field"}, format=DATETIME_FORMAT
        ),
        # input_formats=[DATETIME_FORMAT],
        help_text="When the reinforcement timer ends, in EVE time (UTC).",
    )
    reinforcement_time = forms.TimeField(
        required=False,
        label=_("Vulnerability windows"),
        input_formats=["%H:%M"],
        widget=forms.TimeInput(
            format="%H:%M",
            attrs={
                "placeholder": "HH:MM",
                "pattern": "(?:[01][0-9]|2[0-3]):[0-5][0-9]",
            },
        ),
        help_text=_("24-hour EVE time (UTC), HH:MM."),
    )
    details_image_url = forms.URLField(
        required=False,
        help_text=_("Paste a public URL to an image into this field."),
    )

    class Meta:
        model = Timer
        fields = (
            "database_entry_2",
            "paste_info",
            "structure_name",
            "eve_solar_system_2",
            "structure_type_2",
            "owner_2",
            "date",
            "timer_type",
            "reinforcement_time",
            "location_details",
            "fitting",
            "assigned_to",
            "details_image_url",
            "details_notes",
            "visibility",
            "is_opsec",
            "is_important",
            "discord_timerboard",
        )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        if "instance" in kwargs and kwargs["instance"] is not None:
            my_instance = kwargs["instance"]
            self.is_new = False
        else:
            my_instance = None
            self.is_new = True

        super().__init__(*args, **kwargs)

        self.fields["structure_name"].required = True
        self.fields["structure_name"].label = format_html(
            "{} {}", _("Structure name"), self.ASTERISK_HTML
        )
        self.fields["timer_type"].widget.choices = [
            choice
            for choice in self.fields["timer_type"].choices
            if choice[0] != Timer.Type.PRELIMINARY
        ]
        if my_instance and my_instance.owner_corporation_id:
            self.fields["owner_2"].widget.choices = [
                (
                    str(my_instance.owner_corporation_id),
                    my_instance.owner_corporation.display_name,
                )
            ]
        elif my_instance and my_instance.owner_name:
            self.fields["owner_2"].widget.choices = [
                (
                    owners.LOOKUP_PREFIX + my_instance.owner_name,
                    f'Look up corporation "{my_instance.owner_name}" on ESI',
                )
            ]
            self.initial.setdefault(
                "owner_2", owners.LOOKUP_PREFIX + my_instance.owner_name
            )
        if my_instance and my_instance.owner_corporation_id:
            self.initial.setdefault("owner_2", str(my_instance.owner_corporation_id))
        if isinstance(my_instance, Timer) and my_instance.structure_id:
            self.initial.setdefault("fitting", my_instance.structure.fitting)

        if (
            not self.user
            or not self.user.has_perm("structuretimers.recon_member")
            or isinstance(my_instance, Structure)
        ):
            self.fields.pop("database_entry_2", None)
        elif my_instance and my_instance.structure_id:
            self.fields["database_entry_2"].widget.choices = [
                (
                    str(my_instance.structure_id),
                    my_instance.structure.structure_display_name,
                )
            ]
            self.initial.setdefault("database_entry_2", str(my_instance.structure_id))
        elif my_instance and my_instance.structures_structure_id:
            structure = structures_bridge.get_structure(
                self.user, my_instance.structures_structure_id
            )
            if structure:
                self.fields["database_entry_2"].widget.choices = [
                    (
                        structures_bridge.ROW_PREFIX + str(structure.id),
                        structures_bridge.display_name(structure) + " (Structures)",
                    )
                ]

        if my_instance:
            self.fields["eve_solar_system_2"].widget.choices = [
                (
                    str(my_instance.eve_solar_system_id),
                    my_instance.eve_solar_system.name,
                )
            ]
            if my_instance.structure_type:
                self.fields["structure_type_2"].widget.choices = [
                    (
                        str(my_instance.structure_type_id),
                        my_instance.structure_type.name,
                    )
                ]

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("eve_solar_system_2"):
            self._clean_solar_system(cleaned_data)

        if cleaned_data.get("structure_type_2"):
            self._clean_structure_type(cleaned_data)

        if cleaned_data.get("details_image_url"):
            self._clean_image(cleaned_data)

        if cleaned_data.get("database_entry_2"):
            self._clean_database_entry(cleaned_data)

        if "owner_2" in self.fields and cleaned_data.get("owner_2"):
            self._clean_owner(cleaned_data)

        # No date means "not reinforced": the entry is only a Database record.
        timer_type = cleaned_data.get("timer_type")
        if cleaned_data.get("date") is None:
            if isinstance(self.instance, Timer) and self.instance.pk:
                self.add_error("date", "A timer needs the date it ends.")
            cleaned_data["timer_type"] = Timer.Type.PRELIMINARY.value
        elif not timer_type or timer_type == Timer.Type.PRELIMINARY:
            cleaned_data["timer_type"] = Timer.Type.NONE.value

    def clean_reinforcement_time(self):
        value = self.cleaned_data.get("reinforcement_time")
        raw_value = self.data.get("reinforcement_time", "").strip()
        if raw_value and not re.fullmatch(
            r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", raw_value
        ):
            raise ValidationError(_("Enter a 24-hour time in HH:MM format."))
        return value

    def _clean_structure_type(self, cleaned_data: Dict[str, Any]):
        try:
            structure_type = EveType.objects.get(id=cleaned_data["structure_type_2"])
        except EveType.DoesNotExist as ex:
            raise ValidationError(
                {"structure_type_2": _("Invalid structure type.")}
            ) from ex

        self.fields["structure_type_2"].widget.choices = [
            (str(structure_type.id), structure_type.name)
        ]
        if (
            cleaned_data.get("timer_type") == Timer.Type.MOONMINING
            and structure_type.eve_group_id != EveGroupId.REFINERY
        ):
            raise ValidationError(
                {"timer_type": _("Moon mining timers are valid for refineries only.")}
            )
        if (
            cleaned_data.get("timer_type") == Timer.Type.THEFT
            and structure_type.eve_group_id != EveGroupId.SKYHOOK
        ):
            raise ValidationError(
                {"timer_type": _("Theft timers are valid for skyhook only.")}
            )

    def _clean_solar_system(self, cleaned_data: Dict[str, Any]):
        try:
            solar_system = EveSolarSystem.objects.get(
                id=cleaned_data["eve_solar_system_2"]
            )
        except EveSolarSystem.DoesNotExist as ex:
            raise ValidationError(
                {"eve_solar_system_2": _("Invalid solar system.")}
            ) from ex
        self.fields["eve_solar_system_2"].widget.choices = [
            (str(solar_system.id), solar_system.name)
        ]

    @staticmethod
    def _clean_image(cleaned_data: Dict[str, Any]):
        details_image_url = cleaned_data.get("details_image_url", "")
        try:
            r = requests.get(details_image_url, stream=True, timeout=(3.0, 10.0))
            r.raise_for_status()
        except requests.exceptions.RequestException as ex:
            logger.warning(
                "Failed to load image from URL: %s", details_image_url, exc_info=True
            )
            raise forms.ValidationError(
                {
                    "details_image_url": _(
                        "Failed to load image file. Please double check URL."
                    )
                },
                code="details_url_failed_to_load",
            ) from ex

        image_type = puremagic.from_string(r.content, mime=True)
        if image_type not in {"image/gif", "image/jpeg", "image/png"}:
            logger.warning(
                "%s is not a valid image type for URL: %s",
                image_type,
                details_image_url,
            )
            raise forms.ValidationError(
                {
                    "details_image_url": _(
                        _(
                            "URL does not point to a valid image file. "
                            "Valid types are: gif, jpeg, png"
                        )
                    )
                },
                code="details_url_unsupported_type",
            )

    def _clean_owner(self, cleaned_data):
        value = cleaned_data["owner_2"]
        try:
            owner = owners.clean_owner_value(value)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Could not look up owner %r on ESI", value, exc_info=True)
            self.add_error("owner_2", "Could not reach ESI to check this owner. Try again.")
            return
        if owner is None:
            name = str(value).removeprefix(owners.LOOKUP_PREFIX)
            self.add_error(
                "owner_2", f'No player corporation named "{name}" exists in EVE.'
            )
            return
        cleaned_data["owner_2"] = owner

    def _clean_database_entry(self, cleaned_data):
        value = str(cleaned_data["database_entry_2"])
        if value.startswith(structures_bridge.ROW_PREFIX):
            try:
                structure_id = int(value[len(structures_bridge.ROW_PREFIX) :])
            except ValueError:
                structure_id = None
            structure = (
                structures_bridge.get_structure(self.user, structure_id)
                if structure_id
                else None
            )
            if structure is None:
                self.add_error(
                    "database_entry_2", "This structure is not in the Database."
                )
                cleaned_data.pop("database_entry_2", None)
                return
            cleaned_data["database_entry_2"] = structure
            return
        try:
            entry_pk = int(value)
        except (TypeError, ValueError):
            entry_pk = None
        entry = (
            Structure.objects.visible_to_user(self.user).filter(pk=entry_pk).first()
            if entry_pk
            else None
        )
        if entry is None:
            self.add_error("database_entry_2", "This structure is not in the Database.")
            cleaned_data.pop("database_entry_2", None)
            return
        cleaned_data["database_entry_2"] = entry

    def _creator(self) -> dict:
        """Character, corporation and alliance on whose behalf this is saved."""
        character = self.user.profile.main_character
        try:
            alliance = character.alliance
        except EveAllianceInfo.DoesNotExist:
            alliance = EveAllianceInfo.objects.create_alliance(character.alliance_id)
        try:
            corporation = character.corporation
        except EveCorporationInfo.DoesNotExist:
            corporation = EveCorporationInfo.objects.create_corporation(
                character.corporation_id
            )
        logger.debug(
            "Determined save request is on behalf of character %s corporation %s",
            character,
            corporation,
        )
        return {
            "eve_character": character,
            "eve_corporation": corporation,
            "eve_alliance": alliance,
            "user": self.user,
        }

    def _apply_structure_fields(self, structure) -> None:
        """Copy the structure part of the form onto a Database record."""
        data = self.cleaned_data
        structure.structure_name = data.get("structure_name", structure.structure_name)
        structure.eve_solar_system_id = data.get("eve_solar_system_2")
        structure.structure_type_id = data.get("structure_type_2") or None
        owner = data.get("owner_2")
        if isinstance(owner, owners.Organization):
            structure.owner_corporation = owner
            structure.owner_name = owner.name
        if "reinforcement_time" in self.fields:
            structure.reinforcement_time = data.get("reinforcement_time")
        for field in ("location_details", "fitting"):
            if field in self.fields:
                setattr(structure, field, data.get(field) or "")

    def _new_timer_for(self, structure) -> Timer:
        """A reinforcement timer for a structure, from the timer part of the form."""
        return Timer(
            structure=structure,
            date=self.cleaned_data["date"],
            timer_type=self.cleaned_data.get("timer_type") or Timer.Type.NONE,
            visibility=structure.visibility,
            is_opsec=structure.is_opsec,
            **self._creator(),
        )

    def save(self, commit=True):
        """Save the structure, and its reinforcement timer when there is a date.

        Returns the timer, or the Database record when no timer was entered.
        """
        obj = super().save(commit=False)
        if isinstance(obj, Structure):
            structure = obj
            if self.is_new:
                for key, value in self._creator().items():
                    setattr(structure, key, value)
            self._apply_structure_fields(structure)
            if not commit:
                return structure
            structure.save()
            if self.cleaned_data.get("date"):
                self._new_timer_for(structure).save()
            return structure

        timer = obj
        if self.is_new:
            for key, value in self._creator().items():
                setattr(timer, key, value)
        timer.date = self.cleaned_data.get("date")
        timer.structure_type_id = self.cleaned_data.get("structure_type_2") or None
        timer.eve_solar_system_id = self.cleaned_data.get("eve_solar_system_2")
        owner = self.cleaned_data.get("owner_2")
        if isinstance(owner, owners.Organization):
            timer.owner_corporation = owner
            timer.owner_name = owner.name

        picked = self.cleaned_data.get("database_entry_2") or None
        if "database_entry_2" in self.fields and picked is not None and not isinstance(
            picked, Structure
        ):
            # A friendly structure from aa-structures: it stays in that app.
            timer.structure = None
            timer.structures_structure_id = picked.id
            if commit and timer.date:
                timer.save()
            return timer

        structure = (
            picked
            if isinstance(picked, Structure)
            else timer.structure
            if timer.structure_id
            else Structure.objects.find_matching(
                timer.eve_solar_system_id,
                timer.structure_type_id,
                timer.structure_name,
            )
        )
        if structure is None:
            structure = Structure(
                visibility=timer.visibility,
                is_opsec=timer.is_opsec,
                **{
                    key: getattr(timer, key)
                    for key in ("eve_character", "eve_corporation", "eve_alliance", "user")
                },
            )
        self._apply_structure_fields(structure)
        if not commit:
            return timer
        structure.save()
        if timer.date is None:
            # Not reinforced: only the Database record is kept.
            return structure
        timer.structure = structure
        timer.structures_structure_id = None
        timer.save()
        return timer


class FastTimerForm(TimerForm):
    """Compact form that derives timer details from an EVE Online copy/paste."""

    pasted_timer = forms.CharField(
        label=_("EVE timer text"),
        help_text=_(
            "Copy the structure name, distance, and timer time from EVE "
            "Online, then paste them here. The time is interpreted as EVE time (UTC)."
        ),
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "autofocus": True,
                "placeholder": (
                    "SVM-3K - kongbao\n"
                    "17 km\n"
                    "Reinforced until 2026.09.05 03:47:08"
                ),
            }
        ),
    )

    fast_fields = (
        "pasted_timer",
        "assigned_to",
        "structure_type_2",
        "timer_type",
        "owner_2",
    )
    derived_fields = (
        "eve_solar_system_2",
        "structure_name",
        "date",
        "location_details",
    )

    def __init__(self, *args, **kwargs):
        args = list(args)
        data = kwargs.get("data")
        data_is_positional = data is None and bool(args)
        if data_is_positional:
            data = args[0]

        if data is not None:
            data = data.copy()
            for field_name in self.derived_fields:
                data.pop(field_name, None)
            parsed_owner_name = ""
            parsed_location_details = ""

            try:
                parsed_timer = parse_eve_timer_text(data.get("pasted_timer", ""))
            except ValueError:
                pass
            else:
                solar_system = EveSolarSystem.objects.filter(
                    name__iexact=parsed_timer.solar_system_name
                ).first()
                if solar_system:
                    data["eve_solar_system_2"] = str(solar_system.id)
                data["structure_name"] = parsed_timer.structure_name
                data["date"] = parsed_timer.date.isoformat()
                parsed_owner_name = parsed_timer.owner_name
                parsed_location_details = parsed_timer.location_details
                if parsed_timer.structure_type_name:
                    data["structure_type_2"] = str(EveTypeId.ORBITAL_SKYHOOK.value)

            if parsed_owner_name and not data.get("owner_2"):
                data["owner_2"] = owners.LOOKUP_PREFIX + parsed_owner_name
            if parsed_location_details:
                data["location_details"] = parsed_location_details

            if data_is_positional:
                args[0] = data
            else:
                kwargs["data"] = data

        super().__init__(*args, **kwargs)

        for field_name in tuple(self.fields):
            if field_name not in self.fast_fields + self.derived_fields:
                self.fields.pop(field_name)
        for field_name in self.derived_fields:
            self.fields[field_name].widget = forms.HiddenInput()

        # Invalid paste data is reported against the text area instead of producing
        # a second, confusing "solar system is required" error from the hidden field.
        self.fields["eve_solar_system_2"].required = False
        self.fields["structure_type_2"].widget.attrs[
            "data-skyhook-type-id"
        ] = str(EveTypeId.ORBITAL_SKYHOOK.value)
        self.fields["location_details"].required = False
        self.order_fields(self.fast_fields + self.derived_fields)

    def save(self, commit=True):
        timer = super().save(commit=False)
        timer.discord_timerboard = True
        if commit:
            timer.save()
        return timer

    def clean_pasted_timer(self):
        value = self.cleaned_data["pasted_timer"]
        try:
            parsed_timer = parse_eve_timer_text(value)
        except ValueError as ex:
            raise ValidationError(str(ex)) from ex

        if not EveSolarSystem.objects.filter(
            name__iexact=parsed_timer.solar_system_name
        ).exists():
            raise ValidationError(
                _("Solar system '%(name)s' was not found."),
                params={"name": parsed_timer.solar_system_name},
            )
        return value


class ReconForm(TimerForm):
    """Add or edit a structure in the Database, optionally with a reinforcement
    timer that is added to its history.
    """

    recon_fields = (
        "structure_name",
        "eve_solar_system_2",
        "structure_type_2",
        "owner_2",
        "date",
        "timer_type",
        "reinforcement_time",
        "location_details",
        "fitting",
        "details_notes",
    )

    class Meta:
        model = Structure
        fields = (
            "structure_name",
            "eve_solar_system_2",
            "structure_type_2",
            "owner_2",
            "date",
            "timer_type",
            "reinforcement_time",
            "location_details",
            "fitting",
            "details_notes",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in tuple(self.fields):
            if name not in self.recon_fields:
                self.fields.pop(name)
        self.fields["structure_type_2"].help_text = (
            "Ansiblex and Metenox use a ±30 minute vulnerability window; "
            "other types use ±3 hours."
        )
        self.fields["date"].help_text = (
            "Only if it is reinforced: when the timer ends, in EVE time (UTC)."
        )
        self.fields["location_details"].label = _("Location details")
        self.fields["location_details"].help_text = _(
            "Nearby planet, moon, gate, or other location information."
        )
        self.fields["details_notes"].label = _("Details / notes")
        self.fields["details_notes"].help_text = _(
            "Additional information about this recon."
        )
        if self.is_new:
            self.initial["reinforcement_time"] = dt.time(0, 0)
        self.order_fields(self.recon_fields)

