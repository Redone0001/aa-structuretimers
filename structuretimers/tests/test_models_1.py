import datetime as dt
import json
from typing import NamedTuple
from unittest.mock import Mock, patch

import dhooks_lite

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils.timezone import now
from eveuniverse.models import EveSolarSystem

from app_utils.json import JSONDateTimeDecoder
from app_utils.testing import NoSocketsTestCase

from structuretimers import __title__
from structuretimers.models import (
    NotificationRule,
    ScheduledNotification,
    StagingSystem,
    Timer,
    _task_calc_staging_system,
)

from .testdata.factory import (
    CitadelTypeFactory,
    DiscordWebhookFactory,
    DistancesFromStagingFactory,
    EveSolarSystemFactory,
    EveSolarSystemHighSecFactory,
    EveSolarSystemLowSecFactory,
    EveSolarSystemNullSecFactory,
    EveSolarSystemWSpaceFactory,
    NotificationRuleFactory,
    ScheduledNotificationFactory,
    StagingSystemFactory,
    TimerFactory,
    UserWithAccessFactory,
)

MODULE_PATH = "structuretimers.models"


class TestTimer(NoSocketsTestCase):
    def test_str_1(self):
        timer = TimerFactory.build(
            structure_name="Test",
            timer_type=Timer.Type.ARMOR,
            eve_solar_system=EveSolarSystemFactory(name="Abune"),
            structure_type=CitadelTypeFactory(name="Astrahus"),
            date=dt.datetime(2020, 8, 6, 13, 25, tzinfo=dt.timezone.utc),
        )
        expected = 'Armor timer for Astrahus "Test" in Abune @ 2020-08-06 13:25'
        self.assertEqual(str(timer), expected)

    def test_str_2(self):
        timer = TimerFactory.build(
            structure_name="Test",
            timer_type=Timer.Type.PRELIMINARY,
            eve_solar_system=EveSolarSystemFactory(name="Abune"),
            structure_type=CitadelTypeFactory(name="Astrahus"),
        )
        expected = 'Preliminary timer for Astrahus "Test" in Abune'
        self.assertEqual(str(timer), expected)

    def test_structure_display_name_1(self):
        timer = TimerFactory.build(
            eve_solar_system=EveSolarSystemFactory(name="Abune"),
            structure_type=CitadelTypeFactory(name="Astrahus"),
        )
        expected = "Astrahus in Abune"
        self.assertEqual(timer.structure_display_name, expected)

    def test_structure_display_name_2(self):
        timer = TimerFactory.build(
            eve_solar_system=EveSolarSystemFactory(name="Abune"),
            structure_type=CitadelTypeFactory(name="Astrahus"),
            location_details="P5-M3",
        )
        expected = "Astrahus in Abune near P5-M3"
        self.assertEqual(timer.structure_display_name, expected)

    def test_structure_display_name_3(self):
        timer = TimerFactory.build(
            structure_name="Big Boy",
            eve_solar_system=EveSolarSystemFactory(name="Abune"),
            structure_type=CitadelTypeFactory(name="Astrahus"),
        )
        expected = 'Astrahus "Big Boy" in Abune'
        self.assertEqual(timer.structure_display_name, expected)


