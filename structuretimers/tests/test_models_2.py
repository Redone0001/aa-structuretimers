import datetime as dt
from unittest.mock import patch

from django.utils.timezone import now

from app_utils.testdata_factories import (
    EveAllianceInfoFactory,
    EveCorporationInfoFactory,
)
from app_utils.testing import NoSocketsTestCase

from structuretimers.models import NotificationRule, ScheduledNotification, Timer
from structuretimers.tests.testdata.factory import (
    DiscordWebhookFactory,
    EveSolarSystemHighSecFactory,
    EveSolarSystemLowSecFactory,
    EveSolarSystemNullSecFactory,
    EveSolarSystemWSpaceFactory,
    NotificationRuleFactory,
    ScheduledNotificationFactory,
    TimerFactory,
)

MODULE_PATH = "structuretimers.models"


class TestNotificationRule_IsMatchingTimer(NoSocketsTestCase):
    def test_should_match_when_no_rules_set(self):
        # given
        timer = TimerFactory()
        rule: NotificationRule = NotificationRuleFactory()

        # when/then
        self.assertTrue(rule.is_matching_timer(timer))

    def test_should_match_require_timer_types(self):
        rule: NotificationRule = NotificationRuleFactory(
            require_timer_types=[Timer.Type.ARMOR]
        )
        cases = [
            (Timer.Type.ANCHORING, False),
            (Timer.Type.ARMOR, True),
            (Timer.Type.FINAL, False),
            (Timer.Type.HULL, False),
            (Timer.Type.MOONMINING, False),
            (Timer.Type.NONE, False),
            (Timer.Type.PRELIMINARY, False),
            (Timer.Type.THEFT, False),
        ]

        for tc in cases:
            with self.subTest(type=tc[0]):
                timer: Timer = TimerFactory(timer_type=tc[0])
                self.assertIs(rule.is_matching_timer(timer), tc[1])

    def test_should_match_exclude_timer_types(self):
        rule: NotificationRule = NotificationRuleFactory(
            exclude_timer_types=[Timer.Type.ARMOR]
        )
        cases = [
            (Timer.Type.ANCHORING, True),
            (Timer.Type.ARMOR, False),
            (Timer.Type.FINAL, True),
            (Timer.Type.HULL, True),
            (Timer.Type.MOONMINING, True),
            (Timer.Type.NONE, True),
            (Timer.Type.PRELIMINARY, False),
            (Timer.Type.THEFT, True),
        ]

        for tc in cases:
            with self.subTest(type=tc[0]):
                timer: Timer = TimerFactory(timer_type=tc[0])
                self.assertIs(rule.is_matching_timer(timer), tc[1])

    def test_should_never_match_without_date(self):
        # given
        timer = TimerFactory(date=None)
        rule: NotificationRule = NotificationRuleFactory()

        # when/then
        self.assertFalse(rule.is_matching_timer(timer))

    def test_should_match_require_objectives(self):
        rule: NotificationRule = NotificationRuleFactory(
            require_objectives=[Timer.Objective.HOSTILE]
        )
        cases = [
            (Timer.Objective.FRIENDLY, False),
            (Timer.Objective.HOSTILE, True),
            (Timer.Objective.NEUTRAL, False),
            (Timer.Objective.UNDEFINED, False),
        ]

        for tc in cases:
            with self.subTest(type=tc[0]):
                timer: Timer = TimerFactory(objective=tc[0])
                self.assertIs(rule.is_matching_timer(timer), tc[1])

    def test_should_match_exclude_objectives(self):
        rule: NotificationRule = NotificationRuleFactory(
            exclude_objectives=[Timer.Objective.HOSTILE]
        )
        cases = [
            (Timer.Objective.FRIENDLY, True),
            (Timer.Objective.HOSTILE, False),
            (Timer.Objective.NEUTRAL, True),
            (Timer.Objective.UNDEFINED, True),
        ]

        for tc in cases:
            with self.subTest(type=tc[0]):
                timer: Timer = TimerFactory(objective=tc[0])
                self.assertIs(rule.is_matching_timer(timer), tc[1])

    def test_should_match_require_corporations(self):
        corp = EveCorporationInfoFactory()
        rule: NotificationRule = NotificationRuleFactory()
        rule.require_corporations.add(corp)

        match: Timer = TimerFactory(eve_corporation=corp)
        self.assertTrue(rule.is_matching_timer(match))

        no_match: Timer = TimerFactory()
        self.assertFalse(rule.is_matching_timer(no_match))

    def test_should_match_exclude_corporations(self):
        corp = EveCorporationInfoFactory()
        rule: NotificationRule = NotificationRuleFactory()
        rule.exclude_corporations.add(corp)

        match: Timer = TimerFactory()
        self.assertTrue(rule.is_matching_timer(match))

        no_match: Timer = TimerFactory(eve_corporation=corp)
        self.assertFalse(rule.is_matching_timer(no_match))

    def test_should_match_require_alliance(self):
        all = EveAllianceInfoFactory()
        rule: NotificationRule = NotificationRuleFactory()
        rule.require_alliances.add(all)

        match: Timer = TimerFactory(eve_alliance=all)
        self.assertTrue(rule.is_matching_timer(match))

        no_match: Timer = TimerFactory()
        self.assertFalse(rule.is_matching_timer(no_match))

    def test_should_match_exclude_alliance(self):
        all = EveAllianceInfoFactory()
        rule: NotificationRule = NotificationRuleFactory()
        rule.exclude_alliances.add(all)

        match: Timer = TimerFactory()
        self.assertTrue(rule.is_matching_timer(match))

        no_match: Timer = TimerFactory(eve_alliance=all)
        self.assertFalse(rule.is_matching_timer(no_match))

    def test_should_match_require_visibility(self):
        rule: NotificationRule = NotificationRuleFactory(
            require_visibility=[Timer.Visibility.CORPORATION]
        )

        match: Timer = TimerFactory(visibility=Timer.Visibility.CORPORATION)
        self.assertTrue(rule.is_matching_timer(match))

        no_match: Timer = TimerFactory()
        self.assertFalse(rule.is_matching_timer(no_match))

    def test_should_match_exclude_visibility(self):
        rule: NotificationRule = NotificationRuleFactory(
            exclude_visibility=[Timer.Visibility.CORPORATION]
        )

        match: Timer = TimerFactory()
        self.assertTrue(rule.is_matching_timer(match))

        no_match: Timer = TimerFactory(visibility=Timer.Visibility.CORPORATION)
        self.assertFalse(rule.is_matching_timer(no_match))

    def test_should_match_require_important(self):
        rule: NotificationRule = NotificationRuleFactory(
            is_important=NotificationRule.Clause.REQUIRED
        )

        match: Timer = TimerFactory(is_important=True)
        self.assertTrue(rule.is_matching_timer(match))

        no_match: Timer = TimerFactory()
        self.assertFalse(rule.is_matching_timer(no_match))

    def test_should_match_exclude_important(self):
        rule: NotificationRule = NotificationRuleFactory(
            is_important=NotificationRule.Clause.EXCLUDED
        )

        match: Timer = TimerFactory()
        self.assertTrue(rule.is_matching_timer(match))

        no_match: Timer = TimerFactory(is_important=True)
        self.assertFalse(rule.is_matching_timer(no_match))

    def test_should_match_require_opsec(self):
        rule: NotificationRule = NotificationRuleFactory(
            is_opsec=NotificationRule.Clause.REQUIRED
        )

        match: Timer = TimerFactory(is_opsec=True)
        self.assertTrue(rule.is_matching_timer(match))

        no_match: Timer = TimerFactory()
        self.assertFalse(rule.is_matching_timer(no_match))

    def test_should_match_exclude_opsec(self):
        rule: NotificationRule = NotificationRuleFactory(
            is_opsec=NotificationRule.Clause.EXCLUDED
        )

        match: Timer = TimerFactory()
        self.assertTrue(rule.is_matching_timer(match))

        no_match: Timer = TimerFactory(is_opsec=True)
        self.assertFalse(rule.is_matching_timer(no_match))

    def test_should_match_require_regions(self):
        solar_system = EveSolarSystemLowSecFactory()
        rule: NotificationRule = NotificationRuleFactory()
        rule.require_regions.add(solar_system.eve_constellation.eve_region)

        match: Timer = TimerFactory(eve_solar_system=solar_system)
        self.assertTrue(rule.is_matching_timer(match))

        no_match: Timer = TimerFactory()
        self.assertFalse(rule.is_matching_timer(no_match))

    def test_should_match_exclude_regions(self):
        solar_system = EveSolarSystemLowSecFactory()
        rule: NotificationRule = NotificationRuleFactory()
        rule.exclude_regions.add(solar_system.eve_constellation.eve_region)

        match: Timer = TimerFactory()
        self.assertTrue(rule.is_matching_timer(match))

        no_match: Timer = TimerFactory(eve_solar_system=solar_system)
        self.assertFalse(rule.is_matching_timer(no_match))

    def test_should_match_require_space_types(self):
        rule = NotificationRuleFactory(require_space_types=[Timer.SpaceType.LOW_SEC])

        cases = [
            ("high sec", EveSolarSystemHighSecFactory(), False),
            ("low sec", EveSolarSystemLowSecFactory(), True),
            ("null sec", EveSolarSystemNullSecFactory(), False),
            ("w space", EveSolarSystemWSpaceFactory(), False),
        ]

        for tc in cases:
            with self.subTest(name=tc[0]):
                timer: Timer = TimerFactory(eve_solar_system=tc[1])
                self.assertIs(rule.is_matching_timer(timer), tc[2])

    def test_should_match_exclude_space_types(self):
        rule = NotificationRuleFactory(exclude_space_types=[Timer.SpaceType.LOW_SEC])

        cases = [
            ("high sec", EveSolarSystemHighSecFactory(), True),
            ("low sec", EveSolarSystemLowSecFactory(), False),
            ("null sec", EveSolarSystemNullSecFactory(), True),
            ("w space", EveSolarSystemWSpaceFactory(), True),
        ]

        for tc in cases:
            with self.subTest(name=tc[0]):
                timer: Timer = TimerFactory(eve_solar_system=tc[1])
                self.assertIs(rule.is_matching_timer(timer), tc[2])


