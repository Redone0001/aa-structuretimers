"""Operation access and timer membership shared by views and battle updates."""

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from .models import Operation, Timer


def require_operation_access(user, coordinator=False):
    """Require an operation role in addition to normal app access."""
    can_coordinate = user.has_perm("structuretimers.op_coordinator")
    if not user.has_perm("structuretimers.basic_access") or not (
        can_coordinate
        or (not coordinator and user.has_perm("structuretimers.op_viewer"))
    ):
        raise PermissionDenied()


def get_operation(user, pk):
    """Resolve a permitted operation without exposing private operation names."""
    require_operation_access(user)
    return get_object_or_404(Operation.visible_to(user), pk=pk)


def current_timers(user):
    """Use the Current tab's exact time window and destruction/visibility rules."""
    from .views import MAX_HOURS_PASSED  # pylint: disable=import-outside-toplevel

    return (
        Timer.objects.visible_to_user(user)
        .filter(
            structure__destroyed_at__isnull=True,
        )
        .filter_by_tab(tab_name="current", max_hours_passed=MAX_HOURS_PASSED)
    )


def operation_timers(operation, user):
    """Keep selected timers available as history, while respecting timer access."""
    return Timer.objects.visible_to_user(user).filter(
        operations=operation, date__isnull=False
    )
