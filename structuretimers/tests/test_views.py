import datetime as dt
from http import HTTPStatus
from typing import Optional, Set
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.urls import reverse
from django.utils.timezone import now

from app_utils.testdata_factories import (
    EveAllianceInfoFactory,
    EveCharacterFactory,
    EveCorporationInfoFactory,
)
from app_utils.testing import (
    NoSocketsTestCase,
    json_response_to_dict,
    json_response_to_python,
)

from structuretimers.models import Timer
from structuretimers.tests.testdata.factory import (
    CitadelTypeFactory,
    DistancesFromStagingFactory,
    EveSolarSystemFactory,
    StagingSystemFactory,
    TimerFactory,
    UserMainFactory,
    UserWithAccessFactory,
    UserWithCreateFactory,
    UserWithManageFactory,
)

MODELS_PATH = "structuretimers.models"


class TestViewBase(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_1 = UserWithAccessFactory()
        cls.user_2 = UserWithManageFactory()
        cls.user_3 = UserWithAccessFactory()

        cls.timer_1 = TimerFactory(
            structure_name="Timer 1",
            location_details="Near the star",
            date=now() + dt.timedelta(hours=4),
            eve_character=cls.character_1,
            eve_corporation=cls.corporation_1,
            user=cls.user_1,
            eve_solar_system=cls.system_abune,
            structure_type=cls.type_astrahus,
            owner_name="Big Boss",
            details_image_url="http://www.example.com/dummy.png",
            details_notes="Some notes",
        )
        cls.timer_2 = TimerFactory(
            structure_name="Timer 2",
            date=now() - dt.timedelta(hours=8),
            eve_character=cls.character_1,
            eve_corporation=cls.corporation_1,
            user=cls.user_1,
            eve_solar_system=cls.system_abune,
            structure_type=cls.type_raitaru,
            is_important=True,
        )
        cls.timer_3 = TimerFactory(
            structure_name="Timer 3",
            date=now() - dt.timedelta(hours=8),
            eve_character=cls.character_1,
            eve_corporation=cls.corporation_1,
            user=cls.user_1,
            eve_solar_system=cls.system_enaluri,
            structure_type=cls.type_astrahus,
        )
        cls.timer_4 = TimerFactory(
            timer_type=Timer.Type.PRELIMINARY,
            structure_name="Timer 4",
            eve_character=cls.character_1,
            eve_corporation=cls.corporation_1,
            user=cls.user_1,
        )


class TestTimerList_SelectedStagingSystem(NoSocketsTestCase):
    def test_should_load_multi_select_filter_assets(self):
        self.client.force_login(UserWithAccessFactory())

        response = self.client.get("/structuretimers/")

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "select2.min.js")
        self.assertContains(response, 'data-titleAll="All"')
        self.assertContains(response, 'data-isNightMode="false"')
        self.assertNotContains(response, "filterDropDown.min.js")

    def test_should_open_with_main_staging_system(self):
        # given
        StagingSystemFactory()
        staging_system = StagingSystemFactory(is_main=True)
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get("/structuretimers/")

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(
            response.context_data["selected_staging_system"], staging_system
        )

    def test_should_open_with_first_staging_system_when_there_is_no_main(self):
        # given
        staging_system = StagingSystemFactory()
        StagingSystemFactory()
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get("/structuretimers/")

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(
            response.context_data["selected_staging_system"], staging_system
        )

    def test_should_open_without_staging_system(self):
        # given
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get("/structuretimers/")

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertIsNone(response.context_data["selected_staging_system"])

    def test_should_ignore_wrong_staging_system_name(self):
        # given
        staging_system = StagingSystemFactory(is_main=True)
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get("/structuretimers/?staging=invalid_name")

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(
            response.context_data["selected_staging_system"], staging_system
        )

    def test_should_handle_multiple_invalid_staging_systems(self):
        # given
        StagingSystemFactory(eve_solar_system=None)
        StagingSystemFactory(eve_solar_system=None)
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get("/structuretimers/")

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertIsNone(response.context_data["selected_staging_system"])

    def test_should_handle_multiple_invalid_staging_systems_with_main(self):
        # given
        StagingSystemFactory(eve_solar_system=None, is_main=True)
        StagingSystemFactory(eve_solar_system=None)
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get("/structuretimers/")

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertIsNone(response.context_data["selected_staging_system"])


