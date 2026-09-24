"""Render and synchronize persistent Discord timerboard messages."""

import datetime as dt
import math
import unicodedata

from discordproxy.client import DiscordClient
from discordproxy.exceptions import DiscordProxyHttpError

from django.conf import settings
from django.db.models import Case, IntegerField, Value, When
from django.utils.timezone import now
from django.utils.translation import override

from .models import Timer

HEADER = "**Structure timerboard — EVE time (UTC)**"
MESSAGE_LIMIT = 2000
COLUMNS = (
    ("# EVE UTC", 11),
    ("In / Ago", 14),
    ("Location", 26),
    ("Structure type", 18),
    ("Timer type", 12),
)


def clean_cell(value):
    """Keep user text inside the table, including pasted backticks/control codes."""
    value = " ".join(str(value or "Unknown").split()).replace("`", "'")
    return "".join(
        char for char in value if not unicodedata.category(char).startswith("C")
    )[:400]


def cell_width(value):
    return sum(
        (
            0
            if unicodedata.combining(c)
            else 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
        )
        for c in value
    )


def wrap_cell(value, width):
    lines, line = [], ""
    for char in value:
        if cell_width(line + char) > width:
            lines.append(line)
            line = ""
        line += char
    lines.append(line)
    if len(lines) > 8:
        lines = lines[:8]
        while cell_width(lines[-1]) >= width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


def table_border(left, middle, right):
    return left + middle.join("─" * (width + 2) for _, width in COLUMNS) + right


def table_row(values):
    cells = [wrap_cell(value, width) for value, (_, width) in zip(values, COLUMNS)]
    lines = []
    for index in range(max(map(len, cells))):
        parts = []
        for cell, (_, width) in zip(cells, COLUMNS):
            value = cell[index] if index < len(cell) else ""
            parts.append(" " + value + " " * (width - cell_width(value)) + " ")
        lines.append("│" + "│".join(parts) + "│")
    return "\n".join(lines)


def relative_time(date, current_time):
    seconds = (date - current_time).total_seconds()
    minutes = math.ceil(abs(seconds) / 60)
    if minutes >= 1440:
        duration = f"{minutes // 1440}d {(minutes % 1440) // 60}h"
    elif minutes >= 60:
        duration = f"{minutes // 60}h {minutes % 60}m"
    else:
        duration = f"{minutes}m"
    return f"in {duration}" if seconds >= 0 else f"{duration} ago"


def table_page(rows):
    lines = [
        table_border("┌", "┬", "┐"),
        table_row([label for label, _ in COLUMNS]),
        table_border("├", "┼", "┤"),
    ]
    for index, (row, _countdown) in enumerate(rows):
        if index:
            lines.append(table_border("├", "┼", "┤"))
        lines.append(row)
    if not rows:
        lines.append(table_row(["", "", "No timers", "", ""]))
    lines.append(table_border("└", "┴", "┘"))
    content = HEADER + "\n```text\n" + "\n".join(lines) + "\n```"
    if rows:
        content += "\nLive countdowns: " + " · ".join(
            countdown for _row, countdown in rows
        )
    return content


def render_board(board):
    """Show upcoming timers and a one-hour grace period for elapsed timers."""
    current_time = now()
    timers = Timer.objects.filter(
        date__gte=current_time - dt.timedelta(hours=1), timer_type__in=board.timer_types
    ).exclude(timer_type=Timer.Type.PRELIMINARY)
    if not board.include_unflagged:
        timers = timers.filter(discord_timerboard=True)
    timers = (
        timers.select_related("eve_solar_system", "structure_type")
        .annotate(
            elapsed=Case(
                When(date__lt=current_time, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .order_by("elapsed", "date", "pk")
    )
    pages = []
    rows = []
    with override("en"):
        for number, timer in enumerate(timers, start=1):
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
            row = table_row(
                [
                    f"{number} {date:%H:%M}",
                    relative_time(date, current_time),
                    clean_cell(location),
                    clean_cell(
                        timer.structure_type.name if timer.structure_type else None
                    ),
                    clean_cell(timer.get_timer_type_display()),
                ]
            )
            entry = (row, f"**{number}** <t:{int(date.timestamp())}:R>")
            if rows and len(table_page(rows + [entry])) > MESSAGE_LIMIT:
                pages.append(table_page(rows))
                rows = []
            rows.append(entry)
    pages.append(table_page(rows))
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
