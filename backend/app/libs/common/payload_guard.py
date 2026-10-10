"""Guards for JSON payloads that devices send and that end up in PostgreSQL.

One hostile or broken message must never stop an ingestion process (RV-OP1,
RV-OP3). These helpers refuse what PostgreSQL would reject later, at the
consumer, where the message can simply be dropped: the JSON constants
``NaN`` / ``Infinity`` (not JSON, and refused by JSONB) and the NUL character
(refused by both ``varchar`` and JSONB). Integer range and string length are
checked by each message schema, which knows its own columns.
"""

import json
from collections.abc import Callable

INT16_MAX = 2**15 - 1
INT32_MAX = 2**31 - 1
INT64_MAX = 2**63 - 1


def _refuse_constant(constant: str) -> object:
    """Refuse ``NaN``, ``Infinity`` and ``-Infinity`` while parsing.

    Args:
        constant: The JSON constant token the parser met.

    Raises:
        ValueError: Always.
    """
    raise ValueError(f"the JSON constant {constant} is not accepted")


def loads_strict(text: str) -> object:
    """Parse JSON text, refusing the non-standard ``NaN`` / ``Infinity`` tokens.

    Args:
        text: The decoded message body.

    Returns:
        The parsed value.

    Raises:
        ValueError: For invalid JSON, a non-finite constant or an integer
            over Python's digit limit.
        RecursionError: For a nesting deep enough to exhaust the stack; the
            caller treats it like a ``ValueError``.
    """
    parse_constant: Callable[[str], object] = _refuse_constant
    return json.loads(text, parse_constant=parse_constant)


def contains_nul(value: object) -> bool:
    """Tell whether any string in a parsed JSON value contains a NUL character.

    Checks object keys too. Walks the structure with a stack, so a deep value
    cannot exhaust the recursion limit here.

    Args:
        value: A parsed JSON value.

    Returns:
        ``True`` when a key or a string value holds ``"\\u0000"``.
    """
    pending: list[object] = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if "\x00" in current:
                return True
        elif isinstance(current, dict):
            for key, item in current.items():
                if "\x00" in key:
                    return True
                pending.append(item)
        elif isinstance(current, list):
            pending.extend(current)
    return False