def json_response_pks(response: HttpResponse) -> Set[int]:
    return set(json_response_to_dict(response).keys())


class TestTimerListData(NoSocketsTestCase):
    view_name = "structuretimers:timer_list_data"

    def _get_timer_list_data(
        self, tab_name: str = "current", user: Optional[User] = None
    ):
        if not user:
            user = self.user_1
        self.client.force_login(user)
        return self.client.get(
            reverse("structuretimers:timer_list_data", args=[tab_name])
        )

    def _get_timer_list_data_ids(
        self, tab_name: str = "current", user: Optional[User] = None
    ) -> set:
        response = self._get_timer_list_data(tab_name, user)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        return set(json_response_to_dict(response).keys())

    # def test_timer_list_view_loads(self):
    #     request = self.factory.get(reverse("structuretimers:timer_list"))
    #     request.user = self.user_1
    #     response = views.timer_list(request)
    #     self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_should_return_current_timers(self):
        # given
        timer = TimerFactory()
        TimerFactory(date=now() - dt.timedelta(hours=8))
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get(reverse(self.view_name, args=["current"]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertSetEqual(json_response_pks(response), {timer.id})

    def test_should_return_past_timers(self):
        # given
        TimerFactory()
        timer = TimerFactory(date=now() - dt.timedelta(hours=8))
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get(reverse(self.view_name, args=["past"]))

        # then
        self.assertSetEqual(json_response_pks(response), {timer.id})

    def test_should_return_preliminary_timers(self):
        # given
        TimerFactory()
        timer = TimerFactory(timer_type=Timer.Type.PRELIMINARY)
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get(reverse(self.view_name, args=["preliminary"]))

        # then
        self.assertSetEqual(json_response_pks(response), {timer.id})

    def test_should_not_give_access_without_basic_permission(self):
        # given
        TimerFactory()
        self.client.force_login(UserMainFactory())

        # when
        response = self.client.get(reverse(self.view_name, args=["current"]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_should_return_corp_restricted_for_corp_member_only(self):
        # given
        user = UserWithAccessFactory()
        ec = EveCorporationInfoFactory(
            corporation_id=user.profile.main_character.corporation_id
        )
        timer = TimerFactory(
            visibility=Timer.Visibility.CORPORATION, eve_corporation=ec
        )
        TimerFactory(visibility=Timer.Visibility.CORPORATION)
        self.client.force_login(user)

        # when
        response = self.client.get(reverse(self.view_name, args=["current"]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertSetEqual(json_response_pks(response), {timer.id})

    def test_show_alliance_restricted_to_alliance_member(self):
        # given
        corp = EveCorporationInfoFactory(create_alliance=True)
        ec = EveCharacterFactory(
            corporation_id=corp.corporation_id, alliance_id=corp.alliance.alliance_id
        )
        user = UserWithAccessFactory(main_character__character=ec)
        timer = TimerFactory(
            visibility=Timer.Visibility.ALLIANCE, eve_alliance=corp.alliance
        )
        TimerFactory(visibility=Timer.Visibility.ALLIANCE)
        self.client.force_login(user)

        # when
        response = self.client.get(reverse(self.view_name, args=["current"]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertSetEqual(json_response_pks(response), {timer.id})

    def test_should_show_opsec_restricted__when_user_has_opsec_permission(self):
        # given
        user = UserMainFactory(
            permissions__=[
                "structuretimers.basic_access",
                "structuretimers.opsec_access",
            ]
        )
        timer = TimerFactory(is_opsec=True)
        self.client.force_login(user)

        # when
        response = self.client.get(reverse(self.view_name, args=["current"]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertSetEqual(json_response_pks(response), {timer.id})

    def test_should_not_show_opsec_restricted__when_user_not_has_opsec_permission(self):
        # given
        user = UserMainFactory(
            permissions__=[
                "structuretimers.basic_access",
            ]
        )
        TimerFactory(is_opsec=True)
        self.client.force_login(user)

        # when
        response = self.client.get(reverse(self.view_name, args=["current"]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertSetEqual(json_response_pks(response), set())

    def test_should_return_corp_timer_to_creator_when_in_different_corp(self):
        # given
        user = UserWithAccessFactory()
        timer = TimerFactory(
            visibility=Timer.Visibility.CORPORATION,
            eve_corporation=EveCorporationInfoFactory(),
            user=user,
        )
        self.client.force_login(user)

        # when
        response = self.client.get(reverse(self.view_name, args=["current"]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertSetEqual(json_response_pks(response), {timer.id})

    def test_should_return_corp_timer_to_creator_when_in_different_alliance(self):
        # given
        user = UserWithAccessFactory()
        timer = TimerFactory(
            visibility=Timer.Visibility.ALLIANCE,
            eve_alliance=EveAllianceInfoFactory(),
            user=user,
        )
        self.client.force_login(user)

        # when
        response = self.client.get(reverse(self.view_name, args=["current"]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertSetEqual(json_response_pks(response), {timer.id})

    def test_should_include_distances_1(self):
        # given
        timer = TimerFactory()
        distances = DistancesFromStagingFactory(timer=timer, light_years=1.2, jumps=3)
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get(
            reverse(self.view_name, args=["current"])
            + f"?staging={distances.staging_system.pk}"
        )

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = json_response_to_dict(response)
        obj = data[timer.id]
        self.assertEqual(obj["distance_light_years"], 1.2)
        self.assertEqual(obj["distance_jumps"], 3)
        self.assertTrue(obj["distance"])

    def test_should_show_most_restrictive_distance_range_badge(self):
        cases = [
            (5.999, "Super", "success"),
            (6.0, "Cap", "primary"),
            (7.0, "Command carrier", "danger"),
            (7.5, None, None),
        ]
        self.client.force_login(UserWithAccessFactory())

        for light_years, expected_label, expected_style in cases:
            with self.subTest(light_years=light_years):
                timer = TimerFactory()
                distances = DistancesFromStagingFactory(
                    timer=timer, light_years=light_years, jumps=3
                )

                response = self.client.get(
                    reverse(self.view_name, args=["current"])
                    + f"?staging={distances.staging_system.pk}"
                )

                self.assertEqual(response.status_code, HTTPStatus.OK)
                data = json_response_to_dict(response)
                distance_display = data[timer.id]["distance"]["display"]
                if expected_label:
                    self.assertIn(expected_label, distance_display)
                    self.assertIn(f"text-bg-{expected_style}", distance_display)
                    for _, other_label, _ in cases:
                        if other_label and other_label != expected_label:
                            self.assertNotIn(other_label, distance_display)
                else:
                    self.assertNotIn("<span class=", distance_display)


class TestDetailView(NoSocketsTestCase):
    view_name = "structuretimers:detail"

    def test_should_show_normal_timer(self):
        # given
        timer = TimerFactory(structure_name="Alpha")
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get(reverse(self.view_name, args=[timer.pk]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertTemplateUsed(response, "structuretimers/timer_detail.html")
        self.assertIn("Alpha", response.rendered_content)

    def test_should_show_preliminary_timer(self):
        # given
        timer = TimerFactory(structure_name="Alpha", timer_type=Timer.Type.PRELIMINARY)
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get(reverse(self.view_name, args=[timer.pk]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertTemplateUsed(response, "structuretimers/timer_detail.html")
        self.assertIn("Alpha", response.rendered_content)

    def test_should_not_show_details_when_no_access(self):
        # given
        timer = TimerFactory(structure_name="Alpha", is_opsec=True)
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get(reverse("structuretimers:detail", args=[timer.pk]))
        # then
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)


class TestSelect2Views_SolarSystems(NoSocketsTestCase):
    view_name = "structuretimers:select2_solar_systems"

    def test_should_return_solar_systems(self):
        # given
        EveSolarSystemFactory(id=30004984, name="Abune")
        EveSolarSystemFactory()
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get(reverse(self.view_name), data={"term": "abu"})

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = json_response_to_python(response)
        self.assertEqual(data, {"results": [{"id": 30004984, "text": "Abune"}]})

    def test_should_return_empty_solar_system_list(self):
        # given
        EveSolarSystemFactory(id=30004984, name="Abune")
        EveSolarSystemFactory()
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get(reverse(self.view_name))

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = json_response_to_python(response)
        self.assertEqual(data, {"results": None})


class TestEditRemoveTimerView(NoSocketsTestCase):
    def _edit_form_data(self, timer: Timer) -> dict:
        return {
            "eve_solar_system_2": str(timer.eve_solar_system_id),
            "structure_type_2": str(timer.structure_type_id),
            "timer_type": Timer.Type.ARMOR,
            "objective": Timer.Objective.UNDEFINED,
            "visibility": Timer.Visibility.UNRESTRICTED,
            "date": timer.date.strftime("%Y-%m-%d %H:%M"),
            "structure_name": "Hacked Name",
        }

    def test_should_not_allow_unauthorized_user_to_delete_timer(self):
        # given
        timer = TimerFactory()
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.post(reverse("structuretimers:delete", args=[timer.pk]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)
        self.assertTrue(Timer.objects.filter(pk=timer.pk).exists())

    def test_should_not_allow_unauthorized_user_to_edit_timer(self):
        # given
        timer = TimerFactory(structure_name="Original Name")
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.post(
            reverse("structuretimers:edit", args=[timer.pk]),
            data=self._edit_form_data(timer),
        )

        # then
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)
        timer.refresh_from_db()
        self.assertEqual(timer.structure_name, "Original Name")

    def test_should_allow_manager_to_delete_any_timer(self):
        # given
        timer = TimerFactory()
        self.client.force_login(UserWithManageFactory())

        # when
        response = self.client.post(reverse("structuretimers:delete", args=[timer.pk]))

        # then
        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        self.assertFalse(Timer.objects.filter(pk=timer.pk).exists())

    @patch(
        "structuretimers.models._task_calc_timer_distances_for_all_staging_systems",
        Mock(),
    )
    @patch("structuretimers.models._task_schedule_notifications_for_timer", Mock())
    def test_should_allow_creator_to_edit_own_timer(self):
        # given
        user = UserWithCreateFactory()
        timer = TimerFactory(user=user, structure_name="Original Name")
        self.client.force_login(user)

        # when
        response = self.client.post(
            reverse("structuretimers:edit", args=[timer.pk]),
            data=self._edit_form_data(timer),
        )

        # then
        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        timer.refresh_from_db()
        self.assertEqual(timer.structure_name, "Hacked Name")


class TestSelect2Views_StructureTypes(NoSocketsTestCase):
    view_name = "structuretimers:select2_structure_types"

    def test_should_return_structure_types(self):
        # given
        CitadelTypeFactory(id=35832, name="Astrahus")
        CitadelTypeFactory()
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get(
            reverse("structuretimers:select2_structure_types"), data={"term": "ast"}
        )

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = json_response_to_python(response)
        self.assertEqual(data, {"results": [{"id": 35832, "text": "Astrahus"}]})

    def test_should_return_empty_structure_types_list(self):
        # given
        CitadelTypeFactory(id=35832, name="Astrahus")
        CitadelTypeFactory()
        self.client.force_login(UserWithAccessFactory())

        # when
        response = self.client.get(reverse("structuretimers:select2_structure_types"))

        # then
        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = json_response_to_python(response)
        self.assertEqual(data, {"results": None})
