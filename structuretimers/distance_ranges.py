"""Shared jump-range labels for the web and Discord timerboards."""

from django.utils.translation import gettext_lazy as _

DISTANCE_RANGE_BADGES = (
    (6.0, _("Super"), "success"),
    (7.0, _("Cap"), "primary"),
    (7.5, _("Command carrier"), "danger"),
)


def distance_range(light_years):
    """Return the most restrictive supported range label and badge style."""
    if light_years is not None:
        for maximum_distance, label, style in DISTANCE_RANGE_BADGES:
            if light_years < maximum_distance:
                return label, style
    return None
