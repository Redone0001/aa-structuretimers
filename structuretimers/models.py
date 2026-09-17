"""Models."""

# pylint: disable=too-many-lines

import datetime as dt
import json
from time import sleep
from typing import List, Optional, Tuple

import dhooks_lite
from multiselectfield import MultiSelectField
from multiselectfield.utils import get_max_length
from simple_mq import SimpleMQ

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from eveuniverse.helpers import meters_to_ly
from eveuniverse.models import EveRegion, EveSolarSystem, EveType

from allianceauth.eveonline.evelinks import dotlan
from allianceauth.eveonline.models import (
    EveAllianceInfo,
    EveCharacter,
    EveCorporationInfo,
)
from allianceauth.services.hooks import get_extension_logger
from app_utils.allianceauth import get_redis_client
from app_utils.datetime import DATETIME_FORMAT
from app_utils.json import JSONDateTimeDecoder, JSONDateTimeEncoder
from app_utils.urls import reverse_absolute, static_file_absolute_url

from structuretimers import __title__
from structuretimers.app_settings import (
    STRUCTURETIMER_NOTIFICATION_SET_AVATAR,
    STRUCTURETIMERS_NOTIFICATIONS_ENABLED,
)
from structuretimers.managers import (
    DistancesFromStagingManager,
    NotificationRuleManager,
    TimerManager,
)

logger = get_extension_logger(__name__)


def default_avatar_url() -> str:
    """avatar url for all messages"""
    return static_file_absolute_url("structuretimers/img/structuretimers_logo.png")


def _task_calc_staging_system():
    from .tasks import calc_staging_system

    return calc_staging_system


def _task_calc_timer_distances_for_all_staging_systems():
    from .tasks import calc_timer_distances_for_all_staging_systems

    return calc_timer_distances_for_all_staging_systems


def _task_schedule_notifications_for_timer():
    from .tasks import schedule_notifications_for_timer

    return schedule_notifications_for_timer


class General(models.Model):
    """Meta model for app permissions"""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access", "Can access this app and see timers"),
            ("recon_coordinator", "Can create and coordinate recon campaigns"),
            ("create_timer", "Can create new timers and edit own timers"),
            ("manage_timer", "Can edit and delete any timer"),
            ("opsec_access", "Can create and see opsec timers"),
        )


