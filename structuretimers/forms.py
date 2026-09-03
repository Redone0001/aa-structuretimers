"""Forms."""

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Dict

import puremagic
import requests

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from eveuniverse.models import EveSolarSystem, EveType

from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo
from allianceauth.services.hooks import get_extension_logger

from .constants import EveGroupId
from .models import Timer

logger = get_extension_logger(__name__)

DATETIME_FORMAT = "%Y-%m-%d %H:%M"

EVE_TIMER_DATE_FORMAT = "%Y.%m.%d %H:%M:%S"
EVE_TIMER_UNTIL_PATTERN = re.compile(
    r"^(?:Reinforced|Anchoring) until\s+"
    r"(?P<date>\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2})\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedEveTimer:
    """Data extracted from an EVE Online structure timer copy/paste."""

    solar_system_name: str
    structure_name: str
    date: dt.datetime


def parse_eve_timer_text(value: str) -> ParsedEveTimer:
    """Parse text copied from a supported EVE Online structure timer tooltip."""

    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if len(lines) < 2 or " - " not in lines[0]:
        raise ValueError(
            _(
                "Paste the EVE timer with 'System - Structure name' on the first "
                "line and either 'Reinforced until YYYY.MM.DD HH:MM:SS' or "
                "'Anchoring until YYYY.MM.DD HH:MM:SS' below it."
            )
        )

    solar_system_name, structure_name = (
        part.strip() for part in lines[0].split(" - ", maxsplit=1)
    )
    if not solar_system_name or not structure_name:
        raise ValueError(_("The solar system and structure name cannot be empty."))

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
    )


