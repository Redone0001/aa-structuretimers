from datetime import timedelta
from http import HTTPStatus
from typing import Any, Dict
from unittest.mock import Mock, patch

from django.test import override_settings
from django.urls import reverse
from django.utils.timezone import now

from app_utils.testing import NoSocketsTestCase

from structuretimers.models import ScheduledNotification, Timer
from structuretimers.tasks import send_test_message_to_webhook
from structuretimers.tests.testdata.factory import (
    CitadelTypeFactory,
    DiscordWebhookFactory,
    EveSolarSystemLowSecFactory,
    NotificationRuleFactory,
    TimerFactory,
    UserNoAccessFactory,
    UserWithAccessFactory,
    UserWithCreateFactory,
    UserWithManageFactory,
)

MODELS_PATH = "structuretimers.models"
FORMS_PATH = "structuretimers.forms"
TASKS_PATH = "structuretimers.tasks"

# TODO: Rewrite tests to work also with AA4


@patch(MODELS_PATH + "._task_calc_timer_distances_for_all_staging_systems", Mock())
@override_settings(CELERY_ALWAYS_EAGER=True, CELERY_EAGER_PROPAGATES_EXCEPTIONS=True)
class TestCreateNewTimer(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.add_timer_url = reverse("structuretimers:add")
        cls.timer_list_url = reverse("structuretimers:timer_list")

    def test_user_with_permission_can_add_timer(self):
        # given
        solar_system = EveSolarSystemLowSecFactory()
        structure_type = CitadelTypeFactory()
        form_data = {
            "structure_name": "Timer 4",
            "eve_solar_system_2": [str(solar_system.id)],
            "structure_type_2": [str(structure_type.id)],
            "timer_type": Timer.Type.ANCHORING,
            "days_left": 1,
            "hours_left": 2,
            "minutes_left": 3,
            "objective": Timer.Objective.HOSTILE,
            "visibility": Timer.Visibility.UNRESTRICTED,
            "_save": "Save",
        }
        self.client.force_login(UserWithCreateFactory())

        # when
        response = self.client.post(self.add_timer_url, data=form_data)

        # assert results
        self.assertRedirects(response, self.timer_list_url)
        obj = Timer.objects.get(structure_name="Timer 4")
        self.assertEqual(obj.eve_solar_system, solar_system)
        self.assertEqual(obj.structure_type, structure_type)
        self.assertEqual(obj.timer_type, Timer.Type.ANCHORING)
        timer_date = now() + timedelta(days=1, hours=2, minutes=3)
        self.assertAlmostEqual(obj.date, timer_date, delta=timedelta(seconds=10))

    def test_user_without_permission_can_not_add_timer(self):
        # given
        solar_system = EveSolarSystemLowSecFactory()
        structure_type = CitadelTypeFactory()
        form_data = {
            "structure_name": "Timer 4",
            "eve_solar_system_2": [str(solar_system.id)],
            "structure_type_2": [str(structure_type.id)],
            "timer_type": Timer.Type.ANCHORING,
            "days_left": 1,
            "hours_left": 2,
            "minutes_left": 3,
            "objective": Timer.Objective.HOSTILE,
            "visibility": Timer.Visibility.UNRESTRICTED,
            "_save": "Save",
        }
        self.client.force_login(UserNoAccessFactory())

        # when
        response = self.client.post(self.add_timer_url, data=form_data)

        # then
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)