class DiscordWebhook(models.Model):
    """A Discord webhook"""

    ZKB_KILLMAIL_BASEURL = "https://zkillboard.com/kill/"
    ICON_SIZE = 128

    # delay in seconds between every message sent to Discord
    # this needs to be >= 1 to prevent 429 Too Many Request errors
    SEND_DELAY = 2

    name = models.CharField(
        max_length=64, unique=True, help_text="short name to identify this webhook"
    )
    url = models.CharField(
        max_length=255,
        unique=True,
        help_text=(
            "URL of this webhook, e.g. "
            "https://discordapp.com/api/webhooks/123456/abcdef"
        ),
    )
    notes = models.TextField(
        null=True,
        default=None,
        blank=True,
        help_text="you can add notes about this webhook here if you want",
    )
    is_enabled = models.BooleanField(
        default=True,
        db_index=True,
        help_text="whether notifications are currently sent to this webhook",
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        redis_client = get_redis_client()
        self._main_queue = SimpleMQ(redis_client, f"{__title__}_webhook_{self.pk}_main")
        self._error_queue = SimpleMQ(
            redis_client, f"{__title__}_webhook_{self.pk}_errors"
        )

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.id}, name='{self.name}')"

    def send_message(
        self,
        content: Optional[str] = None,
        embeds: Optional[List[dhooks_lite.Embed]] = None,
        tts: Optional[bool] = None,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ) -> int:
        """Adds Discord message to queue for later sending

        Returns updated size of queue
        Raises ValueError if message is incomplete
        """
        if not content and not embeds:
            raise ValueError("Message must have content or embeds to be valid")

        if embeds:
            embeds_list = [obj.asdict() for obj in embeds]
        else:
            embeds_list = None

        message = {}
        if content:
            message["content"] = content
        if embeds_list:
            message["embeds"] = embeds_list
        if tts:
            message["tts"] = tts
        if username:
            message["username"] = username
        if avatar_url:
            message["avatar_url"] = avatar_url

        return self._main_queue.enqueue(json.dumps(message, cls=JSONDateTimeEncoder))

    def send_queued_messages(self) -> int:
        """Send all messages in the queue to this webhook

        Return number of successful sent messages

        Messages that could not be sent are put back into the queue for later retry
        """
        message_count = 0
        while True:
            message_json = self._main_queue.dequeue()
            if message_json:
                message = json.loads(message_json, cls=JSONDateTimeDecoder)
                logger.debug("Sending message to webhook %s", self)
                if self.send_message_to_webhook(message):
                    message_count += 1
                else:
                    self._error_queue.enqueue(message_json)

                sleep(self.SEND_DELAY)

            else:
                break

        while True:
            message_json = self._error_queue.dequeue()
            if message_json:
                self._main_queue.enqueue(message_json)
            else:
                break

        return message_count

    def queue_size(self) -> int:
        """returns current size of the queue"""
        return self._main_queue.size()

    def clear_queue(self) -> int:
        """deletes all messages from the queue. Returns number of cleared messages."""
        counter = 0
        while True:
            y = self._main_queue.dequeue()
            if y is None:
                break
            counter += 1

        return counter

    def send_message_to_webhook(self, message: dict) -> bool:
        """sends message directly to webhook

        returns True if successful, else False
        """
        hook = dhooks_lite.Webhook(url=self.url)
        if message.get("embeds"):
            embeds = [
                dhooks_lite.Embed.from_dict(embed_dict)
                for embed_dict in message.get("embeds", [])
            ]
        else:
            embeds = None

        response = hook.execute(
            content=message.get("content"),
            embeds=embeds,
            username=message.get("username"),
            avatar_url=message.get("avatar_url"),
            wait_for_response=True,
        )
        logger.debug("headers: %s", response.headers)
        logger.debug("status_code: %s", response.status_code)
        logger.debug("content: %s", response.content)
        if response.status_ok:
            return True

        logger.warning(
            "Failed to send message to Discord. HTTP status code: %d, response: %s",
            response.status_code,
            response.content,
        )
        return False

    @classmethod
    def create_discord_link(cls, name: str, url: str) -> str:
        """Returns a link in markdown format for Disord."""
        return f"[{str(name)}]({str(url)})"

    def send_test_message(self, user: User = None) -> Tuple[str, bool]:
        """Sends a test notification to this webhook and returns send report"""
        try:
            user_text = f" sent by **{user}**" if user else ""
            message = {
                "content": f"Test message for webhook **{self.name}**{user_text}",
                "username": __title__,
                "avatar_url": default_avatar_url(),
            }
            success = self.send_message_to_webhook(message)
        except OSError as ex:
            logger.warning(
                "Failed to send test notification to webhook %s: %s",
                self,
                ex,
                exc_info=True,
            )
            return str(ex), False
        return "(no info)", success

    @staticmethod
    def default_username() -> str:
        """avatar username for all messages"""
        return __title__