@patch(MODULE_PATH + ".NotificationRule._import_schedule_notifications_for_rule")
class TestNotificationRule_UpdateOnSave(NoSocketsTestCase):
    @patch(MODULE_PATH + ".STRUCTURETIMERS_NOTIFICATIONS_ENABLED", True)
    def test_should_schedule_notifications(self, mock_schedule_notifications):
        rule = NotificationRule(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_10,
            webhook=DiscordWebhookFactory(),
        )
        rule.save()
        self.assertTrue(mock_schedule_notifications.called)

    @patch(MODULE_PATH + ".STRUCTURETIMERS_NOTIFICATIONS_ENABLED", False)
    def test_should_not_schedule_when_global_disabled(
        self, mock_schedule_notifications
    ):
        rule = NotificationRule(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_10,
            webhook=DiscordWebhookFactory(),
        )
        rule.save()
        self.assertFalse(mock_schedule_notifications.called)

    @patch(MODULE_PATH + ".STRUCTURETIMERS_NOTIFICATIONS_ENABLED", True)
    def test_should_not_schedule_when_rule_disabled(self, mock_schedule_notifications):
        rule = NotificationRule(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_10,
            webhook=DiscordWebhookFactory(),
            is_enabled=False,
        )
        rule.save()
        self.assertFalse(mock_schedule_notifications.called)

    @patch(MODULE_PATH + ".STRUCTURETIMERS_NOTIFICATIONS_ENABLED", False)
    def test_created_trigger(self, mock_schedule_notifications):
        """
        when trigger is created
        then delete all scheduled notifications based on same rule
        """
        rule: NotificationRule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_10,
            webhook=DiscordWebhookFactory(),
        )
        timer: Timer = TimerFactory(
            date=now() + dt.timedelta(hours=4),
        )
        obj = ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=timer.date,
            notification_date=timer.date - dt.timedelta(minutes=10),
        )
        rule.trigger = NotificationRule.Trigger.NEW_TIMER_CREATED
        rule.scheduled_time = None
        rule.save()

        self.assertFalse(ScheduledNotification.objects.filter(pk=obj.pk).exists())

    @patch(MODULE_PATH + ".STRUCTURETIMERS_NOTIFICATIONS_ENABLED", True)
    def test_should_delete_scheduled_notifications_when_rule_disabled(
        self, mock_schedule_notifications
    ):
        rule: NotificationRule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_10,
            webhook=DiscordWebhookFactory(),
            is_enabled=True,
        )
        timer: Timer = TimerFactory(date=now() + dt.timedelta(hours=4))
        obj = ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=timer.date,
            notification_date=timer.date - dt.timedelta(minutes=10),
        )

        rule.is_enabled = False
        rule.save()

        self.assertFalse(ScheduledNotification.objects.filter(pk=obj.pk).exists())

    @patch(MODULE_PATH + ".STRUCTURETIMERS_NOTIFICATIONS_ENABLED", False)
    def test_should_delete_scheduled_notifications_when_globally_disabled(
        self, mock_schedule_notifications
    ):
        rule: NotificationRule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_10,
            webhook=DiscordWebhookFactory(),
            is_enabled=True,
        )
        timer: Timer = TimerFactory(date=now() + dt.timedelta(hours=4))
        obj = ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=timer.date,
            notification_date=timer.date - dt.timedelta(minutes=10),
        )

        rule.save()

        self.assertFalse(ScheduledNotification.objects.filter(pk=obj.pk).exists())

    @patch(MODULE_PATH + ".STRUCTURETIMERS_NOTIFICATIONS_ENABLED", True)
    def test_should_not_delete_scheduled_notifications_when_rule_stays_enabled(
        self, mock_schedule_notifications
    ):
        rule: NotificationRule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_10,
            webhook=DiscordWebhookFactory(),
            is_enabled=True,
        )
        timer: Timer = TimerFactory(date=now() + dt.timedelta(hours=4))
        obj = ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=timer.date,
            notification_date=timer.date - dt.timedelta(minutes=10),
        )

        rule.save()

        self.assertTrue(ScheduledNotification.objects.filter(pk=obj.pk).exists())