@patch(MODELS_PATH + "._task_calc_timer_distances_for_all_staging_systems", Mock())
@override_settings(CELERY_ALWAYS_EAGER=True, CELERY_EAGER_PROPAGATES_EXCEPTIONS=True)
class TestQuickCreateNewTimer(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.add_fast_timer_url = reverse("structuretimers:add_fast")
        cls.timer_list_url = reverse("structuretimers:timer_list")

    def test_user_with_permission_can_open_quick_add_page(self):
        self.client.force_login(UserWithCreateFactory())

        response = self.client.get(self.add_fast_timer_url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertTemplateUsed(response, "structuretimers/timer_fast_create_form.html")
        self.assertContains(response, "EVE timer text")
        self.assertNotContains(response, "Days Remaining")

    def test_user_with_permission_can_quick_add_timer(self):
        solar_system = EveSolarSystemLowSecFactory(name="SVM-3K")
        structure_type = CitadelTypeFactory()
        self.client.force_login(UserWithCreateFactory())
        form_data = {
            "pasted_timer": (
                "SVM-3K - kongbao\n" "17 km\n" "Reinforced until 2026.09.05 03:47:08"
            ),
            "structure_type_2": str(structure_type.id),
            "timer_type": Timer.Type.HULL,
            "owner_name": "SoyuzMultFilm",
            "objective": Timer.Objective.HOSTILE,
        }

        response = self.client.post(self.add_fast_timer_url, data=form_data)

        self.assertRedirects(response, self.timer_list_url)
        timer = Timer.objects.get(structure_name="kongbao")
        self.assertEqual(timer.eve_solar_system, solar_system)
        self.assertEqual(timer.structure_type, structure_type)
        self.assertEqual(timer.timer_type, Timer.Type.HULL)
        self.assertEqual(timer.owner_name, "SoyuzMultFilm")
        self.assertEqual(timer.objective, Timer.Objective.HOSTILE)
        self.assertEqual(timer.date.isoformat(), "2026-09-05T03:47:08+00:00")

    def test_user_without_permission_can_not_open_quick_add_page(self):
        self.client.force_login(UserNoAccessFactory())

        response = self.client.get(self.add_fast_timer_url)

        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)


@patch(MODELS_PATH + "._task_calc_timer_distances_for_all_staging_systems", Mock())
@override_settings(CELERY_ALWAYS_EAGER=True, CELERY_EAGER_PROPAGATES_EXCEPTIONS=True)
class TestEditTimer(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.timer_list_url = reverse("structuretimers:timer_list")

    def test_user_with_permission_can_open_page(self):
        # given
        user = UserWithCreateFactory()
        timer: Timer = TimerFactory(user=user)
        self.client.force_login(user)

        # when
        response = self.client.get(reverse("structuretimers:edit", args=[timer.pk]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertTemplateUsed(response, "structuretimers/timer_edit.html")
        self.assertContains(response, timer.eve_solar_system.name)

    def test_user_can_change_his_own_timer(self):
        # given
        user = UserWithCreateFactory()
        timer: Timer = TimerFactory(user=user)
        self.client.force_login(user)

        owner_name = "The Boys"
        form_data = _make_form_data_from_timer(timer) | {
            "owner_name": owner_name,
        }

        # when
        response = self.client.post(
            reverse("structuretimers:edit", args=[timer.pk]), data=form_data
        )

        # then
        self.assertRedirects(response, self.timer_list_url)
        timer.refresh_from_db()
        self.assertEqual(timer.owner_name, owner_name)

    def test_user_without_permission_can_not_open_page(self):
        # given
        user = UserWithCreateFactory()
        timer = TimerFactory()
        self.client.force_login(user)

        # when
        response = self.client.get(reverse("structuretimers:edit", args=[timer.pk]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_manager_can_edit_other_timers(self):
        # given
        user = UserWithManageFactory()
        timer: Timer = TimerFactory()
        self.client.force_login(user)

        owner_name = "The Boys"
        form_data = _make_form_data_from_timer(timer) | {
            "owner_name": owner_name,
        }

        # when
        response = self.client.post(
            reverse("structuretimers:edit", args=[timer.pk]), data=form_data
        )

        # then
        self.assertRedirects(response, self.timer_list_url)
        timer.refresh_from_db()
        self.assertEqual(timer.owner_name, owner_name)

    def test_manager_can_not_edit_other_timers_when_corp_restricted(self):
        # given
        timer: Timer = TimerFactory(visibility=Timer.Visibility.CORPORATION)
        user = UserWithManageFactory()
        self.client.force_login(user)

        # when
        response = self.client.get(reverse("structuretimers:edit", args=[timer.pk]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_manager_can_not_edit_other_timers_when_opsec(self):
        # setup
        timer = TimerFactory(is_opsec=True)
        user = UserWithManageFactory()
        self.client.force_login(user)

        # when
        response = self.client.get(reverse("structuretimers:edit", args=[timer.pk]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)


def _make_form_data_from_timer(timer: Timer) -> Dict[str, Any]:
    form_data = {
        "date": timer.date.isoformat(),
        "details_image_url": timer.details_image_url or "",
        "details_notes": timer.details_notes or "",
        "eve_alliance": timer.eve_alliance.pk if timer.eve_alliance else "",
        "eve_character": timer.eve_character.pk if timer.eve_character else "",
        "eve_corporation": timer.eve_corporation.pk if timer.eve_corporation else "",
        "eve_solar_system_2": [timer.eve_solar_system.pk],
        "is_important": timer.is_important,
        "is_opsec": timer.is_opsec,
        "location_details": timer.location_details or "",
        "objective": timer.objective,
        "owner_name": timer.owner_name,
        "structure_name": timer.structure_name,
        "structure_type_2": [timer.structure_type.pk],
        "timer_type": timer.timer_type,
        # "user": timer.user.pk if timer.user else "",
        "visibility": timer.visibility,
    }
    return form_data


@patch(MODELS_PATH + "._task_calc_timer_distances_for_all_staging_systems", Mock())
@override_settings(CELERY_ALWAYS_EAGER=True, CELERY_EAGER_PROPAGATES_EXCEPTIONS=True)
class TestDeleteTimer(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.timer_list_url = reverse("structuretimers:timer_list")

    def test_user_can_delete_own_timer(self):
        # given
        user = UserWithCreateFactory()
        timer: Timer = TimerFactory(user=user)
        self.client.force_login(user)

        # when
        response = self.client.get(reverse("structuretimers:delete", args=[timer.pk]))
        self.assertEqual(response.status_code, HTTPStatus.OK)

        response = self.client.post(reverse("structuretimers:delete", args=[timer.pk]))

        # then
        self.assertRedirects(response, self.timer_list_url)
        self.assertFalse(Timer.objects.filter(pk=timer.pk).exists())

    def test_user_can_not_delete_other_timer(self):
        # given
        user = UserWithCreateFactory()
        timer: Timer = TimerFactory()
        self.client.force_login(user)

        # when
        response = self.client.get(reverse("structuretimers:delete", args=[timer.pk]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_manager_can_delete_timer_from_other(self):
        # given
        user = UserWithManageFactory()
        timer: Timer = TimerFactory()
        self.client.force_login(user)

        # when
        response = self.client.get(reverse("structuretimers:delete", args=[timer.pk]))
        self.assertEqual(response.status_code, HTTPStatus.OK)

        response = self.client.post(reverse("structuretimers:delete", args=[timer.pk]))

        # then
        self.assertRedirects(response, self.timer_list_url)
        self.assertFalse(Timer.objects.filter(pk=timer.pk).exists())


"""
@patch(MODELS_PATH+ ".sleep", new=lambda x: x)
@patch(MODELS_PATH+ ".dhooks_lite.Webhook.execute")
class TestSendNotifications(LoadTestDataMixin, TestCase):
    def setUp(self) -> None:
        self.webhook = DiscordWebhookFactory(
            name="Dummy", url="http://www.example.com"
        )
        self.rule = NotificationRule.objects.create(minutes=NotificationRule.MINUTES_0)
        self.rule.webhooks.add(self.webhook)

    def test_normal(self, mock_execute):
        TimerFactory(
            structure_name="Test_1",
            eve_solar_system=self.system_abune,
            structure_type=self.type_raitaru,
            date=now() + timedelta(seconds=2),
        )
        sleep(3)
        self.assertEqual(mock_execute.call_count, 1)
"""


@override_settings(CELERY_ALWAYS_EAGER=True, CELERY_EAGER_PROPAGATES_EXCEPTIONS=True)
@patch(MODELS_PATH + ".sleep", new=lambda x: x)
@patch(TASKS_PATH + ".notify", spec=True)
@patch(MODELS_PATH + ".dhooks_lite.Webhook.execute", spec=True)
class TestSendTestMessageToWebhook(NoSocketsTestCase):
    def test_without_user(self, mock_execute, mock_notify):
        # given
        webhook = DiscordWebhookFactory()

        # when
        send_test_message_to_webhook.delay(webhook_pk=webhook.pk)

        # then
        self.assertEqual(mock_execute.call_count, 1)
        self.assertFalse(mock_notify.called)

    def test_with_user(self, mock_execute, mock_notify):
        # given
        webhook = DiscordWebhookFactory()
        user = UserWithAccessFactory()

        # when
        send_test_message_to_webhook.delay(webhook_pk=webhook.pk, user_pk=user.pk)

        # then
        self.assertEqual(mock_execute.call_count, 1)
        self.assertTrue(mock_notify.called)


@override_settings(CELERY_ALWAYS_EAGER=True, CELERY_EAGER_PROPAGATES_EXCEPTIONS=True)
@patch(MODELS_PATH + "._task_calc_timer_distances_for_all_staging_systems", Mock())
class TestTimerSave(NoSocketsTestCase):
    def test_schedule_notifications_for_new_timers_2(self):
        # given
        NotificationRuleFactory()

        # when
        timer = Timer.objects.create(
            date=now() + timedelta(hours=4),
            eve_solar_system=EveSolarSystemLowSecFactory(),
            structure_type=CitadelTypeFactory(),
        )
        # then
        self.assertTrue(ScheduledNotification.objects.filter(timer=timer).exists())