# pylint: disable=too-many-locals
class Timer(models.Model):
    """A structure timer"""

    # TODO: Old constants needed to maintain compatibility with other apps
    # during transition only. REMOVE as soon as possible.

    TYPE_NONE = "NO"
    TYPE_ARMOR = "AR"
    TYPE_HULL = "HL"
    TYPE_FINAL = "FI"
    TYPE_ANCHORING = "AN"
    TYPE_UNANCHORING = "UA"
    TYPE_MOONMINING = "MM"

    OBJECTIVE_UNDEFINED = "UN"
    OBJECTIVE_HOSTILE = "HO"
    OBJECTIVE_FRIENDLY = "FR"
    OBJECTIVE_NEUTRAL = "NE"

    VISIBILITY_UNRESTRICTED = "UN"
    VISIBILITY_ALLIANCE = "AL"
    VISIBILITY_CORPORATION = "CO"

    class Type(models.TextChoices):
        """A time type."""

        NONE = "NO", _("Unspecified")
        ARMOR = "AR", _("Armor")
        HULL = "HL", _("Hull")
        FINAL = "FI", _("Final")
        ANCHORING = "AN", _("Anchoring")
        UNANCHORING = "UA", _("Unanchoring")
        MOONMINING = "MM", _("Moon Mining")
        THEFT = "TF", _("Theft")
        PRELIMINARY = "PL", _("Preliminary")  # special timer with no date

        @classmethod
        def choices_for_notification_rules(cls) -> List["Timer.Type"]:
            """Subset of choices suitable for creating notification rules."""
            return [choice for choice in cls.choices if choice[0] != cls.PRELIMINARY]

    class Objective(models.TextChoices):
        """A timer objective."""

        UNDEFINED = "UN", _("undefined")
        HOSTILE = "HO", _("hostile")
        FRIENDLY = "FR", _("friendly")
        NEUTRAL = "NE", _("neutral")

    class Visibility(models.TextChoices):
        """A timer visibility."""

        UNRESTRICTED = "UN", _("unrestricted")
        ALLIANCE = "AL", _("Alliance only")
        CORPORATION = "CO", _("Corporation only")

    class SpaceType(models.TextChoices):
        """A timer space type."""

        UNDEFINED = "UN", _("undefined")
        HIGH_SEC = "HS", _("highsec")
        LOW_SEC = "LS", _("lowsec")
        NULL_SEC = "NS", _("nullsec")
        WH_SPACE = "WS", _("wh space")

        @classmethod
        def from_eve_solar_system(cls, eve_solar_system: Optional[EveSolarSystem]):
            """Determine the space type of a solar system and return it."""
            if not eve_solar_system:
                return cls.UNDEFINED
            if eve_solar_system.is_high_sec:
                return cls.HIGH_SEC
            if eve_solar_system.is_low_sec:
                return cls.LOW_SEC
            if eve_solar_system.is_null_sec:
                return cls.NULL_SEC
            if eve_solar_system.is_w_space:
                return cls.WH_SPACE
            raise NotImplementedError(
                f"System with unknown space type: {eve_solar_system}"
            )

    date = models.DateTimeField(
        db_index=True,
        null=True,
        help_text="Date when this timer happens",
    )
    reinforcement_time = models.TimeField(null=True, blank=True)
    details_image_url = models.CharField(
        max_length=1024,
        default=None,
        blank=True,
        null=True,
        help_text=(
            "URL for details like a screenshot of the structure's fitting, "
            "e.g. https://www.example.com/route/image.jpg"
        ),
    )
    details_notes = models.TextField(
        default="",
        blank=True,
        help_text="Notes with additional information about this timer",
    )
    eve_alliance = models.ForeignKey(
        EveAllianceInfo,
        on_delete=models.SET_DEFAULT,
        default=None,
        null=True,
        blank=True,
        related_name="+",
        help_text="Alliance of the user who created this timer",
    )
    eve_character = models.ForeignKey(
        EveCharacter,
        on_delete=models.SET_DEFAULT,
        default=None,
        null=True,
        blank=True,
        related_name="+",
        help_text="Main character of the user who created this timer",
    )
    eve_corporation = models.ForeignKey(
        EveCorporationInfo,
        on_delete=models.SET_DEFAULT,
        default=None,
        null=True,
        blank=True,
        related_name="+",
        help_text="Corporation of the user who created this timer",
    )
    eve_solar_system = models.ForeignKey(
        EveSolarSystem,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        related_name="+",
    )
    is_important = models.BooleanField(
        default=False,
        help_text="Mark this timer as is_important",
    )
    is_opsec = models.BooleanField(
        default=False,
        db_index=True,
        help_text=(
            "Limit access to users with OPSEC clearance. "
            "Can be combined with visibility."
        ),
    )
    location_details = models.CharField(
        max_length=254,
        default="",
        blank=True,
        help_text=(
            "Additional information about the location of this structure, "
            "e.g. name of nearby planet / moon / gate"
        ),
    )
    notification_rules = models.ManyToManyField(
        "NotificationRule",
        through="ScheduledNotification",
        through_fields=("timer", "notification_rule"),
        help_text="Notification rules conforming with this timer",
    )
    objective = models.CharField(
        max_length=2, choices=Objective.choices, default=Objective.UNDEFINED
    )
    owner_name = models.CharField(
        max_length=254,
        default=None,
        blank=True,
        null=True,
        help_text="Name of the corporation owning the structure",
    )
    structure_type = models.ForeignKey(
        EveType, on_delete=models.CASCADE, related_name="+", null=True, blank=True
    )
    structure_name = models.CharField(max_length=254, default="", blank=True)
    timer_type = models.CharField(max_length=2, choices=Type.choices, default=Type.NONE)
    user = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        blank=True,
        related_name="+",
    )
    visibility = models.CharField(
        max_length=2,
        choices=Visibility.choices,
        default=Visibility.UNRESTRICTED,
        db_index=True,
        help_text=(
            "The visibility of this timer can be limited to members"
            " of your organization"
        ),
    )
    last_updated_at = models.DateTimeField(auto_now=True)

    objects = TimerManager()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_eve_solar_system_id = self.eve_solar_system_id
        self._original_date = self.date
        self._original_timer_type = self.timer_type

    def __str__(self):
        if self.timer_type != Timer.Type.PRELIMINARY and self.date:
            date = f" @ {self.date.strftime(DATETIME_FORMAT)}"
        else:
            date = ""
        timer_type = self.get_timer_type_display()
        structure_name = self.structure_display_name
        return _("%(timer_type)s timer for %(structure)s%(date)s") % {
            "timer_type": timer_type,
            "structure": structure_name,
            "date": date,
        }

    def get_absolute_url(self) -> str:
        """Returns the absolute URL of a timer."""
        url = reverse("structuretimers:timer_list")
        return (
            f"{url}?tab=preliminary"
            if self.timer_type == self.Type.PRELIMINARY
            else url
        )

    @property
    def structure_display_name(self) -> str:
        """Return structure name for display."""
        type_name = self.structure_type.name if self.structure_type else _("(unknown)")
        structure_name = f' "{self.structure_name}"' if self.structure_name else ""
        solar_system = self.eve_solar_system.name if self.eve_solar_system else ""
        location = (
            " " + _("near %(location)s") % {"location": self.location_details}
            if self.location_details
            else ""
        )
        return _("%(type)s%(name)s in %(system)s%(location)s") % {
            "type": type_name,
            "name": structure_name,
            "system": solar_system,
            "location": location,
        }

    @property
    def space_type(self) -> "SpaceType":
        """Return space type of a timer."""
        return self.SpaceType.from_eve_solar_system(self.eve_solar_system)

    def user_can_edit(self, user: User) -> bool:
        """Checks if the given user can edit this timer. Returns True or False"""
        return user.has_perm("structuretimers.manage_timer") or (
            self.user == user and user.has_perm("structuretimers.create_timer")
        )

    def label_type_for_timer_type(self) -> str:
        """returns the Boostrap label type for a timer_type"""
        label_types_map = {
            self.Type.NONE: "secondary",
            self.Type.ARMOR: "danger",
            self.Type.HULL: "danger",
            self.Type.FINAL: "danger",
            self.Type.ANCHORING: "warning",
            self.Type.UNANCHORING: "warning",
            self.Type.MOONMINING: "success",
            self.Type.THEFT: "warning",
        }
        if self.timer_type in label_types_map:
            label_type = label_types_map[self.timer_type]
        else:
            label_type = "secondary"
        return label_type

    def label_type_for_objective(self) -> str:
        """returns the Boostrap label type for objective"""
        label_types_map = {
            self.Objective.FRIENDLY: "primary",
            self.Objective.HOSTILE: "danger",
            self.Objective.NEUTRAL: "info",
            self.Objective.UNDEFINED: "secondary",
        }
        if self.objective in label_types_map:
            label_type = label_types_map[self.objective]
        else:
            label_type = "secondary"
        return label_type

    def schedule_notification(self, notification_rule: "NotificationRule"):
        """Schedule a notification for this timer."""
        if self.timer_type == Timer.Type.PRELIMINARY:
            raise ValueError(f"Can not schedule preliminary timers: {self}")
        if not self.date:
            raise ValueError(f"Timer has no date: {self}")
        if notification_rule.scheduled_time is None:
            raise ValueError(
                f"Notification rule has no scheduled date: {notification_rule}"
            )
        notification_date = self.date - dt.timedelta(
            minutes=notification_rule.scheduled_time
        )
        scheduled_notification, _ = ScheduledNotification.objects.update_or_create(
            timer=self,
            notification_rule=notification_rule,
            defaults={"timer_date": self.date, "notification_date": notification_date},
        )
        logger.info(
            "Scheduled notification for timer #%d, rule #%d",
            scheduled_notification.timer.pk,
            scheduled_notification.notification_rule.pk,
        )

    def send_notification(
        self, webhook: DiscordWebhook, content: Optional[str] = None
    ) -> None:
        """Send notification related to this timer to a webhook."""
        structure_type_name = (
            self.structure_type.name if self.structure_type else "(unknown)"
        )
        solar_system_name = self.eve_solar_system.name if self.eve_solar_system else ""
        title = f"{structure_type_name} in {solar_system_name}"
        if self.structure_name:
            structure_name_text = f'**{structure_type_name}** "{self.structure_name}"'
        else:
            article = "an" if structure_type_name[0:1].lower() in "aeiou" else "a"
            structure_name_text = f"{article} **{structure_type_name}**"

        region_name = (
            self.eve_solar_system.eve_constellation.eve_region.name
            if self.eve_solar_system
            else ""
        )
        solar_system_link = webhook.create_discord_link(
            name=solar_system_name, url=dotlan.solar_system_url(solar_system_name)
        )
        solar_system_text = f"{solar_system_link} ({region_name})"
        near_text = f" near {self.location_details}" if self.location_details else ""
        owned_text = f" owned by **{self.owner_name}**" if self.owner_name else ""
        elapse_at = self.date.strftime(DATETIME_FORMAT) if self.date else "?"
        description = (
            f"The **{self.get_timer_type_display()}** timer for "
            f"{structure_name_text} in {solar_system_text}{near_text}{owned_text} "
            f"will elapse at **{elapse_at}**. "
            f"Our stance is: **{self.get_objective_display()}**."
        )
        structure_icon_url = (
            self.structure_type.icon_url(size=128) if self.structure_type else None
        )
        if self.objective == self.Objective.FRIENDLY:
            color = int("0x375a7f", 16)
        elif self.objective == self.Objective.HOSTILE:
            color = int("0xd9534f", 16)
        else:
            color = None

        embed = dhooks_lite.Embed(
            title=title,
            url=reverse_absolute("structuretimers:timer_list"),
            description=description,
            thumbnail=dhooks_lite.Thumbnail(structure_icon_url),
            color=color,
        )
        if STRUCTURETIMER_NOTIFICATION_SET_AVATAR:
            username = __title__
            avatar_url = default_avatar_url()
        else:
            username = None
            avatar_url = None
        webhook.send_message(
            content=content,
            embeds=[embed],
            username=username,
            avatar_url=avatar_url,
        )


