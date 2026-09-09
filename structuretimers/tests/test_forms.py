from unittest.mock import Mock, patch

from requests.exceptions import ConnectionError as NewConnectionError
from requests.exceptions import HTTPError, Timeout

from app_utils.testing import NoSocketsTestCase

from structuretimers.constants import EveTypeId
from structuretimers.forms import FastTimerForm, TimerForm, parse_eve_timer_text
from structuretimers.models import Timer
from structuretimers.tests.testdata.factory import (
    CitadelTypeFactory,
    EveSolarSystemLowSecFactory,
    RefineryTypeFactory,
    SkyhookTypeFactory,
    UserWithAccessFactory,
)

from .testdata import test_image_filename

FORMS_PATH = "structuretimers.forms"
MODELS_PATH = "structuretimers.models"


def bytes_from_file(filename, chunksize=8192):
    with open(filename, "rb") as f:
        while True:
            chunk = f.read(chunksize)
            if chunk:
                for b in chunk:
                    yield b
            else:
                break


def create_form_data(**kwargs):
    form_data = {
        "eve_solar_system_2": 30004984,
        "structure_type_2": EveTypeId.ASTRAHUS.value,
        "timer_type": Timer.Type.NONE,
        "objective": Timer.Objective.UNDEFINED,
        "visibility": Timer.Visibility.UNRESTRICTED,
    }
    if kwargs:
        form_data.update(kwargs)
    return form_data


def create_fast_form_data(**kwargs):
    form_data = {
        "pasted_timer": (
            "SVM-3K - kongbao\n" "17 km\n" "Reinforced until 2026.09.05 03:47:08"
        ),
        "structure_type_2": EveTypeId.ASTRAHUS.value,
        "timer_type": Timer.Type.ARMOR,
        "owner_name": "SoyuzMultFilm",
        "objective": Timer.Objective.HOSTILE,
    }
    form_data.update(kwargs)
    return form_data


class TestParseEveTimerText(NoSocketsTestCase):
    def test_should_parse_eve_timer_text_as_utc(self):
        parsed = parse_eve_timer_text(
            "SVM-3K - kongbao\r\n" "17 km\r\n" "Reinforced until 2026.09.05 03:47:08"
        )

        self.assertEqual(parsed.solar_system_name, "SVM-3K")
        self.assertEqual(parsed.structure_name, "kongbao")
        self.assertEqual(parsed.date.isoformat(), "2026-09-05T03:47:08+00:00")

    def test_should_ignore_distance_text(self):
        parsed = parse_eve_timer_text(
            "1-SMEB - SoyuzMultFilm\n"
            "Some localized distance text\n"
            "Reinforced until 2026.08.30 18:22:23"
        )

        self.assertEqual(parsed.solar_system_name, "1-SMEB")
        self.assertEqual(parsed.structure_name, "SoyuzMultFilm")

    def test_should_parse_anchoring_timer_text(self):
        parsed = parse_eve_timer_text(
            "BX-VEX - Gorlock's rule continues\n"
            "212 km\n"
            "Anchoring until 2026.09.03 18:57:54"
        )

        self.assertEqual(parsed.solar_system_name, "BX-VEX")
        self.assertEqual(parsed.structure_name, "Gorlock's rule continues")
        self.assertEqual(parsed.date.isoformat(), "2026-09-03T18:57:54+00:00")

    def test_should_reject_invalid_date(self):
        with self.assertRaisesRegex(ValueError, "date or time is invalid"):
            parse_eve_timer_text(
                "SVM-3K - kongbao\n" "17 km\n" "Reinforced until 2026.13.40 25:70:80"
            )


