"""Shared type of a free-text reason typed by a person (RV-ID8, RV-AS8).

A reason ends up in a ``status_reason`` / ``change_reason`` column or in a
change-history row, which refuses a blank text. Validating it once, here,
means a reason of spaces is a 422 at the HTTP boundary on every endpoint
instead of a 500 from the database trigger.
"""

from typing import Annotated

from pydantic import StringConstraints

# Width of every reason column (``varchar(200)``) and of ``change_reason`` in
# the history tables; surrounding spaces are removed before the length check.
Reason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