@patch(MODULE_PATH + "._task_calc_timer_distances_for_all_staging_systems", Mock())
class TestTimer_ScheduleNotificationsOnSave(NoSocketsTestCase):
    @patch(MODULE_PATH + "._task_schedule_notifications_for_timer")
    def test_schedule_notifications_for_new_timers(self, mock_schedule_notifications):
        # when
        timer = Timer.objects.create(
            date=now() + dt.timedelta(hours=4),
            eve_solar_system=EveSolarSystemFactory(),
            structure_type=CitadelTypeFactory(),
            timer_type=Timer.Type.ARMOR,
        )

        # then
        self.assertTrue(mock_schedule_notifications.called)
        _, kwargs = mock_schedule_notifications.return_value.apply_async.call_args
        self.assertEqual(kwargs["kwargs"]["timer_pk"], timer.pk)

    def test_schedule_notifications_when_date_changed(self):
        # when
        timer = TimerFactory(date=now() + dt.timedelta(hours=4))

        with patch(
            MODULE_PATH + "._task_schedule_notifications_for_timer"
        ) as mock_schedule_notifications:
            timer.date = now() + dt.timedelta(hours=3)
            timer.save()
            self.assertTrue(mock_schedule_notifications.called)
            _, kwargs = mock_schedule_notifications.return_value.apply_async.call_args
            self.assertEqual(kwargs["kwargs"]["timer_pk"], timer.pk)

    def test_dont_schedule_notifications_else(self):
        timer = TimerFactory(
            date=now() + dt.timedelta(hours=4),
        )

        with patch(
            MODULE_PATH + "._task_schedule_notifications_for_timer"
        ) as mock_schedule_notifications:
            timer.date = now() + dt.timedelta(hours=3)
            timer.structure_name = "Some fancy name"
            self.assertFalse(mock_schedule_notifications.called)

    @patch(MODULE_PATH + "._task_schedule_notifications_for_timer")
    def test_dont_schedule_notifications_for_new_preliminary_timers(
        self, mock_schedule_notifications
    ):
        # when
        Timer.objects.create(
            timer_type=Timer.Type.PRELIMINARY,
            eve_solar_system=EveSolarSystemFactory(),
            structure_type=CitadelTypeFactory(),
        )

        # then
        self.assertFalse(mock_schedule_notifications.called)

    @patch(MODULE_PATH + "._task_schedule_notifications_for_timer")
    def test_remove_scheduled_notifications_when_timer_changed_to_preliminary(
        self, mock_schedule_notifications
    ):
        # given
        rule = NotificationRuleFactory(is_enabled=False)
        timer = TimerFactory(date=now() + dt.timedelta(hours=4))
        notification = ScheduledNotificationFactory(notification_rule=rule, timer=timer)
        mock_schedule_notifications.reset()

        # when
        timer.timer_type = Timer.Type.PRELIMINARY
        timer.save()

        # then
        self.assertFalse(mock_schedule_notifications.called)
        self.assertFalse(
            ScheduledNotification.objects.filter(pk=notification.pk).exists()
        )


@patch(MODULE_PATH + "._task_schedule_notifications_for_timer", Mock)
class TestTimer_CalcDistancesOnSave(NoSocketsTestCase):
    @patch(MODULE_PATH + "._task_calc_timer_distances_for_all_staging_systems")
    def test_should_calc_distances_when_created(self, mock_calc_distances):
        # when
        timer = Timer.objects.create(
            date=now() + dt.timedelta(hours=4),
            eve_solar_system=EveSolarSystemFactory(),
            structure_type=CitadelTypeFactory(),
        )

        # then
        self.assertTrue(mock_calc_distances.called)
        _, kwargs = mock_calc_distances.return_value.apply_async.call_args
        self.assertEqual(kwargs["args"][0], timer.pk)

    @patch(MODULE_PATH + "._task_calc_timer_distances_for_all_staging_systems")
    def test_should_recalc_distances_when_solar_system_has_changed(
        self, mock_calc_distances
    ):
        # given
        timer: Timer = TimerFactory(date=now() + dt.timedelta(hours=4))

        # when
        timer.eve_solar_system = EveSolarSystemFactory()
        timer.save()

        # then
        self.assertTrue(mock_calc_distances.called)

    @patch(MODULE_PATH + "._task_calc_timer_distances_for_all_staging_systems")
    def test_should_not_recalc_distances_when_other_fields_changed(
        self, mock_calc_distances
    ):
        # given
        timer = TimerFactory(
            date=now() + dt.timedelta(hours=4),
        )
        # when
        timer.structure_type = CitadelTypeFactory()
        timer.save()

        # then
        self.assertFalse(mock_calc_distances.called)

    @patch(MODULE_PATH + "._task_calc_timer_distances_for_all_staging_systems")
    def test_should_not_crash_when_saving_timer_without_solar_system(
        self, mock_calc_distances
    ):
        # given
        timer = TimerFactory(date=now() + dt.timedelta(hours=4), eve_solar_system=None)
        # when
        timer.structure_type = CitadelTypeFactory()
        timer.save()

        # then
        self.assertFalse(mock_calc_distances.called)


