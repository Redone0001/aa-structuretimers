import datetime as dt
from unittest.mock import Mock, patch

from celery import Task

from django.test import TestCase, TransactionTestCase
from django.utils.timezone import now

from structuretimers.models import NotificationRule, ScheduledNotification, Timer
from structuretimers.tasks import (
    calc_timer_distances_for_all_staging_systems,
    dispatch_scheduled_notifications,
    housekeeping,
    notify_about_new_timer,
    schedule_notifications_for_rule,
    schedule_notifications_for_timer,
    send_messages_for_webhook,
    send_scheduled_notification,
)
from structuretimers.tests.testdata.factory import (
    DiscordWebhookFactory,
    NotificationRuleFactory,
    ScheduledNotificationFactory,
    StagingSystemFactory,
    TimerFactory,
)

MODULE_PATH = "structuretimers.tasks"


# class TestCaseBase(TestCase):
#     @patch("structuretimers.models.STRUCTURETIMERS_NOTIFICATIONS_ENABLED", False)
#     def setUp(self) -> None:
#         webhook = DiscordWebhookFactory()
#         webhook.clear_queue()
#         rule = NotificationRuleFactory(
#             trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
#             scheduled_time=NotificationRule.MINUTES_15,
#             webhook=webhook,
#         )
#         timer = TimerFactory(
#             structure_name="Test_1",
#             eve_solar_system=self.system_abune,
#             structure_type=self.type_raitaru,
#             date=now() + dt.timedelta(minutes=30),
#         )


@patch(MODULE_PATH + ".DiscordWebhook.send_queued_messages", spec=True)
@patch(MODULE_PATH + ".logger", spec=True)
class TestSendMessagesForWebhook(TestCase):
    def test_normal(self, mock_logger, mock_send_queued_messages):
        # given
        webhook = DiscordWebhookFactory()

        # when
        send_messages_for_webhook(webhook.pk)

        # then
        self.assertEqual(mock_send_queued_messages.call_count, 1)
        self.assertEqual(mock_logger.info.call_count, 2)
        self.assertEqual(mock_logger.error.call_count, 0)

    def test_disabled_webhook(self, mock_logger, mock_send_queued_messages):
        # given
        webhook = DiscordWebhookFactory(is_enabled=False)

        # when
        send_messages_for_webhook(webhook.pk)

        # then
        self.assertEqual(mock_send_queued_messages.call_count, 0)
        self.assertEqual(mock_logger.info.call_count, 1)
        self.assertEqual(mock_logger.error.call_count, 0)


@patch(MODULE_PATH + ".notify_about_new_timer", spec=True)
@patch(MODULE_PATH + ".send_scheduled_notification", spec=True)
class TestScheduleNotificationForTimer(TestCase):
    def test_should_schedule_new_notification_for_time_reached(
        self, mock_send_notification, mock_send_notification_for_timer
    ):
        # given
        timer = TimerFactory()
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED
        )

        # when
        schedule_notifications_for_timer(timer_pk=timer.pk, is_new=True)

        # then
        self.assertTrue(timer.scheduled_notifications.filter(notification_rule=rule))

    def test_should_not_create_notification_for_preliminary_timer(
        self, mock_send_notification, mock_send_notification_for_timer
    ):
        # given
        timer = TimerFactory(timer_type=Timer.Type.PRELIMINARY)
        NotificationRuleFactory(trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED)

        # when/then
        with self.assertRaises(ValueError):
            schedule_notifications_for_timer(timer_pk=timer.pk, is_new=True)

    def test_should_remove_old_notifications(
        self, mock_send_notification, mock_send_notification_for_timer
    ):
        # given
        timer: Timer = TimerFactory()
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED
        )
        notification_old = ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=timer.date + dt.timedelta(minutes=5),
            notification_date=timer.date - dt.timedelta(minutes=5),
        )

        # when
        schedule_notifications_for_timer(timer_pk=timer.pk, is_new=True)

        # then
        self.assertTrue(
            timer.scheduled_notifications.filter(notification_rule=rule).exists()
        )
        self.assertFalse(
            ScheduledNotification.objects.filter(pk=notification_old.pk).exists()
        )

    def test_should_schedule_notification_for_new_timer(
        self, mock_send_notification, mock_send_notification_for_timer
    ):
        # given
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.NEW_TIMER_CREATED
        )
        timer: Timer = TimerFactory()

        # when
        schedule_notifications_for_timer(timer_pk=timer.pk, is_new=True)

        # then
        self.assertTrue(mock_send_notification_for_timer.apply_async.called)
        _, kwargs = mock_send_notification_for_timer.apply_async.call_args
        self.assertEqual(kwargs["kwargs"]["timer_pk"], timer.pk)
        self.assertEqual(kwargs["kwargs"]["notification_rule_pk"], rule.pk)

    def test_no_notification_for_new_timer_if_no_rule(
        self, mock_send_notification, mock_send_notification_for_timer
    ):
        # given
        timer: Timer = TimerFactory()

        # when
        schedule_notifications_for_timer(timer_pk=timer.pk, is_new=True)

        # then
        self.assertFalse(mock_send_notification_for_timer.apply_async.called)

    def test_should_abort_when_outdated(
        self, mock_send_notification, mock_send_notification_for_timer
    ):
        # given
        timer: Timer = TimerFactory(date=now() - dt.timedelta(hours=1))

        # when
        schedule_notifications_for_timer(timer_pk=timer.pk, is_new=True)

        # then
        self.assertFalse(mock_send_notification_for_timer.apply_async.called)


