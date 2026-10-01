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
the OCPP 1.6J transaction-ID sequence and the four TimescaleDB hypertables.
The three server defaults (``maintenance_status``, ``activation_status``,
``schema_version``) are also hand-added: the models do not declare them, and
``alembic check`` does not compare server defaults.

Revision ID: 0001_baseline_schema
Revises:
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

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
)

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
    op.create_index("ix_drivers_deleted_at", "drivers", ["deleted_at"], unique=False)
    op.create_index(
        op.f("ix_drivers_license_number"), "drivers", ["license_number"], unique=True
    )
    op.create_index(
        op.f("ix_drivers_phone_number"), "drivers", ["phone_number"], unique=True
    )
    op.create_index(op.f("ix_drivers_status"), "drivers", ["status"], unique=False)
    op.create_table(
        "fleets",
        sa.Column("fleet_id", sa.UUID(), nullable=False),
        sa.Column("fleet_code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "status", sa.Enum("ACTIVE", "INACTIVE", name="fleetstatus"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("fleet_id"),
    )
    op.create_index("ix_fleets_deleted_at", "fleets", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_fleets_fleet_code"), "fleets", ["fleet_code"], unique=True)
    op.create_index(op.f("ix_fleets_status"), "fleets", ["status"], unique=False)
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
        "vehicles",
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("license_plate", sa.String(length=20), nullable=False),
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("make", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=50), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "INACTIVE",
                "MAINTENANCE",
                "DECOMMISSIONED",
                name="vehiclestatus",
            ),
            nullable=False,
        ),
        sa.Column(
            "activation_status",
            sa.Enum(
                "PENDING",
                "DEVICE_ASSIGNED",
                "ACTIVATED",
                name="vehicleactivationstatus",
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("battery_capacity_kwh", sa.Double(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("vehicle_id"),
    )
    op.create_index(
        op.f("ix_vehicles_license_plate"), "vehicles", ["license_plate"], unique=True
    )
    op.create_index(op.f("ix_vehicles_status"), "vehicles", ["status"], unique=False)
    op.create_index(op.f("ix_vehicles_vin"), "vehicles", ["vin"], unique=True)
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
        "fleet_vehicle_memberships",
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("fleet_id", sa.UUID(), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["fleet_id"], ["fleets.fleet_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("membership_id"),
    )
    op.create_index(
        op.f("ix_fleet_vehicle_memberships_fleet_id"),
        "fleet_vehicle_memberships",
        ["fleet_id"],
        unique=False,
    )
    op.create_index(
        "ix_fleet_vehicle_memberships_fleet_time",
        "fleet_vehicle_memberships",
        ["fleet_id", "joined_at"],
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
        postgresql_where=sa.text("left_at IS NULL"),
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
        "uq_telematics_active_vehicle",
        "telematics",
        ["vehicle_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_telematics_deleted_at", "telematics", ["deleted_at"], unique=False
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
        "ix_vehicle_telemetry_vehicle_time",
        "vehicle_telemetry",
        ["vehicle_id", sa.literal_column("recorded_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_vehicle_telemetry_vehicle_received",
        "vehicle_telemetry",
        ["vehicle_id", sa.literal_column("received_at DESC")],
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


def downgrade() -> None:
    """Drop every application object; there is no earlier schema to restore."""
    _clear_application_schema()