class TestTimer_UserCanEdit(NoSocketsTestCase):
    def test_creator_can_edit_own_timer(self):
        user = UserWithAccessFactory(
            permissions__=[
                "structuretimers.basic_access",
                "structuretimers.create_timer",
            ]
        )
        timer = TimerFactory(user=user)
        self.assertTrue(timer.user_can_edit(user))

    def test_manager_can_edit_other_timers(self):
        user = UserWithAccessFactory(
            permissions__=[
                "structuretimers.basic_access",
                "structuretimers.manage_timer",
            ]
        )
        timer = TimerFactory(user=UserWithAccessFactory())
        self.assertTrue(timer.user_can_edit(user))

    def test_non_manager_can_not_edit_other_timer(self):
        user = UserWithAccessFactory(
            permissions__=[
                "structuretimers.basic_access",
                "structuretimers.create_timer",
            ]
        )
        timer = TimerFactory(user=UserWithAccessFactory())
        self.assertFalse(timer.user_can_edit(user))

    """
    def test_user_with_basic_access_can_view_normal_timer(self):
        timer = Timer(
            date=now() + dt.timedelta(hours=4),
            eve_solar_system=self.system_abune,
            structure_type=self.type_astrahus,
            user=self.user_1,
        )
        self.assertTrue(timer.user_can_view(self.user_3))

    def test_user_can_not_view_corp_restricted_timer_from_other_corp(self):
        timer = Timer(
            date=now() + dt.timedelta(hours=4),
            eve_solar_system=self.system_abune,
            structure_type=self.type_astrahus,
            eve_corporation=self.corporation_1,
            visibility=Timer.Visibility.CORPORATION,
            user=self.user_1,
        )
        self.assertFalse(timer.user_can_view(self.user_3))

    def test_user_can_view_corp_restricted_timer_from_same_corp(self):
        timer = Timer(
            date=now() + dt.timedelta(hours=4),
            eve_solar_system=self.system_abune,
            structure_type=self.type_astrahus,
            eve_corporation=self.corporation_1,
            visibility=Timer.Visibility.CORPORATION,
            user=self.user_1,
        )
        self.assertTrue(timer.user_can_view(self.user_2))

    def test_user_can_not_view_alliance_restricted_timer_from_other_alliance(self):
        timer = Timer(
            date=now() + dt.timedelta(hours=4),
            eve_solar_system=self.system_abune,
            structure_type=self.type_astrahus,
            eve_alliance=self.alliance_1,
            visibility=Timer.Visibility.ALLIANCE,
            user=self.user_1,
        )
        self.assertFalse(timer.user_can_view(self.user_3))

    def test_opsec_user_can_view_opsec_timer(self):
        timer = Timer(
            date=now() + dt.timedelta(hours=4),
            eve_solar_system=self.system_abune,
            structure_type=self.type_astrahus,
            is_opsec=True,
            user=self.user_2,
        )
        self.assertTrue(timer.user_can_view(self.user_2))

    def test_non_opsec_user_can_not_view_opsec_timer(self):
        timer = Timer(
            date=now() + dt.timedelta(hours=4),
            eve_solar_system=self.system_abune,
            structure_type=self.type_astrahus,
            is_opsec=True,
            user=self.user_2,
        )
        self.assertFalse(timer.user_can_view(self.user_1))
    """


@patch(MODULE_PATH + ".DiscordWebhook.send_message", spec=True)
class TestTimer_SendNotification(NoSocketsTestCase):
    @patch(MODULE_PATH + ".STRUCTURETIMER_NOTIFICATION_SET_AVATAR", True)
    def test_should_send_minimal_notification(self, mock_send_message):
        # given
        timer = TimerFactory()
        webhook = DiscordWebhookFactory()

        # when
        timer.send_notification(webhook)

        # then
        self.assertEqual(mock_send_message.call_count, 1)
        _, kwargs = mock_send_message.call_args
        self.assertEqual(kwargs["username"], __title__)
        self.assertIsNotNone(kwargs["avatar_url"])

    @patch(MODULE_PATH + ".STRUCTURETIMER_NOTIFICATION_SET_AVATAR", False)
    def test_should_send_notification_without_avatar(self, mock_send_message):
        # given
        timer = TimerFactory()
        webhook = DiscordWebhookFactory()

        # when
        timer.send_notification(webhook)

        # then
        self.assertEqual(mock_send_message.call_count, 1)
        _, kwargs = mock_send_message.call_args
        self.assertIsNone(kwargs["username"])
        self.assertIsNone(kwargs["avatar_url"])

    def test_with_content(self, mock_send_message):
        # given
        timer = TimerFactory()
        webhook = DiscordWebhookFactory()

        # when
        timer.send_notification(webhook, "Extra Text")

        # then
        self.assertEqual(mock_send_message.call_count, 1)
        _, kwargs = mock_send_message.call_args
        self.assertIn("Extra Text", kwargs["content"])

    def test_timer_with_options_1(self, mock_send_message):
        # given
        timer = TimerFactory(objective=Timer.Objective.FRIENDLY)
        webhook = DiscordWebhookFactory()

        # when
        timer.send_notification(webhook)

        # then
        self.assertEqual(mock_send_message.call_count, 1)

    def test_timer_with_options_2(self, mock_send_message):
        # given
        timer = TimerFactory(objective=Timer.Objective.HOSTILE)
        webhook = DiscordWebhookFactory()

        # when
        timer.send_notification(webhook)

        # then
        self.assertEqual(mock_send_message.call_count, 1)


