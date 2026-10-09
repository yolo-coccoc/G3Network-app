"""Baseline schema: every application table, recreated from scratch.

During the bootstrap phase (no data worth keeping) the schema has exactly one
migration. A schema change edits this file instead of adding a new revision,
then ``make db-reset`` clears the database and rebuilds it from here (see
``.claude/rules/database.md``).

``upgrade()`` therefore starts by clearing every application-owned object, so
it also works on a database left behind by an older version of this file.
The clear step only touches tables, enum types and sequences in ``public``
that do not belong to an extension: ``alembic_version``, PostGIS's
``spatial_ref_sys`` and every TimescaleDB/PostGIS object are never dropped.
Never run this migration against a database holding data that must be kept.

Objects the SQLAlchemy models do not describe, and which autogenerate
therefore cannot produce, are written by hand at the end of ``upgrade()``:
the OCPP 1.6J transaction-ID sequence, the five TimescaleDB hypertables and
the change history of the tracked tables (``_TRACKED_TABLES``: a
``<singular>_history`` table and an ``AFTER UPDATE`` trigger each, built by
``app.libs.db.history_ddl``; ``env.py`` keeps ``*_history`` out of
autogenerate because the history tables are not models) and the two period
views ``vehicle_ownership_periods`` and ``battery_installation_periods``
(VH-10, VH-16), which read the history tables and are created after them.
The server defaults the models do not declare (``maintenance_status``,
``schema_version``) are also hand-added: ``alembic check`` does not compare
server defaults.

Revision ID: 0001_baseline_schema
Revises:
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.libs.db.history_ddl import create_history

revision: str = "0001_baseline_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Backs OCPP 1.6J's integer transactionId (the backend assigns it). A sequence
# is not transactional: a rolled-back transaction leaves a harmless gap.
_OCPP16_TRANSACTION_ID_SEQUENCE = "charging_ocpp16_transaction_id_seq"

# Time-series tables partitioned by TimescaleDB, with their time column.
# Every one keeps the time column in its primary key, as TimescaleDB requires.
_HYPERTABLES = (
    ("vehicle_telemetry", "recorded_at"),
    ("charging_session_events", "event_occurred_at"),
    ("charging_ocpp_messages", "occurred_at"),
    ("charging_session_measurements", "sampled_at"),
    ("access_audit_logs", "occurred_at"),
)

# Tables whose changes are audited (``@tracked *`` in the DBML): the history
# table that receives the old row on every update, and the columns a machine
# updates constantly that must not write a history row (none today).
_TRACKED_TABLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("users", "user_history", ()),
    ("organizations", "organization_history", ()),
    ("memberships", "membership_history", ()),
    ("organization_settings", "organization_setting_history", ()),
    ("fleets", "fleet_history", ()),
    ("vehicle_models", "vehicle_model_history", ()),
    ("vehicles", "vehicle_history", ()),
    ("battery_models", "battery_model_history", ()),
    ("batteries", "battery_history", ()),
    ("warranties", "warranty_history", ()),
)

# Period views over change history (DM-22, DM-27). Code reads the periods of a
# relationship only through these views, never from a history table. A
# "version" is a row of the history table (an earlier version of the source row,
# replaced at ``changed_at``) or the current source row, which sorts last.
_VEHICLE_OWNERSHIP_PERIODS_VIEW = """
    CREATE VIEW vehicle_ownership_periods AS
    WITH versions AS (
        SELECT vehicle_id, organization_id, acquired_at, history_id AS version_order
        FROM vehicle_history
        UNION ALL
        SELECT vehicle_id, organization_id, acquired_at, 9223372036854775807
        FROM vehicles
    ), marked AS (
        SELECT vehicle_id, organization_id, acquired_at, version_order,
               LAG(organization_id) OVER (
                   PARTITION BY vehicle_id ORDER BY version_order
               ) AS previous_organization_id
        FROM versions
    ), period_starts AS (
        SELECT vehicle_id, organization_id, acquired_at AS owned_from, version_order
        FROM marked
        WHERE organization_id IS DISTINCT FROM previous_organization_id
    )
    SELECT vehicle_id, organization_id, owned_from,
           LEAD(owned_from) OVER (
               PARTITION BY vehicle_id ORDER BY version_order
           ) AS owned_until
    FROM period_starts