@patch(MODULE_PATH + ".send_scheduled_notification", spec=True)
class TestScheduleNotificationForRule(TestCase):
    def test_should_schedule_new_notification_when_rule_matches(
        self, mock_send_notification
    ):
        # given
        timer = TimerFactory()
        rule = NotificationRuleFactory()

        # when
        schedule_notifications_for_rule(rule.pk)

        # then
        self.assertTrue(
            timer.scheduled_notifications.filter(notification_rule=rule).exists()
        )

    def test_should_remove_old_notifications(self, mock_send_notification):
        # given
        timer: Timer = TimerFactory()
        rule = NotificationRuleFactory()
        notification_old = ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=timer.date + dt.timedelta(minutes=5),
            notification_date=timer.date - dt.timedelta(minutes=5),
        )

        # when
        schedule_notifications_for_rule(rule.pk)

        # then
        self.assertTrue(
            timer.scheduled_notifications.filter(notification_rule=rule).exists()
        )
        self.assertFalse(
            ScheduledNotification.objects.filter(pk=notification_old.pk).exists()
        )

    def test_abort_when_has_the_wrong_trigger(self, mock_send_notification):
        # given
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.NEW_TIMER_CREATED
        )

        # when
        schedule_notifications_for_rule(rule.pk)

        # then
        self.assertFalse(mock_send_notification.apply_async.called)


@patch(MODULE_PATH + ".send_scheduled_notification", spec=True)
class TestDispatchScheduledNotifications(TestCase):
    def test_should_dispatch_due_notification(self, mock_send_scheduled_notification):
        # given
        timer = TimerFactory()
        rule = NotificationRuleFactory()
        scheduled_notification = ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=timer.date,
            notification_date=now() - dt.timedelta(minutes=1),
        )

        # when
        dispatch_scheduled_notifications()

        # then
        self.assertTrue(mock_send_scheduled_notification.apply_async.called)
        _, kwargs = mock_send_scheduled_notification.apply_async.call_args
        self.assertEqual(
            kwargs["kwargs"]["scheduled_notification_pk"], scheduled_notification.pk
        )

    def test_should_ignore_notification_not_yet_due(
        self, mock_send_scheduled_notification
    ):
        # given
        timer = TimerFactory()
        rule = NotificationRuleFactory()
        ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=timer.date,
            notification_date=now() + dt.timedelta(minutes=30),
        )

        # when
        dispatch_scheduled_notifications()

        # then
        self.assertFalse(mock_send_scheduled_notification.apply_async.called)

    def test_should_do_nothing_when_no_notifications_scheduled(
        self, mock_send_scheduled_notification
    ):
        # when
        dispatch_scheduled_notifications()

        # then
        self.assertFalse(mock_send_scheduled_notification.apply_async.called)


