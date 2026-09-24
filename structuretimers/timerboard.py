"""Render and synchronize persistent Discord timerboard messages."""

import datetime as dt
import math
import re
import unicodedata

from discordproxy.client import DiscordClient
from discordproxy.exceptions import DiscordProxyHttpError

from django.conf import settings
from django.db.models import (
    Case,
    FloatField,
    IntegerField,
    OuterRef,
    Subquery,
    Value,
    When,
)
from django.utils.timezone import now
from django.utils.translation import override

from .distance_ranges import distance_range
from .models import DistancesFromStaging, StagingSystem, Timer

HEADER = "**Structure timerboard — EVE time (UTC)**"
MESSAGE_LIMIT = 2000
COLUMNS = ("EVE", "In / Ago", "System", "Structure", "Timer", "Objective", "LY / Range")
# Previous table width was 74 display cells; allow at most 25% more.
MAX_TABLE_WIDTH = 92


def structure_label(name):
    """Abbreviate standard racial towers for Discord, preserving their size."""
    match = re.fullmatch(
        r"(?:Amarr|Gallente|Minmatar|Caldari) Control Tower(?: (Small|Medium|Large))?",
        name or "",
    )
    if match:
        size = match.group(1)
        return f"POS {size}" if size in ("Small", "Medium") else "POS"
    return name or "Unknown"


def distance_label(light_years):
    """Match the web board's rounding and use the raw distance for range tags."""
    if light_years is None:
        return "?"
    text = f"{math.ceil(light_years * 10) / 10:.1f}"
    badge = distance_range(light_years)
    return f"{text} {badge[0]}" if badge else text


def column_widths(rows):
    """Fit each page's contents, sharing spare width between longer columns."""
    widths = [
        max(cell_width(label), 5 if index == 0 else 0)
        for index, label in enumerate(COLUMNS)
    ]
    desired = [
        max([width] + [cell_width(row[index]) for row in rows])
        for index, width in enumerate(widths)
    ]
    remaining = MAX_TABLE_WIDTH - (3 * len(COLUMNS) + 1) - sum(widths)
    while remaining > 0:
        expanded = False
        for index in (2, 3, 6, 1, 4, 5, 0):
            if remaining and widths[index] < desired[index]:
                widths[index] += 1
                remaining -= 1
                expanded = True
        if not expanded:
            break
    return widths


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


def table_border(left, middle, right, widths):
    return left + middle.join("─" * (width + 2) for width in widths) + right


def table_row(values, widths):
    cells = [wrap_cell(value, width) for value, width in zip(values, widths)]
    lines = []
    for index in range(max(map(len, cells))):
        parts = []
        for cell, width in zip(cells, widths):
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


def table_page(rows, staging_name=None):
    rows = rows or [["", "", "No timers", "", "", "", ""]]
    widths = column_widths(rows)
    lines = [
        table_border("┌", "┬", "┐", widths),
        table_row(COLUMNS, widths),
        table_border("├", "┼", "┤", widths),
    ]
    lines.extend(table_row(row, widths) for row in rows)
    lines.append(table_border("└", "┴", "┘", widths))
    staging = (
        f"Distance from staging: `{clean_cell(staging_name)[:100]}`"
        if staging_name
        else "Distance: no staging configured"
    )
    return HEADER + "\n" + staging + "\n```text\n" + "\n".join(lines) + "\n```"


def render_board(board):
    """Show upcoming timers and a one-hour grace period for elapsed timers."""
    current_time = now()
    staging = (
        StagingSystem.objects.filter(eve_solar_system__isnull=False)
        .select_related("eve_solar_system")
        .order_by("-is_main", "pk")
        .first()
    )
    staging_name = staging.eve_solar_system.name if staging else None
    distance_query = (
        Subquery(
            DistancesFromStaging.objects.filter(
                timer_id=OuterRef("pk"), staging_system_id=staging.pk
            ).values("light_years")[:1]
        )
        if staging
        else Value(None, output_field=FloatField())
    )
    timers = Timer.objects.filter(
        date__gte=current_time - dt.timedelta(hours=1), timer_type__in=board.timer_types
    ).exclude(timer_type=Timer.Type.PRELIMINARY)
    if not board.include_unflagged:
        timers = timers.filter(discord_timerboard=True)
    timers = (
        timers.select_related("eve_solar_system", "structure_type")
        .annotate(
            distance_ly=distance_query,
            elapsed=Case(
                When(date__lt=current_time, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
        .order_by("elapsed", "date", "pk")
    )
    pages = []
    rows = []
    with override("en"):
        for timer in timers:
            date = timer.date.astimezone(dt.timezone.utc)
            location = (
                timer.eve_solar_system.name if timer.eve_solar_system else "Unknown"
            )
            row = [
                date.strftime("%H:%M"),
                relative_time(date, current_time),
                clean_cell(location),
                clean_cell(
                    structure_label(
                        timer.structure_type.name if timer.structure_type else None
                    )
                ),
                clean_cell(timer.get_timer_type_display()),
                clean_cell(timer.get_objective_display()).capitalize(),
                distance_label(timer.distance_ly),
            ]
            if rows and len(table_page(rows + [row], staging_name)) > MESSAGE_LIMIT:
                pages.append(table_page(rows, staging_name))
                rows = []
            rows.append(row)
    pages.append(table_page(rows, staging_name))
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