class TestTimer_ScheduleNotification(NoSocketsTestCase):
    def test_should_create_scheduled_notification(self):
        # given
        timer = TimerFactory(date=now() + dt.timedelta(hours=2))
        rule = NotificationRuleFactory(scheduled_time=NotificationRule.MINUTES_30)

        # when
        timer.schedule_notification(notification_rule=rule)

        # then
        obj = ScheduledNotification.objects.get(timer=timer, notification_rule=rule)
        self.assertEqual(obj.timer_date, timer.date)
        self.assertEqual(obj.notification_date, timer.date - dt.timedelta(minutes=30))

    def test_should_update_existing_scheduled_notification(self):
        # given
        timer = TimerFactory(date=now() + dt.timedelta(hours=2))
        rule = NotificationRuleFactory(scheduled_time=NotificationRule.MINUTES_30)
        existing = ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=timer.date - dt.timedelta(hours=1),
            notification_date=timer.date - dt.timedelta(hours=1, minutes=30),
        )

        # when
        timer.schedule_notification(notification_rule=rule)

        # then
        existing.refresh_from_db()
        self.assertEqual(existing.timer_date, timer.date)
        self.assertEqual(
            existing.notification_date, timer.date - dt.timedelta(minutes=30)
        )
        self.assertEqual(ScheduledNotification.objects.count(), 1)

    def test_should_raise_error_for_preliminary_timer(self):
        # given
        timer = TimerFactory(timer_type=Timer.Type.PRELIMINARY)
        rule = NotificationRuleFactory(scheduled_time=NotificationRule.MINUTES_30)

        # when/then
        with self.assertRaises(ValueError):
            timer.schedule_notification(notification_rule=rule)

    def test_should_raise_error_when_timer_has_no_date(self):
        # given
        timer = TimerFactory(timer_type=Timer.Type.ARMOR, date=None)
        rule = NotificationRuleFactory(scheduled_time=NotificationRule.MINUTES_30)

        # when/then
        with self.assertRaises(ValueError):
            timer.schedule_notification(notification_rule=rule)

    def test_should_raise_error_when_rule_has_no_scheduled_time(self):
        # given
        timer = TimerFactory(date=now() + dt.timedelta(hours=2))
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.NEW_TIMER_CREATED, scheduled_time=None
        )

        # when/then
        with self.assertRaises(ValueError):
            timer.schedule_notification(notification_rule=rule)

    def test_should_schedule_notification_with_zero_minutes(self):
        # given
        timer = TimerFactory(date=now() + dt.timedelta(hours=2))
        rule = NotificationRuleFactory(scheduled_time=NotificationRule.MINUTES_0)

        # when
        timer.schedule_notification(notification_rule=rule)

        # then
        obj = ScheduledNotification.objects.get(timer=timer, notification_rule=rule)
        self.assertEqual(obj.notification_date, timer.date)


class TestTimer_SpaceType_FromEveSolarSystem(NoSocketsTestCase):
    def test_all(self):
        class Case(NamedTuple):
            name: str
            solar_system: EveSolarSystem
            want: Timer.SpaceType

        cases = [
            Case("high sec", EveSolarSystemHighSecFactory(), Timer.SpaceType.HIGH_SEC),
            Case("low sec", EveSolarSystemLowSecFactory(), Timer.SpaceType.LOW_SEC),
            Case("null sec", EveSolarSystemNullSecFactory(), Timer.SpaceType.NULL_SEC),
            Case("w sec", EveSolarSystemWSpaceFactory(), Timer.SpaceType.WH_SPACE),
        ]
        for tc in cases:
            got = Timer.SpaceType.from_eve_solar_system(tc.solar_system)
            self.assertEqual(got, tc.want)


