import datetime as dt
from typing import Generic, TypeVar
from unittest.mock import Mock, patch

import factory
import factory.fuzzy

from django.contrib.auth.models import User
from django.utils.timezone import now
from eveuniverse.models import EveSolarSystem, EveType
from eveuniverse.tests.testdata.factories_2 import (
    EveGroupFactory,
    EveSolarSystemFactory,
    EveTypeFactory,
)

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from allianceauth.tests.auth_utils import AuthUtils
from allianceauth.timerboard.models import Timer as AuthTimer
from app_utils.helpers import random_string
from app_utils.testdata_factories import UserMainFactory

from structuretimers.constants import EveCategoryId, EveGroupId
from structuretimers.models import (
    DiscordWebhook,
    DistancesFromStaging,
    NotificationRule,
    ScheduledNotification,
    StagingSystem,
    Timer,
    post_save,
)

T = TypeVar("T")


class BaseMetaFactory(Generic[T], factory.base.FactoryMetaClass):
    def __call__(cls, *args, **kwargs) -> T:
        return super().__call__(*args, **kwargs)


class UserNoAccessFactory(UserMainFactory):
    pass


class UserWithAccessFactory(UserMainFactory):
    """User with basic rights."""

    permissions__ = [
        "structuretimers.basic_access",
    ]


class UserWithCreateFactory(UserMainFactory):
    """User with basic rights."""

    permissions__ = [
        "structuretimers.basic_access",
        "structuretimers.create_timer",
    ]


class UserWithManageFactory(UserMainFactory):
    """User with manager permission."""

    permissions__ = [
        "structuretimers.basic_access",
        "structuretimers.manage_timer",
    ]


supported_structure_values = set(AuthTimer.Structure.values) - {
    AuthTimer.Structure.OTHER
}


class AuthTimerFactory(
    factory.django.DjangoModelFactory, metaclass=BaseMetaFactory[AuthTimer]
):
    class Meta:
        model = AuthTimer

    corp_timer = False
    details = factory.faker.Faker("sentence")
    eve_character = factory.LazyAttribute(lambda o: o.user.profile.main_character)
    eve_corp = factory.LazyAttribute(
        lambda o: o.user.profile.main_character.corporation
    )
    eve_time = factory.fuzzy.FuzzyDateTime(
        now() + dt.timedelta(days=1), now() + dt.timedelta(days=7)
    )
    important = False
    objective = factory.fuzzy.FuzzyChoice(AuthTimer.Objective.values)
    structure = factory.fuzzy.FuzzyChoice(supported_structure_values)
    system = "Amamake"
    timer_type = factory.fuzzy.FuzzyChoice(AuthTimer.TimerType.values)
    user = factory.SubFactory(UserMainFactory)


class EveSolarSystemNullSecFactory(EveSolarSystemFactory):
    security_status = -1.0


class EveSolarSystemLowSecFactory(EveSolarSystemFactory):
    security_status = 0.3


class EveSolarSystemHighSecFactory(EveSolarSystemFactory):
    security_status = 0.9


class EveSolarSystemWSpaceFactory(EveSolarSystemFactory):
    id = factory.Sequence(lambda n: 31_900_000 + n)
    security_status = -1.0


class CitadelTypeFactory(EveTypeFactory):
    eve_group = factory.SubFactory(
        EveGroupFactory,
        eve_category__id=EveCategoryId.STRUCTURE,
        eve_category__name="Structure",
        id=EveGroupId.CITADEL,
        name="Citadel",
    )


class RefineryTypeFactory(EveTypeFactory):
    eve_group = factory.SubFactory(
        EveGroupFactory,
        eve_category__id=EveCategoryId.STRUCTURE,
        eve_category__name="Structure",
        id=EveGroupId.REFINERY,
        name="Refinery",
    )


class SkyhookTypeFactory(EveTypeFactory):
    eve_group = factory.SubFactory(
        EveGroupFactory,
        eve_category__id=EveCategoryId.ORBITAL,
        eve_category__name="Orbitals",
        id=EveGroupId.SKYHOOK,
        name="Skyhook",
    )
    id = 81080
    name = "Orbital Skyhook"


class DiscordWebhookFactory(
    factory.django.DjangoModelFactory, metaclass=BaseMetaFactory[DiscordWebhook]
):
    class Meta:
        model = DiscordWebhook

    name = factory.Sequence(lambda n: f"discord_webhook_{n}")
    url = factory.LazyAttribute(lambda o: f"{o.name}_url")
    is_enabled = True


@factory.django.mute_signals(post_save)
class NotificationRuleFactory(
    factory.django.DjangoModelFactory, metaclass=BaseMetaFactory[NotificationRule]
):
    class Meta:
        model = NotificationRule

    is_enabled = True
    scheduled_time = NotificationRule.MINUTES_15
    trigger = NotificationRule.Trigger.SCHEDULED_TIME_REACHED
    webhook = factory.SubFactory(DiscordWebhookFactory)


@factory.django.mute_signals(post_save)
class TimerFactory(factory.django.DjangoModelFactory, metaclass=BaseMetaFactory[Timer]):
    class Meta:
        model = Timer

    eve_solar_system = factory.SubFactory(EveSolarSystemFactory)
    objective = factory.fuzzy.FuzzyChoice(Timer.Objective.values)
    structure_type = factory.SubFactory(CitadelTypeFactory)
    timer_type = factory.fuzzy.FuzzyChoice([Timer.Type.ARMOR, Timer.Type.HULL])

    @factory.lazy_attribute
    def date(self):
        if self.timer_type == Timer.Type.PRELIMINARY:
            return None
        x = factory.fuzzy.FuzzyDateTime(
            start_dt=now() + dt.timedelta(days=1), end_dt=now() + dt.timedelta(days=7)
        ).fuzz()
        return x


