"""DDL of the table change history, used by the baseline migration.

``create_history`` builds, for one tracked table, its history table (every
source column copied with the same type but nullable and without any key or
unique constraint, plus ``history_id``, ``changed_at``, ``changed_by``,
``change_reason``) and the ``AFTER UPDATE`` trigger that copies the old row
into it. The columns are read from the live source table, so the history
always mirrors the table the migration just created. The history tables are
not SQLAlchemy models; ``migrations/env.py`` keeps ``*_history`` out of
autogenerate. The runtime side (``set_change_context``) is in
``app.libs.db.history``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.libs.db.history import (
    CHANGE_REASON_MAX_LENGTH,
    CHANGE_REASON_SETTING,
    CHANGE_USER_SETTING,
    UNSPECIFIED_CHANGE_REASON,
)

_SOURCE_COLUMNS_QUERY = """
    SELECT a.attname
    FROM pg_attribute a
    WHERE a.attrelid = CAST(CAST(:table_name AS text) AS regclass)
      AND a.attnum > 0
      AND NOT a.attisdropped
    ORDER BY a.attnum
"""
_PRIMARY_KEY_QUERY = """
    SELECT a.attname
    FROM pg_index i
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
    WHERE i.indrelid = CAST(CAST(:table_name AS text) AS regclass) AND i.indisprimary
"""


def _quote(identifier: str) -> str:
    """Quote an SQL identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def create_history(
    connection: Connection,
    *,
    source_table: str,
    history_table: str,
    untracked_columns: Sequence[str] = (),
) -> None:
    """Create the history table and the trigger of one tracked table.

    Args:
        connection: Migration connection; the source table, ``users`` and
            every column named here must already exist.
        source_table: The tracked table (single-column primary key).
        history_table: Name of the history table to create.
        untracked_columns: Columns a machine updates constantly; a change to
            them alone writes no history row.

    Raises:
        ValueError: If the source table has no single-column primary key or
            an untracked column does not exist.

    Side Effects:
        Creates the history table, its foreign keys and index, a trigger
        function and the trigger.
    """
    source_columns = list(
        connection.execute(
            sa.text(_SOURCE_COLUMNS_QUERY), {"table_name": source_table}
        ).scalars()
    )
    primary_keys = list(
        connection.execute(
            sa.text(_PRIMARY_KEY_QUERY), {"table_name": source_table}
        ).scalars()
    )
    if len(primary_keys) != 1:
        raise ValueError(f"{source_table} needs a single-column primary key")
    key_column = primary_keys[0]
    unknown_columns = set(untracked_columns) - set(source_columns)
    if unknown_columns:
        raise ValueError(f"{source_table} has no column {sorted(unknown_columns)}")

    source = _quote(source_table)
    history = _quote(history_table)
    key = _quote(key_column)

    # LIKE copies the types; NOT NULL always comes along, so drop it: one
    # source row appears many times and a later column has no old value.
    connection.execute(
        sa.text(
            f"CREATE TABLE {history} ("
            "history_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
            f"LIKE {source} EXCLUDING ALL, "
            "changed_at timestamptz NOT NULL DEFAULT now(), "
            "changed_by uuid, "
            f"change_reason varchar({CHANGE_REASON_MAX_LENGTH}) NOT NULL)"
        )
    )
    for column in source_columns:
        connection.execute(
            sa.text(
                f"ALTER TABLE {history} ALTER COLUMN {_quote(column)} DROP NOT NULL"
            )
        )
    connection.execute(
        sa.text(
            f"ALTER TABLE {history} ADD CONSTRAINT "
            f"{_quote(f'fk_{history_table}_{key_column}')} "
            f"FOREIGN KEY ({key}) REFERENCES {source} ({key}) ON DELETE RESTRICT"
        )
    )
    connection.execute(
        sa.text(
            f"ALTER TABLE {history} ADD CONSTRAINT "
            f"{_quote(f'fk_{history_table}_changed_by')} "
            'FOREIGN KEY (changed_by) REFERENCES "users" (user_id) '
            "ON DELETE RESTRICT"
        )
    )
    connection.execute(
        sa.text(
            f"CREATE INDEX {_quote(f'ix_{history_table}_{key_column}_time')} "
            f"ON {history} ({key}, changed_at)"
        )
    )

    tracked_columns = [
        column
        for column in source_columns
        if column != key_column and column not in set(untracked_columns)
    ]
    function_name = _quote(f"fn_{history_table}")
    copied_columns = ", ".join(_quote(column) for column in source_columns)
    old_values = ", ".join(f"OLD.{_quote(column)}" for column in source_columns)
    connection.execute(
        sa.text(
            f"CREATE OR REPLACE FUNCTION {function_name}() RETURNS trigger "
            "LANGUAGE plpgsql AS $fn$ BEGIN "
            f"INSERT INTO {history} ({copied_columns}, changed_at, changed_by, "
            "change_reason) "
            f"VALUES ({old_values}, now(), "
            f"nullif(current_setting('{CHANGE_USER_SETTING}', true), '')::uuid, "
            "left(coalesce("
            f"nullif(current_setting('{CHANGE_REASON_SETTING}', true), ''), "
            f"'{UNSPECIFIED_CHANGE_REASON}'), {CHANGE_REASON_MAX_LENGTH})); "
            "RETURN NULL; END $fn$"
        )
    )
    changed_condition = " OR ".join(
        f"OLD.{_quote(column)} IS DISTINCT FROM NEW.{_quote(column)}"
        for column in tracked_columns
    )
    update_of = ", ".join(_quote(column) for column in tracked_columns)
    connection.execute(
        sa.text(
            f"CREATE TRIGGER {_quote(f'trg_{history_table}')} "
            f"AFTER UPDATE OF {update_of} ON {source} "
            f"FOR EACH ROW WHEN ({changed_condition}) "
            f"EXECUTE FUNCTION {function_name}()"
        )
    )
