import datetime as dt
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from discordproxy.discord_api_pb2 import Message
from discordproxy.exceptions import (
    DiscordProxyGrpcError,
    DiscordProxyHttpError,
    DiscordProxyTimeoutError,
)
from grpc import StatusCode

from django.apps import apps
from django.test import TestCase, override_settings
from django.utils.timezone import now

from structuretimers.forms import FastTimerForm, ReconForm, TimerForm
from structuretimers.models import DiscordTimerboard, Timer
from structuretimers.tasks import refresh_discord_timerboard
from structuretimers.tests.testdata.factory import EveSolarSystemLowSecFactory
from structuretimers.timerboard import (
    render_board,
    sync_board,
)


class TimerboardTests(TestCase):
    def setUp(self):
        self.board = DiscordTimerboard.objects.create(name="Timers", channel_id="123")
        self.client_patch = patch(
            "structuretimers.timerboard.DiscordClient", autospec=True
        )
        self.client_class = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.client = self.client_class.return_value
        self.client.create_channel_message.side_effect = lambda **kwargs: Message(
            id=456 + self.client.create_channel_message.call_count,
            content=kwargs["content"],
        )

    def timer(self, **kwargs):
        fields = dict(date=now() + dt.timedelta(hours=1), timer_type=Timer.Type.ARMOR)
        system_name = kwargs.pop("system_name", None)
        if system_name is not None:
            fields["eve_solar_system"] = EveSolarSystemLowSecFactory(name=system_name)
        fields.update(kwargs)
        return Timer.objects.bulk_create([Timer(**fields)])[0]

    def test_selection_defaults_preliminary_exclusion_and_order(self):
        later = self.timer(date=now() + dt.timedelta(hours=2), system_name="Later")
        early = self.timer(system_name="Earlier")
        self.timer(
            timer_type=Timer.Type.PRELIMINARY,
            discord_timerboard=True,
            system_name="Hidden",
        )
        page = render_board(self.board)[0]
        self.assertLess(page.index("Earlier"), page.index("Later"))
        self.assertNotIn("Hidden", page)
        self.assertNotIn("<t:", page)
        self.assertIn(later.date.astimezone(dt.timezone.utc).strftime("%H:%M"), page)
        self.board.include_unflagged = False
        self.assertIn("No timers", render_board(self.board)[0])
        Timer.objects.filter(pk=early.pk).update(discord_timerboard=True)
        self.assertIn("Earlier", render_board(self.board)[0])
        self.board.timer_types = [Timer.Type.HULL]
        self.assertIn("No timers", render_board(self.board)[0])

    def test_elapsed_rows_remain_until_deleted(self):
        timer = self.timer(date=now() - dt.timedelta(minutes=30), system_name="Elapsed")
        self.timer(system_name="Upcoming")
        page = render_board(self.board)[0]
        self.assertLess(page.index("Upcoming"), page.index("Elapsed"))
        timer.delete()
        self.assertNotIn("Elapsed", render_board(self.board)[0])

    def test_pagination_preserves_rows(self):
        for i in range(50):
            self.timer(system_name=f"row{i:02d}")
        pages = render_board(self.board)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) <= 2000 for page in pages))
        for i in range(50):
            self.assertEqual("\n".join(pages).count(f"row{i:02d}"), 1)

    def test_twenty_short_rows_fit_in_one_message(self):
        for number in range(20):
            self.timer(system_name=f"System{number:02d}")
        pages = render_board(self.board)
        self.assertEqual(len(pages), 1)
        self.assertLessEqual(len(pages[0]), 2000)
        for number in range(20):
            self.assertEqual(pages[0].count(f"System{number:02d}"), 1)

    def test_one_hour_cutoff_hides_rows_without_deleting_timers(self):
        current = now()
        recent = self.timer(
            date=current - dt.timedelta(minutes=59), system_name="Recent"
        )
        boundary = self.timer(
            date=current - dt.timedelta(hours=1), system_name="Boundary"
        )
        expired = self.timer(
            date=current - dt.timedelta(hours=1, seconds=1), system_name="Expired"
        )
        with patch("structuretimers.timerboard.now", return_value=current):
            page = render_board(self.board)[0]
        self.assertIn("Recent", page)
        self.assertIn("Boundary", page)
        self.assertNotIn("Expired", page)
        self.assertEqual(
            Timer.objects.filter(pk__in=[recent.pk, boundary.pk, expired.pk]).count(), 3
        )

    def test_table_has_aligned_columns_and_no_countdown_footer(self):
        from structuretimers.timerboard import cell_width

        self.timer(
            system_name="Jita 漢字", location_details="Hidden location ``` @everyone"
        )
        page = render_board(self.board)[0]
        self.assertEqual(page.count("```"), 2)
        grid = page.split("```text\n", 1)[1].split("\n```", 1)[0]
        lines = grid.splitlines()
        self.assertEqual(len({cell_width(line) for line in lines}), 1)
        self.assertTrue(lines[0].startswith("┌"))
        self.assertTrue(lines[-1].endswith("┘"))
        self.assertIn("Structure", lines[1])
        self.assertNotIn("<t:", grid)
        self.assertEqual(page.rsplit("\n```", 1)[1], "")
        self.assertNotIn("Live countdowns", page)
        self.assertNotIn("Hidden location", page)
        self.assertIn("Jita 漢字", page)
        self.assertEqual(sum(line.startswith("├") for line in lines), 1)

    def test_large_cells_still_fit_one_complete_message(self):
        from structuretimers.timerboard import table_page

        page = table_page([["X" * 400] * 5])
        self.assertLessEqual(len(page), 2000)
        self.assertIn("…", page)

    def test_racial_tower_labels_preserve_sizes(self):
        from structuretimers.timerboard import structure_label

        for faction in ("Amarr", "Gallente", "Minmatar", "Caldari"):
            for suffix, expected in (
                ("", "POS"),
                (" Small", "POS Small"),
                (" Medium", "POS Medium"),
                (" Large", "POS"),
            ):
                with self.subTest(faction=faction, suffix=suffix):
                    self.assertEqual(
                        structure_label(f"{faction} Control Tower{suffix}"), expected
                    )
        for name in (
            "Fortizar",
            "Amarr Control Tower Blueprint",
            "Dread Guristas Control Tower",
        ):
            self.assertEqual(structure_label(name), name)

    def test_tower_abbreviation_only_changes_discord_display(self):
        from structuretimers.tests.testdata.factory import CitadelTypeFactory

        structure = CitadelTypeFactory(name="Caldari Control Tower Medium")
        self.timer(system_name="Jita", structure_type=structure)
        page = render_board(self.board)[0]
        self.assertIn("POS Medium", page)
        self.assertNotIn("Caldari Control Tower", page)
        structure.refresh_from_db()
        self.assertEqual(structure.name, "Caldari Control Tower Medium")

    def test_columns_shrink_and_expand_to_fit_each_page(self):
        from structuretimers.timerboard import (
            column_widths,
            table_page,
            cell_width,
            MAX_TABLE_WIDTH,
        )

        short = [["12:30", "in 1h 0m", "Jita", "POS", "Armor"]]
        long = [
            [
                "12:30",
                "in 1h 0m",
                "A Very Long Solar System Name",
                "POS Medium",
                "Armor",
            ]
        ]
        self.assertEqual(column_widths(short)[2], len("System"))
        self.assertEqual(column_widths(long)[2], len(long[0][2]))
        self.assertIn(long[0][2], table_page(long))
        for rows in (short, long, [["X" * 400] * 5]):
            grid = table_page(rows).split("```text\n")[1].split("\n```")[0]
            widths = {cell_width(line) for line in grid.splitlines()}
            self.assertEqual(len(widths), 1)
            self.assertLessEqual(widths.pop(), MAX_TABLE_WIDTH)

    def test_expiry_refresh_updates_existing_message(self):
        current = now()
        self.timer(date=current - dt.timedelta(minutes=59), system_name="Expiring")
        with patch("structuretimers.timerboard.now", return_value=current):
            sync_board(self.board)
        message_id = self.board.messages.get().message_id
        self.client.reset_mock()
        with patch(
            "structuretimers.timerboard.now",
            return_value=current + dt.timedelta(minutes=5),
        ):
            sync_board(self.board)
        self.client.create_channel_message.assert_not_called()
        self.client.edit_channel_message.assert_called_once()
        self.assertEqual(self.board.messages.get().message_id, message_id)
        self.assertNotIn("Expiring", self.board.messages.get().content)
        self.assertTrue(
            Timer.objects.filter(eve_solar_system__name="Expiring").exists()
        )

    def test_reuses_edits_and_removes_surplus_messages(self):
        with patch(
            "structuretimers.timerboard.render_board", return_value=["first", "second"]
        ):
            sync_board(self.board)
            self.assertEqual(self.client.create_channel_message.call_count, 2)
            self.client.reset_mock()
            sync_board(self.board)
            self.assertEqual(self.client.mock_calls, [])
        first_id = int(self.board.messages.first().message_id)
        second_id = int(self.board.messages.last().message_id)
        with patch("structuretimers.timerboard.render_board", return_value=["updated"]):
            sync_board(self.board)
        self.client.edit_channel_message.assert_called_once_with(
            channel_id=123,
            message_id=first_id,
            content="updated",
            suppress_mentions=True,
        )
        self.client.delete_channel_message.assert_called_once_with(
            channel_id=123, message_id=second_id
        )
        self.assertEqual(self.board.messages.count(), 1)
        self.assertEqual(self.board.messages.get().content, "updated")

    def test_channel_change_and_disable_remove_messages(self):
        sync_board(self.board)
        old_id = int(self.board.messages.get().message_id)
        self.client.reset_mock()
        self.board.channel_id = "789"
        sync_board(self.board)
        self.assertEqual(
            [c[0] for c in self.client.mock_calls],
            ["delete_channel_message", "create_channel_message"],
        )
        self.client.delete_channel_message.assert_called_once_with(
            channel_id=123, message_id=old_id
        )
        self.assertEqual(
            self.client.create_channel_message.call_args.kwargs["channel_id"], 789
        )
        self.board.is_enabled = False
        sync_board(self.board)
        self.assertFalse(self.board.messages.exists())

    def test_missing_changed_message_is_replaced(self):
        self.board.messages.create(
            channel_id="123", message_id="456", position=0, content="old"
        )
        self.client.edit_channel_message.side_effect = DiscordProxyHttpError(
            404, 10008, "Unknown Message"
        )
        sync_board(self.board)
        self.assertNotEqual(self.board.messages.get().message_id, "456")
        self.client.create_channel_message.assert_called_once()

    def test_partial_failure_preserves_success_and_pending_nonce(self):
        timeout = DiscordProxyTimeoutError(StatusCode.DEADLINE_EXCEEDED, "Timed out")
        self.client.create_channel_message.side_effect = [
            Message(id=456, content="one"),
            timeout,
        ]
        with patch(
            "structuretimers.timerboard.render_board", return_value=["one", "two"]
        ):
            with patch.object(
                refresh_discord_timerboard, "retry", side_effect=timeout
            ) as retry:
                with self.assertRaises(DiscordProxyTimeoutError):
                    refresh_discord_timerboard.run(self.board.pk)
            retry.assert_called_once_with(exc=timeout, countdown=60)
            self.assertEqual(self.board.messages.count(), 2)
            self.assertEqual(self.board.messages.first().content, "one")
            nonce = self.client.create_channel_message.call_args.kwargs["nonce"]
            self.client.create_channel_message.side_effect = None
            self.client.create_channel_message.return_value = Message(
                id=789, content="two"
            )
            self.client.reset_mock()
            sync_board(self.board)
            self.client.create_channel_message.assert_called_once_with(
                channel_id=123,
                content="two",
                suppress_mentions=True,
                nonce=nonce,
                enforce_nonce=True,
            )
            self.client.edit_channel_message.assert_not_called()

    def test_deduplicated_response_preserves_actual_content_for_next_edit(self):
        self.board.messages.create(
            channel_id="123", message_id="", position=0, content=""
        )
        self.client.create_channel_message.side_effect = None
        self.client.create_channel_message.return_value = Message(
            id=456, content="before lost reply"
        )
        with patch(
            "structuretimers.timerboard.render_board", return_value=["current state"]
        ):
            sync_board(self.board)
            self.assertEqual(self.board.messages.get().content, "before lost reply")
            sync_board(self.board)
        self.client.create_channel_message.assert_called_once()
        self.client.edit_channel_message.assert_called_once_with(
            channel_id=123,
            message_id=456,
            content="current state",
            suppress_mentions=True,
        )

    @override_settings(
        STRUCTURETIMERS_DISCORD_PROXY_TARGET="discordproxy:50051",
        STRUCTURETIMERS_DISCORD_PROXY_TIMEOUT=45,
    )
    def test_proxy_configuration_needs_no_bot_token(self):
        sync_board(self.board)
        self.client_class.assert_called_once_with(
            target="discordproxy:50051", timeout=45
        )
        kwargs = self.client.create_channel_message.call_args.kwargs
        self.assertTrue(kwargs["suppress_mentions"])
        self.assertTrue(kwargs["enforce_nonce"])
        self.assertTrue(kwargs["nonce"])

    def test_proxy_transport_errors_are_retried(self):
        failure = DiscordProxyGrpcError(StatusCode.UNAVAILABLE, "Proxy unavailable")
        self.client.create_channel_message.side_effect = failure
        with patch.object(
            refresh_discord_timerboard, "retry", side_effect=failure
        ) as retry:
            with self.assertRaises(DiscordProxyGrpcError):
                refresh_discord_timerboard.run(self.board.pk)
        retry.assert_called_once_with(exc=failure, countdown=60)
        self.assertEqual(self.board.messages.get().message_id, "")

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

        self.timer(date=now() - dt.timedelta(days=31), system_name="Expired")
        self.assertNotIn("Expired", render_board(self.board)[0])
        self.assertTrue(Timer.objects.filter(eve_solar_system__name="Expired").exists())
        with patch(
            "structuretimers.managers.STRUCTURETIMERS_TIMERS_OBSOLETE_AFTER_DAYS", 30
        ):
            housekeeping()
        self.assertIn("No timers", render_board(self.board)[0])
        self.assertFalse(
            Timer.objects.filter(eve_solar_system__name="Expired").exists()
        )

    def test_channel_and_permission_errors_do_not_create_replacements(self):
        self.board.messages.create(
            channel_id="123", message_id="456", position=0, content="old"
        )
        for status, code in [(404, 10003), (403, 50001), (403, 50013)]:
            with self.subTest(code=code):
                self.client.edit_channel_message.side_effect = DiscordProxyHttpError(
                    status, code, "Failure"
                )
                with self.assertRaises(DiscordProxyHttpError):
                    sync_board(self.board)
                self.assertEqual(self.board.messages.get().message_id, "456")
                self.assertEqual(self.board.messages.get().content, "old")
                self.client.create_channel_message.assert_not_called()

    def test_delete_ignores_only_unknown_message(self):
        self.board.messages.create(
            channel_id="123", message_id="456", position=0, content="old"
        )
        self.board.is_enabled = False
        self.client.delete_channel_message.side_effect = DiscordProxyHttpError(
            404, 10003, "Unknown channel"
        )
        with self.assertRaises(DiscordProxyHttpError):
            sync_board(self.board)
        self.assertTrue(self.board.messages.exists())
        self.client.delete_channel_message.side_effect = DiscordProxyHttpError(
            404, 10008, "Unknown message"
        )
        sync_board(self.board)
        self.assertFalse(self.board.messages.exists())