class TestDiscordWebhook(TestCase):
    def test_str(self):
        webhook = DiscordWebhookFactory(name="Dummy")
        self.assertEqual(str(webhook), "Dummy")

    def test_repr(self):
        webhook = DiscordWebhookFactory(name="Dummy")
        self.assertEqual(
            repr(webhook), f"DiscordWebhook(id={webhook.id}, name='Dummy')"
        )

    def test_queue_features(self):
        cache.clear()
        webhook = DiscordWebhookFactory(name="Dummy")
        self.assertEqual(webhook.queue_size(), 0)
        webhook.send_message(content="Dummy message")
        self.assertEqual(webhook.queue_size(), 1)
        webhook.clear_queue()
        self.assertEqual(webhook.queue_size(), 0)

    def test_send_message_normal(self):
        cache.clear()
        webhook = DiscordWebhookFactory(name="Dummy")
        embed = dhooks_lite.Embed(description="my_description")
        self.assertEqual(
            webhook.send_message(
                content="my_content",
                username="my_username",
                avatar_url="my_avatar_url",
                embeds=[embed],
            ),
            1,
        )
        message = json.loads(webhook._main_queue.dequeue(), cls=JSONDateTimeDecoder)
        expected = {
            "content": "my_content",
            "embeds": [{"description": "my_description", "type": "rich"}],
            "username": "my_username",
            "avatar_url": "my_avatar_url",
        }
        self.assertDictEqual(message, expected)

    def test_send_message_empty(self):
        cache.clear()
        webhook = DiscordWebhookFactory(name="Dummy")
        with self.assertRaises(ValueError):
            webhook.send_message()


@patch(MODULE_PATH + ".sleep", new=lambda x: x)
@patch(MODULE_PATH + ".DiscordWebhook.send_message_to_webhook", spec=True)
class TestDiscordWebhook_SendQueuedMessages(NoSocketsTestCase):
    def setUp(self):
        cache.clear()

    def test_one_message(self, mock_send_message_to_webhook):
        """
        when one message in queue
        then send it and returns 1
        """
        mock_send_message_to_webhook.return_value = True
        webhook = DiscordWebhookFactory()
        webhook.send_message("dummy")

        result = webhook.send_queued_messages()

        self.assertEqual(result, 1)
        self.assertTrue(mock_send_message_to_webhook.called)
        self.assertEqual(webhook.queue_size(), 0)

    def test_three_message(self, mock_send_message_to_webhook):
        """
        when three messages in queue
        then sends them and returns 3
        """
        mock_send_message_to_webhook.return_value = True
        webhook = DiscordWebhookFactory()
        webhook.send_message("dummy-1")
        webhook.send_message("dummy-2")
        webhook.send_message("dummy-3")

        result = webhook.send_queued_messages()

        self.assertEqual(result, 3)
        self.assertEqual(mock_send_message_to_webhook.call_count, 3)
        self.assertEqual(webhook.queue_size(), 0)

    def test_no_messages(self, mock_send_message_to_webhook):
        """
        when no message in queue
        then do nothing and return 0
        """
        mock_send_message_to_webhook.return_value = True
        webhook = DiscordWebhookFactory()
        result = webhook.send_queued_messages()

        self.assertEqual(result, 0)
        self.assertFalse(mock_send_message_to_webhook.called)
        self.assertEqual(webhook.queue_size(), 0)

    def test_failed_message(self, mock_send_message_to_webhook):
        """
        given one message in queue
        when sending fails
        then re-queues message and return 0
        """
        mock_send_message_to_webhook.return_value = False
        webhook = DiscordWebhookFactory()
        webhook.send_message("dummy")

        result = webhook.send_queued_messages()

        self.assertEqual(result, 0)
        self.assertTrue(mock_send_message_to_webhook.called)
        self.assertEqual(webhook.queue_size(), 1)


