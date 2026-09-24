"""Render and synchronize persistent Discord timerboard messages."""

import datetime as dt
import re

import requests

from django.conf import settings
from django.db.models import Case, IntegerField, Value, When
from django.utils.timezone import now
from django.utils.translation import override

from .models import Timer

HEADER = "**EVE Time (UTC) / Relative time / Location / Structure type / Timer type**"
MESSAGE_LIMIT = 2000


def clean_cell(value):
    """Keep user text on one row and prevent injected Discord formatting."""
    value = " ".join(str(value or "Unknown").split())
    return re.sub(r"([\\`*_~|<>/@])", r"\\\1", value)[:400]


def render_board(board):
    """Dates are deliberately not filtered against the clock."""
    timers = Timer.objects.filter(
        date__isnull=False, timer_type__in=board.timer_types
    ).exclude(timer_type=Timer.Type.PRELIMINARY)
    if not board.include_unflagged:
        timers = timers.filter(discord_timerboard=True)
    timers = (
        timers.select_related("eve_solar_system", "structure_type")
        .annotate(
            elapsed=Case(
                When(date__lt=now(), then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .order_by("elapsed", "date", "pk")
    )
    pages = []
    content = HEADER
    with override("en"):
        for timer in timers:
            date = timer.date.astimezone(dt.timezone.utc)
            location = " - ".join(
                filter(
                    None,
                    [
                        (
                            timer.eve_solar_system.name
                            if timer.eve_solar_system
                            else "Unknown"
                        ),
                        timer.location_details,
                    ],
                )
            )
            row = " / ".join(
                [
                    date.strftime("%H:%M"),
                    f"<t:{int(date.timestamp())}:R>",
                    clean_cell(location),
                    clean_cell(
                        timer.structure_type.name if timer.structure_type else None
                    ),
                    clean_cell(timer.get_timer_type_display()),
                ]
            )
            if len(content) + len(row) + 1 > MESSAGE_LIMIT:
                pages.append(content)
                content = HEADER
            content += "\n" + row
    pages.append(content if content != HEADER else content + "\nNo timers.")
    return pages


class DiscordRateLimited(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__("Discord rate limit reached")


class DiscordClient:
    def __init__(self):
        self.token = getattr(settings, "STRUCTURETIMERS_DISCORD_BOT_TOKEN", "")
        if not self.token:
            raise ValueError("STRUCTURETIMERS_DISCORD_BOT_TOKEN is not configured")

    def request(self, method, channel_id, message_id=None, content=None, nonce=None):
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        if message_id:
            url += f"/{message_id}"
        payload = None
        if content is not None:
            payload = {"content": content, "allowed_mentions": {"parse": []}}
            if nonce:
                payload.update(nonce=nonce, enforce_nonce=True)
        response = requests.request(
            method,
            url,
            headers={"Authorization": f"Bot {self.token}"},
            json=payload,
            timeout=30,
        )
        if response.status_code == 429:
            raise DiscordRateLimited(float(response.json().get("retry_after", 60)))
        if response.status_code == 404 and method in ("PATCH", "DELETE"):
            # Only an unknown message is safe to replace; a missing channel is not.
            if response.json().get("code") == 10008:
                return None
        response.raise_for_status()
        return response.json() if response.status_code != 204 else None


def sync_board(board):
    """Called with the board row locked to serialize concurrent refreshes."""
    client = DiscordClient()
    # A channel change (or disabling the board) also cleans up the old messages.
    for message in board.messages.all():
        if message.channel_id != board.channel_id or not board.is_enabled:
            if message.message_id:
                client.request("DELETE", message.channel_id, message.message_id)
            message.delete()
    if not board.is_enabled:
        return
    pages = render_board(board)
    messages = list(board.messages.all())
    for position, content in enumerate(pages):
        message = messages[position] if position < len(messages) else None
        if message and message.message_id and message.content == content:
            continue
        if message and message.message_id:
            result = client.request(
                "PATCH", board.channel_id, message.message_id, content
            )
            if result is not None:
                message.content = content
                message.save(update_fields=["content"])
                continue
            message.delete()
            message = None
        if message is None:
            message = board.messages.create(
                channel_id=board.channel_id,
                message_id="",
                position=position,
                content="",
            )
        # Stable nonce also avoids duplicate creates after a short network timeout.
        result = client.request(
            "POST", board.channel_id, content=content, nonce=f"st-{message.pk}"
        )
        message.message_id = result["id"]
        message.content = content
        message.save(update_fields=["message_id", "content"])
    for message in messages[len(pages) :]:
        if message.message_id:
            client.request("DELETE", message.channel_id, message.message_id)
        message.delete()
