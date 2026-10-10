import datetime as dt
from unittest.mock import patch

from django.db.models import QuerySet
from django.utils.timezone import now

from app_utils.testdata_factories import EveCorporationInfoFactory
from app_utils.testing import NoSocketsTestCase, queryset_pks

from structuretimers.models import NotificationRule, Timer
from structuretimers.tests.testdata.factory import (
    DiscordWebhookFactory,
    NotificationRuleFactory,
    TimerFactory,
)

MODELS_PATH = "structuretimers.models"


class TestTimer_QuerySet(NoSocketsTestCase):
    def test_should_match_one_timer(self):
        # given
        timer_1 = TimerFactory(timer_type=Timer.Type.ARMOR)
        TimerFactory(timer_type=Timer.Type.HULL)
        rule = NotificationRuleFactory(require_timer_types=[Timer.Type.ARMOR])

        # when
        qs: QuerySet = Timer.objects.all().conforms_with_notification_rule(rule)

        # then
        self.assertSetEqual(queryset_pks(qs), {timer_1.pk})

    def test_should_match_no_timer(self):
        # given
        TimerFactory(eve_corporation=EveCorporationInfoFactory())
        rule = NotificationRuleFactory()
        rule.require_corporations.add(EveCorporationInfoFactory())

        # when
        qs = Timer.objects.all().conforms_with_notification_rule(rule)

        # then
        self.assertFalse(qs.exists())

    def test_should_match_both_timers(self):
        # given
        timer_1 = TimerFactory(objective=Timer.Objective.FRIENDLY)
        timer_2 = TimerFactory(objective=Timer.Objective.FRIENDLY)
        rule = NotificationRuleFactory(require_objectives=[Timer.Objective.FRIENDLY])

        # when
        qs = Timer.objects.all().conforms_with_notification_rule(rule)

        # then
        self.assertSetEqual(queryset_pks(qs), {timer_1.pk, timer_2.pk})


class TestTimer_Manger(NoSocketsTestCase):
    @patch("structuretimers.managers.STRUCTURETIMERS_TIMERS_OBSOLETE_AFTER_DAYS", 1)
    def test_delete_old_timer(self):
        timer_1 = TimerFactory(date=now())
        old = TimerFactory(date=now() - dt.timedelta(days=1, seconds=1))
        history = TimerFactory(date=now() - dt.timedelta(days=1, seconds=1))
        # Only timers without a structure are cleaned up; the rest is history.
        Timer.objects.filter(pk=old.pk).update(structure=None)
        result = Timer.objects.delete_obsolete()
        self.assertEqual(result, 1)
        # Database records have no date and are never obsolete.
        self.assertSetEqual(
            queryset_pks(Timer.objects.exclude(timer_type=Timer.Type.PRELIMINARY)),
            {timer_1.pk, history.pk},
        )

    def test_can_handle_no_timers(self):
        result = Timer.objects.delete_obsolete()
        self.assertEqual(result, 0)


class TestNotificationRule_QuerySet(NoSocketsTestCase):
    def test_should_contain_matching_rules_only(self):
        # given
        webhook = DiscordWebhookFactory()
        rule_1 = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=10,
            require_timer_types=[Timer.Type.ARMOR],
            webhook=webhook,
        )
        rule_2 = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=15,
            require_timer_types=[Timer.Type.ARMOR],
            webhook=webhook,
        )
        NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=15,
            require_objectives=[Timer.Objective.FRIENDLY],
            webhook=webhook,
        )
        timer = TimerFactory(
            structure_name="Test Timer",
            date=now() + dt.timedelta(hours=4),
            timer_type=Timer.Type.ARMOR,
            objective=Timer.Objective.HOSTILE,
        )

        # when
        qs = NotificationRule.objects.all().conforms_with_timer(timer)

        # then
        self.assertSetEqual(queryset_pks(qs), {rule_1.pk, rule_2.pk})

    def test_should_not_match_any_rule(self):
        # given
        webhook = DiscordWebhookFactory()
        NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=15,
            require_objectives=[Timer.Objective.FRIENDLY],
            webhook=webhook,
        )
        timer = TimerFactory(
            structure_name="Test Timer",
            date=now() + dt.timedelta(hours=4),
            timer_type=Timer.Type.HULL,
            objective=Timer.Objective.HOSTILE,
        )

        # when
        qs = NotificationRule.objects.all().conforms_with_timer(timer)

        # then
        self.assertSetEqual(queryset_pks(qs), set())
