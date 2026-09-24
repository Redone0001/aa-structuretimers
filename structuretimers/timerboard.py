"""Render and synchronize persistent Discord timerboard messages."""

import datetime as dt
import re

from discordproxy.client import DiscordClient
from discordproxy.exceptions import DiscordProxyHttpError

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


def delete_message(client, channel_id, message_id):
    """An already deleted message is success; channel/access errors are not."""
    try:
        client.delete_channel_message(
            channel_id=int(channel_id), message_id=int(message_id)
        )
    except DiscordProxyHttpError as exc:
        if exc.status != 404 or exc.code != 10008:
            raise


def sync_board(board):
    """Called with the board row locked to serialize concurrent refreshes."""
    client = DiscordClient(
        target=getattr(
            settings, "STRUCTURETIMERS_DISCORD_PROXY_TARGET", "localhost:50051"
        ),
        timeout=getattr(settings, "STRUCTURETIMERS_DISCORD_PROXY_TIMEOUT", 30),
    )
    # A channel change (or disabling the board) also cleans up the old messages.
    for message in board.messages.all():
        if message.channel_id != board.channel_id or not board.is_enabled:
            if message.message_id:
                delete_message(client, message.channel_id, message.message_id)
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
            try:
                client.edit_channel_message(
                    channel_id=int(board.channel_id),
                    message_id=int(message.message_id),
                    content=content,
                    suppress_mentions=True,
                )
            except DiscordProxyHttpError as exc:
                if exc.status != 404 or exc.code != 10008:
                    raise
                message.delete()
                message = None
            else:
                message.content = content
                message.save(update_fields=["content"])
                continue
        if message is None:
            message = board.messages.create(
                channel_id=board.channel_id,
                message_id="",
                position=position,
                content="",
            )
        # Stable nonce also avoids duplicate creates after a short network timeout.
        result = client.create_channel_message(
            channel_id=int(board.channel_id),
            content=content,
            suppress_mentions=True,
            nonce=f"st-{message.pk}",
            enforce_nonce=True,
        )
        message.message_id = str(result.id)
        # A deduplicated create can return the original content after a lost reply.
        # Keep the actual state so the next refresh edits it to the desired content.
        message.content = result.content
        message.save(update_fields=["message_id", "content"])
    for message in messages[len(pages) :]:
        if message.message_id:
            delete_message(client, message.channel_id, message.message_id)
        message.delete()