class TestFastTimerFormIsValid(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.solar_system = EveSolarSystemLowSecFactory(name="SVM-3K")
        cls.structure_type = CitadelTypeFactory(
            id=EveTypeId.ASTRAHUS.value, name="Astrahus"
        )

    def test_should_derive_fields_from_eve_timer_text(self):
        form = FastTimerForm(data=create_fast_form_data())

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["eve_solar_system_2"], str(self.solar_system.id)
        )
        self.assertEqual(form.cleaned_data["structure_name"], "kongbao")
        self.assertEqual(
            form.cleaned_data["date"].isoformat(), "2026-09-05T03:47:08+00:00"
        )

    def test_should_default_objective_to_hostile(self):
        form = FastTimerForm()

        self.assertEqual(form.fields["objective"].initial, Timer.Objective.HOSTILE)

    def test_should_only_show_fast_entry_fields(self):
        form = FastTimerForm()

        self.assertEqual(
            [field.name for field in form.visible_fields()],
            list(FastTimerForm.fast_fields),
        )

    def test_should_require_owner(self):
        form = FastTimerForm(data=create_fast_form_data(owner_name=""))

        self.assertFalse(form.is_valid())
        self.assertIn("owner_name", form.errors)

    def test_should_reject_unknown_solar_system(self):
        form = FastTimerForm(
            data=create_fast_form_data(
                pasted_timer=(
                    "NOT-A-SYSTEM - kongbao\n"
                    "17 km\n"
                    "Reinforced until 2026.09.05 03:47:08"
                )
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn("pasted_timer", form.errors)


class TestTimerFormIsValid(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.system_abune = EveSolarSystemLowSecFactory(id=30004984, name="Abune")
        cls.type_astrahus = CitadelTypeFactory(id=35832, name="Astrahus")
        cls.type_athanor = RefineryTypeFactory(id=35835, name="Athanor")
        SkyhookTypeFactory()

    def test_should_accept_normal_timer_with_date_parts(self):
        # given
        form_data = create_form_data(days_left=0, hours_left=3, minutes_left=30)
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())

    def test_should_accept_normal_timer_with_date(self):
        # given
        form_data = create_form_data(date="2022-03-05 20:07")
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())

    def test_should_accept_normal_timer_with_partial_date_1(self):
        # given
        form_data = create_form_data(days_left=1)
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["timer_type"], Timer.Type.NONE)

    def test_should_accept_normal_timer_with_partial_date_2(self):
        # given
        form_data = create_form_data(hours_left=1)
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["timer_type"], Timer.Type.NONE)

    def test_should_accept_normal_timer_with_partial_date_3(self):
        # given
        form_data = create_form_data(minutes_left=1)
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["timer_type"], Timer.Type.NONE)

    def test_should_accept_preliminary_timer_without_date(self):
        # given
        form_data = create_form_data(timer_type=Timer.Type.PRELIMINARY)
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())

    def test_should_upgrade_preliminary_timer_when_date_parts_specified(self):
        # given
        form_data = create_form_data(
            timer_type=Timer.Type.PRELIMINARY,
            days_left=0,
            hours_left=3,
            minutes_left=30,
        )
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["timer_type"], Timer.Type.NONE)
        self.assertIsNone(form.cleaned_data["date"])
        self.assertIsNotNone(form.cleaned_data["days_left"])
        self.assertIsNotNone(form.cleaned_data["hours_left"])
        self.assertIsNotNone(form.cleaned_data["minutes_left"])

    def test_should_upgrade_preliminary_timer_when_date_parts_specified_2(self):
        # given
        form_data = create_form_data(timer_type=Timer.Type.PRELIMINARY, days_left=5)
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["timer_type"], Timer.Type.NONE)
        self.assertIsNone(form.cleaned_data["date"])
        self.assertIsNotNone(form.cleaned_data["days_left"])
        self.assertIsNotNone(form.cleaned_data["hours_left"])
        self.assertIsNotNone(form.cleaned_data["minutes_left"])

    def test_should_upgrade_preliminary_timer_when_date_specified(self):
        # given
        form_data = create_form_data(
            timer_type=Timer.Type.PRELIMINARY, date="2022-03-05 20:07"
        )
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["timer_type"], Timer.Type.NONE)
        self.assertIsNone(form.cleaned_data["days_left"])
        self.assertIsNone(form.cleaned_data["hours_left"])
        self.assertIsNone(form.cleaned_data["minutes_left"])
        self.assertIsNotNone(form.cleaned_data["date"])

    def test_should_set_timer_as_preliminary_timer_when_no_date_specified(self):
        # given
        form_data = create_form_data(timer_type=Timer.Type.ARMOR)
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["timer_type"], Timer.Type.PRELIMINARY)
        self.assertIsNone(form.cleaned_data["date"])
        self.assertIsNone(form.cleaned_data["days_left"])
        self.assertIsNone(form.cleaned_data["hours_left"])
        self.assertIsNone(form.cleaned_data["minutes_left"])

    def test_should_not_accept_timer_without_solar_system(self):
        # given
        form_data = create_form_data(days_left=0, hours_left=3, minutes_left=30)
        del form_data["eve_solar_system_2"]
        form = TimerForm(data=form_data)
        # when / then
        self.assertFalse(form.is_valid())

    def test_should_not_accept_timer_without_structure_type(self):
        # given
        form_data = create_form_data(days_left=0, hours_left=3, minutes_left=30)
        del form_data["structure_type_2"]
        form = TimerForm(data=form_data)
        # when / then
        self.assertFalse(form.is_valid())

    def test_should_not_accept_invalid_days(self):
        # given
        form_data = create_form_data(days_left=-1, hours_left=3, minutes_left=30)
        form = TimerForm(data=form_data)
        # when / then
        self.assertFalse(form.is_valid())

    def test_should_not_accept_invalid_date(self):
        # given
        form_data = create_form_data(date="2022.31.05 20:07:59")
        form = TimerForm(data=form_data)
        # when / then
        self.assertFalse(form.is_valid())

    def test_should_not_accept_moon_mining_type_for_non_mining_structures(self):
        # given
        form_data = create_form_data(
            timer_type=Timer.Type.MOONMINING,
            structure_type_2=self.type_astrahus.id,
            days_left=0,
            hours_left=3,
            minutes_left=30,
        )
        form = TimerForm(data=form_data)
        # when / then
        self.assertFalse(form.is_valid())

    @patch(FORMS_PATH + ".requests.get", spec=True)
    def test_should_create_timer_with_valid_details_image(self, mock_get):
        # given
        image_file = bytearray(bytes_from_file(test_image_filename()))
        mock_get.return_value.content = image_file
        form_data = create_form_data(
            days_left=0,
            hours_left=3,
            minutes_left=30,
            details_image_url="http://www.example.com/image.png",
        )
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())

    @patch(FORMS_PATH + ".requests.get", spec=True)
    def test_should_not_allow_invalid_link_for_detail_images(self, mock_get):
        # given
        image_file = bytearray(bytes_from_file(test_image_filename()))
        mock_get.return_value.content = image_file
        form_data = create_form_data(
            days_left=0,
            hours_left=3,
            minutes_left=30,
            details_image_url="invalid-url",
        )
        form = TimerForm(data=form_data)
        # when / then
        self.assertFalse(form.is_valid())

    @patch(FORMS_PATH + ".requests.get", spec=True)
    def test_should_show_error_when_image_can_not_be_loaded_1(self, mock_get):
        # given
        mock_get.side_effect = NewConnectionError
        form_data = create_form_data(
            days_left=0,
            hours_left=3,
            minutes_left=30,
            details_image_url="http://www.example.com/image.png",
        )
        form = TimerForm(data=form_data)
        # when / then
        self.assertFalse(form.is_valid())

    @patch(FORMS_PATH + ".requests.get", spec=True)
    def test_should_show_error_when_image_can_not_be_loaded_2(self, mock_get):
        # given
        mock_get.side_effect = HTTPError
        form_data = create_form_data(
            days_left=0,
            hours_left=3,
            minutes_left=30,
            details_image_url="http://www.example.com/image.png",
        )
        form = TimerForm(data=form_data)
        # when / then
        self.assertFalse(form.is_valid())

    @patch(FORMS_PATH + ".requests.get", spec=True)
    def test_should_show_error_when_image_can_not_be_loaded_3(self, mock_get):
        # given
        mock_get.side_effect = Timeout
        form_data = create_form_data(
            days_left=0,
            hours_left=3,
            minutes_left=30,
            details_image_url="http://www.example.com/image.png",
        )
        form = TimerForm(data=form_data)
        # when / then
        self.assertFalse(form.is_valid())

    def test_should_allow_theft_timer_for_skyhook_only(self):
        cases = [
            ("skyhook", EveTypeId.ORBITAL_SKYHOOK.value, True),
            ("citadel", EveTypeId.ASTRAHUS.value, False),
            ("poco", EveTypeId.CUSTOMS_OFFICE.value, False),
            ("ihub", EveTypeId.IHUB.value, False),
            ("tcu", EveTypeId.TCU.value, False),
        ]
        for tc in cases:
            with self.subTest(name=tc[0]):
                form_data = create_form_data(
                    days_left=0,
                    hours_left=3,
                    minutes_left=30,
                    timer_type=Timer.Type.THEFT,
                    structure_type_2=tc[1],
                )
                form = TimerForm(data=form_data)
                self.assertIs(form.is_valid(), tc[2], form.errors)