@receiver(post_save, sender=Timer)
def handle_timer_save(
    sender,  # pylint: disable=unused-argument
    instance: Timer,
    created: bool,
    **kwargs,
):
    """Update timer distances from staging and schedule notifications as needed
    after a timer was saved.
    """
    schedule_notifications = (
        STRUCTURETIMERS_NOTIFICATIONS_ENABLED
        and instance.timer_type != Timer.Type.PRELIMINARY
    )
    date_changed = instance.date != instance._original_date
    needs_recalc = (
        created
        or date_changed
        or instance.eve_solar_system_id != instance._original_eve_solar_system_id
    )
    if needs_recalc:
        instance.distances.all().delete()
        _task_calc_timer_distances_for_all_staging_systems().apply_async(
            args=[instance.pk], priority=4
        )
    if (
        instance.timer_type == Timer.Type.PRELIMINARY
        and instance._original_timer_type != Timer.Type.PRELIMINARY
    ):
        instance.scheduled_notifications.all().delete()
    if schedule_notifications and (created or date_changed):
        _task_schedule_notifications_for_timer().apply_async(
            kwargs={"timer_pk": instance.pk, "is_new": created}, priority=3
        )


class NotificationRule(models.Model):
    """A notification rule."""

    class Trigger(models.TextChoices):
        """A trigger choice."""

        NEW_TIMER_CREATED = "TC", _("New timer created")
        SCHEDULED_TIME_REACHED = "TR", _("Scheduled time reached")

    # Minutes choices
    MINUTES_0 = 0
    MINUTES_5 = 5
    MINUTES_10 = 10
    MINUTES_15 = 15
    MINUTES_30 = 30
    MINUTES_45 = 45
    MINUTES_60 = 60
    MINUTES_90 = 90
    MINUTES_120 = 120
    MINUTES_CHOICES = (
        (None, "---------"),
        (MINUTES_0, _("T - 0 minutes")),
        (MINUTES_5, _("T - 5 minutes")),
        (MINUTES_10, _("T - 10 minutes")),
        (MINUTES_15, _("T - 15 minutes")),
        (MINUTES_30, _("T - 30 minutes")),
        (MINUTES_45, _("T - 45 minutes")),
        (MINUTES_60, _("T - 60 minutes")),
        (MINUTES_90, _("T - 90 minutes")),
        (MINUTES_120, _("T - 120 minutes")),
    )

    class PingType(models.TextChoices):
        """Ping type for a notification rule."""

        NONE = "PN", "(no ping)"
        HERE = "PH", "@here"
        EVERYONE = "PE", "@everyone"

        def to_text(self) -> str:
            """returns the text for creating the given ping on Discord"""
            my_map = {self.NONE: "", self.HERE: "@here", self.EVERYONE: "@everyone"}
            try:
                return my_map[self]
            except KeyError:
                return ""

    class Clause(models.TextChoices):
        """A clause in a notification rule."""

        ANY = "AN", "any"
        REQUIRED = "RQ", "required"
        EXCLUDED = "EX", "excluded"

    trigger = models.CharField(
        max_length=2,
        choices=Trigger.choices,
        help_text="Trigger for sending a notification",
    )
    scheduled_time = models.PositiveIntegerField(
        choices=MINUTES_CHOICES,
        null=True,
        default=None,
        blank=True,
        db_index=True,
        help_text=(
            "When to sent a notification in relation to when the timer elapses. "
            "Use together with `Scheduled time reached` trigger."
        ),
    )
    webhook = models.ForeignKey(
        DiscordWebhook,
        on_delete=models.CASCADE,
        help_text="The webhook all notifications are sent to",
    )
    ping_type = models.CharField(
        max_length=2,
        choices=PingType.choices,
        default=PingType.NONE,
        help_text="Options for pinging on notification",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="whether this rule is currently active",
    )
    require_timer_types = MultiSelectField(
        choices=Timer.Type.choices_for_notification_rules(),
        max_length=get_max_length(Timer.Type.choices_for_notification_rules(), None),
        blank=True,
        help_text=(
            "Timer must have one of the given timer types "
            "or leave blank to match any."
        ),
    )
    exclude_timer_types = MultiSelectField(
        choices=Timer.Type.choices_for_notification_rules(),
        max_length=get_max_length(Timer.Type.choices_for_notification_rules(), None),
        blank=True,
        help_text="Timer must NOT have one of the given timer types",
    )
    require_objectives = MultiSelectField(
        choices=Timer.Objective.choices,
        max_length=get_max_length(Timer.Objective.choices, None),
        blank=True,
        help_text=(
            "Timer must have one of the given objectives "
            "or leave blank to match any."
        ),
    )
    exclude_objectives = MultiSelectField(
        choices=Timer.Objective.choices,
        max_length=get_max_length(Timer.Objective.choices, None),
        blank=True,
        help_text="Timer must NOT have one of the given objectives",
    )
    require_corporations = models.ManyToManyField(
        EveCorporationInfo,
        blank=True,
        related_name="+",
        help_text=(
            "Timer must be created by one of the given corporations "
            "or leave blank to match any."
        ),
    )
    exclude_corporations = models.ManyToManyField(
        EveCorporationInfo,
        blank=True,
        related_name="notification_rule_exclude_corporations",
        help_text="Timer must NOT be created by one of the given corporations",
    )
    require_alliances = models.ManyToManyField(
        EveAllianceInfo,
        blank=True,
        related_name="+",
        help_text=(
            "Timer must be created by one of the given alliances "
            "or leave blank to match any."
        ),
    )
    exclude_alliances = models.ManyToManyField(
        EveAllianceInfo,
        blank=True,
        related_name="+",
        help_text="Timer must NOT be created by one of the given alliances",
    )
    require_visibility = MultiSelectField(
        choices=Timer.Visibility.choices,
        max_length=get_max_length(Timer.Visibility.choices, None),
        blank=True,
        help_text=(
            "Visibility must be one of the selected or leave blank to match any."
        ),
    )
    exclude_visibility = MultiSelectField(
        choices=Timer.Visibility.choices,
        max_length=get_max_length(Timer.Visibility.choices, None),
        blank=True,
        help_text="Visibility must NOT be one of the selected",
    )
    is_important = models.CharField(
        max_length=2,
        choices=Clause.choices,
        default=Clause.ANY,
        help_text="Wether the timer must be important",
    )
    is_opsec = models.CharField(
        max_length=2,
        choices=Clause.choices,
        default=Clause.ANY,
        help_text="Wether the timer must be OPSEC",
    )
    require_regions = models.ManyToManyField(
        EveRegion,
        blank=True,
        related_name="+",
        help_text=(
            "Timer must be created within one of the given regions "
            "or leave blank to match any."
        ),
    )
    exclude_regions = models.ManyToManyField(
        EveRegion,
        blank=True,
        related_name="+",
        help_text="Timer must NOT be created within one of the given regions",
    )
    require_space_types = MultiSelectField(
        choices=Timer.SpaceType.choices,
        max_length=get_max_length(Timer.SpaceType.choices, None),
        blank=True,
        help_text=(
            "Space type must be one of the selected or leave blank to match any."
        ),
    )
    exclude_space_types = MultiSelectField(
        choices=Timer.SpaceType.choices,
        max_length=get_max_length(Timer.SpaceType.choices, None),
        blank=True,
        help_text="Space Type must NOT be one of the selected",
    )

    objects = NotificationRuleManager()

    def __str__(self) -> str:
        return f"Notification Rule #{self.id}"

    @staticmethod
    def _import_schedule_notifications_for_rule() -> object:
        from .tasks import schedule_notifications_for_rule

        return schedule_notifications_for_rule

    @property
    def ping_type_text(self) -> str:
        """Returns the ping type as string."""
        return self.PingType(self.ping_type).to_text()

    def prepend_ping_text(self, text: str) -> str:
        """prepends ping text to given text and returns it as new text string"""
        return f"{self.ping_type_text} {text}" if self.ping_type_text else text

    # pylint: disable=too-many-branches
    def is_matching_timer(self, timer: "Timer") -> bool:
        """returns True if notification rule is matching the given timer, else False"""
        if timer.date is None:
            return False

        is_matching = True
        if is_matching and self.is_important == self.Clause.REQUIRED:
            is_matching = timer.is_important

        if is_matching and self.is_important == self.Clause.EXCLUDED:
            is_matching = not timer.is_important

        if is_matching and self.is_opsec == self.Clause.REQUIRED:
            is_matching = timer.is_opsec

        if is_matching and self.is_opsec == self.Clause.EXCLUDED:
            is_matching = not timer.is_opsec

        if is_matching and self.require_visibility:
            is_matching = timer.visibility in self.require_visibility

        if is_matching and self.exclude_visibility:
            is_matching = timer.visibility not in self.exclude_visibility

        if is_matching and self.require_timer_types:
            is_matching = timer.timer_type in self.require_timer_types

        if is_matching and self.exclude_timer_types:
            is_matching = timer.timer_type not in self.exclude_timer_types

        if is_matching and self.require_objectives:
            is_matching = timer.objective in self.require_objectives

        if is_matching and self.exclude_objectives:
            is_matching = timer.objective not in self.exclude_objectives

        if is_matching and self.require_corporations.exists():
            is_matching = timer.eve_corporation in self.require_corporations.all()

        if is_matching and self.exclude_corporations.exists():
            is_matching = timer.eve_corporation not in self.exclude_corporations.all()

        if is_matching and self.require_alliances.exists():
            is_matching = timer.eve_alliance in self.require_alliances.all()

        if is_matching and self.exclude_alliances.exists():
            is_matching = timer.eve_alliance not in self.exclude_alliances.all()

        if is_matching and timer.eve_solar_system and self.require_regions.exists():
            is_matching = (
                timer.eve_solar_system.eve_constellation.eve_region
                in self.require_regions.all()
            )

        if is_matching and timer.eve_solar_system and self.exclude_regions.exists():
            is_matching = (
                timer.eve_solar_system.eve_constellation.eve_region
                not in self.exclude_regions.all()
            )
        if is_matching and self.require_space_types:
            is_matching = timer.space_type in self.require_space_types

        if is_matching and self.exclude_space_types:
            is_matching = timer.space_type not in self.exclude_space_types

        return is_matching


