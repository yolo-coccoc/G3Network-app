"""Thu gọn schema charging theo planner MVP điều kiện lý tưởng.

Migration cũ không bị sửa. Migration này loại technical status history và các
cột reliability khỏi schema active; downgrade khôi phục cấu trúc legacy tối
thiểu để local developer có thể quay lại planner cũ.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d4e8f90123"
down_revision: str | None = "b2c7d4e8f901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Bỏ status history và các cột chỉ phục vụ reliability path."""
    # Technical status không có giá trị trong giả định station/EVSE/connector
    # luôn online và active; drop table trước khi drop enum source action.
    for index_name in (
        "ix_charging_status_station_recorded",
        "ix_charging_status_evse_recorded",
        "ix_charging_status_connector_recorded",
        "ix_charging_status_action_recorded",
    ):
        op.drop_index(index_name, table_name="charging_station_status_events")
    op.drop_table("charging_station_status_events")
    op.execute("DROP TYPE IF EXISTS chargingstationsourceaction")

    # TimescaleDB yêu cầu bỏ unique/check/index liên quan trước khi bỏ cột.
    op.drop_index(
        "ix_charging_session_events_session_seq_time",
        table_name="charging_session_events",
    )
    op.drop_constraint(
        "uq_charging_session_events_session_seq_time",
        "charging_session_events",
        type_="unique",
    )
    op.drop_constraint(
        "ck_charging_session_events_seq_non_negative",
        "charging_session_events",
        type_="check",
    )
    for column_name in (
        "seq_no",
        "end_reason",
        "charging_state",
        "idempotency_key",
        "received_at",
        "sanitized_raw_payload",
    ):
        op.drop_column("charging_session_events", column_name)

    op.drop_index(
        "ix_charging_meter_session_measurand_sampled",
        table_name="charging_session_meter_values",
    )
    op.drop_constraint(
        "uq_charging_meter_session_sample_time",
        "charging_session_meter_values",
        type_="unique",
    )
    op.drop_constraint(
        "ck_charging_meter_seq_non_negative",
        "charging_session_meter_values",
        type_="check",
    )
    for column_name in (
        "measurand",
        "phase",
        "context",
        "source_value",
        "source_unit",
        "seq_no",
        "sample_idempotency_key",
        "received_at",
        "sanitized_raw_payload",
    ):
        op.drop_column("charging_session_meter_values", column_name)

    for constraint_name in (
        "ck_charging_sessions_seq_non_negative",
        "ck_charging_sessions_meter_start_non_negative",
        "ck_charging_sessions_meter_end_non_negative",
        "ck_charging_sessions_energy_non_negative",
    ):
        op.drop_constraint(constraint_name, "charging_sessions", type_="check")
    for column_name in (
        "last_event_at",
        "last_meter_at",
        "last_transaction_seq_no",
        "reconciliation_status",
        "reconciliation_error",
    ):
        op.drop_column("charging_sessions", column_name)

    # Enum labels thừa không được dùng ở application nữa. PostgreSQL không hỗ
    # trợ drop label trực tiếp nên dựng lại ba type nhỏ hơn sau khi bỏ cột.
    _replace_enum(
        "chargingsessionstatus",
        ("active", "completed"),
        "CASE WHEN status::text = 'completed' THEN 'completed' ELSE 'active' END",
    )
    _replace_enum(
        "chargingsessioneventtype",
        ("Started", "Updated", "Ended"),
        "CASE WHEN event_type::text = 'Started' THEN 'Started' "
        "WHEN event_type::text = 'Ended' THEN 'Ended' ELSE 'Updated' END",
    )
    op.execute("DROP TYPE IF EXISTS chargingreconciliationstatus")
    op.execute("DROP TYPE IF EXISTS chargingendreason")
    op.execute("DROP TYPE IF EXISTS chargingstate")