@patch("structuretimers.models.STRUCTURETIMERS_NOTIFICATIONS_ENABLED", False)
@patch(MODULE_PATH + ".send_messages_for_webhook", spec=True)
class TestSendScheduledNotification(TransactionTestCase):
    def test_should_send_notification(self, mock_send_messages_for_webhook):
        # given
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_15,
        )
        timer = TimerFactory(
            structure_name="Test_1",
            date=now() + dt.timedelta(minutes=30),
        )
        scheduled_notification = ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=now() + dt.timedelta(hours=1),
            notification_date=now() + dt.timedelta(minutes=30),
        )
        mock_task = Mock(spec=Task)
        mock_task.request.id = "my-id-123"

        # when
        send_scheduled_notification_inner = (
            send_scheduled_notification.__wrapped__.__func__
        )
        send_scheduled_notification_inner(
            mock_task, scheduled_notification_pk=scheduled_notification.pk
        )

        # then
        self.assertTrue(mock_send_messages_for_webhook.apply_async.called)

    def test_should_send_notification_for_timer_within_grace_period(
        self, mock_send_messages_for_webhook
    ):
        # given
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_0,
        )
        timer = TimerFactory(
            structure_name="Test_1",
            date=now() - dt.timedelta(minutes=2),
        )
        scheduled_notification = ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=timer.date,
            notification_date=timer.date,
        )
        mock_task = Mock(spec=Task)
        mock_task.request.id = "my-id-123"

        # when
        send_scheduled_notification_inner = (
            send_scheduled_notification.__wrapped__.__func__
        )
        send_scheduled_notification_inner(
            mock_task, scheduled_notification_pk=scheduled_notification.pk
        )

        # then
        self.assertTrue(mock_send_messages_for_webhook.apply_async.called)

    @patch(MODULE_PATH + ".Timer.send_notification")
    def test_should_use_past_tense_when_timer_already_elapsed(
        self, mock_send_notification, mock_send_messages_for_webhook
    ):
        # given
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_0,
        )
        timer = TimerFactory(
            structure_name="Test_1",
            date=now() - dt.timedelta(minutes=2),
        )
        scheduled_notification = ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=timer.date,
            notification_date=timer.date,
        )
        mock_task = Mock(spec=Task)
        mock_task.request.id = "my-id-123"

        # when
        send_scheduled_notification_inner = (
            send_scheduled_notification.__wrapped__.__func__
        )
        send_scheduled_notification_inner(
            mock_task, scheduled_notification_pk=scheduled_notification.pk
        )

        # then
        self.assertTrue(mock_send_notification.called)
        _, kwargs = mock_send_notification.call_args
        self.assertIn("has already elapsed", kwargs["content"])

    def test_discard_notification_when_rule_disabled(
        self, mock_send_messages_for_webhook
    ):
        # given
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_15,
            is_enabled=False,
        )
        timer = TimerFactory(
            structure_name="Test_1",
            date=now() + dt.timedelta(minutes=30),
        )
        scheduled_notification = ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=now() + dt.timedelta(hours=1),
            notification_date=now() + dt.timedelta(minutes=30),
        )
        mock_task = Mock(spec=Task)
        mock_task.request.id = "my-id-123"

        # when
        send_scheduled_notification_inner = (
            send_scheduled_notification.__wrapped__.__func__
        )
        send_scheduled_notification_inner(
            mock_task, scheduled_notification_pk=scheduled_notification.pk
        )

        # then
        self.assertFalse(mock_send_messages_for_webhook.apply_async.called)

    def test_should_ignore_when_notification_was_deleted(
        self, mock_send_messages_for_webhook
    ):
        # given
        mock_task = Mock(spec=Task)
        mock_task.request.id = "my-id-123"

        # when
        send_scheduled_notification_inner = (
            send_scheduled_notification.__wrapped__.__func__
        )
        send_scheduled_notification_inner(mock_task, scheduled_notification_pk=666)

        # then
        self.assertFalse(mock_send_messages_for_webhook.apply_async.called)

    def test_should_abort_when_webhook_disabled(self, mock_send_messages_for_webhook):
        # given
        webhook = DiscordWebhookFactory(is_enabled=False)
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_15,
            is_enabled=False,
            webhook=webhook,
        )
        timer = TimerFactory(
            structure_name="Test_1",
            date=now() + dt.timedelta(minutes=30),
        )
        scheduled_notification = ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=now() + dt.timedelta(hours=1),
            notification_date=now() + dt.timedelta(minutes=30),
        )
        mock_task = Mock(spec=Task)
        mock_task.request.id = "my-id-123"

        # when
        send_scheduled_notification_inner = (
            send_scheduled_notification.__wrapped__.__func__
        )
        send_scheduled_notification_inner(
            mock_task, scheduled_notification_pk=scheduled_notification.pk
        )

        # then
        self.assertFalse(mock_send_messages_for_webhook.apply_async.called)

    def test_should_discard_when_timer_is_outdated(
        self, mock_send_messages_for_webhook
    ):
        # given
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_15,
            is_enabled=False,
        )
        timer = TimerFactory(
            structure_name="Test_1", date=now() - dt.timedelta(hours=1)
        )
        scheduled_notification = ScheduledNotificationFactory(
            timer=timer,
            notification_rule=rule,
            timer_date=now() + dt.timedelta(hours=1),
            notification_date=now() + dt.timedelta(minutes=30),
        )
        mock_task = Mock(spec=Task)
        mock_task.request.id = "my-id-123"

        # when
        send_scheduled_notification_inner = (
            send_scheduled_notification.__wrapped__.__func__
        )
        send_scheduled_notification_inner(
            mock_task, scheduled_notification_pk=scheduled_notification.pk
        )

        # then
        self.assertFalse(mock_send_messages_for_webhook.apply_async.called)