@receiver(post_save, sender=NotificationRule)
def handle_rule_save(
    sender,  # pylint: disable=unused-argument
    instance: NotificationRule,
    **kwargs,
):
    """Update scheduled notifications a needed on save."""
    if instance.trigger == NotificationRule.Trigger.SCHEDULED_TIME_REACHED:
        if STRUCTURETIMERS_NOTIFICATIONS_ENABLED and instance.is_enabled:
            instance._import_schedule_notifications_for_rule().apply_async(
                kwargs={"notification_rule_pk": instance.pk}, priority=4
            )
        else:
            instance.scheduled_notifications.all().delete()

    if instance.trigger == NotificationRule.Trigger.NEW_TIMER_CREATED:
        instance.scheduled_notifications.all().delete()


class ScheduledNotification(models.Model):
    """A scheduled notification task"""

    timer = models.ForeignKey(
        Timer, on_delete=models.CASCADE, related_name="scheduled_notifications"
    )
    notification_rule = models.ForeignKey(
        NotificationRule,
        on_delete=models.CASCADE,
        related_name="scheduled_notifications",
    )

    timer_date = models.DateTimeField(db_index=True)
    notification_date = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["timer", "notification_rule"],
                name="unique_notification_schedule",
            )
        ]

    def __repr__(self) -> str:
        return (
            f"ScheduledNotification(timer='{self.timer}', "
            f"notification_rule='{self.notification_rule}', "
            f"timer_date='{self.timer_date}', "
            f"notification_date='{self.notification_date}')"
        )


