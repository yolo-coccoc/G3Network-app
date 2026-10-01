"""Shared internal data types used within the support domain."""

import enum


class SupportCaseType(str, enum.Enum):
    """Discriminates the two case lifecycles this domain currently covers.

    Both a ticket (F-I1) and an SOS (F-I2) are a "support case" with the
    same status/SLA machinery - kept as one table discriminated by this
    enum rather than two tables, since the spec ties them into one
    lifecycle (an SOS case is meant to be forwarded onward, and a
    forwarded case is meant to stay linked to its originating ticket).
    """

    TICKET = "TICKET"
    SOS = "SOS"


class SupportCaseCategory(str, enum.Enum):
    """Categorization of a support case, per F-I1's "categorized" output."""

    TECHNICAL = "TECHNICAL"
    BATTERY = "BATTERY"
    CHARGING = "CHARGING"
    BREAKDOWN = "BREAKDOWN"
    ACCIDENT = "ACCIDENT"
    BILLING = "BILLING"
    OTHER = "OTHER"


class SupportCaseChannel(str, enum.Enum):
    """Where a case originated. F-I1 says Zalo/hotline contacts are also logged as tickets."""

    IN_APP = "IN_APP"
    ZALO = "ZALO"
    HOTLINE = "HOTLINE"


class SupportCaseStatus(str, enum.Enum):
    """Lifecycle status of a support case.

    OPEN -> ACKNOWLEDGED -> RESOLVED -> CLOSED is the happy path;
    CANCELLED is a separate terminal state. CLOSED and CANCELLED are both
    terminal - no further status transition is accepted once a case
    reaches either.
    """

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


# Statuses after which a case accepts no further update (the service refuses
# any PATCH with `SupportCaseStateError`) and which stamp `closed_at`. Private:
# callers ask `is_terminal_status()` so the rule lives in one place.
_TERMINAL_STATUSES = frozenset({SupportCaseStatus.CLOSED, SupportCaseStatus.CANCELLED})


def is_terminal_status(status: SupportCaseStatus) -> bool:
    """Return whether a support case status accepts no further transition.

    Args:
        status: The status to check.

    Returns:
        True if the status is CLOSED or CANCELLED.
    """
    return status in _TERMINAL_STATUSES
