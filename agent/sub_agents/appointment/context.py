from contextlib import contextmanager
from contextvars import ContextVar


_booking_confirmation = ContextVar("booking_confirmation", default=None)


def current_booking_confirmation():
    """Return backend-verified booking data for the current agent invocation."""
    return _booking_confirmation.get()


@contextmanager
def booking_confirmation_scope(confirmation):
    """Expose trusted booking data without persisting duplicate session state."""
    token = _booking_confirmation.set(confirmation)
    try:
        yield
    finally:
        _booking_confirmation.reset(token)
