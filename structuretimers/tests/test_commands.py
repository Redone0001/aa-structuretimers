from io import StringIO
from unittest import skipIf
from unittest.mock import Mock, patch

from django.core.management import call_command
from eveuniverse.tests.testdata.factories_2 import EveTypeFactory

from allianceauth.timerboard.models import Timer as AuthTimer
from app_utils.django import app_labels
from app_utils.testing import NoSocketsTestCase

from structuretimers.constants import EveTypeId
from structuretimers.management.commands.structuretimers_migrate_timers import (
    _STRUCTURE_MAP,
)
from structuretimers.models import Timer
from structuretimers.tests.testdata.factory import (
    AuthTimerFactory,
    EveSolarSystemLowSecFactory,
    UserWithAccessFactory,
)
from structuretimers.tests.utils import isolated_subtest

PACKAGE_PATH = "structuretimers.management.commands"
MODELS_PATH = "structuretimers.models"


@skipIf("timerboard" not in app_labels(), "timerboard not installed")
@patch(MODELS_PATH + "._task_calc_timer_distances_for_all_staging_systems", Mock())
@patch(MODELS_PATH + ".STRUCTURETIMERS_NOTIFICATIONS_ENABLED", False)
class TestMigrateTimers(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.system_amamake = EveSolarSystemLowSecFactory(id=30002537, name="Amamake")
        cls.user = UserWithAccessFactory()

        for name, type_id in _STRUCTURE_MAP.items():
            EveTypeFactory(id=type_id, name=name)

        Timer.objects.all().delete()
        AuthTimer.objects.all().delete()

    def setUp(self) -> None:
        self.out = StringIO()

    def test_can_convert_all_fields(self):
        # given
        character_1 = self.user.profile.main_character
        corporation_1 = self.user.profile.main_character.corporation
        auth_timer = AuthTimerFactory(
            eve_character=character_1,
            eve_corp=corporation_1,
            objective=AuthTimer.Objective.FRIENDLY,
            structure=AuthTimer.Structure.ASTRAHUS,
            timer_type=AuthTimer.TimerType.ARMOR,
            system="Amamake",
            user=self.user,
            planet_moon="moon location",
        )

        # when
        call_command("structuretimers_migrate_timers", stdout=self.out)

        # then
        self.assertEqual(Timer.objects.exclude(timer_type=Timer.Type.PRELIMINARY).count(), 1)
        new_timer = Timer.objects.exclude(timer_type=Timer.Type.PRELIMINARY).first()
        self.assertEqual(new_timer.date, auth_timer.eve_time)
        self.assertEqual(new_timer.details_notes, auth_timer.details)
        self.assertEqual(new_timer.eve_character, character_1)
        self.assertEqual(new_timer.eve_corporation, corporation_1)
        self.assertEqual(new_timer.eve_solar_system, self.system_amamake)
        self.assertEqual(new_timer.location_details, auth_timer.planet_moon)
        self.assertEqual(new_timer.objective, Timer.Objective.FRIENDLY)
        self.assertEqual(new_timer.structure_type.id, EveTypeId.ASTRAHUS)
        self.assertEqual(new_timer.timer_type, Timer.Type.ARMOR)
        self.assertEqual(new_timer.user, self.user)

    def test_should_handle_all_timer_types(self):
        for timer_type in AuthTimer.TimerType.values:
            with self.subTest(timer_type=timer_type), isolated_subtest():
                # given
                AuthTimerFactory(
                    timer_type=timer_type,
                    user=self.user,
                )

                # when
                call_command("structuretimers_migrate_timers", stdout=self.out)

                # then
                self.assertEqual(Timer.objects.exclude(timer_type=Timer.Type.PRELIMINARY).count(), 1, msg=timer_type)

    def test_should_handle_all_objectives(self):
        for objective in AuthTimer.Objective.values:
            with self.subTest(objective=objective), isolated_subtest():
                # given
                AuthTimerFactory(
                    objective=objective,
                    user=self.user,
                )

                # when
                call_command("structuretimers_migrate_timers", stdout=self.out)

                # then
                self.assertEqual(Timer.objects.exclude(timer_type=Timer.Type.PRELIMINARY).count(), 1, msg=objective)

    def test_should_handle_all_structure_types(self):
        supported_values = set(AuthTimer.Structure.values) - {AuthTimer.Structure.OTHER}
        for structure in supported_values:
            with self.subTest(structure=structure), isolated_subtest():
                # given
                AuthTimerFactory(
                    structure=structure,
                    user=self.user,
                )

                # when
                call_command("structuretimers_migrate_timers", stdout=self.out)

                # then
                self.assertEqual(Timer.objects.exclude(timer_type=Timer.Type.PRELIMINARY).count(), 1, msg=structure)

    def test_final_corp_timer(self):
        # given
        AuthTimerFactory(
            corp_timer=True,
            user=self.user,
        )

        # when
        call_command("structuretimers_migrate_timers", stdout=self.out)

        # then
        self.assertEqual(Timer.objects.exclude(timer_type=Timer.Type.PRELIMINARY).count(), 1)
        new_timer = Timer.objects.exclude(timer_type=Timer.Type.PRELIMINARY).first()
        self.assertEqual(new_timer.visibility, Timer.Visibility.CORPORATION)

    def test_moon_mining(self):
        # given
        AuthTimerFactory(
            structure=AuthTimer.Structure.MOONPOP,
            timer_type=AuthTimer.TimerType.UNSPECIFIED,
            user=self.user,
        )

        # when
        call_command("structuretimers_migrate_timers", stdout=self.out)

        # then
        new_timer = Timer.objects.exclude(timer_type=Timer.Type.PRELIMINARY).first()
        self.assertEqual(new_timer.timer_type, Timer.Type.MOONMINING)
        self.assertEqual(new_timer.structure_type.id, EveTypeId.ATHANOR)

    def test_should_skip_timers_with_unknown_system(self):
        # given
        AuthTimerFactory(
            system="Invalid",
            user=self.user,
        )

        # when
        call_command("structuretimers_migrate_timers", stdout=self.out)

        self.assertFalse(Timer.objects.all().exists())

    def test_should_skip_timers_with_unknown_structure(self):
        # given
        AuthTimerFactory(
            structure="Invalid",
            user=self.user,
        )

        # when
        call_command("structuretimers_migrate_timers", stdout=self.out)

        self.assertFalse(Timer.objects.all().exists())

    def test_should_not_create_duplicates(self):
        # given
        AuthTimerFactory()

        # when
        call_command("structuretimers_migrate_timers", stdout=self.out)
        call_command("structuretimers_migrate_timers", stdout=self.out)

        # then
        self.assertEqual(Timer.objects.exclude(timer_type=Timer.Type.PRELIMINARY).count(), 1)