class ScheduledNotificationFactory(
    factory.django.DjangoModelFactory, metaclass=BaseMetaFactory[ScheduledNotification]
):
    class Meta:
        model = ScheduledNotification

    notification_date = factory.LazyAttribute(
        lambda o: o.timer_date - dt.timedelta(minutes=15)
    )
    timer_date = factory.fuzzy.FuzzyDateTime(
        start_dt=now() + dt.timedelta(days=1), end_dt=now() + dt.timedelta(days=7)
    )


@factory.django.mute_signals(post_save)
class StagingSystemFactory(
    factory.django.DjangoModelFactory, metaclass=BaseMetaFactory[StagingSystem]
):
    class Meta:
        model = StagingSystem

    eve_solar_system = factory.SubFactory(EveSolarSystemFactory)


class DistancesFromStagingFactory(
    factory.django.DjangoModelFactory, metaclass=BaseMetaFactory[DistancesFromStaging]
):
    class Meta:
        model = DistancesFromStaging

    timer = factory.SubFactory(TimerFactory)
    staging_system = factory.SubFactory(StagingSystemFactory)
    light_years = factory.fuzzy.FuzzyFloat(0.5, 50)
    jumps = factory.fuzzy.FuzzyInteger(1, 50)


# -------- old factories


def add_main_to_user(user: User, character: EveCharacter):
    CharacterOwnership.objects.create(
        user=user, owner_hash="x1" + character.character_name, character=character
    )
    user.profile.main_character = character
    user.profile.save()


def create_user(character: EveCharacter) -> User:
    User.objects.filter(username=character.character_name).delete()
    user = AuthUtils.create_user(character.character_name)
    add_main_to_user(user, character)
    AuthUtils.add_permission_to_user_by_name("structuretimers.basic_access", user)
    user = User.objects.get(pk=user.pk)
    return user


def create_distances_from_staging(
    timer: Timer, staging_system: StagingSystem, **kwargs
) -> DistancesFromStaging:
    params = {
        "timer": timer,
        "staging_system": staging_system,
        "light_years": 1.2,
        "jumps": 3,
    }
    params.update(kwargs)
    return DistancesFromStaging.objects.create(**params)


def create_timer(light_years=None, jumps=None, enabled_notifications=False, **kwargs):
    params = {
        "eve_solar_system": EveSolarSystem.objects.get(id=30004984),
        "structure_type": EveType.objects.get(id=35825),
    }
    if "timer_type" not in kwargs or kwargs["timer_type"] != Timer.Type.PRELIMINARY:
        params["date"] = now() + dt.timedelta(days=3)

    params.update(kwargs)
    with patch(
        "structuretimers.models._task_calc_timer_distances_for_all_staging_systems",
        Mock(),
    ):
        if enabled_notifications:
            timer = Timer.objects.create(**params)
        else:
            with patch(
                "structuretimers.models._task_schedule_notifications_for_timer", Mock()
            ):
                timer = Timer.objects.create(**params)
        if light_years or jumps:
            for staging_system in StagingSystem.objects.all():
                DistancesFromStaging.objects.update_or_create(
                    staging_system=staging_system,
                    timer=timer,
                    defaults={"light_years": light_years, "jumps": jumps},
                )
        return timer


def create_staging_system(light_years=None, jumps=None, **kwargs):
    params = {"eve_solar_system": EveSolarSystem.objects.get(id=30045339)}  # enaluri
    params.update(kwargs)
    with patch("structuretimers.models._task_calc_staging_system", Mock()):
        staging_system = StagingSystem.objects.create(**params)
        if light_years or jumps:
            for timer in Timer.objects.all():
                DistancesFromStaging.objects.update_or_create(
                    staging_system=staging_system,
                    timer=timer,
                    defaults={"light_years": light_years, "jumps": jumps},
                )
        return staging_system


def create_discord_webhook(**kwargs):
    if "name" not in kwargs:
        while True:
            name = f"dummy{random_string(8)}"
            if not DiscordWebhook.objects.filter(name=name).exists():
                break
        kwargs["name"] = name
    if "url" not in kwargs:
        kwargs["url"] = f"https://www.example.com/{kwargs['name']}"
    return DiscordWebhook.objects.create(**kwargs)


def create_notification_rule(schedule_notification=False, **kwargs):
    if "webhook" not in kwargs:
        kwargs["webhook"] = create_discord_webhook()
    if "trigger" not in kwargs:
        kwargs["trigger"] = NotificationRule.Trigger.SCHEDULED_TIME_REACHED
    if "scheduled_time" not in kwargs:
        kwargs["scheduled_time"] = 60
    with patch(
        "structuretimers.models.STRUCTURETIMERS_NOTIFICATIONS_ENABLED",
        schedule_notification,
    ):
        return NotificationRule.objects.create(**kwargs)


def create_scheduled_notification(**kwargs):
    if "timer_date" not in kwargs:
        kwargs["timer_date"] = now() + dt.timedelta(hours=1)
    if "notification_date" not in kwargs:
        kwargs["notification_date"] = now() + dt.timedelta(minutes=45)
    return ScheduledNotification.objects.create(**kwargs)