class StagingSystem(models.Model):
    """A staging system."""

    eve_solar_system = models.OneToOneField(
        EveSolarSystem,
        on_delete=models.SET_DEFAULT,
        default=None,
        null=True,
        blank=True,
        related_name="+",
    )  # TODO: Remove Nullable if possible, because it is causing issues
    is_main = models.BooleanField(default=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_eve_solar_system_id = self.eve_solar_system_id

    def __str__(self) -> str:
        return str(self.eve_solar_system)


@receiver(post_save, sender=StagingSystem)
def handle_staging_system_save(
    sender,  # pylint: disable=unused-argument
    instance: StagingSystem,
    created: bool,
    **kwargs,
):
    """Update distances for staging system on save as needed."""
    needs_recalc = (
        created
        or instance.eve_solar_system_id != instance._original_eve_solar_system_id
    )
    if instance.is_main:
        StagingSystem.objects.exclude(pk=instance.pk).update(is_main=False)
    if needs_recalc:
        instance.distances.all().delete()
        _task_calc_staging_system().delay(instance.pk)


class DistancesFromStaging(models.Model):
    """A distance from a staging system."""

    timer = models.ForeignKey(Timer, on_delete=models.CASCADE, related_name="distances")
    staging_system = models.ForeignKey(
        StagingSystem, on_delete=models.CASCADE, related_name="distances"
    )

    light_years = models.FloatField(null=True, default=None, blank=True)
    jumps = models.PositiveIntegerField(null=True, default=None, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DistancesFromStagingManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["timer", "staging_system"], name="fpk_distances_from_staging"
            )
        ]

    def __str__(self) -> str:
        return f"{self.timer}-{self.staging_system}"

    def calculate(self):
        """Calculate all distances."""
        if self.staging_system.eve_solar_system:
            self.light_years = meters_to_ly(
                self.staging_system.eve_solar_system.distance_to(
                    self.timer.eve_solar_system
                )
            )
            self.jumps = self.staging_system.eve_solar_system.jumps_to(
                self.timer.eve_solar_system
            )


class ReconCampaign(models.Model):
    class ImportStatus(models.TextChoices):
        READY = "ready", "Ready"
        PENDING = "pending", "Importing"
        FAILED = "failed", "Import failed"

    region_ids = models.JSONField(default=list, blank=True)
    import_status = models.CharField(
        max_length=10, choices=ImportStatus.choices, default=ImportStatus.READY
    )
    gates_status = models.CharField(
        max_length=10, choices=ImportStatus.choices, default=ImportStatus.READY
    )
    name = models.CharField(max_length=200)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("structuretimers:campaign_detail", args=[self.pk])


class ReconCampaignSystem(models.Model):
    campaign = models.ForeignKey(
        ReconCampaign, related_name="systems", on_delete=models.CASCADE
    )
    solar_system = models.ForeignKey(EveSolarSystem, on_delete=models.PROTECT)
    reserved_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recon_reservations",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="completed_recon_systems",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "solar_system"], name="unique_recon_campaign_system"
            )
        ]
        ordering = ["solar_system__name"]
