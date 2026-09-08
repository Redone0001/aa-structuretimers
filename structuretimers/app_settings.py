"""Settings."""

from app_utils.app_settings import clean_setting

STRUCTURETIMERS_MAX_AGE_FOR_NOTIFICATIONS = clean_setting(
    "STRUCTURETIMERS_MAX_AGE_FOR_NOTIFICATIONS", 15
)
"""Grace period in minutes. A scheduled notification will still be sent
if its timer elapsed less than x minutes ago, and discarded as outdated
otherwise.
"""

STRUCTURETIMERS_NOTIFICATIONS_ENABLED = clean_setting(
    "STRUCTURETIMERS_NOTIFICATIONS_ENABLED", True
)
"""Whether notifications for timers are scheduled at all."""

STRUCTURETIMERS_TIMERS_OBSOLETE_AFTER_DAYS = clean_setting(
    "STRUCTURETIMERS_TIMERS_OBSOLETE_AFTER_DAYS", default_value=30, min_value=1
)
"""Minimum age in days for a timer to be considered obsolete.
Obsolete timers will automatically be deleted.
"""

STRUCTURETIMERS_DEFAULT_PAGE_LENGTH = clean_setting(
    "STRUCTURETIMERS_DEFAULT_PAGE_LENGTH", 10
)
"""Default page size for timerboard.
Must be an integer value from the current options as seen in the app.
"""

STRUCTURETIMERS_PAGING_ENABLED = clean_setting("STRUCTURETIMERS_PAGING_ENABLED", True)
"""Whether paging is enabled on the timerboard."""

STRUCTURETIMER_NOTIFICATION_SET_AVATAR = clean_setting(
    "STRUCTURETIMER_NOTIFICATION_SET_AVATAR", True
)
"""Whether structures sets the name and avatar icon of a webhook."""