@patch(MODELS_PATH + "._task_calc_timer_distances_for_all_staging_systems", Mock())
@patch(MODELS_PATH + "._task_schedule_notifications_for_timer", Mock())
class TestTimerFormSave(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = UserWithAccessFactory()
        cls.system_abune = EveSolarSystemLowSecFactory(id=30004984, name="Abune")
        cls.type_astrahus = CitadelTypeFactory(id=35832, name="Astrahus")
        cls.type_athanor = RefineryTypeFactory(id=35835, name="Athanor")
        SkyhookTypeFactory()

    def test_should_create_new_normal_timer(self):
        # given
        form_data = create_form_data(
            days_left=0, hours_left=3, minutes_left=30, timer_type=Timer.Type.ARMOR
        )
        form = TimerForm(user=self.user, data=form_data)
        # when
        form.save()
        # then
        timer = Timer.objects.first()
        self.assertEqual(timer.timer_type, Timer.Type.ARMOR)
        self.assertIsNotNone(timer.date)

    def test_should_create_new_preliminary_timer(self):
        # given
        form_data = create_form_data()
        form = TimerForm(user=self.user, data=form_data)
        # when
        form.save()
        # then
        timer = Timer.objects.first()
        self.assertEqual(timer.timer_type, Timer.Type.PRELIMINARY)
        self.assertIsNone(timer.date)

    def test_should_promote_preliminary_timer_to_normal_timer(self):
        # given
        form_data = create_form_data(timer_type=Timer.Type.PRELIMINARY, days_left=1)
        form = TimerForm(user=self.user, data=form_data)
        # when
        form.save()
        # then
        timer = Timer.objects.first()
        self.assertEqual(timer.timer_type, Timer.Type.NONE)
        self.assertIsNotNone(timer.date)