"""
_BATTERY_INSTALLATION_PERIODS_VIEW = """
    CREATE VIEW battery_installation_periods AS
    WITH versions AS (
        SELECT battery_id, vehicle_id, installed_at, changed_at,
               history_id AS version_order
        FROM battery_history
        UNION ALL
        SELECT battery_id, vehicle_id, installed_at, NULL, 9223372036854775807
        FROM batteries
    ), marked AS (
        SELECT battery_id, vehicle_id, installed_at, version_order,
               LAG(vehicle_id) OVER (
                   PARTITION BY battery_id ORDER BY version_order
               ) AS previous_vehicle_id,
               LAG(changed_at) OVER (
                   PARTITION BY battery_id ORDER BY version_order
               ) AS previous_changed_at
        FROM versions
    )
    SELECT start_version.battery_id,
           start_version.vehicle_id,
           start_version.installed_at AS installed_from,
           (
               SELECT CASE
                          WHEN next_change.vehicle_id IS NOT NULL
                              THEN next_change.installed_at
                          ELSE next_change.previous_changed_at
                      END
               FROM marked next_change
               WHERE next_change.battery_id = start_version.battery_id
                 AND next_change.version_order > start_version.version_order
                 AND next_change.vehicle_id IS DISTINCT FROM
                     next_change.previous_vehicle_id
               ORDER BY next_change.version_order
               LIMIT 1
           ) AS installed_until
    FROM marked start_version
    WHERE start_version.vehicle_id IS NOT NULL
      AND start_version.vehicle_id IS DISTINCT FROM start_version.previous_vehicle_id
"""

# Application objects in ``public``: anything not owned by an extension
# (pg_depend deptype 'e'), excluding Alembic's own version table.
_NOT_EXTENSION_MEMBER = """
    NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = '{catalog}'::regclass
          AND d.objid = {oid}
          AND d.deptype = 'e'
    )
"""
_APPLICATION_TABLES_QUERY = f"""
    SELECT c.relname FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p')
      AND c.relname <> 'alembic_version'
      AND {_NOT_EXTENSION_MEMBER.format(catalog="pg_class", oid="c.oid")}
"""
_APPLICATION_SEQUENCES_QUERY = f"""
    SELECT c.relname FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'S'
      AND {_NOT_EXTENSION_MEMBER.format(catalog="pg_class", oid="c.oid")}
"""
_APPLICATION_ENUMS_QUERY = f"""
    SELECT t.typname FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = 'public'
      AND t.typtype = 'e'
      AND {_NOT_EXTENSION_MEMBER.format(catalog="pg_type", oid="t.oid")}
