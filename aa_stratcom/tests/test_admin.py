from http import HTTPStatus

from django.test import override_settings
from django.urls import reverse

from app_utils.testdata_factories import (
    EveAllianceInfoFactory,
    EveCorporationInfoFactory,
    UserFactory,
)
from app_utils.testing import NoSocketsTestCase

from aa_stratcom.admin import _get_multiselect_display
from aa_stratcom.models import NotificationRule, StagingSystem, Timer

from .testdata.factory import (
    DiscordWebhookFactory,
    EveSolarSystemFactory,
    NotificationRuleFactory,
    StagingSystemFactory,
)


class TestNotificationRule_ChangeList(NoSocketsTestCase):
    def test_can_open_page_normally(self):
        # given
        webhook = DiscordWebhookFactory()
        NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_10,
            webhook=webhook,
        )
        NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_10,
            require_timer_types=[Timer.Type.ARMOR],
            webhook=webhook,
        )
        rule = NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_10,
            webhook=webhook,
        )
        rule.require_corporations.add(EveCorporationInfoFactory())
        NotificationRuleFactory(
            trigger=NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            scheduled_time=NotificationRule.MINUTES_10,
            is_important=NotificationRule.Clause.EXCLUDED,
            webhook=webhook,
        )
        user = UserFactory(is_staff=True, is_superuser=True)
        self.client.force_login(user)

        # user tries to add new notification rule
        add_page = self.client.get(
            reverse("admin:aa_stratcom_notificationrule_changelist")
        )
        self.assertEqual(add_page.status_code, HTTPStatus.OK)


class TestNotificationRule_Validations(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.webhook = DiscordWebhookFactory()
        cls.add_url = reverse("admin:aa_stratcom_notificationrule_add")
        cls.changelist_url = reverse("admin:aa_stratcom_notificationrule_changelist")
        cls.form_data = {
            "is_important": NotificationRule.Clause.ANY,
            "is_opsec": NotificationRule.Clause.ANY,
            "ping_type": NotificationRule.PingType.NONE,
            "scheduled_time": NotificationRule.MINUTES_15,
            "trigger": NotificationRule.Trigger.SCHEDULED_TIME_REACHED,
            "webhook": cls.webhook.pk,
            "_save": "Save",
        }
        cls.user = UserFactory(is_staff=True, is_superuser=True)

    def setUp(self):
        self.client.force_login(self.user)

    def test_can_create_rule(self):
        # given
        form_data = self.form_data | {}

        # when
        response = self.client.post(self.add_url, data=form_data)

        # then
        self.assertRedirects(response, self.changelist_url)
        self.assertEqual(NotificationRule.objects.count(), 1)

    def test_can_not_have_same_options_timer_types(self):
        # given
        form_data = self.form_data | {
            "require_timer_types": [Timer.Type.ANCHORING, Timer.Type.HULL],
            "exclude_timer_types": [Timer.Type.ANCHORING, Timer.Type.ARMOR],
        }

        # when
        response = self.client.post(self.add_url, data=form_data)

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(NotificationRule.objects.count(), 0)
        form = response.context["adminform"].form
        self.assertTrue(form.errors)

    def test_can_not_have_same_options_objectives(self):
        # given
        form_data = self.form_data | {
            "require_objectives": [Timer.Objective.FRIENDLY, Timer.Objective.HOSTILE],
            "exclude_objectives": [Timer.Objective.FRIENDLY, Timer.Objective.NEUTRAL],
        }

        # when
        response = self.client.post(self.add_url, data=form_data)

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(NotificationRule.objects.count(), 0)
        form = response.context["adminform"].form
        self.assertTrue(form.errors)

    def test_can_not_have_same_options_visibility(self):
        # given
        form_data = self.form_data | {
            "require_visibility": [Timer.Visibility.CORPORATION],
            "exclude_visibility": [Timer.Visibility.CORPORATION],
        }

        # when
        response = self.client.post(self.add_url, data=form_data)

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(NotificationRule.objects.count(), 0)
        form = response.context["adminform"].form
        self.assertTrue(form.errors)

    def test_can_not_have_same_options_corporations(self):
        # given
        corp_1 = EveCorporationInfoFactory()
        corp_2 = EveCorporationInfoFactory()

        form_data = self.form_data | {
            "require_corporations": [corp_1.pk, corp_2.pk],
            "exclude_corporations": [corp_1.pk],
        }

        # when
        response = self.client.post(self.add_url, data=form_data)

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(NotificationRule.objects.count(), 0)
        form = response.context["adminform"].form
        self.assertTrue(form.errors)

    def test_can_not_have_same_options_alliances(self):
        # given
        all_1 = EveAllianceInfoFactory()
        all_2 = EveAllianceInfoFactory()
        form_data = self.form_data | {
            "require_alliances": [all_1.pk, all_2.pk],
            "exclude_alliances": [all_1.pk],
        }

        # when
        response = self.client.post(self.add_url, data=form_data)

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(NotificationRule.objects.count(), 0)
        form = response.context["adminform"].form
        self.assertTrue(form.errors)


@override_settings(CELERY_ALWAYS_EAGER=True, CELERY_EAGER_PROPAGATES_EXCEPTIONS=True)
class TestStagingSystemAdmin(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = UserFactory(is_staff=True, is_superuser=True)
        cls.url_add = reverse("admin:aa_stratcom_stagingsystem_add")

    def test_should_create_new_staging_system(self):
        # given
        self.client.force_login(self.user)
        solar_system = EveSolarSystemFactory()

        # when
        res = self.client.post(self.url_add, data={"eve_solar_system": solar_system.pk})

        # then
        self.assertEqual(res.status_code, HTTPStatus.FOUND)
        self.assertEqual(StagingSystem.objects.count(), 1)
        obj = StagingSystem.objects.first()
        self.assertEqual(obj.eve_solar_system, solar_system)
        self.assertFalse(obj.is_main)

    def test_should_ensure_only_one_obj_is_main(self):
        # given
        self.client.force_login(self.user)
        solar_system_1 = EveSolarSystemFactory()
        StagingSystemFactory(eve_solar_system=solar_system_1, is_main=True)
        solar_system_2 = EveSolarSystemFactory()

        # when
        res = self.client.post(
            self.url_add,
            data={"eve_solar_system": solar_system_2.pk, "is_main": True},
        )

        # then
        self.assertEqual(res.status_code, HTTPStatus.FOUND)
        self.assertEqual(
            StagingSystem.objects.filter(is_main=True).get().eve_solar_system,
            solar_system_2,
        )


class TestGetMultiselectDisplay(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.choices = [
            (1, "alpha"),
            (2, "bravo"),
        ]

    def test_returns_value_if_found(self):
        self.assertEqual(_get_multiselect_display(1, self.choices), "alpha")
        self.assertEqual(_get_multiselect_display(2, self.choices), "bravo")

    def test_raises_exception_if_not_found(self):
        with self.assertRaises(ValueError):
            _get_multiselect_display(3, self.choices)
