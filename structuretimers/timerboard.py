"""Render and synchronize persistent Discord timerboard messages."""

import datetime as dt
import math
import re
import unicodedata
from urllib.parse import quote

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


def clean_cell(value):
    """Normalize and bound text, escaping Discord markup outside code blocks."""
    value = " ".join(str(value or "Unknown").split()).replace("`", "'")
    value = "".join(
        char for char in value if not unicodedata.category(char).startswith("C")
    )[:160]
    return re.sub(r"([\\*_~|<>\[\]])", r"\\\1", value)


def board_page(rows, staging_name=None):
    staging = (
        f"Distance from staging: {clean_cell(staging_name)}"
        if staging_name
        else "Distance: no staging configured"
    )
    return HEADER + "\n" + staging + "\n\n" + "\n".join(rows or ["No timers"])


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
        timers.select_related(
            "eve_solar_system__eve_constellation__eve_region", "structure_type"
        )
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
    previous_window = None
    with override("en"):
        for timer in timers:
            date = timer.date.astimezone(dt.timezone.utc)
            system = timer.eve_solar_system
            location = "Unknown"
            if system:
                region = system.eve_constellation.eve_region
                region_path = quote(region.name.replace(" ", "_"), safe="")
                system_path = quote(system.name.replace(" ", "_"), safe="")
                url = f"https://evemaps.dotlan.net/map/{region_path}/{system_path}"
                location = clean_cell(system.name)
                # Malformed imported names must not overflow Discord's message limit.
                if len(url) <= 512:
                    location = f"[{location}]({url})"
            structure = structure_label(
                timer.structure_type.name if timer.structure_type else None
            )
            structure_text = clean_cell(structure)
            if structure in {"Fortizar", "Tatara", "Sotiyo", "Keepstar"}:
                structure_text = f"**{structure_text}**"
                if structure == "Keepstar":
                    structure_text += " 🏰"
            distance = distance_label(timer.distance_ly)
            distance = (
                distance.replace(" ", " LY • ", 1)
                if " " in distance
                else f"{distance} LY"
            )
            row = " • ".join(
                [
                    f"`{date:%Y-%m-%d %H:%M}`",
                    f"<t:{int(date.timestamp())}:R>",
                    f"{location} ({distance})",
                    structure_text,
                    clean_cell(timer.get_timer_type_display()),
                    clean_cell(timer.get_objective_display()).capitalize(),
                ]
            )
            # Rolling 24-hour windows, measured from this refresh, not midnight.
            window = int((date - current_time).total_seconds() // 86400)
            additions = []
            if rows and window != previous_window:
                additions.append("")
            additions.append(row)
            if rows and len(board_page(rows + additions, staging_name)) > MESSAGE_LIMIT:
                pages.append(board_page(rows, staging_name))
                # The message boundary already separates the groups; no edge spacer.
                rows = [row]
            else:
                rows.extend(additions)
            previous_window = window
    pages.append(board_page(rows, staging_name))
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
