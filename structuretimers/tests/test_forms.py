import datetime as dt
from unittest.mock import Mock, patch

from django.utils.timezone import now

from requests.exceptions import ConnectionError as NewConnectionError
from requests.exceptions import HTTPError, Timeout

from app_utils.testing import NoSocketsTestCase

from structuretimers.constants import EveTypeId
from structuretimers.forms import FastTimerForm, ReconForm, TimerForm, parse_eve_timer_text
from structuretimers.models import Organization, Structure, Timer
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


#: A date for reinforcement timers in tests, in the form's input format.
FUTURE = (now() + dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")


def make_owner(name="Test Owner Corp", corporation_id=98000001) -> str:
    """Remember an owner corporation so forms need no ESI lookup."""
    organization, _ = Organization.objects.get_or_create(
        id=corporation_id,
        defaults={"name": name, "category": Organization.Category.CORPORATION},
    )
    return str(organization.pk)


def create_form_data(**kwargs):
    form_data = {
        "structure_name": "Test structure",
        "owner_2": make_owner(),
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
        "owner_2": make_owner("SoyuzMultFilm", 98000002),
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

    def test_should_parse_orbital_skyhook_timer_text(self):
        parsed = parse_eve_timer_text(
            "Orbital Skyhook (F-NXLQ VIII) [Guns-R-Us Toy Company]\n"
            "28.551 km\n"
            "Reinforced until 2026.10.09 00:44:58"
        )

        self.assertEqual(parsed.solar_system_name, "F-NXLQ")
        self.assertEqual(parsed.structure_name, "VIII")
        self.assertEqual(parsed.location_details, "VIII")
        self.assertEqual(parsed.owner_name, "Guns-R-Us Toy Company")
        self.assertEqual(parsed.structure_type_name, "Orbital Skyhook")
        self.assertEqual(parsed.date.isoformat(), "2026-10-09T00:44:58+00:00")

    def test_should_parse_skyhook_in_multiword_solar_system(self):
        parsed = parse_eve_timer_text(
            "Orbital Skyhook (New Caldari VIII) [Guns-R-Us Toy Company]\n"
            "28.551 km\n"
            "Reinforced until 2026.10.09 00:44:58"
        )

        self.assertEqual(parsed.solar_system_name, "New Caldari")
        self.assertEqual(parsed.structure_name, "VIII")

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

    def test_should_derive_skyhook_location_and_owner(self):
        solar_system = EveSolarSystemLowSecFactory(name="F-NXLQ")
        SkyhookTypeFactory()
        pasted_timer = (
            "Orbital Skyhook (F-NXLQ VIII) [Guns-R-Us Toy Company]\n"
            "28.551 km\n"
            "Reinforced until 2026.10.09 00:44:58"
        )
        make_owner("Guns-R-Us Toy Company", 98000005)
        form = FastTimerForm(
            data=create_fast_form_data(
                pasted_timer=pasted_timer,
                structure_type_2=EveTypeId.ASTRAHUS.value,
                owner_2="",
            )
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["eve_solar_system_2"], str(solar_system.id))
        self.assertEqual(form.cleaned_data["structure_name"], "VIII")
        self.assertEqual(
            form.cleaned_data["structure_type_2"], str(EveTypeId.ORBITAL_SKYHOOK.value)
        )
        self.assertEqual(form.cleaned_data["location_details"], "VIII")
        self.assertEqual(form.cleaned_data["owner_2"].name, "Guns-R-Us Toy Company")
        self.assertEqual(
            form.cleaned_data["date"].isoformat(), "2026-10-09T00:44:58+00:00"
        )

    def test_objective_is_not_entered_by_people(self):
        self.assertNotIn("objective", FastTimerForm().fields)
        self.assertNotIn("objective", TimerForm().fields)

    def test_should_only_show_fast_entry_fields(self):
        form = FastTimerForm()

        self.assertEqual(
            [field.name for field in form.visible_fields()],
            list(FastTimerForm.fast_fields),
        )
        self.assertNotIn("location_details", FastTimerForm.fast_fields)

    def test_should_require_owner(self):
        form = FastTimerForm(data=create_fast_form_data(owner_2=""))

        self.assertFalse(form.is_valid())
        self.assertIn("owner_2", form.errors)

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

    def test_should_accept_normal_timer_with_date(self):
        # given
        form_data = create_form_data(date="2022-03-05 20:07")
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())

    def test_should_accept_preliminary_timer_without_date(self):
        # given
        form_data = create_form_data(timer_type=Timer.Type.PRELIMINARY)
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())

    def test_should_upgrade_preliminary_timer_when_date_specified(self):
        # given
        form_data = create_form_data(
            timer_type=Timer.Type.PRELIMINARY, date="2022-03-05 20:07"
        )
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["timer_type"], Timer.Type.NONE)
        self.assertIsNotNone(form.cleaned_data["date"])

    def test_should_set_timer_as_preliminary_timer_when_no_date_specified(self):
        # given
        form_data = create_form_data(timer_type=Timer.Type.ARMOR)
        form = TimerForm(data=form_data)
        # when / then
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["timer_type"], Timer.Type.PRELIMINARY)
        self.assertIsNone(form.cleaned_data["date"])

    def test_should_not_accept_timer_without_solar_system(self):
        # given
        form_data = create_form_data(date=FUTURE)
        del form_data["eve_solar_system_2"]
        form = TimerForm(data=form_data)
        # when / then
        self.assertFalse(form.is_valid())

    def test_should_not_accept_timer_without_structure_type(self):
        # given
        form_data = create_form_data(date=FUTURE)
        del form_data["structure_type_2"]
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
            date=FUTURE,
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
            date=FUTURE,
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
            date=FUTURE,
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
            date=FUTURE,
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
            date=FUTURE,
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
            date=FUTURE,
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
                    date=FUTURE,
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

    def test_assignment_can_be_created_changed_and_cleared(self):
        assignee = UserWithAccessFactory()
        replacement = UserWithAccessFactory()
        form = TimerForm(
            user=self.user,
            data=create_form_data(assigned_to=assignee.pk, date=FUTURE),
        )
        self.assertTrue(form.is_valid(), form.errors)
        timer = form.save()
        timer.refresh_from_db()
        self.assertEqual(timer.assigned_to, assignee)
        self.assertEqual(timer.user, self.user)
        self.assertEqual(
            timer.assigned_character_name,
            assignee.profile.main_character.character_name,
        )
        self.assertEqual(
            form.fields["assigned_to"].label_from_instance(assignee),
            assignee.profile.main_character.character_name,
        )
        for selected in (replacement.pk, ""):
            form = TimerForm(
                user=self.user, instance=timer,
                data=create_form_data(assigned_to=selected, date=FUTURE),
            )
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
            timer.refresh_from_db()
            self.assertEqual(timer.assigned_to_id, selected or None)
            self.assertEqual(timer.user, self.user)
        self.assertEqual(timer.assigned_character_name, "")

    def test_assignment_rejects_inactive_users(self):
        assignee = UserWithAccessFactory(is_active=False)
        form = TimerForm(
            user=self.user,
            data=create_form_data(assigned_to=assignee.pk, date=FUTURE),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("assigned_to", form.errors)

    def test_deleted_assignee_does_not_delete_timer(self):
        assignee = UserWithAccessFactory()
        form = TimerForm(
            user=self.user,
            data=create_form_data(assigned_to=assignee.pk, date=FUTURE),
        )
        self.assertTrue(form.is_valid(), form.errors)
        timer = form.save()
        assignee.delete()
        timer.refresh_from_db()
        self.assertIsNone(timer.assigned_to)

    def test_quick_add_can_assign_user(self):
        form = FastTimerForm(
            user=self.user,
            data=create_fast_form_data(
                assigned_to=self.user.pk,
                pasted_timer="Abune - Test\nReinforced until 2030.09.05 03:47:08",
            ),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().assigned_to, self.user)

    def test_should_create_new_normal_timer(self):
        # given
        form_data = create_form_data(
            date=FUTURE, timer_type=Timer.Type.ARMOR
        )
        form = TimerForm(user=self.user, data=form_data)
        # when
        form.save()
        # then
        timer = Timer.objects.exclude(timer_type=Timer.Type.PRELIMINARY).first()
        self.assertEqual(timer.timer_type, Timer.Type.ARMOR)
        self.assertIsNotNone(timer.date)

    def test_without_date_only_the_database_record_is_saved(self):
        form = TimerForm(user=self.user, data=create_form_data(fitting="[Astrahus]"))
        self.assertTrue(form.is_valid(), form.errors)
        structure = form.save()
        self.assertIsInstance(structure, Structure)
        self.assertEqual(structure.structure_name, "Test structure")
        self.assertEqual(structure.fitting, "[Astrahus]")
        self.assertEqual(structure.owner_name, "Test Owner Corp")
        self.assertEqual(structure.user, self.user)
        self.assertFalse(Timer.objects.exists())

    def test_should_promote_preliminary_timer_to_normal_timer(self):
        # given
        form_data = create_form_data(timer_type=Timer.Type.PRELIMINARY, date=FUTURE)
        form = TimerForm(user=self.user, data=form_data)
        # when
        form.save()
        # then
        timer = Timer.objects.exclude(timer_type=Timer.Type.PRELIMINARY).first()
        self.assertEqual(timer.timer_type, Timer.Type.NONE)
        self.assertIsNotNone(timer.date)

    def test_normal_timer_requires_explicit_flag(self):
        for selected in (False, True):
            form = TimerForm(
                user=self.user,
                data=create_form_data(
                    date=FUTURE,
                    discord_timerboard=selected,
                ),
            )
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.save(commit=False).discord_timerboard, selected)

    def test_quick_add_sets_flag(self):
        form = FastTimerForm(
            user=self.user,
            data=create_fast_form_data(
                pasted_timer="Abune - Test\n17 km\nReinforced until 2030.09.05 03:47:08",
            ),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.save(commit=False).discord_timerboard)

    def test_recon_does_not_accept_injected_flag(self):
        form = ReconForm(
            user=self.user,
            data=create_form_data(
                structure_name="Recon",
                discord_timerboard=True,
            ),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("discord_timerboard", form.fields)
        self.assertIsInstance(form.save(commit=False), Structure)