@patch(MODULE_PATH + ".send_messages_for_webhook", spec=True)
class TestNotifyAboutNewTimer(TestCase):
    def test_should_notify_about_new_timer(self, mock_send_messages_for_webhook):
        # given
        webhook = DiscordWebhookFactory()
        timer = TimerFactory()
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.NEW_TIMER_CREATED, webhook=webhook
        )

        # when
        notify_about_new_timer(timer.pk, rule.pk)

        # then
        self.assertTrue(mock_send_messages_for_webhook.apply_async.called)
        _, kwargs = mock_send_messages_for_webhook.apply_async.call_args
        self.assertListEqual(kwargs["args"], [webhook.pk])

    def test_should_notify_about_timer_when_rule_does_not_exist(
        self, mock_send_messages_for_webhook
    ):
        # given
        webhook = DiscordWebhookFactory()
        timer: Timer = TimerFactory()
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED, webhook=webhook
        )

        # when
        notify_about_new_timer(timer.pk, rule.pk)

        # then
        self.assertTrue(mock_send_messages_for_webhook.apply_async.called)
        _, kwargs = mock_send_messages_for_webhook.apply_async.call_args
        self.assertListEqual(kwargs["args"], [webhook.pk])

    def test_should_abort_when_rule_disabled(self, mock_send_messages_for_webhook):
        # given
        timer = TimerFactory()
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.NEW_TIMER_CREATED,
            is_enabled=False,
        )
        # when
        notify_about_new_timer(timer.pk, rule.pk)

        # then
        self.assertFalse(mock_send_messages_for_webhook.apply_async.called)


@patch(MODULE_PATH + ".Timer.objects.delete_obsolete", spec=True)
class TestHousekeeping(TestCase):
    def test_should_run_housekeeping(self, mock_delete_obsolete):
        # given
        mock_delete_obsolete.return_value = 1
        # when
        housekeeping()
        # then
        self.assertTrue(mock_delete_obsolete.called)


@patch(MODULE_PATH + ".calc_timer_distances_for_staging_system", spec=True)
class TestTimerDistancesForAllStagingSystems(TestCase):
    def test_should_calc_distances(self, mock_calc_timer_distances_for_staging_system):
        # given
        timer = TimerFactory(date=now() + dt.timedelta(minutes=30))
        StagingSystemFactory()

        # when
        calc_timer_distances_for_all_staging_systems(timer.pk)

        # then
        self.assertEqual(
            mock_calc_timer_distances_for_staging_system.apply_async.call_count, 1
        )
