import datetime as dt
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.apps import apps
from django.test import TestCase, override_settings
from django.utils.timezone import now
from requests import Timeout

from structuretimers.forms import FastTimerForm, ReconForm, TimerForm
from structuretimers.models import DiscordTimerboard, Timer
from structuretimers.tasks import refresh_discord_timerboard
from structuretimers.timerboard import (
    DiscordClient,
    DiscordRateLimited,
    render_board,
    sync_board,
)


@override_settings(STRUCTURETIMERS_DISCORD_BOT_TOKEN="test-token")
class TimerboardTests(TestCase):
    def setUp(self):
        self.board = DiscordTimerboard.objects.create(name="Timers", channel_id="123")

    def timer(self, **kwargs):
        fields = dict(date=now() + dt.timedelta(hours=1), timer_type=Timer.Type.ARMOR)
        fields.update(kwargs)
        return Timer.objects.bulk_create([Timer(**fields)])[0]

    def test_selection_defaults_preliminary_exclusion_and_order(self):
        later = self.timer(date=now() + dt.timedelta(hours=2), location_details="Later")
        early = self.timer(location_details="Earlier")
        self.timer(
            timer_type=Timer.Type.PRELIMINARY,
            discord_timerboard=True,
            location_details="Hidden",
        )
        page = render_board(self.board)[0]
        self.assertLess(page.index("Earlier"), page.index("Later"))
        self.assertNotIn("Hidden", page)
        self.assertIn(f"<t:{int(early.date.timestamp())}:R>", page)
        self.assertIn(later.date.astimezone(dt.timezone.utc).strftime("%H:%M"), page)
        self.board.include_unflagged = False
        self.assertIn("No timers", render_board(self.board)[0])
        Timer.objects.filter(pk=early.pk).update(discord_timerboard=True)
        self.assertIn("Earlier", render_board(self.board)[0])
        self.board.timer_types = [Timer.Type.HULL]
        self.assertIn("No timers", render_board(self.board)[0])

    def test_elapsed_rows_remain_until_deleted(self):
        timer = self.timer(
            date=now() - dt.timedelta(hours=2), location_details="Elapsed"
        )
        self.timer(location_details="Upcoming")
        page = render_board(self.board)[0]
        self.assertLess(page.index("Upcoming"), page.index("Elapsed"))
        timer.delete()
        self.assertNotIn("Elapsed", render_board(self.board)[0])

    def test_pagination_preserves_rows(self):
        for i in range(25):
            self.timer(location_details=f"row{i:02d}-" + "X" * 240)
        pages = render_board(self.board)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) <= 2000 for page in pages))
        for i in range(25):
            self.assertEqual("\n".join(pages).count(f"row{i:02d}-"), 1)

    @patch("structuretimers.timerboard.DiscordClient.request")
    def test_reuses_edits_and_removes_surplus_messages(self, request):
        request.return_value = {"id": "456"}
        with patch(
            "structuretimers.timerboard.render_board", return_value=["first", "second"]
        ):
            sync_board(self.board)
            self.assertEqual(request.call_count, 2)
            request.reset_mock()
            sync_board(self.board)
            request.assert_not_called()
        with patch("structuretimers.timerboard.render_board", return_value=["updated"]):
            sync_board(self.board)
        self.assertEqual(
            [c.args[0] for c in request.call_args_list], ["PATCH", "DELETE"]
        )
        self.assertEqual(self.board.messages.count(), 1)
        self.assertEqual(self.board.messages.get().content, "updated")

    @patch("structuretimers.timerboard.DiscordClient.request")
    def test_channel_change_and_disable_remove_messages(self, request):
        request.return_value = {"id": "456"}
        sync_board(self.board)
        request.reset_mock()
        self.board.channel_id = "789"
        sync_board(self.board)
        self.assertEqual(
            [c.args[0] for c in request.call_args_list], ["DELETE", "POST"]
        )
        self.assertEqual(request.call_args_list[0].args[1], "123")
        self.board.is_enabled = False
        sync_board(self.board)
        self.assertFalse(self.board.messages.exists())

    @patch("structuretimers.timerboard.DiscordClient.request")
    def test_missing_changed_message_is_replaced(self, request):
        self.board.messages.create(
            channel_id="123", message_id="456", position=0, content="old"
        )
        request.side_effect = [None, {"id": "789"}]
        sync_board(self.board)
        self.assertEqual(self.board.messages.get().message_id, "789")

    @patch("structuretimers.timerboard.DiscordClient.request")
    def test_partial_failure_preserves_success_and_pending_nonce(self, request):
        request.side_effect = [{"id": "456"}, Timeout()]
        with patch(
            "structuretimers.timerboard.render_board", return_value=["one", "two"]
        ):
            with patch.object(refresh_discord_timerboard, "retry", side_effect=Timeout):
                with self.assertRaises(Timeout):
                    refresh_discord_timerboard.run(self.board.pk)
            self.assertEqual(self.board.messages.count(), 2)
            self.assertEqual(self.board.messages.first().content, "one")
            nonce = request.call_args.kwargs["nonce"]
            request.side_effect = None
            request.return_value = {"id": "789"}
            request.reset_mock()
            sync_board(self.board)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.kwargs["nonce"], nonce)

    @patch("structuretimers.timerboard.requests.request")
    def test_api_disables_mentions_and_handles_rate_limit(self, request):
        request.return_value = Mock(
            status_code=200, json=Mock(return_value={"id": "456"})
        )
        DiscordClient().request("PATCH", "123", "456", "@everyone")
        self.assertEqual(
            request.call_args.kwargs["json"]["allowed_mentions"], {"parse": []}
        )
        request.return_value = Mock(
            status_code=429, json=Mock(return_value={"retry_after": 2.5})
        )
        with self.assertRaises(DiscordRateLimited) as result:
            DiscordClient().request("POST", "123", content="test")
        self.assertEqual(result.exception.retry_after, 2.5)

    def test_form_defaults(self):
        self.assertFalse(TimerForm()["discord_timerboard"].value())
        self.assertNotIn("discord_timerboard", ReconForm().fields)
        self.assertNotIn("discord_timerboard", FastTimerForm().fields)

    def test_backfill_flags_only_non_preliminary_timers(self):
        scheduled = self.timer()
        preliminary = self.timer(timer_type=Timer.Type.PRELIMINARY, date=None)
        migration = import_module(
            "structuretimers.migrations.0013_backfill_discord_timerboard"
        )
        migration.flag_existing_timers(
            apps, SimpleNamespace(connection=SimpleNamespace(alias="default"))
        )
        scheduled.refresh_from_db()
        preliminary.refresh_from_db()
        self.assertTrue(scheduled.discord_timerboard)
        self.assertFalse(preliminary.discord_timerboard)

    def test_housekeeping_removes_expired_rows(self):
        from structuretimers.tasks import housekeeping

        self.timer(date=now() - dt.timedelta(days=31), location_details="Expired")
        self.assertIn("Expired", render_board(self.board)[0])
        with patch(
            "structuretimers.managers.STRUCTURETIMERS_TIMERS_OBSOLETE_AFTER_DAYS", 30
        ):
            housekeeping()
        self.assertIn("No timers", render_board(self.board)[0])

    @patch("structuretimers.timerboard.requests.request")
    def test_only_unknown_message_is_treated_as_missing(self, request):
        from requests import HTTPError

        response = Mock(status_code=404, json=Mock(return_value={"code": 10003}))
        response.raise_for_status.side_effect = HTTPError()
        request.return_value = response
        with self.assertRaises(HTTPError):
            DiscordClient().request("PATCH", "123", "456", "test")
        response.json.return_value = {"code": 10008}
        self.assertIsNone(DiscordClient().request("PATCH", "123", "456", "test"))