@patch(MODULE_PATH + ".dhooks_lite.Webhook.execute", spec=True)
@patch(MODULE_PATH + ".logger", spec=True)
class TestDiscordWebhook_SendMessageToWebhook(NoSocketsTestCase):
    def setUp(self) -> None:
        cache.clear()

    def test_send_normal(self, mock_logger, mock_execute):
        """
        when sending of message successful
        return True
        """
        mock_execute.return_value = dhooks_lite.WebhookResponse(
            headers={}, status_code=200
        )
        message = {
            "content": "my_content",
            "embeds": [{"description": "my_description", "type": "rich"}],
            "username": "my_username",
            "avatar_url": "my_avatar_url",
        }
        webhook = DiscordWebhookFactory()

        result = webhook.send_message_to_webhook(message)

        self.assertTrue(result)
        self.assertTrue(mock_execute.called)
        _, kwargs = mock_execute.call_args
        self.assertDictEqual(
            kwargs,
            {
                "content": "my_content",
                "embeds": [
                    dhooks_lite.Embed.from_dict(
                        {"description": "my_description", "type": "rich"}
                    )
                ],
                "username": "my_username",
                "avatar_url": "my_avatar_url",
                "wait_for_response": True,
            },
        )
        self.assertFalse(mock_logger.warning.called)

    def test_send_failed(self, mock_logger, mock_execute):
        """
        when sending of message failed
        then log warning and return False
        """
        mock_execute.return_value = dhooks_lite.WebhookResponse(
            headers={}, status_code=440
        )
        message = {
            "content": "my_content",
            "embeds": [{"description": "my_description", "type": "rich"}],
            "username": "my_username",
            "avatar_url": "my_avatar_url",
        }
        webhook = DiscordWebhookFactory()

        result = webhook.send_message_to_webhook(message)

        self.assertFalse(result)
        self.assertTrue(mock_execute.called)
        self.assertTrue(mock_logger.warning.called)


@patch(MODULE_PATH + ".EveSolarSystem.distance_to", lambda *args, **kwargs: 4.257e16)
@patch(MODULE_PATH + ".EveSolarSystem.jumps_to", lambda *args, **kwargs: 3)
@patch(MODULE_PATH + "._task_calc_staging_system", wraps=_task_calc_staging_system)
@override_settings(CELERY_ALWAYS_EAGER=True, CELERY_EAGER_PROPAGATES_EXCEPTIONS=True)
class TestStagingSystem(NoSocketsTestCase):
    def test_should_calc_distances(self, spy_task_calc_staging_system):
        # given
        timer = TimerFactory(
            structure_name="Test",
            timer_type=Timer.Type.ARMOR,
            date=dt.datetime(2020, 8, 6, 13, 25, tzinfo=dt.timezone.utc),
        )

        # when
        staging_system = StagingSystem.objects.create(
            eve_solar_system=EveSolarSystemFactory()
        )

        # then
        obj = timer.distances.first()
        self.assertEqual(obj.staging_system, staging_system)
        self.assertAlmostEqual(obj.light_years, 4.5, delta=0.1)
        self.assertEqual(obj.jumps, 3)
        self.assertTrue(spy_task_calc_staging_system.called)

    def test_should_not_update_distances_when_solar_system_not_changed(
        self, spy_task_calc_staging_system
    ):
        # given
        TimerFactory()
        staging_system = StagingSystemFactory()

        # when
        staging_system.save()

        # then
        self.assertFalse(spy_task_calc_staging_system.called)

    def test_should_not_crash_when_solar_system_is_none(
        self, spy_task_calc_staging_system
    ):
        # given
        staging_system = StagingSystemFactory(eve_solar_system=None)

        # when
        staging_system.save()

        # then
        self.assertFalse(spy_task_calc_staging_system.called)


@patch(MODULE_PATH + ".EveSolarSystem.jumps_to", spec=True)
@patch(MODULE_PATH + ".EveSolarSystem.distance_to", spec=True)
class TestDistancesFromStaging_Calculate(NoSocketsTestCase):
    def test_should_calculate_distances(self, mock_distance_to, mock_jumps_to):
        # given
        mock_distance_to.return_value = 2.3
        mock_jumps_to.return_value = 4
        timer = TimerFactory()
        staging_system = StagingSystemFactory()
        distances = DistancesFromStagingFactory(
            timer=timer,
            staging_system=staging_system,
            light_years=None,
            jumps=None,
        )
        # when
        distances.calculate()
        # then
        self.assertGreater(distances.light_years, 0)
        self.assertEqual(distances.jumps, 4)

    def test_should_calculate_distances_when_none(
        self, mock_distance_to, mock_jumps_to
    ):
        # given
        mock_distance_to.return_value = 2.3
        mock_jumps_to.return_value = 4
        timer = TimerFactory()
        staging_system = StagingSystemFactory(eve_solar_system=None)
        distances = DistancesFromStagingFactory(
            timer=timer,
            staging_system=staging_system,
            light_years=None,
            jumps=None,
        )
        # when
        distances.calculate()
        # then
        self.assertIsNone(distances.light_years)
        self.assertIsNone(distances.jumps)
