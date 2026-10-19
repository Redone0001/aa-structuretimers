"""One replica write boundary shared by forms, ORM paths and the sync receiver."""

from contextlib import contextmanager
from contextvars import ContextVar

from django.core.exceptions import PermissionDenied

_receiving = ContextVar("aa_stratcom_receiving", default=False)


def receiving():
    """Whether this execution is inside an authenticated receive transaction."""
    return _receiving.get()


def read_only():
    """Missing configuration means an ordinary, independently writable board."""
    from .sync_models import TimerSyncConfig

    return TimerSyncConfig.objects.filter(pk=1, replica=True).exists()


def require_local_write():
    """Fail closed on replica mutations, including direct ORM integrations."""
    if not receiving() and read_only():
        raise PermissionDenied("This timerboard is a read-only replica of its master.")


@contextmanager
def receive_write():
    """Private bypass for validated incoming replication, scoped to this execution."""
    marker = _receiving.set(True)
    try:
        yield
    finally:
        _receiving.reset(marker)