"""


def _clear_application_schema() -> None:
    """Drop every application table, sequence and enum type in ``public``.

    Side effects:
        Drops objects with CASCADE, so hypertable chunks and dependent
        constraints go with their table. Extension-owned objects and
        ``alembic_version`` are excluded by the catalog queries above.
    """
    connection = op.get_bind()
    # Tables first, so no column still references a type or sequence below.
    for query, object_kind in (
        (_APPLICATION_TABLES_QUERY, "TABLE"),
        (_APPLICATION_SEQUENCES_QUERY, "SEQUENCE"),
        (_APPLICATION_ENUMS_QUERY, "TYPE"),
    ):
        for object_name in connection.execute(sa.text(query)).scalars().all():
            op.execute(f'DROP {object_kind} IF EXISTS "{object_name}" CASCADE')


def upgrade() -> None:
    """Clear the application schema, then create every table from scratch."""
    _clear_application_schema()

    op.create_table(
        "charging_stations",
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_identity", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column(
            "location",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
            ),
            nullable=True,
        ),
        sa.Column("power_rating_kw", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("connector_standard", sa.String(length=20), nullable=True),
        sa.Column("operating_hours", sa.String(length=100), nullable=True),
        sa.Column(
            "maintenance_status",
            sa.Enum(
                "OPERATIONAL",
                "UNDER_MAINTENANCE",
                "OUT_OF_SERVICE",
                name="chargingstationmaintenancestatus",
            ),
            server_default="OPERATIONAL",
            nullable=False,
        ),
        sa.Column("ocpp_protocol_version", sa.String(length=20), nullable=True),
        sa.Column("vendor", sa.String(length=100), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("serial_number", sa.String(length=100), nullable=True),
        sa.Column("firmware_version", sa.String(length=100), nullable=True),
        sa.Column("last_boot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "charger_status",
            sa.Enum(
                "Available",
                "Occupied",
                "Reserved",
                "Unavailable",
                "Faulted",
                "Preparing",
                "Charging",
                "SuspendedEV",
                "SuspendedEVSE",
                "Finishing",
                name="chargingconnectorstatus",
            ),
            nullable=True,
        ),
        sa.Column(
            "charger_status_updated_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("charger_error_code", sa.String(length=50), nullable=True),
        sa.Column("charger_vendor_error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("station_id"),
        sa.UniqueConstraint("ocpp_identity", name="uq_charging_stations_ocpp_identity"),
    )
    op.create_index(
        "ix_charging_stations_deleted_at",
        "charging_stations",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_charging_stations_location",
        "charging_stations",
        ["location"],
        unique=False,
        postgresql_using="gist",
    )
    op.create_table(
        "drivers",
        sa.Column("driver_id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=100), nullable=False),
        sa.Column("phone_number", sa.String(length=20), nullable=False),
        sa.Column("license_number", sa.String(length=50), nullable=False),
        sa.Column(
            "status", sa.Enum("ACTIVE", "INACTIVE", name="driverstatus"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("driver_id"),
    )
    op.create_index(
        op.f("ix_drivers_deleted_at"), "drivers", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_drivers_license_number"), "drivers", ["license_number"], unique=True
    )
    op.create_index(
        op.f("ix_drivers_phone_number"), "drivers", ["phone_number"], unique=True
    )
    op.create_index(op.f("ix_drivers_status"), "drivers", ["status"], unique=False)
    op.create_table(
        "users",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone_number", sa.String(length=20), nullable=False),
        sa.Column("full_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("status_reason", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "deleted_at IS NULL OR status = 'LOCKED'", name="ck_users_deleted_is_locked"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "uq_users_active_email",
        "users",
        [sa.literal_column("lower(email)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND email IS NOT NULL"),
    )
    op.create_index(
        "uq_users_active_phone_number",
        "users",
        ["phone_number"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "organizations",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("is_internal", sa.Boolean(), nullable=False),
        sa.Column("legal_form", sa.String(length=20), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=False),
        sa.Column("tax_code", sa.String(length=20), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("status_reason", sa.String(length=200), nullable=True),
        sa.Column("account_manager_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'CLOSED') = (deleted_at IS NOT NULL)",
            name="ck_organizations_closed_iff_deleted",
        ),
        sa.ForeignKeyConstraint(
            ["account_manager_id"], ["users.user_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("organization_id"),
    )
    op.create_index(
        "uq_organizations_active_tax_code",
        "organizations",
        ["tax_code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "vehicle_models",
        sa.Column("vehicle_model_id", sa.UUID(), nullable=False),
        sa.Column("make", sa.String(length=50), nullable=False),
        sa.Column("model_name", sa.String(length=50), nullable=False),
        sa.Column("gross_vehicle_weight_kg", sa.Integer(), nullable=True),
        sa.Column("max_payload_kg", sa.Integer(), nullable=True),
        sa.Column(
            "nominal_battery_capacity_kwh",
            sa.Numeric(precision=7, scale=1),
            nullable=True,
        ),
        sa.Column(
            "consumption_curve", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("vehicle_model_id"),
    )
    op.create_index(
        "uq_vehicle_models_live_make_model",
        "vehicle_models",
        ["make", "model_name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "vehicles",
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("license_plate", sa.String(length=20), nullable=False),
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("vehicle_model_id", sa.UUID(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "INACTIVE", name="vehiclestatus"),
            nullable=False,
        ),
        sa.Column("status_reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "deleted_at IS NULL OR status = 'INACTIVE'",
            name="ck_vehicles_deleted_inactive",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.organization_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_model_id"],
            ["vehicle_models.vehicle_model_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("vehicle_id"),
    )
    op.create_index(
        op.f("ix_vehicles_organization_id"),
        "vehicles",
        ["organization_id"],
        unique=False,
    )
    op.create_index(op.f("ix_vehicles_status"), "vehicles", ["status"], unique=False)
    op.create_index(
        "uq_vehicles_live_license_plate",
        "vehicles",
        ["license_plate"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_vehicles_live_vin",
        "vehicles",
        ["vin"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "charging_evses",
        sa.Column("evse_id", sa.UUID(), nullable=False),
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_evse_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "ocpp_evse_id > 0", name="ck_charging_evses_ocpp_id_positive"
        ),
        sa.ForeignKeyConstraint(
            ["station_id"], ["charging_stations.station_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("evse_id"),
        sa.UniqueConstraint(
            "station_id", "ocpp_evse_id", name="uq_charging_evses_station_ocpp_id"
        ),
    )
    op.create_index(
        "ix_charging_evses_station_deleted",
        "charging_evses",
        ["station_id", "deleted_at"],
        unique=False,
    )
    op.create_table(
        "charging_ocpp_messages",
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_subprotocol", sa.String(length=20), nullable=False),
        sa.Column(
            "direction",
            sa.Enum("CP_TO_CSMS", "CSMS_TO_CP", name="chargingocppmessagedirection"),
            nullable=False,
        ),
        sa.Column("raw_frame", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"], ["charging_stations.station_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("message_id", "occurred_at"),
    )
    op.create_index(
        "ix_charging_ocpp_messages_station_time",
        "charging_ocpp_messages",
        ["station_id", "occurred_at"],
        unique=False,
    )
    op.create_table(
        "charging_station_configuration_entries",
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("capture_id", sa.UUID(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("is_readonly", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"], ["charging_stations.station_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("entry_id"),
    )
    op.create_index(
        "ix_charging_config_entries_station_captured",
        "charging_station_configuration_entries",
        ["station_id", "captured_at"],
        unique=False,
    )
    op.create_table(
        "driver_vehicle_assignments",
        sa.Column("assignment_id", sa.UUID(), nullable=False),
        sa.Column("driver_id", sa.UUID(), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unassigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["driver_id"], ["drivers.driver_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("assignment_id"),
    )
    op.create_index(
        op.f("ix_driver_vehicle_assignments_driver_id"),
        "driver_vehicle_assignments",
        ["driver_id"],
        unique=False,
    )
    op.create_index(
        "ix_driver_vehicle_assignments_driver_time",
        "driver_vehicle_assignments",
        ["driver_id", "assigned_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_driver_vehicle_assignments_vehicle_id"),
        "driver_vehicle_assignments",
        ["vehicle_id"],
        unique=False,
    )
    op.create_index(
        "uq_driver_vehicle_assignments_active_driver",
        "driver_vehicle_assignments",
        ["driver_id"],
        unique=True,
        postgresql_where=sa.text("unassigned_at IS NULL"),
    )
    op.create_index(
        "uq_driver_vehicle_assignments_active_vehicle",
        "driver_vehicle_assignments",
        ["vehicle_id"],
        unique=True,
        postgresql_where=sa.text("unassigned_at IS NULL"),
    )
    op.create_table(
        "legal_documents",
        sa.Column("legal_document_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.String(length=30), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("legal_document_id"),
        sa.UniqueConstraint(
            "purpose", "version", name="uq_legal_documents_purpose_version"
        ),
    )
    op.create_table(
        "notifications",
        sa.Column(
            "notification_id", sa.BigInteger(), autoincrement=True, nullable=False
        ),
        sa.Column(
            "notification_type",
            sa.Enum(
                "BATTERY_ALERT",
                "ANOMALY_ALERT",
                "SOH_ALERT",
                "DEVICE_OFFLINE_ALERT",
                "SOS_ALERT",
                "GEOFENCE_ALERT",
                name="notificationtype",
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum("INFO", "WARNING", "CRITICAL", name="notificationseverity"),
            nullable=False,
        ),
        sa.Column("vehicle_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("notification_id"),
    )
    op.create_index(
        "ix_notifications_vehicle_id", "notifications", ["vehicle_id"], unique=False
    )
    op.create_table(
        "one_time_codes",
        sa.Column("one_time_code_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("phone_number", sa.String(length=20), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("issued_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "failed_attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["issued_by"], ["users.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("one_time_code_id"),
    )
    op.create_index(
        "ix_one_time_codes_phone_time",
        "one_time_codes",
        ["phone_number", "created_at"],
        unique=False,
    )
    op.create_table(
        "support_cases",
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column(
            "case_type",
            sa.Enum("TICKET", "SOS", name="supportcasetype"),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.Enum(
                "TECHNICAL",
                "BATTERY",
                "CHARGING",
                "BREAKDOWN",
                "ACCIDENT",
                "BILLING",
                "OTHER",
                name="supportcasecategory",
            ),
            nullable=False,
        ),
        sa.Column(
            "channel",
            sa.Enum("IN_APP", "ZALO", "HOTLINE", name="supportcasechannel"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "ACKNOWLEDGED",
                "RESOLVED",
                "CLOSED",
                "CANCELLED",
                name="supportcasestatus",
            ),
            nullable=False,
        ),
        sa.Column("vehicle_id", sa.UUID(), nullable=True),
        sa.Column("driver_id", sa.UUID(), nullable=True),
        sa.Column("vin", sa.String(length=17), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column(
            "location",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
            ),
            nullable=True,
        ),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sla_response_minutes", sa.Integer(), nullable=False),
        sa.Column("response_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["driver_id"], ["drivers.driver_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("case_id"),
    )
    op.create_index(
        "ix_support_cases_deleted_at", "support_cases", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_support_cases_driver_id"), "support_cases", ["driver_id"], unique=False
    )
    op.create_index(
        "ix_support_cases_response_due_pending",
        "support_cases",
        ["response_due_at"],
        unique=False,
        postgresql_where=sa.text("first_responded_at IS NULL"),
    )
    op.create_index(
        op.f("ix_support_cases_status"), "support_cases", ["status"], unique=False
    )
    op.create_index(
        "ix_support_cases_status_created_at",
        "support_cases",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_support_cases_vehicle_created_at",
        "support_cases",
        ["vehicle_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_support_cases_vehicle_id"),
        "support_cases",
        ["vehicle_id"],
        unique=False,
    )
    op.create_table(
        "telematics",
        sa.Column("telematic_id", sa.UUID(), nullable=False),
        sa.Column("telematic_serial", sa.String(length=50), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "INACTIVE", "MAINTENANCE", name="telematicstatus"),
            nullable=False,
        ),
        sa.Column("firmware_version", sa.String(length=50), nullable=True),
        sa.Column("telemetry_interval_seconds", sa.Integer(), nullable=True),
        sa.Column("config_pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("telematic_id"),
    )
    op.create_index(
        op.f("ix_telematics_deleted_at"), "telematics", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_telematics_status"), "telematics", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_telematics_telematic_serial"),
        "telematics",
        ["telematic_serial"],
        unique=True,
    )
    op.create_index(
        op.f("ix_telematics_vehicle_id"), "telematics", ["vehicle_id"], unique=False
    )
    op.create_index(
        "uq_telematics_active_vehicle",
        "telematics",
        ["vehicle_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "battery_models",
        sa.Column("battery_model_id", sa.UUID(), nullable=False),
        sa.Column("manufacturer", sa.String(length=50), nullable=False),
        sa.Column("model_name", sa.String(length=50), nullable=False),
        sa.Column("chemistry", sa.String(length=10), nullable=False),
        sa.Column(
            "design_capacity_kwh", sa.Numeric(precision=7, scale=1), nullable=True
        ),
        sa.Column("nominal_voltage_v", sa.Numeric(precision=6, scale=1), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("battery_model_id"),
    )
    op.create_index(
        "uq_battery_models_live_manufacturer_model",
        "battery_models",
        ["manufacturer", "model_name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "batteries",
        sa.Column("battery_id", sa.UUID(), nullable=False),
        sa.Column("serial_number", sa.String(length=50), nullable=False),
        sa.Column("battery_model_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=True),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manufactured_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("status_reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "deleted_at IS NULL OR status = 'INACTIVE'",
            name="ck_batteries_deleted_inactive",
        ),
        sa.ForeignKeyConstraint(
            ["battery_model_id"],
            ["battery_models.battery_model_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.organization_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("battery_id"),
    )
    op.create_index(
        "uq_batteries_installed_vehicle",
        "batteries",
        ["vehicle_id"],
        unique=True,
        postgresql_where=sa.text("vehicle_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_batteries_live_serial_number",
        "batteries",
        ["serial_number"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "warranties",
        sa.Column("warranty_id", sa.UUID(), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=True),
        sa.Column("battery_id", sa.UUID(), nullable=True),
        sa.Column("telematic_id", sa.UUID(), nullable=True),
        sa.Column("station_id", sa.UUID(), nullable=True),
        sa.Column("warranty_type", sa.String(length=20), nullable=False),
        sa.Column("contract_reference", sa.String(length=100), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("limits", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("status_reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "deleted_at IS NULL OR status = 'VOIDED'",
            name="ck_warranties_deleted_voided",
        ),
        sa.CheckConstraint(
            "num_nonnulls(vehicle_id, battery_id, telematic_id, station_id) = 1",
            name="ck_warranties_one_link",
        ),
        sa.ForeignKeyConstraint(
            ["battery_id"], ["batteries.battery_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["station_id"], ["charging_stations.station_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["telematic_id"], ["telematics.telematic_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("warranty_id"),
    )
    op.create_index(
        op.f("ix_warranties_battery_id"), "warranties", ["battery_id"], unique=False
    )
    op.create_index(
        op.f("ix_warranties_ends_on"), "warranties", ["ends_on"], unique=False
    )
    op.create_index(
        op.f("ix_warranties_station_id"), "warranties", ["station_id"], unique=False
    )
    op.create_index(
        op.f("ix_warranties_telematic_id"),
        "warranties",
        ["telematic_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_warranties_vehicle_id"), "warranties", ["vehicle_id"], unique=False
    )
    op.create_table(
        "user_credentials",
        sa.Column("user_credential_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("credential_type", sa.String(length=20), nullable=False),
        sa.Column("secret_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_credential_id"),
    )
    op.create_index(
        "uq_user_credentials_active_type",
        "user_credentials",
        ["user_id", "credential_type"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "access_audit_logs",
        sa.Column(
            "access_audit_log_id", sa.BigInteger(), autoincrement=True, nullable=False
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("resource_id", sa.String(length=100), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.organization_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("access_audit_log_id", "occurred_at"),
    )
    op.create_table(
        "charging_connectors",
        sa.Column("connector_id", sa.UUID(), nullable=False),
        sa.Column("evse_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_connector_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "Available",
                "Occupied",
                "Reserved",
                "Unavailable",
                "Faulted",
                "Preparing",
                "Charging",
                "SuspendedEV",
                "SuspendedEVSE",
                "Finishing",
                name="chargingconnectorstatus",
            ),
            nullable=True,
        ),
        sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("vendor_error_code", sa.String(length=100), nullable=True),
        sa.Column("status_info", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "ocpp_connector_id > 0", name="ck_charging_connectors_ocpp_id_positive"
        ),
        sa.ForeignKeyConstraint(
            ["evse_id"], ["charging_evses.evse_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("connector_id"),
        sa.UniqueConstraint(
            "evse_id", "ocpp_connector_id", name="uq_charging_connectors_evse_ocpp_id"
        ),
    )
    op.create_index(
        "ix_charging_connectors_evse_deleted",
        "charging_connectors",
        ["evse_id", "deleted_at"],
        unique=False,
    )
    op.create_table(
        "fleets",
        sa.Column("fleet_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("parent_fleet_id", sa.UUID(), nullable=True),
        sa.Column("fleet_code", sa.String(length=50), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(name, fleet_code) >= 1", name="ck_fleets_name_or_code"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.organization_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_fleet_id"], ["fleets.fleet_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("fleet_id"),
    )
    op.create_index(
        op.f("ix_fleets_deleted_at"), "fleets", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_fleets_organization_id"), "fleets", ["organization_id"], unique=False
    )
    op.create_index(
        op.f("ix_fleets_parent_fleet_id"), "fleets", ["parent_fleet_id"], unique=False
    )
    op.create_index(
        "uq_fleets_live_organization_code",
        "fleets",
        ["organization_id", "fleet_code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND fleet_code IS NOT NULL"),
    )
    op.create_table(
        "memberships",
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("status_reason", sa.String(length=200), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.organization_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("membership_id"),
        sa.UniqueConstraint(
            "membership_id", "organization_id", name="uq_memberships_id_organization"
        ),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"], unique=False)
    op.create_index(
        "uq_memberships_active",
        "memberships",
        ["organization_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("left_at IS NULL"),
    )
    op.create_table(
        "organization_settings",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "telemetry_interval_seconds",
            sa.Integer(),
            server_default=sa.text("10"),
            nullable=False,
        ),
        sa.Column(
            "driving_session_auto_end_minutes",
            sa.Integer(),
            server_default=sa.text("120"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.organization_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("organization_id"),
    )
    op.create_table(
        "user_consents",
        sa.Column("user_consent_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("legal_document_id", sa.UUID(), nullable=False),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("device_label", sa.String(length=100), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["legal_document_id"],
            ["legal_documents.legal_document_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.organization_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_consent_id"),
    )
    op.create_index(
        "uq_user_consents_organization_legal_document",
        "user_consents",
        ["organization_id", "legal_document_id"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.create_index(
        "uq_user_consents_person_legal_document",
        "user_consents",
        ["user_id", "legal_document_id"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )
    op.create_table(
        "user_sessions",
        sa.Column("user_session_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("platform", sa.String(length=10), nullable=False),
        sa.Column("app_version", sa.String(length=20), nullable=True),
        sa.Column("device_label", sa.String(length=100), nullable=True),
        sa.Column("refresh_token_hash", sa.String(length=255), nullable=False),
        sa.Column("push_token", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.organization_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_session_id"),
        sa.UniqueConstraint("push_token"),
        sa.UniqueConstraint("refresh_token_hash"),
    )
    op.create_index(
        "ix_user_sessions_expires_at", "user_sessions", ["expires_at"], unique=False
    )
    op.create_index(
        "ix_user_sessions_user_id", "user_sessions", ["user_id"], unique=False
    )
    op.create_table(
        "user_state",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_organization_id", sa.UUID(), nullable=True),
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("login_locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["last_organization_id"],
            ["organizations.organization_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "vehicle_telemetry",
        sa.Column("message_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("message_uuid", sa.UUID(), nullable=False),
        sa.Column("telematic_id", sa.UUID(), nullable=False),
        sa.Column("telematic_serial", sa.String(length=50), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "location",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column("speed", sa.Double(), nullable=True),
        sa.Column("heading", sa.Double(), nullable=True),
        sa.Column("soc", sa.Double(), nullable=False),
        sa.Column("battery_voltage", sa.Double(), nullable=True),
        sa.Column("battery_current", sa.Double(), nullable=True),
        sa.Column("battery_temperature", sa.Double(), nullable=True),
        sa.Column("soh_percent", sa.Double(), nullable=True),
        sa.Column("cycle_count", sa.Integer(), nullable=True),
        sa.Column("motor_temperature", sa.Double(), nullable=True),
        sa.Column("odometer", sa.Double(), nullable=True),
        sa.Column("signal_strength", sa.BigInteger(), nullable=True),
        sa.Column(
            "error_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["telematic_id"], ["telematics.telematic_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("message_id", "recorded_at"),
        sa.UniqueConstraint(
            "telematic_id", "recorded_at", name="uq_telematic_recorded_at"
        ),
    )
    op.create_index(
        op.f("ix_vehicle_telemetry_message_uuid"),
        "vehicle_telemetry",
        ["message_uuid"],
        unique=False,
    )
    op.create_index(
        "ix_vehicle_telemetry_vehicle_received",
        "vehicle_telemetry",
        ["vehicle_id", sa.literal_column("received_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_vehicle_telemetry_vehicle_time",
        "vehicle_telemetry",
        ["vehicle_id", sa.literal_column("recorded_at DESC")],
        unique=False,
    )
    op.create_table(
        "charging_sessions",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("evse_id", sa.UUID(), nullable=False),
        sa.Column("connector_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_transaction_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "completed", name="chargingsessionstatus"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meter_start_wh", sa.Numeric(precision=24, scale=3), nullable=True),
        sa.Column("meter_end_wh", sa.Numeric(precision=24, scale=3), nullable=True),
        sa.Column("meter_end_sampled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "energy_delivered_wh", sa.Numeric(precision=24, scale=3), nullable=True
        ),
        sa.Column("id_tag", sa.String(length=20), nullable=True),
        sa.Column("stop_reason", sa.String(length=30), nullable=True),
        sa.Column("meter_stop_wh", sa.Numeric(precision=24, scale=3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connector_id"], ["charging_connectors.connector_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["evse_id"], ["charging_evses.evse_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["station_id"], ["charging_stations.station_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("session_id"),
        sa.UniqueConstraint(
            "station_id",
            "ocpp_transaction_id",
            name="uq_charging_sessions_station_transaction",
        ),
    )
    op.create_index(
        "ix_charging_sessions_connector_status",
        "charging_sessions",
        ["connector_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_charging_sessions_ended_at", "charging_sessions", ["ended_at"], unique=False
    )
    op.create_index(
        "ix_charging_sessions_evse_status",
        "charging_sessions",
        ["evse_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_charging_sessions_started_at",
        "charging_sessions",
        ["started_at"],
        unique=False,
    )
    op.create_index(
        "ix_charging_sessions_station_status",
        "charging_sessions",
        ["station_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_charging_sessions_status_updated",
        "charging_sessions",
        ["status", "updated_at"],
        unique=False,
    )
    op.create_table(
        "fleet_vehicle_memberships",
        sa.Column("fleet_vehicle_membership_id", sa.UUID(), nullable=False),
        sa.Column("fleet_id", sa.UUID(), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("added_by", sa.UUID(), nullable=True),
        sa.Column("removed_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["added_by"], ["users.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fleet_id"], ["fleets.fleet_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["removed_by"], ["users.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("fleet_vehicle_membership_id"),
    )
    op.create_index(
        "ix_fleet_vehicle_memberships_fleet_time",
        "fleet_vehicle_memberships",
        ["fleet_id", "added_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fleet_vehicle_memberships_vehicle_id"),
        "fleet_vehicle_memberships",
        ["vehicle_id"],
        unique=False,
    )
    op.create_index(
        "uq_fleet_vehicle_memberships_active_vehicle",
        "fleet_vehicle_memberships",
        ["vehicle_id"],
        unique=True,
        postgresql_where=sa.text("removed_at IS NULL"),
    )
    op.create_table(
        "geofences",
        sa.Column("geofence_id", sa.UUID(), nullable=False),
        sa.Column("fleet_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "boundary",
            geoalchemy2.types.Geography(
                geometry_type="POLYGON",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["fleet_id"], ["fleets.fleet_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("geofence_id"),
    )
    op.create_index(
        op.f("ix_geofences_fleet_id"), "geofences", ["fleet_id"], unique=False
    )
    op.create_table(
        "user_role_assignments",
        sa.Column("user_role_assignment_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by", sa.UUID(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["granted_by"], ["users.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["membership_id", "organization_id"],
            ["memberships.membership_id", "memberships.organization_id"],
            name="fk_user_role_assignments_membership_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id"], ["memberships.membership_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.organization_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_role_assignment_id"),
    )
    op.create_index(
        "uq_user_role_assignments_active_role",
        "user_role_assignments",
        ["membership_id", "role"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "uq_user_role_assignments_one_org_admin",
        "user_role_assignments",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("role = 'ORG_ADMIN' AND revoked_at IS NULL"),
    )
    op.create_table(
        "charging_session_events",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("event_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum("Started", "Updated", "Ended", name="chargingsessioneventtype"),
            nullable=False,
        ),
        sa.Column("seq_no", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["charging_sessions.session_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("event_id", "event_occurred_at"),
    )
    op.create_index(
        "ix_charging_session_events_session_time",
        "charging_session_events",
        ["session_id", "event_occurred_at", "event_id"],
        unique=False,
    )
    op.create_table(
        "charging_session_measurements",
        sa.Column("measurement_id", sa.UUID(), nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("measurand", sa.String(length=60), nullable=False),
        sa.Column("value", sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("context", sa.String(length=30), nullable=True),
        sa.Column("phase", sa.String(length=10), nullable=True),
        sa.Column("location", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["charging_sessions.session_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("measurement_id", "sampled_at"),
    )
    op.create_index(
        "ix_charging_measurements_session_measurand_time",
        "charging_session_measurements",
        ["session_id", "measurand", "sampled_at"],
        unique=False,
    )

    op.execute(
        f"CREATE SEQUENCE {_OCPP16_TRANSACTION_ID_SEQUENCE} AS integer "
        "START WITH 1 INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 NO CYCLE"
    )
    for table_name, time_column in _HYPERTABLES:
        op.execute(
            "SELECT create_hypertable("
            f"'{table_name}', '{time_column}', "
            "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)"
        )
    for source_table, history_table, untracked_columns in _TRACKED_TABLES:
        create_history(
            op.get_bind(),
            source_table=source_table,
            history_table=history_table,
            untracked_columns=untracked_columns,
        )
    # The views read the history tables, so they come after them.
    op.execute(_VEHICLE_OWNERSHIP_PERIODS_VIEW)
    op.execute(_BATTERY_INSTALLATION_PERIODS_VIEW)


def downgrade() -> None:
    """Drop every application object; there is no earlier schema to restore."""
    _clear_application_schema()