class TimerForm(forms.ModelForm):
    """Form for timers."""

    ASTERISK_HTML = mark_safe('<i class="fas fa-asterisk"></i>')
    TIME_REMAINING_WIDGET_ATTRS = {
        "class": "timer-time-remaining-field",
    }
    TIME_REMAINING_HELP_TEXT = _(
        "This field is calculated from the current time. "
        "Alternatively, you can enter the date above in the `Date` field."
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
    objective = forms.ChoiceField(
        initial=Timer.Objective.UNDEFINED,
        choices=Timer.Objective.choices,
        widget=forms.Select(attrs={"class": "select2-render"}),
    )
    timer_type = forms.ChoiceField(
        choices=Timer.Type.choices,
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
        help_text=_(
            "The date when the timer happens. "
            "Alternatively, you can enter the remaining time below."
        ),
    )
    days_left = forms.IntegerField(
        required=False,
        label=_("Days Remaining"),
        validators=[MinValueValidator(0)],
        widget=forms.NumberInput(attrs=TIME_REMAINING_WIDGET_ATTRS),
        help_text=TIME_REMAINING_HELP_TEXT,
    )
    hours_left = forms.IntegerField(
        required=False,
        label=_("Hours Remaining"),
        validators=[MinValueValidator(0), MaxValueValidator(23)],
        widget=forms.NumberInput(attrs=TIME_REMAINING_WIDGET_ATTRS),
        help_text=TIME_REMAINING_HELP_TEXT,
    )
    minutes_left = forms.IntegerField(
        required=False,
        label=_("Minutes Remaining"),
        validators=[MinValueValidator(0), MaxValueValidator(59)],
        widget=forms.NumberInput(attrs=TIME_REMAINING_WIDGET_ATTRS),
        help_text=TIME_REMAINING_HELP_TEXT,
    )
    details_image_url = forms.URLField(
        required=False,
        help_text=_("Paste a public URL to an image into this field."),
    )

    class Meta:
        model = Timer
        fields = (
            "eve_solar_system_2",
            "location_details",
            "structure_type_2",
            "timer_type",
            "structure_name",
            "owner_name",
            "objective",
            "date",
            "days_left",
            "hours_left",
            "minutes_left",
            "details_image_url",
            "details_notes",
            "visibility",
            "is_opsec",
            "is_important",
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

        if my_instance:
            self.fields["eve_solar_system_2"].widget.choices = [
                (
                    str(my_instance.eve_solar_system_id),
                    my_instance.eve_solar_system.name,
                )
            ]
            self.fields["structure_type_2"].widget.choices = [
                (str(my_instance.structure_type_id), my_instance.structure_type.name)
            ]

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("eve_solar_system_2"):
            self._clean_solar_system(cleaned_data)

        if cleaned_data.get("structure_type_2"):
            self._clean_structure_type(cleaned_data)

        if cleaned_data.get("details_image_url"):
            self._clean_image(cleaned_data)

        days_left = cleaned_data.get("days_left")
        hours_left = cleaned_data.get("hours_left")
        minutes_left = cleaned_data.get("minutes_left")
        date = cleaned_data.get("date")
        if any([days_left, hours_left, minutes_left]):
            if days_left is None:
                days_left = cleaned_data["days_left"] = 0
            if hours_left is None:
                hours_left = cleaned_data["hours_left"] = 0
            if minutes_left is None:
                minutes_left = cleaned_data["minutes_left"] = 0

        timer_type = cleaned_data.get("timer_type")
        if (
            timer_type != Timer.Type.PRELIMINARY
            and days_left is None
            and hours_left is None
            and minutes_left is None
            and date is None
        ):
            cleaned_data["timer_type"] = Timer.Type.PRELIMINARY.value
        if timer_type == Timer.Type.PRELIMINARY and (
            days_left is not None
            or hours_left is not None
            or minutes_left is not None
            or date is not None
        ):
            cleaned_data["timer_type"] = Timer.Type.NONE.value

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
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError,
        ) as ex:
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

    def save(self, commit=True):
        timer = super().save(commit=False)

        # character / corporation / alliance
        if self.is_new:
            character = self.user.profile.main_character
            try:
                alliance = character.alliance
            except EveAllianceInfo.DoesNotExist:
                alliance = EveAllianceInfo.objects.create_alliance(
                    character.alliance_id
                )
            try:
                corporation = character.corporation
            except EveCorporationInfo.DoesNotExist:
                corporation = EveCorporationInfo.objects.create_corporation(
                    character.corporation_id
                )
            logger.debug(
                (
                    "Determined timer save request is on behalf of "
                    "character %s corporation %s"
                ),
                character,
                corporation,
            )
            timer.eve_character = character
            timer.eve_corporation = corporation
            timer.eve_alliance = alliance
            timer.user = self.user

        # calculate future time
        days_left = self.cleaned_data.get("days_left")
        hours_left = self.cleaned_data.get("hours_left")
        minutes_left = self.cleaned_data.get("minutes_left")
        date = self.cleaned_data.get("date")
        if date is not None:
            timer.date = date
        elif (
            days_left is not None
            and hours_left is not None
            and minutes_left is not None
        ):
            future_time = dt.timedelta(
                days=days_left, hours=hours_left, minutes=minutes_left
            )
            current_time = now()
            date = current_time + future_time
            logger.debug(
                "Determined timer eve time is %s - current time %s, adding %s",
                date,
                current_time,
                future_time,
            )
            timer.date = date
        else:
            timer.date = None

        # structure type
        timer.structure_type_id = self.cleaned_data.get("structure_type_2")
        timer.eve_solar_system_id = self.cleaned_data.get("eve_solar_system_2")

        if commit:
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
    objective = forms.ChoiceField(
        initial=Timer.Objective.HOSTILE,
        choices=Timer.Objective.choices,
        widget=forms.Select(attrs={"class": "select2-render"}),
    )

    fast_fields = (
        "pasted_timer",
        "structure_type_2",
        "timer_type",
        "owner_name",
        "objective",
    )
    derived_fields = ("eve_solar_system_2", "structure_name", "date")

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
        self.fields["owner_name"].required = True
        self.fields["owner_name"].label = _("Owner")
        self.order_fields(self.fast_fields + self.derived_fields)

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