def _replace_enum(name: str, values: tuple[str, ...], expression: str) -> None:
    """Thay enum PostgreSQL và chuẩn hóa dữ liệu cũ về happy path."""
    legacy_name = f"{name}_legacy"
    quoted_values = ", ".join(f"'{value}'" for value in values)
    op.execute(f'ALTER TYPE "{name}" RENAME TO "{legacy_name}"')
    op.execute(f'CREATE TYPE "{name}" AS ENUM ({quoted_values})')
    table_name = (
        "charging_sessions"
        if name == "chargingsessionstatus"
        else "charging_session_events"
    )
    column_name = "status" if table_name == "charging_sessions" else "event_type"
    op.execute(
        f'ALTER TABLE "{table_name}" ALTER COLUMN "{column_name}" TYPE "{name}" '
        f'USING ({expression})::text::"{name}"'
    )
    op.execute(f'DROP TYPE "{legacy_name}"')


def downgrade() -> None:
    """Khôi phục các cột legacy ở dạng nullable để quay lại planner cũ."""
    # Downgrade chủ ý phục hồi cấu trúc, không tự khôi phục dữ liệu đã bị loại
    # khỏi history khi upgrade vì các payload/replay đó không còn tồn tại.
    op.execute(
        "CREATE TYPE chargingreconciliationstatus AS ENUM "
        "('pending', 'reconciled', 'inconsistent', 'unavailable')"
    )
    op.execute(
        "CREATE TYPE chargingendreason AS ENUM "
        "('normal', 'abnormal', 'offline', 'unknown')"
    )
    op.execute("CREATE TYPE chargingstate AS ENUM ('pending', 'active')")
    _replace_enum(
        "chargingsessionstatus",
        ("pending", "active", "ending", "completed", "interrupted"),
        "CASE WHEN status::text = 'completed' THEN 'completed' ELSE 'active' END",
    )
    _replace_enum(
        "chargingsessioneventtype",
        ("Started", "Updated", "Ended", "Interrupted"),
        "CASE WHEN event_type::text = 'Started' THEN 'Started' "
        "WHEN event_type::text = 'Ended' THEN 'Ended' ELSE 'Updated' END",
    )

    op.add_column(
        "charging_sessions",
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "charging_sessions",
        sa.Column("last_meter_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "charging_sessions",
        sa.Column("last_transaction_seq_no", sa.Integer(), nullable=True),
    )
    op.add_column(
        "charging_sessions",
        sa.Column(
            "reconciliation_status",
            sa.Enum(
                "pending",
                "reconciled",
                "inconsistent",
                "unavailable",
                name="chargingreconciliationstatus",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "charging_sessions",
        sa.Column("reconciliation_error", sa.Text(), nullable=True),
    )

    for column in (
        sa.Column("seq_no", sa.Integer(), nullable=True),
        sa.Column(
            "end_reason",
            sa.Enum(
                "normal", "abnormal", "offline", "unknown", name="chargingendreason"
            ),
            nullable=True,
        ),
        sa.Column(
            "charging_state",
            sa.Enum("pending", "active", name="chargingstate"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sanitized_raw_payload", sa.JSON(), nullable=True),
    ):
        op.add_column("charging_session_events", column)
    for column in (
        sa.Column("measurand", sa.String(100), nullable=True),
        sa.Column("phase", sa.String(50), nullable=True),
        sa.Column("context", sa.String(50), nullable=True),
        sa.Column("source_value", sa.Numeric(24, 6), nullable=True),
        sa.Column("source_unit", sa.String(20), nullable=True),
        sa.Column("seq_no", sa.Integer(), nullable=True),
        sa.Column("sample_idempotency_key", sa.String(255), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sanitized_raw_payload", sa.JSON(), nullable=True),
    ):
        op.add_column("charging_session_meter_values", column)

    op.execute(
        "CREATE TYPE chargingstationsourceaction AS ENUM ('BootNotification', 'Heartbeat', 'StatusNotification', 'NotifyEvent')"
    )
    op.create_table(
        "charging_station_status_events",
        sa.Column("status_event_id", sa.UUID(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("evse_id", sa.UUID(), nullable=True),
        sa.Column("connector_id", sa.UUID(), nullable=True),
        sa.Column(
            "source_action", sa.Enum(name="chargingstationsourceaction"), nullable=False
        ),
        sa.Column("technical_status", sa.String(50), nullable=True),
        sa.Column("event_code", sa.String(100), nullable=True),
        sa.Column("ocpp_message_id", sa.String(255), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sanitized_raw_payload", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("status_event_id", "recorded_at"),
    )
    op.execute(
        "SELECT create_hypertable('charging_station_status_events', 'recorded_at', if_not_exists => TRUE)"
    )
