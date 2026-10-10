"""Shared jump-range labels for the web and Discord timerboards."""

from django.utils.translation import gettext_lazy as _

# Keys also drive regional-map presets; badge and map limits cannot drift.
JUMP_RANGES = (
    ("super", 6.0, _("Super"), "success"),
    ("carrier", 7.0, _("Cap"), "primary"),
    ("command", 7.5, _("Command carrier"), "danger"),
    ("blops", 8.0, _("Blops"), "info"),
)
DISTANCE_RANGE_BADGES = tuple(
    (limit, label, style) for _, limit, label, style in JUMP_RANGES
)


def distance_range(light_years):
    """Return the most restrictive supported range label and badge style."""
    if light_years is not None:
        for maximum_distance, label, style in DISTANCE_RANGE_BADGES:
            if light_years < maximum_distance:
                return label, style
    return None