class ProxyWireTests(TestCase):
    @patch("discordproxy.client.grpc.insecure_channel")
    @patch("discordproxy.client.DiscordApiStub")
    def test_real_proxy_client_serializes_lifecycle_requests(self, stub_class, channel):
        from discordproxy.discord_api_pb2 import (
            SendChannelMessageResponse,
            EditChannelMessageResponse,
            DeleteChannelMessageResponse,
        )

        stub = stub_class.return_value
        # Snowflakes must remain exact integers, including above 2**53.
        channel_id, message_id = 123456789012345678, 987654321098765432
        board = DiscordTimerboard.objects.create(
            name="Wire test", channel_id=str(channel_id)
        )
        stub.SendChannelMessage.return_value = SendChannelMessageResponse(
            message=Message(id=message_id, content="first")
        )
        stub.EditChannelMessage.return_value = EditChannelMessageResponse(
            message=Message(id=message_id, content="updated")
        )
        stub.DeleteChannelMessage.return_value = DeleteChannelMessageResponse()
        with patch("structuretimers.timerboard.render_board", return_value=["first"]):
            sync_board(board)
        create = stub.SendChannelMessage.call_args.kwargs
        self.assertEqual(create["timeout"], 30)
        self.assertEqual(create["request"].channel_id, channel_id)
        self.assertTrue(create["request"].suppress_mentions)
        self.assertTrue(create["request"].enforce_nonce)
        self.assertTrue(create["request"].nonce)
        self.assertEqual(board.messages.get().message_id, str(message_id))
        with patch("structuretimers.timerboard.render_board", return_value=["updated"]):
            sync_board(board)
        edit = stub.EditChannelMessage.call_args.kwargs["request"]
        self.assertEqual(edit.channel_id, channel_id)
        self.assertEqual(edit.message_id, message_id)
        self.assertEqual(edit.content, "updated")
        self.assertTrue(edit.suppress_mentions)
        board.is_enabled = False
        sync_board(board)
        delete = stub.DeleteChannelMessage.call_args.kwargs["request"]
        self.assertEqual(delete.channel_id, channel_id)
        self.assertEqual(delete.message_id, message_id)
        self.assertFalse(board.messages.exists())
