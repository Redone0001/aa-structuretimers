"""Recon translations must work for every language exposed by Alliance Auth."""

import gettext
import re
from pathlib import Path

from django.test import RequestFactory
from django.utils import translation
from app_utils.testing import NoSocketsTestCase

from structuretimers.forms import ReconForm
from structuretimers.tests.testdata.factory import TimerFactory, UserWithCreateFactory
from structuretimers.models import Timer
from structuretimers.views import TimerListView

LOCALE_ROOT = Path(__file__).resolve().parents[1] / "locale"
TRANSLATED_TITLES = {
    "fr": "Gérer les reconnaissances",
    "de": "Aufklärung verwalten",
    "es": "Gestionar reconocimientos",
    "it": "Gestisci ricognizioni",
    "ja": "偵察を管理",
    "ko": "정찰 관리",
    "ru": "Управление разведданными",
    "zh-hans": "管理侦察记录",
}


def messages_in(value):
    if isinstance(value, dict):
        return [message for child in value.values() for message in messages_in(child)]
    return [value]


class TestReconTranslations(NoSocketsTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/?tab=manage-recon")
        self.request.user = UserWithCreateFactory()

    def test_each_language_renders_translated_page_and_javascript_messages(self):
        for language, title in TRANSLATED_TITLES.items():
            with self.subTest(language=language), translation.override(language):
                response = TimerListView.as_view()(self.request)
                response.render()
                self.assertContains(response, title)
                self.assertContains(response, 'id="recon-translations"')
                messages = response.context_data["recon_translations"]
                self.assertNotEqual(messages["noMatches"], "No matching recon")
                self.assertNotEqual(messages["saving"], "Saving…")
                self.assertIn("%(count)s", messages["peak"])
                self.assertIn("_TOTAL_", messages["table"]["info"])
                form = ReconForm(user=self.request.user)
                self.assertNotEqual(
                    str(form.fields["reinforcement_time"].label), "Reinforcement timer"
                )
                self.assertNotEqual(
                    str(form.fields["details_notes"].label), "Details / notes"
                )

    def test_recon_details_description_is_translated(self):
        timer = TimerFactory(
            timer_type=Timer.Type.PRELIMINARY,
            structure_type=None,
            structure_name="Recon target",
            location_details="Moon 2",
        )
        with translation.override("fr"):
            description = str(timer)
            self.assertIn("Timer Préliminaire pour", description)
            self.assertIn("(inconnu)", description)
            self.assertIn("près de Moon 2", description)
            self.assertIn("Recon target", description)

    def test_regional_language_codes_fall_back_to_the_base_catalog(self):
        for language, base in [("fr-fr", "fr"), ("it-it", "it"), ("ko-kr", "ko")]:
            with self.subTest(language=language), translation.override(language):
                self.assertEqual(
                    translation.gettext("Manage recon"), TRANSLATED_TITLES[base]
                )

    def test_compiled_catalogs_cover_recon_ui_and_preserve_placeholders(self):
        with translation.override("en"):
            response = TimerListView.as_view()(self.request)
            required = set(messages_in(response.context_data["recon_translations"]))
        templates = (
            Path(__file__).resolve().parents[1] / "templates" / "structuretimers"
        )
        for relative in [
            "partials/manage_recon.html",
            "partials/recon_actions.html",
            "recon_create_form.html",
        ]:
            source = (templates / relative).read_text()
            required.update(
                a or b
                for a, b in re.findall(
                    r"""{% translate (?:"([^"]+)"|'([^']+)') %}""", source
                )
            )
        for path in LOCALE_ROOT.glob("*/LC_MESSAGES/django.mo"):
            with self.subTest(language=path.parts[-3]), path.open("rb") as stream:
                catalog = gettext.GNUTranslations(stream)
                for message in required:
                    self.assertIn(message, catalog._catalog)
                for message, translated in catalog._catalog.items():
                    if not message or not isinstance(message, str):
                        continue
                    self.assertEqual(
                        sorted(re.findall(r"%\(\w+\)s|_[A-Z]+_", message)),
                        sorted(re.findall(r"%\(\w+\)s|_[A-Z]+_", translated)),
                        message,
                    )
