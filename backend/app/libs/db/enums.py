"""SQLAlchemy helpers for PostgreSQL enum columns.

By default an ``Enum`` column stores Python member *names*. A domain enum
that mirrors an external protocol (e.g. OCPP connector statuses) passes
``values_callable=enum_values`` so the database stores the protocol's own
values instead (see ``.claude/rules/database.md``).
"""

from enum import Enum


def enum_values(enum_type: type[Enum]) -> list[str]:
    """Return an enum's member values in declaration order.

    Args:
        enum_type: The enum class whose values become the database labels.

    Returns:
        The list of member values.
    """
    return [str(member.value) for member in enum_type]
