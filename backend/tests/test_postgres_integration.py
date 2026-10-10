"""PostgreSQL integration test for the baseline migrations and telemetry repository."""

import asyncio
import dataclasses
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
import pytest_asyncio
import websockets
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.api.charging_session_flow as charging_session_flow
import app.domains.billing.router as billing_router
import app.domains.billing.service as billing_service
import app.domains.charging_sessions.repository as charging_repository
import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.service as charging_stations_service
import app.domains.drivers.repository as driver_repository
import app.domains.drivers.service as driver_service
import app.domains.drivers.trip_service as trip_service
import app.domains.fleet.repository as fleet_repository
import app.domains.fleet.service as fleet_service
import app.domains.identity.account_service as identity_account_service
import app.domains.identity.audit_service as identity_audit_service
import app.domains.identity.bootstrap as identity_bootstrap
import app.domains.identity.member_service as identity_member_service
import app.domains.identity.organization_service as identity_organization_service
import app.domains.identity.repository as identity_repository
import app.domains.identity.service as identity_service
import app.domains.notifications.delivery as notification_delivery
import app.domains.notifications.providers as notification_providers
import app.domains.notifications.repository as notification_repository
import app.domains.notifications.service as notifications_service
import app.domains.notifications.settings_service as notification_settings_service
import app.domains.support.repository as support_repository
import app.domains.support.service as support_service
import app.domains.telematics.monitoring.device_health_monitor as device_health_monitor
import app.domains.telematics.repository as telematics_repository
import app.domains.telematics.service as telematics_service
import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.repository as vehicle_repository
import app.domains.vehicles.service as vehicle_service
from app.domains.batteries.models import BatteryModel, BatteryModelModel
from app.domains.batteries.types import BatteryStatus
from app.domains.billing.exceptions import (
    InsufficientBalanceError,
    NoTariffInForceError,
    TariffConflictError,
    TariffInputError,
    WalletBlockedError,
)
from app.domains.billing.schemas import (
    TariffCreateRequest,
    TariffPeriodSchema,
    TariffStatusRequest,
    TariffVersionPublishRequest,
    TopUpRequest,
    WalletAdjustmentRequest,
    WalletStatusRequest,
)
from app.domains.billing.types import BankNotificationResult, WalletStatus
from app.domains.charging_sessions.exceptions import ChargingSessionAlreadyOpenError
from app.domains.charging_sessions.schemas import (
    ChargingSessionScanRequest,
    ChargingSessionStopRequest,
)
from app.domains.charging_sessions.types import EnergySeriesGranularity, SessionStatus
from app.domains.charging_stations.exceptions import (
    ChargingLocationAccessConflictError,
    ChargingStationCommandConflictError,
    ChargingStationCommandInputError,
    ChargingStationNotFoundError,
    ChargingStationOfflineError,
)
from app.domains.charging_stations.schemas import (
    ChargingConnectorCreateRequest,
    ChargingEvseCreateRequest,
    ChargingLocationAccessCreateRequest,
    ChargingLocationAccessRevokeRequest,
    ChargingLocationCreateRequest,
    ChargingLocationUpdateRequest,
    ChargingStationCommandCreateRequest,
    ChargingStationCreateRequest,
    ChargingStationUpdateRequest,
)
from app.domains.charging_stations.types import (
    ConnectorStandard,
    OcppMessageDirection,
    StationCommandOutcome,
    StationCommandType,
    StationListScope,
)
from app.domains.drivers.exceptions import (
    DriverTooFarFromVehicleError,
    TripInProgressConflictError,
)
from app.domains.drivers.models import DriverModel, TripModel
from app.domains.drivers.schemas import (
    DrivingSessionCheckInRequest,
    PersonalTripStartRequest,
    TripPlanRequest,
    TripStartRequest,
)
from app.domains.drivers.types import (
    CheckInMethod,
    CheckInWarning,
    DriverStatus,
    DrivingSessionEndCause,
    TripStatus,
)
from app.domains.fleet.exceptions import (
    FleetConflictError,
    FleetHasSubFleetsError,
    FleetHierarchyLoopError,
    FleetParentNotFoundError,
    FleetParentOrganizationMismatchError,
)
from app.domains.fleet.schemas import (
    FleetCreateRequest,
    FleetUpdateRequest,
    GeofenceCreateRequest,
    GeofencePolygonGeoJson,
    GeofenceUpdateRequest,
)
from app.domains.identity.exceptions import (
    AccessDeniedError,
    FailureRecordedError,
    InvalidCredentialsError,
    InvalidOneTimeCodeError,
    LoginLockedError,
    OrgAdminProtectedError,
    OrganizationInactiveError,
    OrganizationNotFoundError,
    SessionInvalidError,
)
from app.domains.identity.models import (
    MembershipModel,
    OrganizationModel,
    UserModel,
    UserSessionModel,
)
from app.domains.identity.schemas import (
    AdminHandoverRequest,
    InvitationAcceptRequest,
    LoginRequest,
    MemberInviteRequest,
    OneTimeCodeSendRequest,
    OrganizationCreateRequest,
    OrganizationStatusRequest,
    PasswordResetRequest,
    RefreshRequest,
    TokenResponse,
)
from app.domains.identity.types import (
    AccessAuditAction,
    MembershipEndKind,
    MembershipStatus,
    OneTimeCodePurpose,
    OrganizationLegalForm,
    OrganizationStatus,
    Principal,
    SessionPlatform,
    UserRole,
    UserStatus,
)
from app.domains.notifications.models import NotificationModel
from app.domains.notifications.schemas import NotificationSettingUpdateRequest
from app.domains.notifications.types import (
    NotificationListOrder,
    NotificationSeverity,
    NotificationType,
)
from app.domains.support.schemas import SupportSosCreateRequest
from app.domains.support.types import (
    SupportCaseCategory,
    SupportCaseChannel,
    SupportCaseStatus,
    SupportCaseType,
)
from app.domains.telematics.exceptions import TelematicConflictError
from app.domains.telematics.models import TelematicModel
from app.domains.telematics.schemas import (
    TelematicCreateRequest,
    TelematicStatusEnvelope,
    TelematicStatusMessage,
)
from app.domains.telematics.types import TelematicHealthState, TelematicStatus
from app.domains.telemetry.models import TelemetryModel
from app.domains.telemetry.schemas import TelemetryEnvelope, TelemetryMessage
from app.domains.telemetry.types import ReportGranularity, VehicleActivationStatus
from app.domains.vehicles.models import VehicleModel, VehicleModelModel
from app.domains.vehicles.types import VehicleStatus
from app.domains.warranties.models import WarrantyModel
from app.domains.warranties.types import WarrantyStatus, WarrantyType
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location
from app.libs.db.history import UNSPECIFIED_CHANGE_REASON, set_change_context
from tests.builders import build_organization_record
from tests.principals import ACTOR_USER_ID, build_internal_principal, build_principal

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="Set RUN_DB_INTEGRATION=1 to run the PostgreSQL integration test",
)


def _backend_root() -> Path:
    """Return the backend directory containing alembic.ini."""
    return Path(__file__).resolve().parents[1]


def _quote_identifier(identifier: str) -> str:
    """Quote a test-generated database name before putting it into a SQL statement."""
    return '"' + identifier.replace('"', '""') + '"'


def _database_connection_kwargs(database: str) -> dict[str, object]:
    """Convert DATABASE_URL into asyncpg connection parameters for one database."""
    url = make_url(settings.DATABASE_URL)
    return {
        "database": database,
        "user": url.username,
        "password": url.password,
        "host": url.host or "localhost",
        "port": url.port or 5432,
    }


async def _create_database(database: str) -> None:
    """Create a temporary database and enable the extensions needed by the Timescale/PostGIS migration."""
    connection = await asyncpg.connect(**_database_connection_kwargs("postgres"))
    try:
        await connection.execute(f"CREATE DATABASE {_quote_identifier(database)}")
    finally:
        await connection.close()

    connection = await asyncpg.connect(**_database_connection_kwargs(database))
    try:
        await connection.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        await connection.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    finally:
        await connection.close()


async def _drop_database(database: str) -> None:
    """Close any leftover connections and drop the temporary database after the test."""
    connection = await asyncpg.connect(**_database_connection_kwargs("postgres"))
    try:
        await connection.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1 AND pid <> pg_backend_pid()
            """,
            database,
        )
        await connection.execute(
            f"DROP DATABASE IF EXISTS {_quote_identifier(database)}"
        )
    finally:
        await connection.close()


def _run_alembic(database_url: str, *arguments: str) -> None:
    """Run an Alembic command with the temporary database's DATABASE_URL."""
    alembic = Path(sys.executable).with_name("alembic")
    environment = os.environ | {"DATABASE_URL": database_url}
    result = subprocess.run(
        [str(alembic), *arguments],
        cwd=_backend_root(),
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Alembic {' '.join(arguments)} failed:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


async def _insert_test_actor(database_url: str) -> None:
    """Insert the user every test principal acts as (history and audit FKs).

    Args:
        database_url: URL of the temporary database, migrated to head.
    """
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db, db.begin():
            db.add(
                UserModel(
                    user_id=ACTOR_USER_ID,
                    phone_number="+84900000000",
                    full_name="Test actor",
                    status=UserStatus.ACTIVE.value,
                )
            )
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def temporary_database() -> AsyncIterator[str]:
    """Spin up a temporary database, verify upgrade/downgrade/upgrade, then clean up."""
    database = f"g3network_test_{uuid4().hex[:12]}"
    base_url = make_url(settings.DATABASE_URL)
    database_url = base_url.set(database=database).render_as_string(hide_password=False)

    try:
        await _create_database(database)
    except (OSError, asyncpg.PostgresConnectionError) as error:
        pytest.skip(f"PostgreSQL is not ready for the integration test: {error}")
    except asyncpg.InsufficientPrivilegeError as error:
        pytest.skip(
            f"PostgreSQL user lacks privilege to create a test database: {error}"
        )

    try:
        _run_alembic(database_url, "upgrade", "head")
        _run_alembic(database_url, "downgrade", "base")
        _run_alembic(database_url, "upgrade", "head")
        await _insert_test_actor(database_url)
        yield database_url
    finally:
        await _drop_database(database)


@pytest.mark.asyncio
async def test_migration_upgrade_downgrade_upgrade_creates_baseline(
    temporary_database: str,
) -> None:
    """The baseline can be built, torn down, and rebuilt on a temporary PostgreSQL database."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            tables = set(
                (
                    await connection.execute(
                        text("""
                            SELECT table_name
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                            AND table_name IN (
                                'vehicles', 'telematics', 'telemetry',
                                'charging_locations', 'charging_location_access',
                                'charging_stations', 'charging_station_state',
                                'charging_evses', 'charging_connectors',
                                'charging_connector_state', 'charging_sessions',
                                'charging_session_measurements',
                                'notification_recipients',
                                'organization_notification_settings',
                                'tariffs', 'tariff_versions',
                                'charging_session_bills', 'payments', 'wallets',
                                'wallet_transactions',
                                'charging_ocpp_messages',
                                'charging_station_commands',
                                'charging_station_configuration_captures',
                                'charging_station_configuration_entries'
                            )
                            """)
                    )
                ).scalars()
            )
            hypertables = set(
                (
                    await connection.execute(
                        text(
                            "SELECT hypertable_name "
                            "FROM timescaledb_information.hypertables"
                        )
                    )
                ).scalars()
            )

        # Pinned to the single bootstrap-phase baseline revision (see
        # .claude/rules/database.md): a schema change edits that revision
        # instead of adding a new one, so the head never moves.
        assert version == "0001_baseline_schema"
        assert len(tables) == 24
        assert "charging_session_events" not in tables
        # The raw OCPP message log must be a real TimescaleDB hypertable
        # partitioned on occurred_at, not just an ordinary table.
        # Exactly four hypertables: the session events table is gone (CE-15);
        # the measurements, the raw message log, telemetry and the access
        # audit log remain.
        assert hypertables == {
            "telemetry",
            "charging_ocpp_messages",
            "charging_session_measurements",
            "access_audit_logs",
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ocpp16_transaction_id_sequence_is_32_bit_and_increasing(
    temporary_database: str,
) -> None:
    """OCPP 1.6J transaction IDs come from a bounded, non-cycling database sequence."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory.begin() as db:
            first = await charging_repository.next_ocpp16_transaction_id(db)
            second = await charging_repository.next_ocpp16_transaction_id(db)
            details = (
                await db.execute(
                    text(
                        "SELECT data_type, max_value, cycle FROM pg_sequences "
                        "WHERE sequencename = 'charging_ocpp16_transaction_id_seq'"
                    )
                )
            ).one()
        assert (first, second) == (1, 2)
        assert details.data_type == "integer"
        assert details.max_value == 2147483647
        assert details.cycle is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_telemetry_repository_round_trip_rolls_back(
    temporary_database: str,
) -> None:
    """The repository writes and reads real telemetry, then the transaction rolls back cleanly."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    vehicle_id = uuid4()
    telematic_id = uuid4()
    serial = f"IT-TBOX-{uuid4().hex[:12]}"
    vin = f"1{uuid4().hex[:16]}"
    license_plate = f"IT-{uuid4().hex[:12]}"
    recorded_at = datetime.now(timezone.utc)

    try:
        async with session_factory() as session:
            organization_id, vehicle_model_id = await _insert_vehicle_parents(session)
            vehicle = VehicleModel(
                vehicle_id=vehicle_id,
                organization_id=organization_id,
                vehicle_model_id=vehicle_model_id,
                license_plate=license_plate,
                vin=vin,
                year=2026,
                status=VehicleStatus.ACTIVE,
            )
            telematic = TelematicModel(
                telematic_id=telematic_id,
                telematic_serial=serial,
                organization_id=organization_id,
                acquired_at=recorded_at,
                vehicle_id=vehicle_id,
                status=TelematicStatus.ACTIVE,
            )
            session.add(vehicle)
            await session.flush()
            session.add(telematic)
            await session.flush()

            inserted = await telemetry_repository.insert_telemetry(
                session,
                {
                    "organization_id": organization_id,
                    "device_message_id": uuid4(),
                    "telematic_id": telematic_id,
                    "vehicle_id": vehicle_id,
                    "recorded_at": recorded_at,
                    "received_at": recorded_at,
                    "location": coordinates_to_location(10.8, 106.7),
                    "speed_kmh": 42.0,
                    "heading_degrees": 180.0,
                    "soc_percent": 80.0,
                    "battery_voltage_v": 650.0,
                    "battery_current_a": -120.0,
                    "battery_temperature_celsius": 30.0,
                    "motor_temperature_celsius": 45.0,
                    "odometer_km": 12500.5,
                    "signal_dbm": -70,
                    "error_codes": {"codes": []},
                    "raw_payload": {"source": "postgres-integration"},
                },
            )
            latest = await telemetry_repository.get_latest_vehicle_telemetry(
                session, vehicle_id
            )

            assert inserted == 1
            assert latest is not None
            assert latest.telematic_id == telematic_id
            assert latest.organization_id == organization_id
            assert latest.soc_percent == 80.0

            await session.rollback()

        async with session_factory() as session:
            vehicle_after_rollback = await vehicle_repository.get_by_id(
                session, vehicle_id
            )
            telemetry_after_rollback = await session.scalar(
                select(TelemetryModel.message_id).where(
                    TelemetryModel.vehicle_id == vehicle_id
                )
            )

            assert vehicle_after_rollback is None
            assert telemetry_after_rollback is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_last_telemetry_time_follows_receive_clock_not_device_clock(
    temporary_database: str,
) -> None:
    """A future-dated device clock cannot hide newer arrivals (F-J1/F-J3).

    Regression: resolve_last_telemetry_at took the row with the largest
    device `recorded_at` and returned its `received_at`, so one reading
    stamped a day in the future stayed "latest" forever and newer arrivals
    looked like silence (false device-offline alerts).
    """
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    vehicle_id, telematic_id = uuid4(), uuid4()
    serial = f"IT-TBOX-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    def reading(recorded_at: datetime, received_at: datetime) -> dict[str, object]:
        """Build one minimal telemetry row for this vehicle."""
        return {
            "organization_id": organization_id,
            "device_message_id": uuid4(),
            "telematic_id": telematic_id,
            "vehicle_id": vehicle_id,
            "recorded_at": recorded_at,
            "received_at": received_at,
            "location": coordinates_to_location(10.8, 106.7),
            "soc_percent": 80.0,
            "raw_payload": {"source": "postgres-integration"},
        }

    try:
        async with session_factory() as session:
            organization_id, vehicle_model_id = await _insert_vehicle_parents(session)
            session.add(
                VehicleModel(
                    vehicle_id=vehicle_id,
                    organization_id=organization_id,
                    vehicle_model_id=vehicle_model_id,
                    license_plate=f"IT-{uuid4().hex[:12]}",
                    vin=f"1{uuid4().hex[:16]}",
                    year=2026,
                    status=VehicleStatus.ACTIVE,
                )
            )
            await session.flush()
            session.add(
                TelematicModel(
                    telematic_id=telematic_id,
                    telematic_serial=serial,
                    organization_id=organization_id,
                    acquired_at=now,
                    vehicle_id=vehicle_id,
                    status=TelematicStatus.ACTIVE,
                )
            )
            await session.flush()
            # Received 2 h ago but stamped by the device a day in the future...
            await telemetry_repository.insert_telemetry(
                session, reading(now + timedelta(days=1), now - timedelta(hours=2))
            )
            # ...then a correctly stamped reading received just now.
            await telemetry_repository.insert_telemetry(
                session, reading(now - timedelta(minutes=1), now)
            )

            last_seen = await telemetry_service.resolve_last_telemetry_at(
                session, vehicle_id
            )

            assert last_seen == now
            await session.rollback()
    finally:
        await engine.dispose()


def _free_port() -> int:
    """Ask the operating system for a TCP port nothing is listening on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for_port(port: int, timeout_seconds: float = 30.0) -> None:
    """Block until something accepts connections on ``port`` or fail the test."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise AssertionError(f"Nothing listened on port {port} after {timeout_seconds}s")


async def _provision_location(
    engine: object,
    *,
    longitude: float = 106.0,
    is_public: bool = True,
    status: str = "ACTIVE",
) -> tuple[UUID, UUID]:
    """Insert an organization and a location it owns; return both IDs."""
    organization = build_organization_record()
    organization_id = organization.organization_id
    async with AsyncSession(engine) as db, db.begin():  # type: ignore[arg-type]
        db.add(organization)
    location_id = uuid4()
    now = datetime.now(timezone.utc)
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        await connection.execute(
            text(
                "INSERT INTO charging_locations (location_id, organization_id, "
                "display_name, address, coordinates, is_public, status, "
                "status_reason, created_at, updated_at) VALUES (:l, :o, 'IT location', "
                "'IT road', ST_GeogFromText(:p), :pub, :st, :reason, :t, :t)"
            ),
            {
                "l": location_id,
                "o": organization_id,
                "p": f"SRID=4326;POINT({longitude} 10.0)",
                "pub": is_public,
                "st": status,
                "reason": None if status == "ACTIVE" else "closed for the test",
                "t": now,
            },
        )
    return organization_id, location_id


async def _provision_station_at(
    engine: object,
    location_id: UUID,
    identity: str,
    connector_statuses: list[str | None],
    *,
    status: str = "ACTIVE",
) -> tuple[UUID, list[UUID]]:
    """Insert a charger with its state row and one EVSE/connector per given gun status.

    Returns the station ID and the connector IDs, in EVSE-number order. The
    gun status goes to ``charging_connector_state`` (what the charger reported).
    """
    now = datetime.now(timezone.utc)
    station_id = uuid4()
    connector_ids: list[UUID] = []
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        await connection.execute(
            text(
                "INSERT INTO charging_stations (station_id, location_id, "
                "ocpp_identity, registered_serial_number, status, status_reason, "
                "created_at, updated_at) VALUES (:s, :l, :i, :i, :st, :reason, :t, :t)"
            ),
            {
                "s": station_id,
                "l": location_id,
                "i": identity,
                "st": status,
                "reason": None if status == "ACTIVE" else "repair",
                "t": now,
            },
        )
        await connection.execute(
            text("INSERT INTO charging_station_state (station_id) VALUES (:s)"),
            {"s": station_id},
        )
        for evse_number, connector_status in enumerate(connector_statuses, start=1):
            evse_id, connector_id = uuid4(), uuid4()
            await connection.execute(
                text(
                    "INSERT INTO charging_evses (evse_id, station_id, ocpp_evse_id, "
                    "emi3_evse_id, status, created_at, updated_at) "
                    "VALUES (:e, :s, :n, :emi3, 'ACTIVE', :t, :t)"
                ),
                {
                    "e": evse_id,
                    "s": station_id,
                    "n": evse_number,
                    "emi3": f"VN*G3N*E{identity}-{evse_number}",
                    "t": now,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO charging_connectors (connector_id, evse_id, "
                    "ocpp_connector_id, standard, max_power_kw, max_voltage_v, "
                    "max_current_a, created_at, updated_at) VALUES "
                    "(:c, :e, 1, 'IEC_62196_T2_COMBO', 120, 1000, 250, :t, :t)"
                ),
                {"c": connector_id, "e": evse_id, "t": now},
            )
            await connection.execute(
                text(
                    "INSERT INTO charging_connector_state (connector_id, status, "
                    "status_updated_at) VALUES (:c, :st, :t)"
                ),
                {"c": connector_id, "st": connector_status, "t": now},
            )
            connector_ids.append(connector_id)
    return station_id, connector_ids


async def _provision_organization_and_user(engine: object) -> tuple[UUID, UUID]:
    """Insert an organization and a user; return both IDs (a payer and a scanner)."""
    organization = build_organization_record()
    user = UserModel(
        phone_number=f"+849{uuid4().int % 10**8:08d}",
        full_name="Scanner",
        status=UserStatus.ACTIVE.value,
    )
    async with AsyncSession(engine, expire_on_commit=False) as db, db.begin():  # type: ignore[arg-type]
        db.add_all([organization, user])
    return organization.organization_id, user.user_id


async def _scan(engine: object, identity: str, id_token: str) -> UUID:
    """Insert the PENDING session of a QR scan on a charger; return its ID (CE-10)."""
    organization_id, user_id = await _provision_organization_and_user(engine)
    session_id = uuid4()
    now = datetime.now(timezone.utc)
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        await connection.execute(
            text(
                "INSERT INTO charging_sessions (session_id, station_id, "
                "organization_id, started_by, status, id_token, created_at, "
                "updated_at) SELECT :x, station_id, :o, :u, 'PENDING', :tok, :t, :t "
                "FROM charging_stations WHERE ocpp_identity = :i"
            ),
            {
                "x": session_id,
                "o": organization_id,
                "u": user_id,
                "tok": id_token,
                "t": now,
                "i": identity,
            },
        )
    return session_id


async def _provision_station(
    engine: object, identity: str, evse_ids: list[int]
) -> UUID:
    """Insert a public location with a station and one EVSE (and connector) per EVSE number.

    Returns:
        The organization that owns the location (whose tariff prices the charger).
    """
    owner_id, location_id = await _provision_location(engine)
    station_id, _ = await _provision_station_at(engine, location_id, identity, [])
    now = datetime.now(timezone.utc)
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        for evse_number in evse_ids:
            evse_id, connector_id = uuid4(), uuid4()
            await connection.execute(
                text(
                    "INSERT INTO charging_evses (evse_id, station_id, ocpp_evse_id, "
                    "emi3_evse_id, status, created_at, updated_at) "
                    "VALUES (:e, :s, :n, :emi3, 'ACTIVE', :t, :t)"
                ),
                {
                    "e": evse_id,
                    "s": station_id,
                    "n": evse_number,
                    "emi3": f"VN*G3N*E{identity}-{evse_number}",
                    "t": now,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO charging_connectors (connector_id, evse_id, "
                    "ocpp_connector_id, standard, max_power_kw, max_voltage_v, "
                    "max_current_a, created_at, updated_at) VALUES "
                    "(:c, :e, 1, 'IEC_62196_T2_COMBO', 120, 1000, 250, :t, :t)"
                ),
                {"c": connector_id, "e": evse_id, "t": now},
            )
            await connection.execute(
                text("INSERT INTO charging_connector_state (connector_id) VALUES (:c)"),
                {"c": connector_id},
            )
    return owner_id


@pytest.mark.asyncio
async def test_ocpp16_charging_session_end_to_end_on_a_clean_database(
    temporary_database: str,
) -> None:
    """Real gateway + both simulators on a freshly migrated database.

    Runs the OCPP 1.6J simulator's ``session`` scenario and checks everything
    the OCPP 1.6J planner promises is stored: the charger's device fields,
    connector statuses for 0/1/2, a completed session with the right energy,
    ``idTag``, stop reason and ``meterStop``, the measurements, a raw-log row for
    every frame in both directions, and one configuration capture. Then runs the
    2.0.1 simulator against the same gateway to prove that path is unaffected.
    """
    port = _free_port()
    simulator_dir = _backend_root().parent / "simulator"
    environment = os.environ | {
        "DATABASE_URL": temporary_database,
        "CHARGING_OCPP_HOST": "127.0.0.1",
        "CHARGING_OCPP_PORT": str(port),
    }
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    await _provision_station(engine, "E2E-16", [1, 2])
    await _provision_station(engine, "E2E-201", [1])
    # Only a token a scan issued starts a session (CE-11): the simulator's tag.
    await _scan(engine, "E2E-16", "SIMTAG001")
    gateway = subprocess.Popen(
        [sys.executable, "-m", "app.domains.charging_stations.ocpp.entrypoint"],
        cwd=_backend_root(),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port)
        url = f"ws://127.0.0.1:{port}"
        run_16 = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                str(simulator_dir / "ocpp16_charge_point_simulator.py"),
                "--url",
                url,
                "--identity",
                "E2E-16",
                "--scenario",
                "session",
                "--linger",
                "1.5",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert run_16.returncode == 0, run_16.stdout + run_16.stderr
        run_201 = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                str(simulator_dir / "charging_session_simulator.py"),
                "--url",
                url,
                "--identity",
                "E2E-201",
                "--transaction-id",
                "E2E-TX-201",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert run_201.returncode == 0, run_201.stdout + run_201.stderr

        async with engine.connect() as connection:
            station = (
                await connection.execute(
                    text(
                        "SELECT s.station_id, st.ocpp_protocol_version, st.vendor, st.model, "
                        "st.firmware_version, st.last_boot_at IS NOT NULL AS booted, "
                        "st.last_seen_at IS NOT NULL AS seen, st.charger_status, "
                        "st.charger_error_code, s.updated_at = s.created_at AS station_kept "
                        "FROM charging_stations s "
                        "JOIN charging_station_state st USING (station_id) "
                        "WHERE s.ocpp_identity = 'E2E-16'"
                    )
                )
            ).one()
            gun_statuses = (
                await connection.execute(
                    text(
                        "SELECT e.ocpp_evse_id, cs.status, cs.error_code, "
                        "c.updated_at = c.created_at AS updated_at_kept "
                        "FROM charging_connectors c JOIN charging_evses e USING (evse_id) "
                        "JOIN charging_connector_state cs USING (connector_id) "
                        "WHERE e.station_id = :s ORDER BY e.ocpp_evse_id"
                    ),
                    {"s": station.station_id},
                )
            ).all()
            session = (
                await connection.execute(
                    text(
                        "SELECT session_id, ocpp_transaction_id, status::text, id_token, "
                        "stop_reason, meter_start_wh, meter_stop_wh, evse_id, "
                        "connector_id, started_at FROM charging_sessions "
                        "WHERE station_id = :s"
                    ),
                    {"s": station.station_id},
                )
            ).one()
            energy = (
                (
                    await connection.execute(
                        text(
                            "SELECT value FROM charging_session_measurements "
                            "WHERE session_id = :x AND measurand = 'Energy.Active.Import.Register' "
                            "ORDER BY sampled_at"
                        ),
                        {"x": session.session_id},
                    )
                )
                .scalars()
                .all()
            )
            measurands = set(
                (
                    await connection.execute(
                        text(
                            "SELECT DISTINCT measurand FROM charging_session_measurements "
                            "WHERE session_id = :x"
                        ),
                        {"x": session.session_id},
                    )
                ).scalars()
            )
            frames = (
                await connection.execute(
                    text(
                        "SELECT direction::text, ocpp_subprotocol, raw_frame "
                        "FROM charging_ocpp_messages WHERE station_id = :s "
                        "ORDER BY occurred_at"
                    ),
                    {"s": station.station_id},
                )
            ).all()
            captures = (
                await connection.execute(
                    text(
                        "SELECT count(DISTINCT cap.capture_id) AS captures, count(e.entry_id) AS keys, "
                        "max(e.value) FILTER (WHERE e.variable_name = 'SupportedFeatureProfiles') AS profiles, "
                        "max(cap.outcome) AS outcome, max(cap.reason) AS reason, "
                        "max(cmd.command_type) AS command_type, max(cmd.outcome) AS command_outcome, "
                        "max(cmd.ocpp_message_id) AS message_id, "
                        "bool_and(e.mutability IN ('READ_ONLY', 'READ_WRITE')) AS mutability_ok "
                        "FROM charging_station_configuration_captures cap "
                        "JOIN charging_station_commands cmd USING (command_id) "
                        "JOIN charging_station_configuration_entries e USING (capture_id) "
                        "WHERE cmd.station_id = :s"
                    ),
                    {"s": station.station_id},
                )
            ).one()
            paired = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM charging_ocpp_messages m "
                        "WHERE m.station_id = :s AND m.action = 'GetConfiguration' "
                        "AND m.ocpp_message_id = :m"
                    ),
                    {"s": station.station_id, "m": captures.message_id},
                )
            ).scalar_one()
            legacy = (
                await connection.execute(
                    text(
                        "SELECT st.ocpp_protocol_version, "
                        "(SELECT count(*) FROM charging_sessions x "
                        "WHERE x.station_id = s.station_id) AS sessions "
                        "FROM charging_stations s "
                        "JOIN charging_station_state st ON st.station_id = s.station_id "
                        "WHERE s.ocpp_identity = 'E2E-201'"
                    )
                )
            ).one()

        # Device fields (Boot) and liveness.
        assert (station.ocpp_protocol_version, station.vendor) == (
            "ocpp1.6",
            "Willdigits",
        )
        assert station.model == "DC-240kW-Dual-CCS2"
        assert station.firmware_version == "OCPP_L4.05_SIM"
        assert station.booted and station.seen
        # Connector 0 (whole charger) and guns 1/2 (EVSE n / connector 1).
        assert (station.charger_status, station.charger_error_code) == (
            "Available",
            "NoError",
        )
        # Device reports go to the state tables and never touch the profile rows
        # (updated_at keeps meaning "last edit by a person", DM-16).
        assert station.station_kept
        assert [tuple(row) for row in gun_statuses] == [
            (1, "Available", "NoError", True),
            (2, "Available", "NoError", True),
        ]
        # The scanned session: allocated ID, gun, token, reason, declared meters.
        assert (session.ocpp_transaction_id, session.status) == ("1", "COMPLETED")
        assert (session.id_token, session.stop_reason) == (
            "SIMTAG001",
            "EVDisconnected",
        )
        assert (session.meter_start_wh, session.meter_stop_wh) == (1000, 1500)
        assert session.evse_id is not None and session.started_at is not None
        # Measurements: 1.25 kWh really is 1250 Wh, plus the extra measurands.
        assert list(energy) == [1250, 1450, 1500]
        assert {
            "SoC",
            "Power.Active.Import",
            "Voltage",
            "Current.Import",
            "Temperature",
            "Power.Offered",
            "Voltage.Demand",  # vendor-specific, stored as sent
        } <= measurands
        # Raw log: every request has its answer, in both directions.
        parsed = [(direction, json.loads(raw)) for direction, _, raw in frames]
        assert {subprotocol for _, subprotocol, _ in frames} == {"ocpp1.6"}
        inbound_calls = {m[1] for d, m in parsed if d == "CP_TO_CSMS" and m[0] == 2}
        outbound_answers = {
            m[1] for d, m in parsed if d == "CSMS_TO_CP" and m[0] in (3, 4)
        }
        outbound_calls = {m[1] for d, m in parsed if d == "CSMS_TO_CP" and m[0] == 2}
        inbound_answers = {
            m[1] for d, m in parsed if d == "CP_TO_CSMS" and m[0] in (3, 4)
        }
        assert inbound_calls == outbound_answers and len(inbound_calls) >= 14
        assert (
            outbound_calls == inbound_answers and len(outbound_calls) == 1
        )  # GetConfiguration
        # One configuration snapshot of one GET_CONFIGURATION command (CS-21):
        # the system's own request after boot, answered, its frame in the raw log.
        assert (captures.captures, captures.keys) == (1, 16)
        assert captures.profiles == "Core,SmartCharging,RemoteTrigger"
        assert (captures.outcome, captures.reason) == ("COMPLETE", "BOOT")
        assert (captures.command_type, captures.command_outcome) == (
            "GET_CONFIGURATION",
            "ACCEPTED",
        )
        assert captures.mutability_ok and paired == 1
        # The 2.0.1 simulator sends no idToken, so its start is refused (CE-11):
        # the charger is served but no session row appears.
        assert tuple(legacy) == ("ocpp2.0.1", 0)
    finally:
        gateway.terminate()
        try:
            gateway.wait(timeout=15)
        except subprocess.TimeoutExpired:
            gateway.kill()
        await engine.dispose()


@pytest.mark.asyncio
async def test_notification_insert_and_mark_read_need_no_refresh(
    temporary_database: str,
) -> None:
    """A flushed notification is fully populated without a refresh (F-A2).

    The identity ID comes back from ``INSERT ... RETURNING`` and ``created_at``
    is a client-side value, so the service builds its response straight from
    the object; a person's mark-read stays idempotent (NT-04, NT-10).
    """
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        organization_id, user_id = await _provision_organization_and_user(engine)
        async with session_factory.begin() as db:
            notification_record = await notification_repository.insert(
                db,
                organization_id=organization_id,
                notification_type=NotificationType.BATTERY_ALERT,
                severity=NotificationSeverity.WARNING,
                vehicle_id=None,
                subject_type=None,
                subject_id=None,
                title="Battery at 18%",
                body="Vehicle battery dropped below the threshold.",
                payload={"soc": 18.0},
            )
            record_state = inspect(notification_record)
            assert record_state.unloaded == set()
            assert record_state.expired_attributes == set()
            assert notification_record.notification_id > 0
            created_at = notification_record.created_at
            assert created_at.utcoffset() is not None
            added = await notifications_service.add_notification_recipients(
                db, notification_record.notification_id, [user_id]
            )
            assert added == 1

        async with session_factory.begin() as db:
            first_read = await notifications_service.mark_notification_read(
                db,
                notification_record.notification_id,
                principal=build_principal(user_id=user_id),
            )
        async with session_factory.begin() as db:
            second_read = await notifications_service.mark_notification_read(
                db,
                notification_record.notification_id,
                principal=build_principal(user_id=user_id),
            )
            again = await notifications_service.add_notification_recipients(
                db, notification_record.notification_id, [user_id]
            )
            unread = await notifications_service.count_unread_notifications(
                db, principal=build_principal(user_id=user_id)
            )

        async with session_factory() as db:
            stored = (
                await db.execute(
                    select(NotificationModel).where(
                        NotificationModel.notification_id
                        == notification_record.notification_id
                    )
                )
            ).scalar_one()

        assert stored.created_at == created_at
        assert stored.payload == {"soc": 18.0}
        assert first_read.read_at is not None
        assert first_read.seen_at == first_read.read_at
        assert second_read.read_at == first_read.read_at
        assert (again, unread.unread_count) == (0, 0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_vehicle_takes_replacement_device_after_soft_delete(
    temporary_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A soft-deleted device frees its vehicle; a second live one still conflicts.

    Covers deferred.md item 82 (partial unique index
    ``uq_telematics_active_vehicle``) through the service, both the
    pre-check and the flush-time ``IntegrityError`` path, and D11 (a
    device of a soft-deleted vehicle resolves to no ingestion mapping).
    """
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    vin = f"1{uuid4().hex[:16].upper()}"
    serial_prefix = f"IT-TBOX-{uuid4().hex[:8]}"
    try:
        async with session_factory.begin() as db:
            organization_id, vehicle_model_id = await _insert_vehicle_parents(db)
            vehicle_record = VehicleModel(
                organization_id=organization_id,
                vehicle_model_id=vehicle_model_id,
                license_plate=f"IT-{uuid4().hex[:12]}",
                vin=vin,
                year=2026,
                status=VehicleStatus.ACTIVE,
            )
            db.add(vehicle_record)
            await db.flush()
            vehicle_id = vehicle_record.vehicle_id

        async with session_factory.begin() as db:
            first_device = await telematics_service.create_telematic(
                db,
                TelematicCreateRequest(
                    telematic_serial=f"{serial_prefix}-A",
                    organization_id=organization_id,
                    vehicle_vin=vin,
                ),
                principal=build_internal_principal(),
            )
        async with session_factory.begin() as db:
            await telematics_service.soft_delete_telematic(
                db, first_device.telematic_id, principal=build_internal_principal()
            )
        async with session_factory() as db:
            # DM-25: the deleted device is INACTIVE and unmounted, and the
            # change history kept its last mounted state (TX-08).
            deleted_row = (
                await db.execute(
                    text(
                        "SELECT status::text, vehicle_id, deleted_at, status_reason "
                        "FROM telematics WHERE telematic_id = :id"
                    ),
                    {"id": first_device.telematic_id},
                )
            ).one()
            assert deleted_row.status == "INACTIVE"
            assert deleted_row.vehicle_id is None
            assert deleted_row.deleted_at is not None
            history_vehicle_id = await db.scalar(
                text(
                    "SELECT vehicle_id FROM telematic_history WHERE telematic_id = :id"
                ),
                {"id": first_device.telematic_id},
            )
            assert history_vehicle_id == vehicle_id
        async with session_factory.begin() as db:
            replacement_device = await telematics_service.create_telematic(
                db,
                TelematicCreateRequest(
                    telematic_serial=f"{serial_prefix}-B",
                    organization_id=organization_id,
                    vehicle_vin=vin,
                ),
                principal=build_internal_principal(),
            )
        assert replacement_device.vehicle_id == vehicle_id

        # Pre-check path: the service sees the live replacement device.
        with pytest.raises(TelematicConflictError):
            async with session_factory.begin() as db:
                await telematics_service.create_telematic(
                    db,
                    TelematicCreateRequest(
                        telematic_serial=f"{serial_prefix}-C",
                        organization_id=organization_id,
                        vehicle_vin=vin,
                    ),
                    principal=build_internal_principal(),
                )

        # Flush-time path: with the pre-check blinded, the partial unique
        # index itself rejects a second live device for the vehicle.
        async def no_assigned_device(db_session: AsyncSession, vid: object) -> None:
            return None

        monkeypatch.setattr(
            telematics_repository, "find_by_vehicle_id", no_assigned_device
        )
        with pytest.raises(TelematicConflictError):
            async with session_factory.begin() as db:
                await telematics_service.create_telematic(
                    db,
                    TelematicCreateRequest(
                        telematic_serial=f"{serial_prefix}-D",
                        organization_id=organization_id,
                        vehicle_vin=vin,
                    ),
                    principal=build_internal_principal(),
                )
        monkeypatch.undo()

        async with session_factory.begin() as db:
            live_mapping = await telematics_service.resolve_mapping_by_serial(
                db, f"{serial_prefix}-B"
            )
            await vehicle_service.soft_delete_vehicle(
                db, vehicle_id, principal=build_internal_principal()
            )
            deleted_vehicle_mapping = (
                await telematics_service.resolve_mapping_by_serial(
                    db, f"{serial_prefix}-B"
                )
            )
        assert live_mapping is not None
        assert live_mapping.vehicle_id == vehicle_id
        assert deleted_vehicle_mapping is None
    finally:
        await engine.dispose()


async def _seed_vehicle_readings(
    db: AsyncSession, readings: list[dict[str, object]]
) -> VehicleModel:
    """Insert a vehicle (200 kWh pack), its device and the given readings.

    Args:
        db: Session of the caller's transaction.
        readings: Per-reading column values (``recorded_at`` and ``soc`` at
            least); identity and location columns are filled in here.

    Returns:
        The inserted vehicle.
    """
    organization_id, vehicle_model_id = await _insert_vehicle_parents(
        db, nominal_battery_capacity_kwh=Decimal("200.0")
    )
    vehicle = VehicleModel(
        vehicle_id=uuid4(),
        organization_id=organization_id,
        vehicle_model_id=vehicle_model_id,
        license_plate=f"IT-{uuid4().hex[:12]}",
        vin=f"1{uuid4().hex[:16]}",
        year=2026,
        status=VehicleStatus.ACTIVE,
    )
    db.add(vehicle)
    await db.flush()
    telematic = TelematicModel(
        telematic_id=uuid4(),
        telematic_serial=f"IT-TBOX-{uuid4().hex[:12]}",
        organization_id=organization_id,
        acquired_at=datetime.now(timezone.utc),
        vehicle_id=vehicle.vehicle_id,
        status=TelematicStatus.ACTIVE,
    )
    db.add(telematic)
    await db.flush()
    for reading in readings:
        await telemetry_repository.insert_telemetry(
            db,
            {
                "organization_id": organization_id,
                "device_message_id": uuid4(),
                "telematic_id": telematic.telematic_id,
                "vehicle_id": vehicle.vehicle_id,
                "received_at": reading["recorded_at"],
                "location": coordinates_to_location(10.8, 106.7),
                "raw_payload": {"source": "postgres-integration"},
                **reading,
            },
        )
    return vehicle


def _september_2026(day: int, hour: int, minute: int = 0) -> datetime:
    """Build a UTC timestamp in September 2026.

    Args:
        day: Day of month.
        hour: Hour (UTC).
        minute: Minute.

    Returns:
        The timezone-aware UTC datetime.
    """
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_battery_health_buckets_by_report_timezone_day(
    temporary_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-A3: one point per Asia/Ho_Chi_Minh day, last reported value wins.

    16:30Z and 17:30Z on the same UTC date are different local days (UTC+7);
    a later reading without SOH does not blank the day's SOH; a day whose
    readings carry neither SOH nor cycle count is omitted.
    """
    monkeypatch.setattr(settings, "APP_REPORT_TIMEZONE", "Asia/Ho_Chi_Minh")
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory() as session:
            vehicle = await _seed_vehicle_readings(
                session,
                [
                    {
                        "recorded_at": _september_2026(1, 16, 30),
                        "soc_percent": 80.0,
                        "soh_percent": 95.0,
                        "cycle_count": 100,
                    },
                    {
                        "recorded_at": _september_2026(1, 17, 30),
                        "soc_percent": 79.0,
                        "soh_percent": 94.5,
                        "cycle_count": 100,
                    },
                    {
                        "recorded_at": _september_2026(1, 20),
                        "soc_percent": 78.0,
                        "cycle_count": 101,
                    },
                    {"recorded_at": _september_2026(3, 5), "soc_percent": 70.0},
                ],
            )

            trend = await telemetry_service.get_vehicle_battery_health_response(
                session,
                vehicle_id=vehicle.vehicle_id,
                start_time=datetime(2026, 8, 31, tzinfo=timezone.utc),
                end_time=datetime(2026, 9, 4, tzinfo=timezone.utc),
                principal=build_internal_principal(),
            )
            await session.rollback()

        assert [
            (
                point.day_start,
                point.soh_percent,
                point.cycle_count,
                point.estimated_capacity_kwh,
            )
            for point in trend.points
        ] == [
            (datetime(2026, 8, 31, 17, tzinfo=timezone.utc), 95.0, 100, 190.0),
            (_september_2026(1, 17), 94.5, 101, 189.0),
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_operating_report_periods_sum_to_the_whole_window(
    temporary_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-A6: daily periods in UTC+7 split the lag fold without losing deltas.

    The delta from the last reading of local day 1 to the first of local
    day 2 counts in day 2, and a reading stamped exactly at the inclusive
    end_time (a local midnight) stays in the last listed day.
    """
    monkeypatch.setattr(settings, "APP_REPORT_TIMEZONE", "Asia/Ho_Chi_Minh")
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    start_time = datetime(2026, 8, 31, 17, tzinfo=timezone.utc)  # Sep 1, local
    end_time = _september_2026(2, 17)  # Sep 3 00:00, local
    try:
        async with session_factory() as session:
            vehicle = await _seed_vehicle_readings(
                session,
                [
                    {
                        "recorded_at": _september_2026(1, 1),
                        "soc_percent": 90.0,
                        "odometer_km": 1000.0,
                    },
                    {
                        "recorded_at": _september_2026(1, 10),
                        "soc_percent": 80.0,
                        "odometer_km": 1050.0,
                    },
                    {
                        "recorded_at": _september_2026(1, 18),
                        "soc_percent": 75.0,
                        "odometer_km": 1070.0,
                    },
                    {
                        "recorded_at": _september_2026(2, 10),
                        "soc_percent": 60.0,
                        "odometer_km": 1120.0,
                    },
                    {
                        "recorded_at": end_time,
                        "soc_percent": 55.0,
                        "odometer_km": 1130.0,
                    },
                ],
            )

            daily_report = await telemetry_service.get_vehicle_operating_report(
                session,
                vehicle_id=vehicle.vehicle_id,
                start_time=start_time,
                end_time=end_time,
                granularity=ReportGranularity.DAY,
                principal=build_internal_principal(),
            )
            monthly_report = await telemetry_service.get_vehicle_operating_report(
                session,
                vehicle_id=vehicle.vehicle_id,
                start_time=start_time,
                end_time=end_time,
                granularity=ReportGranularity.MONTH,
                principal=build_internal_principal(),
            )
            await session.rollback()

        assert daily_report.periods is not None
        assert [
            (
                period.period_start,
                period.sample_count,
                period.distance_km,
                period.energy_consumed_kwh,
            )
            for period in daily_report.periods
        ] == [
            (start_time, 2, 50.0, 20.0),
            (_september_2026(1, 17), 3, 80.0, 50.0),
        ]
        assert daily_report.sample_count == 5
        assert daily_report.distance_km == 130.0
        assert daily_report.energy_consumed_kwh == pytest.approx(70.0)
        assert monthly_report.periods is not None
        assert len(monthly_report.periods) == 1
        assert monthly_report.periods[0].distance_km == 130.0
    finally:
        await engine.dispose()


async def _provision_located_station(
    engine: object,
    *,
    identity: str,
    longitude: float,
    connector_statuses: list[str | None],
    station_status: str = "ACTIVE",
    is_public: bool = True,
) -> tuple[UUID, list[UUID]]:
    """Insert a located charger with one EVSE/connector per given gun status.

    Returns the station ID and the connector IDs, in EVSE-number order.
    """
    _, location_id = await _provision_location(
        engine, longitude=longitude, is_public=is_public
    )
    return await _provision_station_at(
        engine, location_id, identity, connector_statuses, status=station_status
    )


@pytest.mark.asyncio
async def test_station_availability_counts_only_available_connectors_on_postgres(
    temporary_database: str,
) -> None:
    """D3 on real PostGIS: ACTIVE + >=1 active Available connector, online not needed.

    Query point at longitude 106.0020. From nearest to farthest:
    deleted-gun (0.0002 away, its only Available gun is soft-deleted), maintenance
    (0.0005, Available but the charger is INACTIVE), busy (0.0010, Charging only)
    and free (0.0020, one Charging and one Available gun). Only "free" is
    available. A private location (nearer than all of them) is never found.
    """
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        free_id, _ = await _provision_located_station(
            engine,
            identity="AV-FREE",
            longitude=106.0,
            connector_statuses=["Charging", "Available"],
        )
        busy_id, _ = await _provision_located_station(
            engine,
            identity="AV-BUSY",
            longitude=106.001,
            connector_statuses=["Charging"],
        )
        maintenance_id, _ = await _provision_located_station(
            engine,
            identity="AV-MAINT",
            longitude=106.0015,
            connector_statuses=["Available"],
            station_status="INACTIVE",
        )
        deleted_gun_id, deleted_connectors = await _provision_located_station(
            engine,
            identity="AV-DELETED",
            longitude=106.0018,
            connector_statuses=["Available", None],
        )
        await _provision_located_station(
            engine,
            identity="AV-PRIVATE",
            longitude=106.0019,
            connector_statuses=["Available"],
            is_public=False,
        )
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE charging_connectors SET deleted_at = now() "
                    "WHERE connector_id = :c"
                ),
                {"c": deleted_connectors[0]},
            )

        # A caller of some other organization sees public locations only.
        outsider = build_principal(organization_id=uuid4())
        async with session_factory() as db:
            nearest = await charging_stations_service.find_nearest_operational_station(
                db, latitude=10.0, longitude=106.002
            )
            available_only = (
                await charging_stations_service.list_nearby_charging_stations(
                    db,
                    latitude=10.0,
                    longitude=106.002,
                    radius_km=5,
                    is_available_only=True,
                    principal=outsider,
                )
            )
            operational = await charging_stations_service.list_nearby_charging_stations(
                db,
                latitude=10.0,
                longitude=106.002,
                radius_km=5,
                principal=outsider,
            )
            free_detail = await charging_stations_service.get_charging_station(
                db, free_id, principal=build_internal_principal()
            )
            maintenance_detail = await charging_stations_service.get_charging_station(
                db, maintenance_id, principal=build_internal_principal()
            )
            free_status = await charging_stations_service.get_charging_station_status(
                db, free_id, principal=build_internal_principal()
            )

        assert nearest is not None and nearest.station_id == free_id
        assert [item.station_id for item in available_only.items] == [free_id]
        assert available_only.total == 1
        # Without the filter: every OPERATIONAL station, nearest first.
        assert [
            (item.station_id, item.available_connector_count)
            for item in operational.items
        ] == [(deleted_gun_id, 0), (busy_id, 0), (free_id, 1)]
        assert all(item.is_online is False for item in operational.items)
        assert (free_detail.connector_count, free_detail.available_connector_count) == (
            2,
            1,
        )
        # The raw count ignores maintenance; the "available" filter does not.
        assert maintenance_detail.available_connector_count == 1
        assert [
            (gun.ocpp_evse_id, gun.status.value if gun.status else None)
            for gun in free_status.connectors
        ] == [(1, "Charging"), (2, "Available")]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_station_energy_series_splits_energy_across_a_bucket_boundary(
    temporary_database: str,
) -> None:
    """D5 on real data: per-delta attribution, local-day buckets, matching totals.

    One completed session 09:40Z-10:20Z (meter 1000 -> 4000 Wh) with samples at
    09:55 (2000), 10:10 (3500) and 10:20 (4000): 1 kWh lands in the 09:00 hour,
    2 kWh in the 10:00 hour, and all 3 kWh in the local (UTC+7) day.
    """
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    t0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    session_id = uuid4()
    try:
        station_id, connector_ids = await _provision_located_station(
            engine, identity="SERIES-1", longitude=106.5, connector_statuses=[None]
        )
        async with engine.begin() as connection:
            evse_id = await connection.scalar(
                text("SELECT evse_id FROM charging_connectors WHERE connector_id = :c"),
                {"c": connector_ids[0]},
            )
            organization_id, user_id = await _provision_organization_and_user(engine)
            await connection.execute(
                text(
                    "INSERT INTO charging_sessions (session_id, station_id, evse_id, "
                    "connector_id, organization_id, started_by, ocpp_transaction_id, "
                    "status, started_at, ended_at, meter_start_wh, id_token, "
                    "meter_stop_wh, created_at, updated_at) VALUES "
                    "(:x, :s, :e, :c, :o, :u, 'SERIES-TX', 'COMPLETED', :start, "
                    ":end, 1000, 'SERIES-TOKEN', 4000, :start, :end)"
                ),
                {
                    "x": session_id,
                    "s": station_id,
                    "e": evse_id,
                    "c": connector_ids[0],
                    "o": organization_id,
                    "u": user_id,
                    "start": t0 + timedelta(minutes=40),
                    "end": t0 + timedelta(hours=1, minutes=20),
                },
            )
            for minutes, value_wh in ((55, 2000), (70, 3500), (80, 4000)):
                await connection.execute(
                    text(
                        "INSERT INTO charging_session_measurements (measurement_id, "
                        "sampled_at, session_id, measurand, value, unit, context, "
                        "measurement_location) VALUES "
                        "(:m, :t, :x, 'Energy.Active.Import.Register', :v, 'Wh', "
                        "'Sample.Periodic', 'Outlet')"
                    ),
                    {
                        "m": uuid4(),
                        "t": t0 + timedelta(minutes=minutes),
                        "x": session_id,
                        "v": value_wh,
                    },
                )

        async with session_factory() as db:
            hourly = await charging_sessions_service.get_station_energy_series(
                db,
                station_id=station_id,
                start_time=t0,
                end_time=t0 + timedelta(hours=3),
                granularity=EnergySeriesGranularity.HOUR,
            )
            daily = await charging_sessions_service.get_station_energy_series(
                db,
                station_id=station_id,
                start_time=t0,
                end_time=t0 + timedelta(days=1),
                granularity=EnergySeriesGranularity.DAY,
            )
            totals = await charging_stations_service.list_station_energy_totals(
                db,
                start_time=t0,
                end_time=t0 + timedelta(days=1),
                principal=build_internal_principal(),
            )
            completed = await charging_sessions_service.list_charging_sessions(
                db,
                page=1,
                page_size=10,
                station_id=station_id,
                status=SessionStatus.COMPLETED,
                principal=build_internal_principal(),
            )
            active = await charging_sessions_service.list_charging_sessions(
                db,
                page=1,
                page_size=10,
                station_id=station_id,
                status=SessionStatus.ACTIVE,
                principal=build_internal_principal(),
            )

        assert [(item.bucket_start, item.energy_kwh) for item in hourly.items] == [
            (t0, 1.0),
            (t0 + timedelta(hours=1), 2.0),
            (t0 + timedelta(hours=2), 0.0),
        ]
        # 09:00Z is 16:00 in Asia/Ho_Chi_Minh: the local day began 17:00Z the
        # day before, and the window reaches into the next local day.
        assert [item.bucket_start for item in daily.items] == [
            datetime(2026, 9, 30, 17, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 1, 17, 0, tzinfo=timezone.utc),
        ]
        assert [item.energy_kwh for item in daily.items] == [3.0, 0.0]
        station_total = next(
            item for item in totals.items if item.station_id == station_id
        )
        assert (station_total.total_energy_kwh, station_total.session_count) == (
            3.0,
            1,
        )
        assert (completed.total, active.total) == (1, 0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ocpp201_boot_notification_stores_device_info_and_liveness(
    temporary_database: str,
) -> None:
    """The 2.0.1 simulator now boots first: vendor/model/firmware, last_boot_at, last_seen_at."""
    port = _free_port()
    simulator_dir = _backend_root().parent / "simulator"
    environment = os.environ | {
        "DATABASE_URL": temporary_database,
        "CHARGING_OCPP_HOST": "127.0.0.1",
        "CHARGING_OCPP_PORT": str(port),
    }
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    await _provision_station(engine, "BOOT-201", [1])
    gateway = subprocess.Popen(
        [sys.executable, "-m", "app.domains.charging_stations.ocpp.entrypoint"],
        cwd=_backend_root(),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port)
        run_201 = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                str(simulator_dir / "charging_session_simulator.py"),
                "--url",
                f"ws://127.0.0.1:{port}",
                "--identity",
                "BOOT-201",
                "--transaction-id",
                "BOOT-TX-201",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert run_201.returncode == 0, run_201.stdout + run_201.stderr

        async with engine.connect() as connection:
            station = (
                await connection.execute(
                    text(
                        "SELECT st.ocpp_protocol_version, st.vendor, st.model, "
                        "st.firmware_version, st.last_boot_at IS NOT NULL AS booted, "
                        "st.last_seen_at IS NOT NULL AS seen "
                        "FROM charging_stations s "
                        "JOIN charging_station_state st USING (station_id) "
                        "WHERE s.ocpp_identity = 'BOOT-201'"
                    )
                )
            ).one()

        assert tuple(station) == (
            "ocpp2.0.1",
            "G3Network-Sim",
            "SIM-201",
            "SIM-201-1.0",
            True,
            True,
        )
    finally:
        gateway.terminate()
        try:
            gateway.wait(timeout=15)
        except subprocess.TimeoutExpired:
            gateway.kill()
        await engine.dispose()


async def _integration_organization(db: AsyncSession) -> UUID:
    """Insert one organization (the owner fleets require) and return its ID."""
    organization = build_organization_record()
    db.add(organization)
    await db.flush()
    return organization.organization_id


async def _insert_vehicle_parents(
    db: AsyncSession, *, nominal_battery_capacity_kwh: Decimal | None = None
) -> tuple[UUID, UUID]:
    """Insert the organization and the vehicle model a vehicle needs.

    Args:
        db: Session of the caller's transaction.
        nominal_battery_capacity_kwh: Battery capacity of the new model.

    Returns:
        ``(organization_id, vehicle_model_id)``, both flushed.
    """
    organization = build_organization_record()
    vehicle_model = VehicleModelModel(
        make="G3Network",
        model_name=f"IT-{uuid4().hex[:12]}",
        nominal_battery_capacity_kwh=nominal_battery_capacity_kwh,
    )
    db.add_all([organization, vehicle_model])
    await db.flush()
    return organization.organization_id, vehicle_model.vehicle_model_id


async def _integration_vehicle(db: AsyncSession, *, license_plate: str) -> VehicleModel:
    """Build an unsaved, active vehicle with a random 17-character VIN.

    Its organization and vehicle model are inserted (flushed) first.
    """
    organization_id, vehicle_model_id = await _insert_vehicle_parents(db)
    return VehicleModel(
        vehicle_id=uuid4(),
        organization_id=organization_id,
        vehicle_model_id=vehicle_model_id,
        license_plate=license_plate,
        vin=f"IT{uuid4().hex[:15]}".upper(),
        year=2026,
        status=VehicleStatus.ACTIVE,
    )


async def _integration_driver(
    db: AsyncSession, *, license_number: str, organization_id: UUID | None = None
) -> DriverModel:
    """Insert an organization (unless given), a user, a membership and a driver profile.

    Args:
        db: Session of the caller's transaction.
        license_number: Licence number of the new profile.
        organization_id: Existing organization to join, or ``None`` for a new one.

    Returns:
        The flushed driver profile.
    """
    if organization_id is None:
        organization_id = await _integration_organization(db)
    suffix = uuid4().hex[:9]
    user = UserModel(
        phone_number=f"+84{suffix}",
        full_name="Integration Driver",
        status=UserStatus.ACTIVE.value,
    )
    db.add(user)
    await db.flush()
    membership = MembershipModel(
        organization_id=organization_id,
        user_id=user.user_id,
        status=MembershipStatus.ACTIVE.value,
    )
    db.add(membership)
    await db.flush()
    return await driver_repository.insert(
        db,
        {
            "membership_id": membership.membership_id,
            "license_number": license_number,
            "license_class": "CE",
            "license_expires_on": date(2030, 1, 1),
        },
    )


@pytest.mark.asyncio
async def test_driving_session_indexes_driver_history_and_trip_checks_on_postgres(
    temporary_database: str,
) -> None:
    """One open session per truck and per driver, driver history, trip checks (DR-07/09/12)."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory() as db:
            first_vehicle = await _integration_vehicle(db, license_plate="IT-DRV-001")
            second_vehicle = await _integration_vehicle(db, license_plate="IT-DRV-002")
            db.add_all([first_vehicle, second_vehicle])
            await db.flush()
            alice = await _integration_driver(db, license_number="LIC-50%-A")
            bob = await _integration_driver(db, license_number="LIC-B")
            now = datetime.now(timezone.utc)
            first_session = await driver_repository.insert_session(
                db,
                organization_id=first_vehicle.organization_id,
                driver_id=alice.driver_id,
                vehicle_id=first_vehicle.vehicle_id,
                check_in_method=CheckInMethod.APP,
                check_in_location=None,
                started_at=now,
            )

            # A second open session on the same truck, or for the same driver,
            # breaks one of the two partial unique indexes.
            for driver, vehicle in (
                (bob, first_vehicle),
                (alice, second_vehicle),
            ):
                with pytest.raises(IntegrityError):
                    async with db.begin_nested():
                        await driver_repository.insert_session(
                            db,
                            organization_id=vehicle.organization_id,
                            driver_id=driver.driver_id,
                            vehicle_id=vehicle.vehicle_id,
                            check_in_method=CheckInMethod.APP,
                            check_in_location=None,
                            started_at=now,
                        )

            # Closed rows are unlimited: close, then another driver checks in.
            await driver_repository.close_session(
                db,
                first_session,
                ended_at=now + timedelta(minutes=1),
                end_cause=DrivingSessionEndCause.TAKEN_OVER,
            )
            await driver_repository.insert_session(
                db,
                organization_id=first_vehicle.organization_id,
                driver_id=bob.driver_id,
                vehicle_id=first_vehicle.vehicle_id,
                check_in_method=CheckInMethod.PORTAL,
                check_in_location=None,
                started_at=now + timedelta(minutes=2),
            )
            by_vehicle = await driver_repository.list_all(
                db, offset=0, limit=10, vehicle_id=first_vehicle.vehicle_id
            )
            by_licence = await driver_repository.list_all(
                db, offset=0, limit=10, search_text="50%"
            )
            no_wildcard = await driver_repository.count(db, search_text="%")
            assert [driver.driver_id for driver in by_vehicle] == [bob.driver_id]
            assert [driver.driver_id for driver in by_licence] == [alice.driver_id]
            assert no_wildcard == 1
            assert await driver_repository.count_sessions(db, vehicle_id=None) == 2

            # A tracked edit writes driver_history; a deleted profile must be INACTIVE.
            await driver_repository.update_fields(
                db,
                alice.driver_id,
                {"status": DriverStatus.INACTIVE},
                change_reason="Suspended after an accident",
            )
            history_reasons = (
                (
                    await db.execute(
                        text(
                            "SELECT change_reason FROM driver_history "
                            "WHERE driver_id = :driver_id"
                        ),
                        {"driver_id": alice.driver_id},
                    )
                )
                .scalars()
                .all()
            )
            assert history_reasons == ["Suspended after an accident"]
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await db.execute(
                        text(
                            "UPDATE drivers SET deleted_at = now(), status = 'ACTIVE' "
                            "WHERE driver_id = :driver_id"
                        ),
                        {"driver_id": bob.driver_id},
                    )

            # A completed trip needs its session and its end time (DR-12).
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    db.add(
                        TripModel(
                            organization_id=first_vehicle.organization_id,
                            status=TripStatus.COMPLETED.value,
                        )
                    )
                    await db.flush()
            db.add(
                TripModel(
                    organization_id=first_vehicle.organization_id,
                    status=TripStatus.PLANNED.value,
                    planned_driver_id=bob.driver_id,
                )
            )
            await db.flush()
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_check_in_takeover_trip_flow_and_auto_end_on_postgres(
    temporary_database: str,
) -> None:
    """Check-in takeover closes the running trip; trips run; idle sessions auto-end (DR-07/10/12)."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory() as db:
            now = datetime.now(timezone.utc)
            truck = await _seed_vehicle_readings(
                db,
                [
                    {
                        "recorded_at": now - timedelta(minutes=1),
                        "soc_percent": 80.0,
                        "odometer_km": 100.0,
                        "speed_kmh": 0.0,
                    }
                ],
            )
            alice = await _integration_driver(
                db, license_number="LIC-ALICE", organization_id=truck.organization_id
            )
            bob = await _integration_driver(db, license_number="LIC-BOB")
            bob_organization_id = (
                await identity_service.resolve_membership_person_reference(
                    db, bob.membership_id
                )
            ).organization_id  # type: ignore[union-attr]
            manager = build_principal(
                roles=frozenset({UserRole.FLEET_MANAGER}),
                organization_id=truck.organization_id,
            )
            alice_principal = dataclasses.replace(
                build_principal(
                    roles=frozenset({UserRole.DRIVER}),
                    organization_id=truck.organization_id,
                ),
                membership_id=alice.membership_id,
            )
            bob_principal = dataclasses.replace(
                build_principal(
                    roles=frozenset({UserRole.DRIVER}),
                    organization_id=bob_organization_id,
                ),
                membership_id=bob.membership_id,
            )

            # The manager plans a trip for Alice on the truck (DR-12).
            planned = await trip_service.plan_trip(
                db,
                TripPlanRequest(
                    origin_name="Depot",
                    destination_name="Port",
                    planned_start_at=now + timedelta(hours=1),
                    planned_driver_id=alice.driver_id,
                    planned_vehicle_id=truck.vehicle_id,
                ),
                principal=manager,
            )
            assert planned.status == TripStatus.PLANNED

            # A phone 5 km away is refused; one next to the truck checks in by VIN.
            with pytest.raises(DriverTooFarFromVehicleError):
                await driver_service.check_in_driver(
                    db,
                    DrivingSessionCheckInRequest(
                        vehicle_vin=truck.vin,
                        check_in_method=CheckInMethod.QR,
                        latitude=10.85,
                        longitude=106.7,
                    ),
                    principal=alice_principal,
                )
            alice_session = await driver_service.check_in_driver(
                db,
                DrivingSessionCheckInRequest(
                    vehicle_vin=truck.vin,
                    check_in_method=CheckInMethod.QR,
                    latitude=10.8001,
                    longitude=106.7,
                ),
                principal=alice_principal,
            )
            assert alice_session.warnings == []
            current = await driver_service.get_current_driving_session(
                db, principal=alice_principal
            )
            assert current.driving_session_id == alice_session.driving_session_id

            started = await trip_service.start_trip(
                db, planned.trip_id, TripStartRequest(), principal=alice_principal
            )
            assert started.status == TripStatus.IN_PROGRESS
            assert started.start_odometer_km == 100.0

            # Bob, from another organization, takes the truck over by plate: Alice's
            # session ends TAKEN_OVER and her running trip closes by itself.
            bob_session = await driver_service.check_in_driver(
                db,
                DrivingSessionCheckInRequest(
                    vehicle_code=truck.license_plate,
                    check_in_method=CheckInMethod.QR,
                    latitude=10.8,
                    longitude=106.7001,
                ),
                principal=bob_principal,
            )
            assert [ended.end_cause for ended in bob_session.ended_sessions] == [
                DrivingSessionEndCause.TAKEN_OVER
            ]
            assert CheckInWarning.OTHER_ORGANIZATION in bob_session.warnings
            closed_trip = await trip_service.get_trip(
                db, planned.trip_id, principal=manager
            )
            assert closed_trip.status == TripStatus.COMPLETED
            assert closed_trip.status_reason == driver_service.TRIP_AUTO_CLOSED_REASON
            assert closed_trip.actual_driver_id == alice.driver_id

            # Bob runs a personal trip; a second one in the same shift is a conflict.
            personal = await trip_service.start_personal_trip(
                db,
                PersonalTripStartRequest(origin_name="Port", destination_name="Depot"),
                principal=bob_principal,
            )
            assert personal.organization_id == truck.organization_id
            with pytest.raises(TripInProgressConflictError):
                await trip_service.start_personal_trip(
                    db, PersonalTripStartRequest(), principal=bob_principal
                )
            telematic_id = (
                await db.execute(
                    select(TelematicModel.telematic_id).where(
                        TelematicModel.vehicle_id == truck.vehicle_id
                    )
                )
            ).scalar_one()
            await telemetry_repository.insert_telemetry(
                db,
                {
                    "organization_id": truck.organization_id,
                    "device_message_id": uuid4(),
                    "telematic_id": telematic_id,
                    "vehicle_id": truck.vehicle_id,
                    "recorded_at": datetime.now(timezone.utc),
                    "received_at": datetime.now(timezone.utc),
                    "location": coordinates_to_location(10.8, 106.7),
                    "raw_payload": {"source": "postgres-integration"},
                    "soc_percent": 70.0,
                    "odometer_km": 130.0,
                    "speed_kmh": 0.0,
                },
            )
            finished = await trip_service.finish_trip(
                db, personal.trip_id, principal=bob_principal
            )
            assert finished.status == TripStatus.COMPLETED
            assert finished.distance_km == 30.0
            assert finished.energy_kwh == 20.0  # 10 % of the 200 kWh pack

            my_trips = await trip_service.list_trips(
                db, principal=bob_principal, statuses=[TripStatus.COMPLETED]
            )
            assert [trip.trip_id for trip in my_trips.items] == [personal.trip_id]
            history_reasons = (
                (
                    await db.execute(
                        text(
                            "SELECT change_reason FROM trip_history "
                            "WHERE trip_id = :trip_id ORDER BY history_id"
                        ),
                        {"trip_id": planned.trip_id},
                    )
                )
                .scalars()
                .all()
            )
            assert history_reasons == [
                "Trip started",
                driver_service.TRIP_AUTO_CLOSED_REASON,
            ]

            # Hours later the truck is still standing and has reported since the
            # check-in: the session auto-ends at the countdown start.
            sweep = await driver_service.end_idle_driving_sessions(
                db, now=datetime.now(timezone.utc) + timedelta(hours=3)
            )
            assert (sweep.checked, sweep.ended) == (1, 1)
            ended = await driver_service.get_own_driving_summary(
                db, principal=bob_principal
            )
            assert [item.end_cause for item in ended.sessions] == [
                DrivingSessionEndCause.AUTO_ENDED
            ]

            # Ending Alice's membership removes her profile (DR-10).
            await driver_service.handle_membership_end(
                db,
                membership_id=alice.membership_id,
                kind=MembershipEndKind.ENDED,
                acting_user_id=ACTOR_USER_ID,
                reason="Left the company",
            )
            deleted = (
                await db.execute(
                    select(DriverModel.deleted_at, DriverModel.status_reason).where(
                        DriverModel.driver_id == alice.driver_id
                    )
                )
            ).one()
            assert deleted.deleted_at is not None
            assert deleted.status_reason == "Left the company"
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fleet_membership_index_filters_and_geofences_on_postgres(
    temporary_database: str,
) -> None:
    """One active fleet per vehicle, fleet filters, close-by-ID and geofence
    containment against a real polygon (F-E1, F-A5, #84, #85)."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    square_ring = [
        (106.70, 10.77),
        (106.71, 10.77),
        (106.71, 10.78),
        (106.70, 10.78),
        (106.70, 10.77),
    ]
    try:
        async with session_factory() as db:
            vehicle = await _integration_vehicle(db, license_plate="IT-FLT-001")
            db.add(vehicle)
            await db.flush()
            organization_id = await _integration_organization(db)
            hanoi = await fleet_repository.insert(
                db,
                {
                    "organization_id": organization_id,
                    "fleet_code": "HN-01",
                    "name": "Hanoi Trucks",
                },
            )
            saigon = await fleet_repository.insert(
                db,
                {
                    "organization_id": organization_id,
                    "fleet_code": "SG-01",
                    "name": "Saigon Trucks",
                },
            )
            now = datetime.now(timezone.utc)
            membership = await fleet_repository.insert_membership(
                db,
                fleet_id=hanoi.fleet_id,
                vehicle_id=vehicle.vehicle_id,
                added_at=now,
            )
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await fleet_repository.insert_membership(
                        db,
                        fleet_id=saigon.fleet_id,
                        vehicle_id=vehicle.vehicle_id,
                        added_at=now,
                    )

            by_vehicle = await fleet_repository.list_all(
                db, offset=0, limit=10, vehicle_id=vehicle.vehicle_id
            )
            by_code = await fleet_repository.list_all(
                db, offset=0, limit=10, search_text="sg-"
            )
            by_name_count = await fleet_repository.count(db, search_text="TRUCKS")
            assert [fleet.fleet_id for fleet in by_vehicle] == [hanoi.fleet_id]
            assert [fleet.fleet_id for fleet in by_code] == [saigon.fleet_id]
            assert by_name_count == 2
            assert await fleet_service.find_current_fleet_id_by_vehicle(
                db, vehicle.vehicle_id
            ) == (hanoi.fleet_id)
            assert await fleet_service.list_active_member_vehicle_ids(
                db, hanoi.fleet_id
            ) == [vehicle.vehicle_id]

            geofence = await fleet_service.create_geofence(
                db,
                hanoi.fleet_id,
                GeofenceCreateRequest(
                    name="Depot",
                    boundary=GeofencePolygonGeoJson(coordinates=[square_ring]),
                ),
                principal=build_internal_principal(),
            )
            assert geofence.boundary.coordinates == [square_ring]

            inside = await fleet_service.list_geofences_containing(
                db, hanoi.fleet_id, latitude=10.775, longitude=106.705
            )
            outside = await fleet_service.list_geofences_containing(
                db, hanoi.fleet_id, latitude=10.80, longitude=106.705
            )
            other_fleet = await fleet_service.list_geofences_containing(
                db, saigon.fleet_id, latitude=10.775, longitude=106.705
            )
            assert [reference.geofence_id for reference in inside] == [
                geofence.geofence_id
            ]
            assert inside[0].name == "Depot"
            assert outside == []
            assert other_fleet == []

            # Moving the boundary away and then deleting it both take effect.
            moved_ring = [(lon + 1.0, lat) for lon, lat in square_ring]
            moved = await fleet_service.update_geofence(
                db,
                hanoi.fleet_id,
                geofence.geofence_id,
                GeofenceUpdateRequest(
                    name=None, boundary=GeofencePolygonGeoJson(coordinates=[moved_ring])
                ),
                principal=build_internal_principal(),
            )
            assert moved.name == "Depot"
            assert moved.boundary.coordinates == [moved_ring]
            assert (
                await fleet_service.list_geofences_containing(
                    db, hanoi.fleet_id, latitude=10.775, longitude=106.705
                )
                == []
            )
            await fleet_service.soft_delete_geofence(
                db,
                hanoi.fleet_id,
                geofence.geofence_id,
                principal=build_internal_principal(),
            )
            assert (
                await fleet_service.list_geofences_containing(
                    db, hanoi.fleet_id, latitude=10.775, longitude=107.705
                )
                == []
            )
            listed = await fleet_service.list_geofences(
                db, hanoi.fleet_id, principal=build_internal_principal()
            )
            assert listed.total == 0

            # #84: close the membership by ID; a second close is a 404.
            await fleet_service.close_fleet_membership(
                db,
                hanoi.fleet_id,
                membership.fleet_vehicle_membership_id,
                principal=build_internal_principal(),
            )
            assert (
                await fleet_service.find_current_fleet_id_by_vehicle(
                    db, vehicle.vehicle_id
                )
                is None
            )
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fleet_tree_and_name_or_code_rule_on_postgres(
    temporary_database: str,
) -> None:
    """Fleets nest under live parents, never in a loop; a fleet with
    sub-fleets cannot be deleted; a fleet needs a name or a code (FL-02,
    FL-08)."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory() as db:
            organization_id = await _integration_organization(db)
            region = await fleet_service.create_fleet(
                db,
                FleetCreateRequest(organization_id=organization_id, name="South"),
                principal=build_internal_principal(),
            )
            depot = await fleet_service.create_fleet(
                db,
                FleetCreateRequest(
                    organization_id=organization_id,
                    fleet_code="SG-D1",
                    parent_fleet_id=region.fleet_id,
                ),
                principal=build_internal_principal(),
            )
            assert depot.parent_fleet_id == region.fleet_id
            assert (depot.name, region.fleet_code) == (None, None)

            with pytest.raises(FleetHierarchyLoopError):
                await fleet_service.update_fleet(
                    db,
                    region.fleet_id,
                    FleetUpdateRequest(parent_fleet_id=depot.fleet_id),
                    principal=build_internal_principal(),
                )
            with pytest.raises(FleetHasSubFleetsError):
                await fleet_service.soft_delete_fleet(
                    db, region.fleet_id, principal=build_internal_principal()
                )

            await fleet_service.soft_delete_fleet(
                db, depot.fleet_id, principal=build_internal_principal()
            )
            await fleet_service.soft_delete_fleet(
                db, region.fleet_id, principal=build_internal_principal()
            )
            with pytest.raises(FleetParentNotFoundError):
                await fleet_service.create_fleet(
                    db,
                    FleetCreateRequest(
                        organization_id=organization_id,
                        name="Orphan",
                        parent_fleet_id=region.fleet_id,
                    ),
                    principal=build_internal_principal(),
                )

            # The database itself refuses a fleet with neither name nor code.
            with pytest.raises(IntegrityError, match="ck_fleets_name_or_code"):
                async with db.begin_nested():
                    await fleet_repository.insert(
                        db, {"organization_id": organization_id, "name": None}
                    )
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fleet_code_is_unique_per_organization_and_parent_shares_it(
    temporary_database: str,
) -> None:
    """A fleet code is unique among live fleets of one organization only; a
    parent must belong to the same organization; an unknown organization is a
    404 (FL-08)."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory() as db:
            first_organization_id = await _integration_organization(db)
            second_organization_id = await _integration_organization(db)
            first = await fleet_service.create_fleet(
                db,
                FleetCreateRequest(
                    organization_id=first_organization_id, fleet_code="HQ"
                ),
                principal=build_internal_principal(),
            )
            # The same code in another organization is fine.
            await fleet_service.create_fleet(
                db,
                FleetCreateRequest(
                    organization_id=second_organization_id, fleet_code="HQ"
                ),
                principal=build_internal_principal(),
            )
            with pytest.raises(FleetConflictError):
                await fleet_service.create_fleet(
                    db,
                    FleetCreateRequest(
                        organization_id=first_organization_id, fleet_code="HQ"
                    ),
                    principal=build_internal_principal(),
                )
            with pytest.raises(FleetParentOrganizationMismatchError):
                await fleet_service.create_fleet(
                    db,
                    FleetCreateRequest(
                        organization_id=second_organization_id,
                        name="Wrong tree",
                        parent_fleet_id=first.fleet_id,
                    ),
                    principal=build_internal_principal(),
                )
            await db.rollback()

        async with session_factory() as db:
            with pytest.raises(OrganizationNotFoundError):
                await fleet_service.create_fleet(
                    db,
                    FleetCreateRequest(organization_id=uuid4(), name="Ghost"),
                    principal=build_internal_principal(),
                )
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tracked_update_writes_history_row_with_actor_and_reason(
    temporary_database: str,
) -> None:
    """An update of a tracked table copies the old row into its history table
    with the acting user and reason set for the transaction; without a context
    the trigger records the fallback reason and no actor (DM-29)."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory() as db:
            actor = UserModel(
                phone_number="+84900000001",
                full_name="Admin",
                status=UserStatus.ACTIVE.value,
            )
            organization = build_organization_record()
            db.add_all([actor, organization])
            await db.flush()

            await set_change_context(
                db, changed_by=actor.user_id, change_reason="Unpaid invoices"
            )
            organization.status = OrganizationStatus.SUSPENDED.value
            organization.status_reason = "Unpaid invoices"
            await db.flush()

            # A second change in the same transaction, then one with no context.
            organization.display_name = "Renamed Organization"
            await db.flush()
            await db.commit()

        async with session_factory() as db:
            organization_record = await db.get(
                OrganizationModel, organization.organization_id
            )
            assert organization_record is not None
            organization_record.address = "1 Nguyen Hue"
            await db.flush()
            await db.commit()

        async with session_factory() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT status, display_name, changed_by, change_reason "
                        "FROM organization_history "
                        "WHERE organization_id = :organization_id "
                        "ORDER BY history_id"
                    ),
                    {"organization_id": organization.organization_id},
                )
            ).all()
        assert [row.status for row in rows] == ["ACTIVE", "SUSPENDED", "SUSPENDED"]
        assert rows[0].display_name == "Test Organization"
        assert rows[1].display_name == "Test Organization"
        assert rows[2].display_name == "Renamed Organization"
        assert [row.changed_by for row in rows[:2]] == [actor.user_id] * 2
        assert rows[0].change_reason == "Unpaid invoices"
        assert rows[2].changed_by is None
        assert rows[2].change_reason == UNSPECIFIED_CHANGE_REASON
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_vehicle_history_ownership_periods_and_live_uniqueness_on_postgres(
    temporary_database: str,
) -> None:
    """A transfer writes vehicle_history and shows in vehicle_ownership_periods
    (VH-10); a plate frees up when its vehicle is deleted, and a deleted vehicle
    must be INACTIVE (DM-25)."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    first_handover = datetime(2026, 1, 1, tzinfo=timezone.utc)
    second_handover = datetime(2026, 6, 1, tzinfo=timezone.utc)
    try:
        async with session_factory() as db:
            vehicle = await _integration_vehicle(db, license_plate="IT-OWN-001")
            vehicle.acquired_at = first_handover
            seller_id = vehicle.organization_id
            buyer = build_organization_record()
            db.add_all([vehicle, buyer])
            await db.commit()

        async with session_factory() as db:
            vehicle_record = await db.get(VehicleModel, vehicle.vehicle_id)
            assert vehicle_record is not None
            await set_change_context(
                db, changed_by=None, change_reason="Sold to another company"
            )
            vehicle_record.organization_id = buyer.organization_id
            vehicle_record.acquired_at = second_handover
            await db.commit()

        async with session_factory() as db:
            periods = (
                await db.execute(
                    text(
                        "SELECT organization_id, owned_from, owned_until "
                        "FROM vehicle_ownership_periods "
                        "WHERE vehicle_id = :vehicle_id ORDER BY owned_from"
                    ),
                    {"vehicle_id": vehicle.vehicle_id},
                )
            ).all()
            assert [tuple(row) for row in periods] == [
                (seller_id, first_handover, second_handover),
                (buyer.organization_id, second_handover, None),
            ]

            # A deleted vehicle must be INACTIVE; once it is, its plate is free.
            vehicle_record = await db.get(VehicleModel, vehicle.vehicle_id)
            assert vehicle_record is not None
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    vehicle_record.deleted_at = datetime.now(timezone.utc)
                    await db.flush()
            await db.refresh(vehicle_record)
            vehicle_record.status = VehicleStatus.INACTIVE
            vehicle_record.status_reason = "Left the system"
            vehicle_record.deleted_at = datetime.now(timezone.utc)
            await db.flush()
            replacement = await _integration_vehicle(db, license_plate="IT-OWN-001")
            replacement.vin = vehicle_record.vin
            db.add(replacement)
            await db.flush()
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    duplicate = await _integration_vehicle(
                        db, license_plate="IT-OWN-001"
                    )
                    db.add(duplicate)
                    await db.flush()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_battery_installation_periods_and_warranty_links_on_postgres(
    temporary_database: str,
) -> None:
    """One battery per truck, installation periods from battery_history
    (VH-16), and exactly one covered object per warranty (VH-18, VH-19)."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    installed_on_first = datetime(2026, 2, 1, tzinfo=timezone.utc)
    installed_on_second = datetime(2026, 5, 1, tzinfo=timezone.utc)
    try:
        async with session_factory() as db:
            first_vehicle = await _integration_vehicle(db, license_plate="IT-BAT-001")
            second_vehicle = await _integration_vehicle(db, license_plate="IT-BAT-002")
            battery_model = BatteryModelModel(
                manufacturer="CATL", model_name="LFP-282", chemistry="LFP"
            )
            db.add_all([first_vehicle, second_vehicle, battery_model])
            await db.flush()
            battery = BatteryModel(
                serial_number="IT-BAT-SERIAL-1",
                battery_model_id=battery_model.battery_model_id,
                organization_id=first_vehicle.organization_id,
                acquired_at=installed_on_first,
                vehicle_id=first_vehicle.vehicle_id,
                installed_at=installed_on_first,
                status=BatteryStatus.ACTIVE.value,
            )
            db.add(battery)
            await db.commit()

            # A truck holds at most one live battery.
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    db.add(
                        BatteryModel(
                            serial_number="IT-BAT-SERIAL-2",
                            battery_model_id=battery_model.battery_model_id,
                            organization_id=first_vehicle.organization_id,
                            acquired_at=installed_on_first,
                            vehicle_id=first_vehicle.vehicle_id,
                            installed_at=installed_on_first,
                            status=BatteryStatus.ACTIVE.value,
                        )
                    )
                    await db.flush()

            # Move to the second truck, then take it out (separate transactions,
            # so each history row carries its own changed_at).
            battery.vehicle_id = second_vehicle.vehicle_id
            battery.installed_at = installed_on_second
            await db.commit()
            battery.vehicle_id = None
            battery.installed_at = None
            await db.commit()

            periods = (
                await db.execute(
                    text(
                        "SELECT vehicle_id, installed_from, installed_until "
                        "FROM battery_installation_periods "
                        "WHERE battery_id = :battery_id ORDER BY installed_from"
                    ),
                    {"battery_id": battery.battery_id},
                )
            ).all()
            assert [(row.vehicle_id, row.installed_from) for row in periods] == [
                (first_vehicle.vehicle_id, installed_on_first),
                (second_vehicle.vehicle_id, installed_on_second),
            ]
            # Straight to another truck: ends when the next stay starts; the
            # removal ends the second stay at the time it was saved.
            assert periods[0].installed_until == installed_on_second
            assert periods[1].installed_until is not None
            assert periods[1].installed_until > installed_on_second

            today = datetime.now(timezone.utc).date()

            def warranty(**links: UUID) -> WarrantyModel:
                return WarrantyModel(
                    warranty_type=WarrantyType.STANDARD.value,
                    starts_on=today,
                    ends_on=today,
                    status=WarrantyStatus.ACTIVE.value,
                    **links,
                )

            db.add(warranty(vehicle_id=first_vehicle.vehicle_id))
            await db.flush()
            for invalid_warranty in (
                warranty(),
                warranty(
                    vehicle_id=first_vehicle.vehicle_id,
                    battery_id=battery.battery_id,
                ),
            ):
                with pytest.raises(IntegrityError):
                    async with db.begin_nested():
                        db.add(invalid_warranty)
                        await db.flush()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_support_filters_and_sos_alert_on_postgres(
    temporary_database: str,
) -> None:
    """SLA/awaiting filters agree with is_sla_breached; an SOS raises an
    SOS_ALERT that the notification reads and mark-all-read see (F-I1, F-I2,
    F-A2, #85)."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory() as db:
            vehicle = await _integration_vehicle(db, license_plate="IT-SUP-001")
            db.add(vehicle)
            await db.flush()
            driver = await _integration_driver(db, license_number="LIC-C")
            now = datetime.now(timezone.utc)
            past_due = now - timedelta(hours=1)
            future_due = now + timedelta(hours=1)

            async def insert_case(
                label: str,
                *,
                status: SupportCaseStatus,
                response_due_at: datetime,
                first_responded_at: datetime | None = None,
                closed_at: datetime | None = None,
                channel: SupportCaseChannel = SupportCaseChannel.IN_APP,
            ) -> tuple[str, object]:
                case_record = await support_repository.insert(
                    db,
                    {
                        "case_type": SupportCaseType.TICKET,
                        "category": SupportCaseCategory.TECHNICAL,
                        "channel": channel,
                        "status": status,
                        "driver_id": driver.driver_id,
                        "subject": label,
                        "sla_response_minutes": 60,
                        "response_due_at": response_due_at,
                        "first_responded_at": first_responded_at,
                        "closed_at": closed_at,
                    },
                )
                return label, case_record.case_id

            case_ids = dict(
                [
                    await insert_case(
                        "open_overdue",
                        status=SupportCaseStatus.OPEN,
                        response_due_at=past_due,
                    ),
                    await insert_case(
                        "open_in_time",
                        status=SupportCaseStatus.OPEN,
                        response_due_at=future_due,
                        channel=SupportCaseChannel.ZALO,
                    ),
                    await insert_case(
                        "answered_late",
                        status=SupportCaseStatus.ACKNOWLEDGED,
                        response_due_at=past_due,
                        first_responded_at=now,
                    ),
                    await insert_case(
                        "cancelled_in_time",
                        status=SupportCaseStatus.CANCELLED,
                        response_due_at=past_due,
                        closed_at=past_due - timedelta(minutes=5),
                    ),
                    await insert_case(
                        "closed_in_time",
                        status=SupportCaseStatus.CLOSED,
                        response_due_at=future_due,
                        first_responded_at=now,
                        closed_at=now,
                    ),
                ]
            )
            labels_by_case_id = {case_id: label for label, case_id in case_ids.items()}

            async def labels(**filters: object) -> set[str]:
                case_list = await support_service.list_support_cases(
                    db,
                    principal=build_internal_principal(),
                    page_size=100,
                    **filters,  # type: ignore[arg-type]
                )
                assert case_list.total == len(case_list.items)
                if "sla_breached_filter" in filters:
                    assert all(
                        item.is_sla_breached is filters["sla_breached_filter"]
                        for item in case_list.items
                    )
                return {labels_by_case_id[item.case_id] for item in case_list.items}

            assert await labels(sla_breached_filter=True) == {
                "open_overdue",
                "answered_late",
            }
            assert await labels(sla_breached_filter=False) == {
                "open_in_time",
                "cancelled_in_time",
                "closed_in_time",
            }
            assert await labels(awaiting_response_filter=True) == {
                "open_overdue",
                "open_in_time",
            }
            assert await labels(awaiting_response_filter=False) == {
                "answered_late",
                "cancelled_in_time",
                "closed_in_time",
            }
            assert await labels(channel_filter=SupportCaseChannel.ZALO) == {
                "open_in_time"
            }
            assert len(await labels(driver_id_filter=driver.driver_id)) == 5
            assert await labels(category_filter=SupportCaseCategory.BILLING) == set()

            sos = await support_service.create_support_sos(
                db,
                SupportSosCreateRequest.model_validate(
                    {
                        "vehicle_vin": vehicle.vin,
                        "driver_id": str(driver.driver_id),
                        "latitude": 10.8,
                        "longitude": 106.7,
                        "error_code": "E-042",
                    }
                ),
                principal=build_internal_principal(),
            )
            assert sos.channel is SupportCaseChannel.IN_APP

            recipient = UserModel(
                phone_number="+84900000077",
                full_name="Dispatcher",
                status=UserStatus.ACTIVE.value,
            )
            db.add(recipient)
            await db.flush()
            sos_alerts = await notifications_service.list_notifications(
                db,
                after_id=0,
                limit=10,
                vehicle_id=vehicle.vehicle_id,
                notification_type=NotificationType.SOS_ALERT,
                severity=NotificationSeverity.CRITICAL,
                order=NotificationListOrder.DESC,
                principal=build_internal_principal(),
            )
            assert sos_alerts.count == 1
            sos_alert = sos_alerts.notifications[0]
            assert sos_alert.payload["case_id"] == str(sos.case_id)
            assert sos_alert.payload["vehicle_vin"] == vehicle.vin
            assert sos_alert.payload["latitude"] == pytest.approx(10.8)
            assert sos_alert.payload["error_code"] == "E-042"

            assert sos_alert.organization_id == vehicle.organization_id
            assert (sos_alert.subject_type, sos_alert.subject_id) == (
                "SUPPORT_CASE",
                sos.case_id,
            )
            await notifications_service.add_notification_recipients(
                db, sos_alert.notification_id, [recipient.user_id]
            )
            unread = await notifications_service.count_unread_notifications(
                db, principal=build_principal(user_id=recipient.user_id)
            )
            first_mark = await notifications_service.mark_all_notifications_read(
                db, principal=build_principal(user_id=recipient.user_id)
            )
            second_mark = await notifications_service.mark_all_notifications_read(
                db, principal=build_principal(user_id=recipient.user_id)
            )
            still_unread = await notifications_service.list_notifications(
                db,
                principal=build_principal(user_id=recipient.user_id),
                after_id=0,
                limit=10,
                mine_only=True,
                unread_only=True,
            )
            assert unread.unread_count == 1
            assert first_mark.marked_count == 1
            assert second_mark.marked_count == 0
            assert still_unread.count == 0
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_device_health_fields_follow_real_telemetry_on_postgres(
    temporary_database: str,
) -> None:
    """A fresh reading is online; one back-dated past the silence threshold is
    silent; an unmounted device has no health (F-J1, D2)."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    now = datetime.now(timezone.utc)
    silent_received_at = now - timedelta(
        minutes=settings.TELEMATICS_SILENT_THRESHOLD_MINUTES + 5
    )
    try:
        async with session_factory() as db:
            online_vehicle = await _integration_vehicle(
                db, license_plate="IT-HEALTH-ON"
            )
            silent_vehicle = await _integration_vehicle(
                db, license_plate="IT-HEALTH-OFF"
            )
            db.add_all([online_vehicle, silent_vehicle])
            await db.flush()
            devices: dict[str, TelematicModel] = {}
            for label, vehicle_id in (
                ("online", online_vehicle.vehicle_id),
                ("silent", silent_vehicle.vehicle_id),
                ("unmounted", None),
            ):
                devices[label] = TelematicModel(
                    telematic_id=uuid4(),
                    telematic_serial=f"IT-TBOX-{uuid4().hex[:12]}",
                    organization_id=online_vehicle.organization_id,
                    acquired_at=now,
                    vehicle_id=vehicle_id,
                    status=TelematicStatus.ACTIVE,
                )
                db.add(devices[label])
            await db.flush()
            for label, received_at, signal_strength in (
                ("online", now - timedelta(seconds=30), -67),
                ("silent", silent_received_at, -95),
            ):
                device = devices[label]
                await telemetry_repository.insert_telemetry(
                    db,
                    {
                        "organization_id": device.organization_id,
                        "device_message_id": uuid4(),
                        "telematic_id": device.telematic_id,
                        "vehicle_id": device.vehicle_id,
                        "recorded_at": received_at,
                        "received_at": received_at,
                        "location": coordinates_to_location(10.8, 106.7),
                        "soc_percent": 80.0,
                        "signal_dbm": signal_strength,
                        "raw_payload": {"source": "postgres-integration"},
                    },
                )

            online = await telematics_service.get_telematic(
                db, devices["online"].telematic_id, principal=build_internal_principal()
            )
            silent = await telematics_service.get_telematic(
                db, devices["silent"].telematic_id, principal=build_internal_principal()
            )
            unmounted = await telematics_service.get_telematic(
                db,
                devices["unmounted"].telematic_id,
                principal=build_internal_principal(),
            )

            assert online.last_seen_at is not None
            assert online.is_online is True
            assert online.is_silent is False
            assert online.last_signal_strength_dbm == -67
            assert silent.last_seen_at == silent_received_at
            assert silent.is_online is False
            assert silent.is_silent is True
            assert silent.last_signal_strength_dbm == -95
            assert unmounted.last_seen_at is None
            assert unmounted.is_online is False
            assert unmounted.is_silent is False
            await db.rollback()
    finally:
        await engine.dispose()


def _integration_envelope(
    telematic_serial: str, recorded_at: datetime, latitude: float, longitude: float
) -> TelemetryEnvelope:
    """Build a validated telemetry message at one position.

    Args:
        telematic_serial: Serial of the reporting device.
        recorded_at: Device timestamp of the reading.
        latitude: GPS latitude.
        longitude: GPS longitude.

    Returns:
        The envelope ``process_message`` takes (SOC 80, no other fields).
    """
    message = TelemetryMessage.model_validate(
        {
            "message_uuid": str(uuid4()),
            "telematic_serial": telematic_serial,
            "recorded_at": recorded_at.isoformat(),
            "location": {"latitude": latitude, "longitude": longitude},
            "battery": {"soc": 80.0},
        }
    )
    return TelemetryEnvelope(message=message, raw_payload={"source": "integration"})


@pytest.mark.asyncio
async def test_geofence_enter_then_exit_raises_alerts_through_ingestion(
    temporary_database: str,
) -> None:
    """F-A5: a member vehicle driving into, then out of, a fleet polygon raises
    one ENTER and one EXIT GEOFENCE_ALERT, in the reading's transaction."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    square_ring = [
        (106.70, 10.77),
        (106.71, 10.77),
        (106.71, 10.78),
        (106.70, 10.78),
        (106.70, 10.77),
    ]
    try:
        async with session_factory() as db:
            # The seeded previous reading sits at (10.8, 106.7): outside.
            vehicle = await _seed_vehicle_readings(
                db, [{"recorded_at": _september_2026(1, 1), "soc_percent": 80.0}]
            )
            telematic_serial = (
                await db.execute(
                    select(TelematicModel.telematic_serial).where(
                        TelematicModel.vehicle_id == vehicle.vehicle_id
                    )
                )
            ).scalar_one()
            fleet = await fleet_repository.insert(
                db,
                {
                    "organization_id": await _integration_organization(db),
                    "fleet_code": "GF-01",
                    "name": "Geofence Fleet",
                },
            )
            await fleet_repository.insert_membership(
                db,
                fleet_id=fleet.fleet_id,
                vehicle_id=vehicle.vehicle_id,
                added_at=datetime.now(timezone.utc),
            )
            geofence = await fleet_service.create_geofence(
                db,
                fleet.fleet_id,
                GeofenceCreateRequest(
                    name="Depot",
                    boundary=GeofencePolygonGeoJson(coordinates=[square_ring]),
                ),
                principal=build_internal_principal(),
            )

            entered = await telemetry_service.process_message(
                db,
                _integration_envelope(
                    telematic_serial, _september_2026(1, 2), 10.775, 106.705
                ),
            )
            left = await telemetry_service.process_message(
                db,
                _integration_envelope(
                    telematic_serial, _september_2026(1, 3), 10.80, 106.705
                ),
            )
            alerts = list(
                (
                    await db.execute(
                        select(NotificationModel)
                        .where(
                            NotificationModel.vehicle_id == vehicle.vehicle_id,
                            NotificationModel.notification_type
                            == NotificationType.GEOFENCE_ALERT,
                        )
                        .order_by(NotificationModel.notification_id)
                    )
                ).scalars()
            )
            # Read everything before the rollback expires the ORM rows.
            fleet_id = fleet.fleet_id
            alert_payloads = [alert.payload for alert in alerts]
            alert_severities = [alert.severity for alert in alerts]
            await db.rollback()

        assert entered["processed"] == 1
        assert left["processed"] == 1
        assert [payload["transition"] for payload in alert_payloads] == [
            "ENTER",
            "EXIT",
        ]
        enter_payload = alert_payloads[0]
        assert enter_payload["geofence_id"] == str(geofence.geofence_id)
        assert enter_payload["geofence_name"] == "Depot"
        assert enter_payload["fleet_id"] == str(fleet_id)
        assert enter_payload["latitude"] == pytest.approx(10.775)
        assert enter_payload["recorded_at"] == _september_2026(1, 2).isoformat()
        assert enter_payload["previous_recorded_at"] == (
            _september_2026(1, 1).isoformat()
        )
        assert alert_payloads[1]["previous_recorded_at"] == (
            _september_2026(1, 2).isoformat()
        )
        assert alert_severities == [NotificationSeverity.WARNING] * 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fleet_operating_report_totals_sum_members_on_postgres(
    temporary_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-A6: the fleet rollup folds each member's telemetry and recomputes
    the fleet rates from the summed distance and energy."""
    monkeypatch.setattr(settings, "TELEMETRY_ENERGY_COST_PER_KWH_VND", 3000.0)
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory() as db:
            # 200 kWh packs: 10% = 20 kWh over 50 km; 30% = 60 kWh over 250 km.
            first_vehicle = await _seed_vehicle_readings(
                db,
                [
                    {
                        "recorded_at": _september_2026(1, 1),
                        "soc_percent": 90.0,
                        "odometer_km": 1000.0,
                    },
                    {
                        "recorded_at": _september_2026(1, 5),
                        "soc_percent": 80.0,
                        "odometer_km": 1050.0,
                    },
                ],
            )
            second_vehicle = await _seed_vehicle_readings(
                db,
                [
                    {
                        "recorded_at": _september_2026(1, 2),
                        "soc_percent": 70.0,
                        "odometer_km": 0.0,
                    },
                    {
                        "recorded_at": _september_2026(1, 6),
                        "soc_percent": 40.0,
                        "odometer_km": 250.0,
                    },
                ],
            )
            fleet = await fleet_repository.insert(
                db,
                {
                    "organization_id": await _integration_organization(db),
                    "fleet_code": "RU-01",
                    "name": "Rollup Fleet",
                },
            )
            added_at_base = datetime.now(timezone.utc)
            for offset_seconds, member_vehicle in enumerate(
                (first_vehicle, second_vehicle)
            ):
                await fleet_repository.insert_membership(
                    db,
                    fleet_id=fleet.fleet_id,
                    vehicle_id=member_vehicle.vehicle_id,
                    added_at=added_at_base + timedelta(seconds=offset_seconds),
                )

            report = await telemetry_service.get_fleet_operating_report(
                db,
                fleet.fleet_id,
                start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
                end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
                principal=build_internal_principal(),
            )
            await db.rollback()

        assert [row.vehicle_id for row in report.vehicles] == [
            first_vehicle.vehicle_id,
            second_vehicle.vehicle_id,
        ]
        assert [row.energy_per_100km_kwh for row in report.vehicles] == [
            pytest.approx(40.0),
            pytest.approx(24.0),
        ]
        assert report.totals.vehicle_count == 2
        assert report.totals.sample_count == 4
        assert report.totals.distance_km == pytest.approx(300.0)
        assert report.totals.energy_consumed_kwh == pytest.approx(80.0)
        # 80 kWh / 300 km, not the mean of 40 and 24 (32).
        assert report.totals.energy_per_100km_kwh == pytest.approx(80.0 / 3.0)
        assert report.totals.energy_cost_vnd == pytest.approx(240_000.0)
        assert report.totals.cost_per_km_vnd == pytest.approx(800.0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_location_station_topology_history_and_cascade_on_postgres(
    temporary_database: str,
) -> None:
    """Locations, chargers, EVSEs and guns through the service on real PostgreSQL.

    A private location is invisible to the nearby search until it is made public
    (CS-10); every tracked update writes a history row with its reason (DM-21);
    access grants refuse the owner and duplicates and can be granted again after
    a revoke (CS-13); a soft delete cascades down to the guns, sets the closing
    status (DM-25) and closes the grants; a command to the deleted charger is
    refused.
    """
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory.begin() as db:
            owner = build_organization_record()
            partner = build_organization_record()
            actor = UserModel(
                phone_number="+84900000077",
                full_name="Admin",
                status=UserStatus.ACTIVE.value,
            )
            db.add_all([owner, partner, actor])
            await db.flush()
            owner_id, partner_id, actor_id = (
                owner.organization_id,
                partner.organization_id,
                actor.user_id,
            )

        async with session_factory.begin() as db:
            location = await charging_stations_service.create_charging_location(
                db,
                ChargingLocationCreateRequest(
                    organization_id=owner_id,
                    display_name="Binh Duong",
                    address="Km 1",
                    latitude=10.0,
                    longitude=106.0,
                    is_public=False,
                ),
                principal=build_internal_principal(),
            )
            station = await charging_stations_service.create_charging_station(
                db,
                ChargingStationCreateRequest(
                    location_id=location.location_id,
                    ocpp_identity="IT-CRUD-1",
                    registered_serial_number="SN-CRUD-1",
                    physical_reference="Tru 1",
                    max_power_kw=240,
                ),
                principal=build_internal_principal(),
            )
            evse = await charging_stations_service.create_charging_evse(
                db,
                station.station_id,
                ChargingEvseCreateRequest(
                    ocpp_evse_id=1, emi3_evse_id="VN*G3N*E-CRUD-1"
                ),
                principal=build_internal_principal(),
            )
            connector = await charging_stations_service.create_charging_connector(
                db,
                evse.evse_id,
                ChargingConnectorCreateRequest(
                    ocpp_connector_id=1,
                    standard=ConnectorStandard.IEC_62196_T2_COMBO,
                    max_power_kw=120,
                    max_voltage_v=1000,
                    max_current_a=250,
                ),
                principal=build_internal_principal(),
            )
        assert station.organization_id == owner_id
        assert station.is_online is False and station.charger_status is None
        assert connector.status is None and connector.standard.value.startswith("IEC")

        async def nearby_total(principal: Principal) -> int:
            """Count the chargers around the pin that the caller may see."""
            async with session_factory() as db:
                found = await charging_stations_service.list_nearby_charging_stations(
                    db,
                    latitude=10.0,
                    longitude=106.0,
                    radius_km=5,
                    principal=principal,
                )
            return found.total

        # A private location is visible to its owner and to internal staff,
        # not to an unrelated organization (CS-10).
        assert await nearby_total(build_principal(organization_id=uuid4())) == 0
        assert await nearby_total(build_principal(organization_id=owner_id)) == 1
        assert await nearby_total(build_internal_principal()) == 1

        async with session_factory.begin() as db:
            await charging_stations_service.update_charging_location(
                db,
                location.location_id,
                ChargingLocationUpdateRequest.model_validate({"is_public": True}),
                principal=build_internal_principal(),
            )
            await charging_stations_service.update_charging_station(
                db,
                station.station_id,
                ChargingStationUpdateRequest.model_validate(
                    {"status": "INACTIVE", "status_reason": "Power module broken"}
                ),
                principal=build_internal_principal(),
            )
        async with session_factory() as db:
            shown = await charging_stations_service.list_nearby_charging_stations(
                db,
                latitude=10.0,
                longitude=106.0,
                radius_km=5,
                connector_standard="CCS2",
                is_operational_only=False,
                principal=build_internal_principal(),
            )
            operational_only = (
                await charging_stations_service.list_nearby_charging_stations(
                    db,
                    latitude=10.0,
                    longitude=106.0,
                    radius_km=5,
                    principal=build_internal_principal(),
                )
            )
        # An INACTIVE charger is found only when operational filtering is off.
        assert (shown.total, operational_only.total) == (1, 0)

        async with engine.connect() as connection:
            location_history = (
                await connection.execute(
                    text(
                        "SELECT is_public, change_reason FROM charging_location_history "
                        "WHERE location_id = :l"
                    ),
                    {"l": location.location_id},
                )
            ).all()
            station_history = (
                await connection.execute(
                    text(
                        "SELECT status, change_reason FROM charging_station_history "
                        "WHERE station_id = :s"
                    ),
                    {"s": station.station_id},
                )
            ).all()
        assert [tuple(row) for row in location_history] == [
            (False, "Charging location edited")
        ]
        assert [tuple(row) for row in station_history] == [
            ("ACTIVE", "Power module broken")
        ]

        async with session_factory.begin() as db:
            with pytest.raises(ChargingLocationAccessConflictError):
                await charging_stations_service.grant_charging_location_access(
                    db,
                    location.location_id,
                    ChargingLocationAccessCreateRequest(
                        allowed_organization_id=owner_id
                    ),
                    principal=build_internal_principal(),
                )
            grant = await charging_stations_service.grant_charging_location_access(
                db,
                location.location_id,
                ChargingLocationAccessCreateRequest(allowed_organization_id=partner_id),
                principal=build_internal_principal(),
            )
            with pytest.raises(ChargingLocationAccessConflictError):
                await charging_stations_service.grant_charging_location_access(
                    db,
                    location.location_id,
                    ChargingLocationAccessCreateRequest(
                        allowed_organization_id=partner_id
                    ),
                    principal=build_internal_principal(),
                )
            await charging_stations_service.revoke_charging_location_access(
                db,
                location.location_id,
                grant.access_id,
                ChargingLocationAccessRevokeRequest(revoke_reason="Contract ended"),
                principal=build_internal_principal(),
            )
            regrant = await charging_stations_service.grant_charging_location_access(
                db,
                location.location_id,
                ChargingLocationAccessCreateRequest(allowed_organization_id=partner_id),
                principal=build_internal_principal(),
            )
        assert regrant.access_id != grant.access_id

        async with session_factory.begin() as db:
            queued = await charging_stations_service.queue_station_command(
                db,
                station_id=station.station_id,
                command_type=StationCommandType.RESET,
                requested_by=actor_id,
                reason="Hung charger",
            )
            with pytest.raises(ChargingStationCommandInputError):
                await charging_stations_service.queue_station_command(
                    db,
                    station_id=station.station_id,
                    command_type=StationCommandType.CHANGE_CONFIGURATION,
                )
        assert queued.outcome is StationCommandOutcome.PENDING

        async with session_factory.begin() as db:
            await charging_stations_service.soft_delete_charging_location(
                db,
                location.location_id,
                status_reason="Site closed",
                principal=build_internal_principal(),
            )
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT s.status, s.deleted_at IS NOT NULL, "
                        "(SELECT bool_and(c.deleted_at IS NOT NULL) "
                        " FROM charging_connectors c WHERE c.connector_id = :c), "
                        "(SELECT count(*) FROM charging_location_access a "
                        " WHERE a.location_id = :l AND a.revoked_at IS NULL) "
                        "FROM charging_stations s WHERE s.station_id = :s"
                    ),
                    {
                        "s": station.station_id,
                        "c": connector.connector_id,
                        "l": location.location_id,
                    },
                )
            ).one()
        assert tuple(rows) == ("INACTIVE", True, True, 0)
        async with session_factory.begin() as db:
            with pytest.raises(ChargingStationNotFoundError):
                await charging_stations_service.queue_station_command(
                    db,
                    station_id=station.station_id,
                    command_type=StationCommandType.RESET,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_gateway_command_loop_sends_queued_commands_and_writes_answers(
    temporary_database: str,
) -> None:
    """A queued command reaches a connected charger and its answer comes back (PR-16).

    Real gateway process, a raw WebSocket "charger" speaking OCPP 1.6J: after the
    boot the gateway asks for the configuration itself (answered); a RESET queued
    through the service is then sent with the message ID stored on the row and
    the charger's verdict is written back, a refused CHANGE_CONFIGURATION ends
    REJECTED, and a command for a charger that never connects ends NOT_SENT.
    """
    port = _free_port()
    environment = os.environ | {
        "DATABASE_URL": temporary_database,
        "CHARGING_OCPP_HOST": "127.0.0.1",
        "CHARGING_OCPP_PORT": str(port),
        "CHARGING_OCPP_COMMAND_POLL_SECONDS": "0.2",
        "CHARGING_OCPP_COMMAND_PICKUP_TIMEOUT_SECONDS": "2",
    }
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    await _provision_station(engine, "CMD-16", [1])
    await _provision_station(engine, "CMD-OFFLINE", [1])
    gateway = subprocess.Popen(
        [sys.executable, "-m", "app.domains.charging_stations.ocpp.entrypoint"],
        cwd=_backend_root(),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    async def station_id_of(identity: str) -> UUID:
        async with engine.connect() as connection:
            return UUID(
                str(
                    (
                        await connection.execute(
                            text(
                                "SELECT station_id FROM charging_stations "
                                "WHERE ocpp_identity = :i"
                            ),
                            {"i": identity},
                        )
                    ).scalar_one()
                )
            )

    async def wait_for_outcome(command_id: UUID) -> tuple[str, str | None, str | None]:
        for _ in range(100):
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            "SELECT outcome, response_status, ocpp_message_id "
                            "FROM charging_station_commands WHERE command_id = :c"
                        ),
                        {"c": command_id},
                    )
                ).one()
            if row.outcome != "PENDING":
                return (row.outcome, row.response_status, row.ocpp_message_id)
            await asyncio.sleep(0.2)
        raise AssertionError("the command was never answered")

    try:
        _wait_for_port(port)
        station_id = await station_id_of("CMD-16")
        offline_id = await station_id_of("CMD-OFFLINE")
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/ocpp/CMD-16",
            subprotocols=["ocpp1.6"],  # type: ignore[list-item]
        ) as charger:
            await charger.send(
                json.dumps(
                    [
                        2,
                        "boot-1",
                        "BootNotification",
                        {"chargePointVendor": "V", "chargePointModel": "M"},
                    ]
                )
            )
            boot_answer = json.loads(await asyncio.wait_for(charger.recv(), 10))
            assert (boot_answer[0], boot_answer[1]) == (3, "boot-1")
            get_configuration = json.loads(await asyncio.wait_for(charger.recv(), 10))
            assert get_configuration[2] == "GetConfiguration"
            await charger.send(
                json.dumps([3, get_configuration[1], {"configurationKey": []}])
            )

            async with session_factory.begin() as db:
                reset = await charging_stations_service.queue_station_command(
                    db,
                    station_id=station_id,
                    command_type=StationCommandType.RESET,
                    parameters={"reset_type": "Hard"},
                )
            reset_call = json.loads(await asyncio.wait_for(charger.recv(), 10))
            assert (reset_call[0], reset_call[2]) == (2, "Reset")
            assert reset_call[3] == {"type": "Hard"}
            await charger.send(json.dumps([3, reset_call[1], {"status": "Accepted"}]))
            outcome, response_status, message_id = await wait_for_outcome(
                reset.command_id
            )
            assert (outcome, response_status) == ("ACCEPTED", "Accepted")
            assert message_id == reset_call[1]

            async with session_factory.begin() as db:
                change = await charging_stations_service.queue_station_command(
                    db,
                    station_id=station_id,
                    command_type=StationCommandType.CHANGE_CONFIGURATION,
                    parameters={"key": "HeartbeatInterval", "value": "5"},
                )
            change_call = json.loads(await asyncio.wait_for(charger.recv(), 10))
            assert change_call[2] == "ChangeConfiguration"
            assert change_call[3] == {"key": "HeartbeatInterval", "value": "5"}
            await charger.send(json.dumps([3, change_call[1], {"status": "Rejected"}]))
            assert (await wait_for_outcome(change.command_id))[:2] == (
                "REJECTED",
                "Rejected",
            )

        async with session_factory.begin() as db:
            unsent = await charging_stations_service.queue_station_command(
                db,
                station_id=offline_id,
                command_type=StationCommandType.RESET,
            )
        outcome, response_status, message_id = await wait_for_outcome(unsent.command_id)
        assert (outcome, response_status, message_id) == ("NOT_SENT", None, None)
    finally:
        gateway.terminate()
        try:
            gateway.wait(timeout=15)
        except subprocess.TimeoutExpired:
            gateway.kill()
        await engine.dispose()


@pytest.mark.asyncio
async def test_qr_charge_flow_scan_remote_start_meter_values_remote_stop_receipt(
    temporary_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole QR charge on PostgreSQL with a raw OCPP 1.6J charger (WP8, CHG-01..04).

    Real gateway process, a raw WebSocket "charger": the scan checks the wallet
    minimum, creates the PENDING session and queues a REMOTE_START that the
    gateway's loop sends with the session token; Authorize accepts only that
    token; StartTransaction with it turns the session ACTIVE; MeterValues feed
    the live progress; the stop endpoint queues a REMOTE_STOP and the charger's
    StopTransaction completes the session; the receipt and the user's history
    read it. Then a rejected remote start abandons its session at once, and a
    scan the charger never starts is swept to ABANDONED, after which its token
    is refused.

    WP9 adds the money side: the wallet is funded through a VietQR top-up and a
    (duplicated) bank notification, a scan needs a tariff and freezes its price
    on a QUOTED bill, the gateway process bills the stop (BILLED, wallet debited
    once) and voids the bills of the abandoned scans.
    """
    port = _free_port()
    environment = os.environ | {
        "DATABASE_URL": temporary_database,
        "CHARGING_OCPP_HOST": "127.0.0.1",
        "CHARGING_OCPP_PORT": str(port),
        "CHARGING_OCPP_COMMAND_POLL_SECONDS": "0.2",
        "CHARGING_PENDING_SESSION_TIMEOUT_SECONDS": "6",
        "CHARGING_SESSION_SWEEP_INTERVAL_SECONDS": "1",
    }
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    station_owner_id = await _provision_station(engine, "QR-16", [1])
    await _provision_station(engine, "QR-OFFLINE", [1])
    organization_id, user_id = await _provision_organization_and_user(engine)
    driver = build_principal(
        roles=frozenset({UserRole.DRIVER}),
        organization_id=organization_id,
        user_id=user_id,
    )
    billing_staff = build_internal_principal(roles=frozenset({UserRole.ACCOUNTANT}))
    owner_admin = build_internal_principal()
    monkeypatch.setattr(settings, "BILLING_WEBHOOK_SECRET", "it-secret")
    gateway = subprocess.Popen(
        [sys.executable, "-m", "app.domains.charging_stations.ocpp.entrypoint"],
        cwd=_backend_root(),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    async def next_call(charger: Any, action: str) -> list[Any]:
        frame = json.loads(await asyncio.wait_for(charger.recv(), 10))
        assert (frame[0], frame[2]) == (2, action), frame
        return list(frame)

    async def request(
        charger: Any, message_id: str, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        await charger.send(json.dumps([2, message_id, action, payload]))
        frame = json.loads(await asyncio.wait_for(charger.recv(), 10))
        assert (frame[0], frame[1]) == (3, message_id), frame
        return dict(frame[2])

    async def command_outcome(command_id: UUID) -> str:
        for _ in range(100):
            async with engine.connect() as connection:
                outcome = (
                    await connection.execute(
                        text(
                            "SELECT outcome FROM charging_station_commands "
                            "WHERE command_id = :c"
                        ),
                        {"c": command_id},
                    )
                ).scalar_one()
            if outcome != "PENDING":
                return str(outcome)
            await asyncio.sleep(0.2)
        raise AssertionError("the command was never answered")

    async def session_status(session_id: UUID) -> str:
        async with engine.connect() as connection:
            return str(
                (
                    await connection.execute(
                        text(
                            "SELECT status::text FROM charging_sessions "
                            "WHERE session_id = :s"
                        ),
                        {"s": session_id},
                    )
                ).scalar_one()
            )

    def stamp(offset_seconds: int) -> str:
        moment = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    scan_request = ChargingSessionScanRequest(charger_code="QR-16", connector_number=1)
    try:
        _wait_for_port(port)
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/ocpp/QR-16",
            subprotocols=["ocpp1.6"],  # type: ignore[list-item]
        ) as charger:
            await request(
                charger,
                "boot-1",
                "BootNotification",
                {"chargePointVendor": "V", "chargePointModel": "M"},
            )
            get_configuration = await next_call(charger, "GetConfiguration")
            await charger.send(
                json.dumps([3, get_configuration[1], {"configurationKey": []}])
            )

            # A charger that never connected cannot be scanned (offline).
            async with session_factory.begin() as db:
                with pytest.raises(ChargingStationOfflineError):
                    await charging_session_flow.scan_charging_session_endpoint(
                        ChargingSessionScanRequest(charger_code="QR-OFFLINE"),
                        principal=driver,
                        db_session=db,
                    )

            # The wallet minimum is read from the wallets table (BL-14).
            monkeypatch.setattr(settings, "BILLING_MIN_BALANCE_VND", Decimal(50000))
            async with session_factory.begin() as db:
                with pytest.raises(InsufficientBalanceError):
                    await charging_session_flow.scan_charging_session_endpoint(
                        scan_request, principal=driver, db_session=db
                    )

            # Top up 60,000 through VietQR: the bank notification (sent twice)
            # credits the wallet exactly once.
            async with session_factory.begin() as db:
                top_up = await billing_router.create_top_up_endpoint(
                    TopUpRequest(amount=60_000), principal=driver, db_session=db
                )
            assert top_up.transfer_code in top_up.vietqr_payload
            bank_notification = {
                "bank_transaction_id": "FT-IT-1",
                "amount": 60_000,
                "content": f"{top_up.transfer_code.lower()} nap tien",
            }
            results = []
            for _ in range(2):
                async with session_factory.begin() as db:
                    answer = await billing_router.receive_bank_notification_endpoint(
                        bank_notification, "it-secret", db
                    )
                results.append(answer.result)
            assert results == [
                BankNotificationResult.CREDITED,
                BankNotificationResult.DUPLICATE,
            ]
            async with session_factory.begin() as db:
                paid = await billing_router.get_payment_endpoint(
                    top_up.payment_id, principal=driver, db_session=db
                )
                wallet = await billing_router.get_my_wallet_endpoint(
                    principal=driver, db_session=db
                )
            assert (paid.status.value, wallet.balance) == ("SUCCEEDED", 60_000)

            # No tariff prices the charger yet: the scan is refused and nothing
            # is created.
            async with session_factory.begin() as db:
                with pytest.raises(NoTariffInForceError):
                    await charging_session_flow.scan_charging_session_endpoint(
                        scan_request, principal=driver, db_session=db
                    )
            async with engine.connect() as connection:
                assert (
                    await connection.execute(
                        text("SELECT count(*) FROM charging_sessions")
                    )
                ).scalar_one() == 0

            # The station owner's default tariff: 4,500 per kWh, 10 % VAT.
            async with session_factory.begin() as db:
                tariff = await billing_router.create_tariff_endpoint(
                    TariffCreateRequest(
                        name="IT standard", organization_id=station_owner_id
                    ),
                    principal=owner_admin,
                    db_session=db,
                )
                version = await billing_router.publish_tariff_version_endpoint(
                    tariff.tariff_id,
                    TariffVersionPublishRequest(
                        price_per_kwh=4500,
                        vat_rate_percent=Decimal(10),
                        change_reason="Launch price",
                    ),
                    principal=owner_admin,
                    db_session=db,
                )

            # 1. The scan: PENDING session, token, REMOTE_START queued.
            async with session_factory.begin() as db:
                scan = await charging_session_flow.scan_charging_session_endpoint(
                    scan_request, principal=driver, db_session=db
                )
            assert scan.status is SessionStatus.PENDING
            # The price of the scan's hour is frozen on a QUOTED bill (PAY-10).
            assert (scan.tariff_version_id, scan.price_per_kwh) == (
                version.tariff_version_id,
                4500,
            )
            async with session_factory.begin() as db:
                quoted = await charging_session_flow.get_charging_session_bill_endpoint(
                    scan.session_id, principal=driver, db_session=db
                )
            assert (quoted.status.value, quoted.price_per_kwh) == ("QUOTED", 4500)
            assert quoted.total_amount is None and not quoted.is_paid
            remote_start = await next_call(charger, "RemoteStartTransaction")
            assert remote_start[3]["idTag"] == scan.id_token
            assert remote_start[3]["connectorId"] == 1
            await charger.send(json.dumps([3, remote_start[1], {"status": "Accepted"}]))
            assert await command_outcome(scan.command_id) == "ACCEPTED"
            assert await session_status(scan.session_id) == "PENDING"

            # A second scan while this one is open is refused.
            async with session_factory.begin() as db:
                with pytest.raises(ChargingSessionAlreadyOpenError):
                    await charging_session_flow.scan_charging_session_endpoint(
                        scan_request, principal=driver, db_session=db
                    )

            # 2. Authorize: only the token we issued.
            wrong = await request(charger, "auth-1", "Authorize", {"idTag": "OTHER"})
            assert wrong["idTagInfo"]["status"] == "Invalid"
            right = await request(
                charger, "auth-2", "Authorize", {"idTag": scan.id_token}
            )
            assert right["idTagInfo"]["status"] == "Accepted"

            # 3. StartTransaction with the token turns the session ACTIVE.
            invalid_start = await request(
                charger,
                "start-0",
                "StartTransaction",
                {
                    "connectorId": 1,
                    "idTag": "OTHER",
                    "meterStart": 1000,
                    "timestamp": stamp(0),
                },
            )
            assert invalid_start["idTagInfo"]["status"] == "Invalid"
            started = await request(
                charger,
                "start-1",
                "StartTransaction",
                {
                    "connectorId": 1,
                    "idTag": scan.id_token,
                    "meterStart": 1000,
                    "timestamp": stamp(0),
                },
            )
            assert started["idTagInfo"]["status"] == "Accepted"
            transaction_id = started["transactionId"]
            assert await session_status(scan.session_id) == "ACTIVE"

            # 4. MeterValues: live progress from the measurements.
            await request(
                charger,
                "meter-1",
                "MeterValues",
                {
                    "connectorId": 1,
                    "transactionId": transaction_id,
                    "meterValue": [
                        {
                            "timestamp": stamp(5),
                            "sampledValue": [
                                {
                                    "value": "1250",
                                    "measurand": "Energy.Active.Import.Register",
                                    "unit": "Wh",
                                },
                                {"value": "60", "measurand": "SoC", "unit": "Percent"},
                                {
                                    "value": "90",
                                    "measurand": "Power.Active.Import",
                                    "unit": "kW",
                                },
                            ],
                        }
                    ],
                },
            )
            async with session_factory.begin() as db:
                live = await charging_sessions_service.get_charging_session(
                    db, scan.session_id, principal=driver
                )
            assert live.status is SessionStatus.ACTIVE
            assert live.energy_delivered_wh == Decimal(250)
            assert (live.soc_end_percent, live.current_power_kw) == (60.0, 90.0)

            # 5. Stop from the app: REMOTE_STOP with the transaction ID.
            async with session_factory.begin() as db:
                stop = await charging_session_flow.stop_charging_session_endpoint(
                    scan.session_id,
                    ChargingSessionStopRequest(),
                    principal=driver,
                    db_session=db,
                )
            remote_stop = await next_call(charger, "RemoteStopTransaction")
            assert remote_stop[3] == {"transactionId": transaction_id}
            await charger.send(json.dumps([3, remote_stop[1], {"status": "Accepted"}]))
            assert stop.command_id is not None
            assert await command_outcome(stop.command_id) == "ACCEPTED"
            await request(
                charger,
                "stop-1",
                "StopTransaction",
                {
                    "meterStop": 1500,
                    "timestamp": stamp(10),
                    "transactionId": transaction_id,
                    "reason": "Remote",
                },
            )
            assert await session_status(scan.session_id) == "COMPLETED"

            # 6. Receipt and history.
            async with session_factory.begin() as db:
                receipt = (
                    await charging_session_flow.get_charging_session_receipt_endpoint(
                        scan.session_id, principal=driver, db_session=db
                    )
                )
                history = await charging_sessions_service.list_my_charging_sessions(
                    db, principal=driver, page=1, page_size=10
                )
            assert receipt.energy_delivered_wh == Decimal(500)
            assert (receipt.location_name, receipt.gun_number) == ("IT location", 1)
            assert receipt.stop_reason == "Remote"
            # 500 Wh x 4,500 = 2,250 + 10 % VAT 225, billed by the gateway's hook.
            assert (receipt.bill_status, receipt.price_per_kwh) == ("BILLED", 4500)
            assert (
                receipt.amount_before_vat,
                receipt.vat_amount,
                receipt.total_amount,
            ) == (2250, 225, 2475)
            assert [item.session_id for item in history.items] == [scan.session_id]
            assert history.items[0].energy_delivered_wh == Decimal(500)

            # The wallet paid the bill once: top-up, then the session bill.
            async with session_factory.begin() as db:
                bill = await charging_session_flow.get_charging_session_bill_endpoint(
                    scan.session_id, principal=driver, db_session=db
                )
                wallet = await billing_router.get_my_wallet_endpoint(
                    principal=driver, db_session=db
                )
                statement = await billing_router.list_my_wallet_transactions_endpoint(
                    page=1, page_size=10, principal=driver, db_session=db
                )
                org_bills = await billing_router.list_session_bills_endpoint(
                    page=1,
                    page_size=10,
                    organization_id=None,
                    started_by=None,
                    status_filter=None,
                    created_from=None,
                    created_to=None,
                    principal=billing_staff,
                    db_session=db,
                )
            assert (bill.status.value, bill.is_paid) == ("BILLED", True)
            assert bill.energy_source == "METER_STOP"
            assert wallet.balance == 60_000 - 2475
            assert [
                (line.transaction_type.value, line.amount, line.balance_after)
                for line in statement.items
            ] == [("SESSION_BILL", -2475, 57_525), ("TOP_UP", 60_000, 60_000)]
            assert [item.session_id for item in org_bills.items] == [scan.session_id]

            # 7. A rejected remote start abandons its session at once.
            async with session_factory.begin() as db:
                rejected_scan = (
                    await charging_session_flow.scan_charging_session_endpoint(
                        scan_request, principal=driver, db_session=db
                    )
                )
            rejected_start = await next_call(charger, "RemoteStartTransaction")
            await charger.send(
                json.dumps([3, rejected_start[1], {"status": "Rejected"}])
            )
            assert await command_outcome(rejected_scan.command_id) == "REJECTED"
            assert await session_status(rejected_scan.session_id) == "ABANDONED"
            async with session_factory.begin() as db:
                voided = await charging_session_flow.get_charging_session_bill_endpoint(
                    rejected_scan.session_id, principal=driver, db_session=db
                )
            assert voided.status.value == "VOID"

            # 8. An accepted remote start nobody follows up is swept, and its
            #    token no longer starts a charge.
            async with session_factory.begin() as db:
                idle_scan = await charging_session_flow.scan_charging_session_endpoint(
                    scan_request, principal=driver, db_session=db
                )
            idle_start = await next_call(charger, "RemoteStartTransaction")
            await charger.send(json.dumps([3, idle_start[1], {"status": "Accepted"}]))
            for _ in range(60):
                if await session_status(idle_scan.session_id) == "ABANDONED":
                    break
                await asyncio.sleep(0.5)
            assert await session_status(idle_scan.session_id) == "ABANDONED"
            late = await request(
                charger,
                "start-late",
                "StartTransaction",
                {
                    "connectorId": 1,
                    "idTag": idle_scan.id_token,
                    "meterStart": 1500,
                    "timestamp": stamp(30),
                },
            )
            assert late["idTagInfo"]["status"] == "Invalid"
            async with session_factory.begin() as db:
                swept = await charging_session_flow.get_charging_session_bill_endpoint(
                    idle_scan.session_id, principal=driver, db_session=db
                )
            assert swept.status.value == "VOID"

            # Staff correct the balance by hand; the ledger keeps the running balance.
            async with session_factory.begin() as db:
                adjustment = await billing_router.adjust_wallet_endpoint(
                    user_id,
                    WalletAdjustmentRequest(amount=-525, reason="Goodwill test"),
                    principal=billing_staff,
                    db_session=db,
                )
            assert adjustment.balance_after == 57_000
            async with engine.connect() as connection:
                ledger_sum, balance = (
                    await connection.execute(
                        text(
                            "SELECT (SELECT sum(amount) FROM wallet_transactions), "
                            "(SELECT balance FROM wallets WHERE user_id = :u)"
                        ),
                        {"u": user_id},
                    )
                ).one()
            assert ledger_sum == balance == 57_000
    finally:
        gateway.terminate()
        try:
            gateway.wait(timeout=15)
        except subprocess.TimeoutExpired:
            gateway.kill()
        await engine.dispose()


@pytest.mark.asyncio
async def test_tariff_versions_wallet_ledger_and_concurrent_notifications_on_postgres(
    temporary_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tariff rules, ledger sums and the once-only credit on real tables (WP9).

    One active tariff per owner and location (the partial unique index), the
    location tariff beating the owner's default, time-of-use prices read in
    Vietnam time, versions that never change and take over on their start
    date, tracked retirement; a wallet whose ledger sums to its balance
    without flooding its history, blocking that records a history row and
    refuses a top-up but not a bank transfer, and two bank notifications for
    one top-up arriving at the same moment crediting it once.
    """
    monkeypatch.setattr(settings, "BILLING_WEBHOOK_SECRET", "it-secret")
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    owner_id = await _provision_station(engine, "TAR-1", [1])
    _, user_id = await _provision_organization_and_user(engine)
    driver = build_principal(roles=frozenset({UserRole.DRIVER}), user_id=user_id)
    admin = build_internal_principal()
    async with engine.connect() as connection:
        station_id, location_id = (
            await connection.execute(
                text(
                    "SELECT station_id, location_id FROM charging_stations "
                    "WHERE ocpp_identity = 'TAR-1'"
                )
            )
        ).one()
    try:
        # One ACTIVE default per owner: the second is a conflict, and the
        # database index is the last line of defence.
        async with session_factory.begin() as db:
            default = await billing_router.create_tariff_endpoint(
                TariffCreateRequest(name="Default", organization_id=owner_id),
                principal=admin,
                db_session=db,
            )
        async with session_factory.begin() as db:
            with pytest.raises(TariffConflictError):
                await billing_router.create_tariff_endpoint(
                    TariffCreateRequest(name="Second", organization_id=owner_id),
                    principal=admin,
                    db_session=db,
                )
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO tariffs (tariff_id, organization_id, name, "
                        "currency, status, created_at, updated_at) VALUES "
                        "(:t, :o, 'Dup', 'VND', 'ACTIVE', now(), now())"
                    ),
                    {"t": uuid4(), "o": owner_id},
                )
        # A tariff of another owner's location is refused.
        other_owner_id, _ = await _provision_organization_and_user(engine)
        async with session_factory.begin() as db:
            with pytest.raises(TariffInputError):
                await billing_router.create_tariff_endpoint(
                    TariffCreateRequest(
                        name="Foreign",
                        location_id=location_id,
                        organization_id=other_owner_id,
                    ),
                    principal=admin,
                    db_session=db,
                )

        night_periods = [
            TariffPeriodSchema.model_validate(
                {
                    "days": ["MON", "TUE", "WED", "THU", "FRI", "SAT"],
                    "from": "22:00",
                    "to": "04:00",
                    "price_per_kwh": 3200,
                }
            )
        ]
        async with session_factory.begin() as db:
            await billing_router.publish_tariff_version_endpoint(
                default.tariff_id,
                TariffVersionPublishRequest(
                    price_per_kwh=4500,
                    vat_rate_percent=Decimal(10),
                    change_reason="Default price",
                ),
                principal=admin,
                db_session=db,
            )
            local = await billing_router.create_tariff_endpoint(
                TariffCreateRequest(
                    name="Depot", location_id=location_id, organization_id=owner_id
                ),
                principal=admin,
                db_session=db,
            )
            local_v1 = await billing_router.publish_tariff_version_endpoint(
                local.tariff_id,
                TariffVersionPublishRequest(
                    price_per_kwh=4000,
                    vat_rate_percent=Decimal(8),
                    time_periods=night_periods,
                    change_reason="Depot price",
                ),
                principal=admin,
                db_session=db,
            )
        # Monday 2027-01-04 23:00 Vietnam time (16:00 UTC) is in the night window.
        night = datetime(2027, 1, 4, 16, tzinfo=timezone.utc)
        day = datetime(2027, 1, 4, 3, tzinfo=timezone.utc)
        async with session_factory.begin() as db:
            at_night = await billing_service.resolve_tariff_for_station(
                db, station_id, night
            )
            at_day = await billing_service.resolve_tariff_for_station(
                db, station_id, day
            )
        assert at_night.tariff_version_id == at_day.tariff_version_id
        assert (at_night.price_per_kwh, at_day.price_per_kwh) == (
            Decimal(3200),
            Decimal(4000),
        )
        assert at_day.vat_rate_percent == Decimal("8.00")

        # Versions never change: a new one takes over only on its date.
        async with session_factory.begin() as db:
            local_v2 = await billing_router.publish_tariff_version_endpoint(
                local.tariff_id,
                TariffVersionPublishRequest(
                    price_per_kwh=5000,
                    vat_rate_percent=Decimal(10),
                    effective_from=datetime.now(timezone.utc) + timedelta(days=1),
                    change_reason="Winter price",
                ),
                principal=admin,
                db_session=db,
            )
            now_quote = await billing_service.resolve_tariff_for_station(
                db, station_id, datetime.now(timezone.utc)
            )
            later_quote = await billing_service.resolve_tariff_for_station(
                db, station_id, datetime.now(timezone.utc) + timedelta(days=2)
            )
            versions = await billing_router.list_tariff_versions_endpoint(
                local.tariff_id, principal=admin, db_session=db
            )
        assert now_quote.tariff_version_id == local_v1.tariff_version_id
        assert later_quote.tariff_version_id == local_v2.tariff_version_id
        assert [(v.version_no, v.price_per_kwh) for v in versions] == [
            (2, 5000),
            (1, 4000),
        ]

        # Retiring is a tracked decision; the price falls back to the default.
        async with session_factory.begin() as db:
            await billing_router.retire_tariff_endpoint(
                local.tariff_id,
                TariffStatusRequest(reason="Depot closed for works"),
                principal=admin,
                db_session=db,
            )
            fallback = await billing_service.resolve_tariff_for_station(
                db, station_id, datetime.now(timezone.utc)
            )
        assert fallback.price_per_kwh == Decimal(4500)
        async with engine.connect() as connection:
            history = (
                await connection.execute(
                    text(
                        "SELECT status, change_reason, changed_by FROM tariff_history "
                        "WHERE tariff_id = :t"
                    ),
                    {"t": local.tariff_id},
                )
            ).all()
        assert [tuple(row) for row in history] == [
            ("ACTIVE", "Depot closed for works", admin.user_id)
        ]

        # Two bank notifications for one top-up at the same moment: one credit.
        async with session_factory.begin() as db:
            top_up = await billing_router.create_top_up_endpoint(
                TopUpRequest(amount=200_000), principal=driver, db_session=db
            )

        async def notify(reference: str) -> BankNotificationResult:
            async with session_factory.begin() as db:
                answer = await billing_router.receive_bank_notification_endpoint(
                    {
                        "bank_transaction_id": reference,
                        "amount": 200_000,
                        "content": top_up.transfer_code,
                    },
                    "it-secret",
                    db,
                )
            return answer.result

        outcomes = await asyncio.gather(notify("FT-A"), notify("FT-A"), notify("FT-B"))
        assert sorted(outcome.value for outcome in outcomes).count("CREDITED") == 1
        async with session_factory.begin() as db:
            wallet = await billing_router.get_my_wallet_endpoint(
                principal=driver, db_session=db
            )
        assert wallet.balance == 200_000

        # Blocking is a tracked decision: top-ups are refused, but money that
        # arrives by bank transfer is still credited; the balance moves leave
        # the wallet history alone.
        staff = build_internal_principal(roles=frozenset({UserRole.ACCOUNTANT}))
        async with session_factory.begin() as db:
            await billing_router.set_wallet_status_endpoint(
                user_id,
                WalletStatusRequest(status=WalletStatus.BLOCKED, reason="Fraud check"),
                principal=staff,
                db_session=db,
            )
        async with session_factory.begin() as db:
            with pytest.raises(WalletBlockedError):
                await billing_router.create_top_up_endpoint(
                    TopUpRequest(amount=50_000), principal=driver, db_session=db
                )
        async with session_factory.begin() as db:
            await billing_router.adjust_wallet_endpoint(
                user_id,
                WalletAdjustmentRequest(amount=-1000, reason="Test correction"),
                principal=staff,
                db_session=db,
            )
        async with engine.connect() as connection:
            wallet_history = (
                await connection.execute(
                    text(
                        "SELECT status, change_reason FROM wallet_history "
                        "WHERE user_id = :u"
                    ),
                    {"u": user_id},
                )
            ).all()
            ledger_sum, balance = (
                await connection.execute(
                    text(
                        "SELECT (SELECT sum(amount) FROM wallet_transactions), "
                        "(SELECT balance FROM wallets WHERE user_id = :u)"
                    ),
                    {"u": user_id},
                )
            ).one()
        assert [tuple(row) for row in wallet_history] == [("ACTIVE", "Fraud check")]
        assert ledger_sum == balance == 199_000
    finally:
        await engine.dispose()


class _CapturingSmsSender:
    """SMS provider that keeps every message so the test can read the codes."""

    def __init__(self) -> None:
        """Start with no messages."""
        self.messages: list[tuple[str, str]] = []

    async def send_sms(self, phone_number: str, message: str) -> None:
        """Remember the message instead of sending it."""
        self.messages.append((phone_number, message))

    def last_code(self) -> str:
        """Return the 6-digit code in the newest message."""
        import re

        match = re.search(r"\b(\d{6})\b", self.messages[-1][1])
        assert match is not None
        return match.group(1)


@pytest.mark.asyncio
async def test_identity_login_flow_roles_lockout_and_handover(
    temporary_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bootstrap, login, onboarding, lockout, reset, handover and suspension work end to end.

    Each step runs in its own transaction like a request; a failure that must
    leave a trace is committed the way the router does for a
    `FailureRecordedError`.
    """
    sms = _CapturingSmsSender()
    monkeypatch.setattr(identity_account_service, "get_sms_sender", lambda: sms)
    monkeypatch.setattr(identity_member_service, "get_sms_sender", lambda: sms)
    monkeypatch.setattr(settings, "IDENTITY_BOOTSTRAP_ADMIN_PHONE", "+84900000001")
    monkeypatch.setattr(settings, "IDENTITY_BOOTSTRAP_ADMIN_PASSWORD", "head admin pw")
    monkeypatch.setattr(settings, "IDENTITY_OTP_RESEND_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(settings, "IDENTITY_LOGIN_MAX_FAILED_ATTEMPTS", 3)

    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async def login(phone_number: str, password: str) -> TokenResponse:
        """Log in as the router would: commit recorded failures, then raise."""
        async with session_factory() as db:
            try:
                tokens = await identity_account_service.login(
                    db,
                    LoginRequest(
                        phone_number=phone_number,
                        password=password,
                        platform=SessionPlatform.ANDROID,
                    ),
                    None,
                )
            except FailureRecordedError:
                await db.commit()
                raise
            await db.commit()
            return tokens

    async def principal_of(tokens: TokenResponse) -> Principal:
        """Authenticate an access token the way the dependencies do."""
        async with session_factory() as db:
            identity = await identity_account_service.authenticate_session(
                db, tokens.access_token
            )
            principal = await identity_account_service.resolve_principal(db, identity)
            await db.commit()
            return principal

    try:
        # Bootstrap creates the internal organization and first HEAD_ADMIN; a
        # second run changes nothing.
        async with session_factory.begin() as db:
            bootstrap_result = await identity_bootstrap.bootstrap_identity(db)
        async with session_factory.begin() as db:
            assert (await identity_bootstrap.bootstrap_identity(db)).created == []
        assert "organization" in bootstrap_result.created

        head_tokens = await login("+84900000001", "head admin pw")
        head = await principal_of(head_tokens)
        assert head.is_internal and UserRole.HEAD_ADMIN in head.roles
        assert head.organization_id == bootstrap_result.organization_id

        # Our staff create a customer; its first admin accepts the SMS invite.
        async with session_factory.begin() as db:
            customer = await identity_organization_service.create_organization(
                db,
                head,
                OrganizationCreateRequest(
                    legal_form=OrganizationLegalForm.COMPANY,
                    display_name="Minh Phat",
                    legal_name="Minh Phat Co.",
                    tax_code="0312345678",
                    first_admin={  # type: ignore[arg-type]
                        "phone_number": "+84900000002",
                        "full_name": "Customer Admin",
                    },
                ),
            )
        # A wrong code is counted against the code and the count survives.
        async with session_factory() as db:
            wrong_code = "000000" if sms.last_code() != "000000" else "111111"
            with pytest.raises(InvalidOneTimeCodeError):
                await identity_account_service.accept_invitation(
                    db,
                    InvitationAcceptRequest(
                        phone_number="+84900000002",
                        code=wrong_code,
                        password="customer pw 1",
                        platform=SessionPlatform.WEB,
                    ),
                    None,
                )
            await db.commit()
        async with engine.connect() as connection:
            failed_attempts = await connection.scalar(
                text(
                    "SELECT failed_attempt_count FROM one_time_codes "
                    "WHERE phone_number = '+84900000002'"
                )
            )
        assert failed_attempts == 1
        async with session_factory() as db:
            accepted = await identity_account_service.accept_invitation(
                db,
                InvitationAcceptRequest(
                    phone_number="+84900000002",
                    code=sms.last_code(),
                    password="customer pw 1",
                    platform=SessionPlatform.WEB,
                ),
                None,
            )
            await db.commit()
        admin = await principal_of(accepted)
        assert admin.organization_id == customer.organization_id
        assert admin.roles == {UserRole.ORG_ADMIN}
        assert not admin.is_internal
        assert admin.can_access_organization(customer.organization_id)
        assert not admin.can_access_organization(head.organization_id)
        async with session_factory() as db:
            with pytest.raises(OrganizationNotFoundError):
                await identity_organization_service.get_organization(
                    db, admin, head.organization_id
                )

        # Wrong passwords count and persist; the next try is locked out (423).
        for _attempt in range(3):
            with pytest.raises(InvalidCredentialsError):
                await login("+84900000002", "wrong password")
        with pytest.raises(LoginLockedError):
            await login("+84900000002", "customer pw 1")
        async with session_factory() as db:
            audit_page = await identity_audit_service.search_audit_logs(
                db,
                head,
                user_id=admin.user_id,
                organization_id=None,
                action=AccessAuditAction.LOGIN_LOCKED,
                resource_type=None,
                occurred_from=None,
                occurred_to=None,
                page=1,
                page_size=10,
            )
        assert audit_page.total == 1

        # A password reset by code clears the lockout and ends the old session.
        async with session_factory() as db:
            await identity_account_service.send_one_time_code(
                db,
                OneTimeCodeSendRequest(
                    phone_number="+84900000002",
                    purpose=OneTimeCodePurpose.PASSWORD_RESET,
                ),
            )
            await db.commit()
        async with session_factory() as db:
            await identity_account_service.reset_password(
                db,
                PasswordResetRequest(
                    phone_number="+84900000002",
                    code=sms.last_code(),
                    new_password="customer pw 2",
                ),
                None,
            )
            await db.commit()
        with pytest.raises(SessionInvalidError):
            await principal_of(accepted)
        new_tokens = await login("+84900000002", "customer pw 2")

        # Refreshing replaces the refresh token; the old one is dead.
        async with session_factory() as db:
            refreshed = await identity_account_service.refresh_session(
                db, RefreshRequest(refresh_token=new_tokens.refresh_token)
            )
            await db.commit()
        async with session_factory() as db:
            with pytest.raises(SessionInvalidError):
                await identity_account_service.refresh_session(
                    db, RefreshRequest(refresh_token=new_tokens.refresh_token)
                )
        admin = await principal_of(refreshed)

        # Invite a fleet manager, hand the ORG_ADMIN role over to them.
        async with session_factory.begin() as db:
            invited = await identity_member_service.invite_member(
                db,
                admin,
                customer.organization_id,
                MemberInviteRequest(
                    phone_number="+84900000003",
                    full_name="Fleet Manager",
                    roles=[UserRole.FLEET_MANAGER],
                ),
            )
        async with session_factory() as db:
            manager_tokens = await identity_account_service.accept_invitation(
                db,
                InvitationAcceptRequest(
                    phone_number="+84900000003",
                    code=sms.last_code(),
                    password="manager pw 1",
                    platform=SessionPlatform.ANDROID,
                ),
                None,
            )
            await db.commit()
        manager = await principal_of(manager_tokens)
        assert manager.membership_id == invited.membership_id
        assert manager.roles == {UserRole.FLEET_MANAGER}

        async with session_factory.begin() as db:
            new_admin = await identity_member_service.handover_org_admin(
                db,
                admin,
                customer.organization_id,
                AdminHandoverRequest(
                    to_membership_id=invited.membership_id, reason="Retiring"
                ),
            )
        assert UserRole.ORG_ADMIN in new_admin.roles
        old_admin = await principal_of(refreshed)
        assert UserRole.ORG_ADMIN not in old_admin.roles
        async with session_factory() as db:
            with pytest.raises(OrgAdminProtectedError):
                await identity_member_service.lock_member(
                    db, head, invited.membership_id, "test"
                )

        # Suspending the organization blocks its members' logins (ID-34).
        async with session_factory.begin() as db:
            await identity_organization_service.change_organization_status(
                db,
                head,
                customer.organization_id,
                OrganizationStatusRequest(
                    status=OrganizationStatus.SUSPENDED, reason="Contract ended"
                ),
            )
        with pytest.raises(OrganizationInactiveError):
            await login("+84900000003", "manager pw 1")
        async with engine.connect() as connection:
            history_reasons = (
                (
                    await connection.execute(
                        text(
                            "SELECT change_reason FROM organization_history "
                            "WHERE organization_id = :organization_id"
                        ),
                        {"organization_id": customer.organization_id},
                    )
                )
                .scalars()
                .all()
            )
        assert "Contract ended" in history_reasons
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_data_scope_hides_another_organizations_records_on_postgres(
    temporary_database: str,
) -> None:
    """ACC-15 on real rows: vehicle, fleet and driver of organization A answer 404
    to organization B, are found by A and by internal staff, and a list of B
    stays empty."""
    from app.domains.drivers.exceptions import DriverNotFoundError
    from app.domains.fleet.exceptions import FleetNotFoundError
    from app.domains.vehicles.exceptions import VehicleNotFoundError
    from app.domains.vehicles.schemas import VehicleCreateRequest

    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory.begin() as db:
            organization_a, vehicle_model_id = await _insert_vehicle_parents(db)
            organization_b = await _integration_organization(db)
            driver = await _integration_driver(
                db, license_number="SCOPE-1", organization_id=organization_a
            )
        principal_a = build_principal(organization_id=organization_a)
        principal_b = build_principal(organization_id=organization_b)
        staff = build_internal_principal()

        async with session_factory.begin() as db:
            vehicle = await vehicle_service.create_vehicle(
                db,
                VehicleCreateRequest(
                    license_plate="SCOPE-001",
                    vin=f"SC{uuid4().hex[:15]}".upper(),
                    vehicle_model_id=vehicle_model_id,
                    year=2026,
                ),
                principal=principal_a,
            )
            fleet = await fleet_service.create_fleet(
                db, FleetCreateRequest(name="Scope fleet"), principal=principal_a
            )
        assert vehicle.organization_id == organization_a
        assert fleet.organization_id == organization_a

        async with session_factory() as db:
            for principal in (principal_a, staff):
                assert (
                    await vehicle_service.get_vehicle(
                        db, vehicle.vehicle_id, principal=principal
                    )
                ).vehicle_id == vehicle.vehicle_id
                assert (
                    await fleet_service.get_fleet(
                        db, fleet.fleet_id, principal=principal
                    )
                ).fleet_id == fleet.fleet_id
                assert (
                    await driver_service.get_driver(
                        db, driver.driver_id, principal=principal
                    )
                ).driver_id == driver.driver_id
            with pytest.raises(VehicleNotFoundError):
                await vehicle_service.get_vehicle(
                    db, vehicle.vehicle_id, principal=principal_b
                )
            with pytest.raises(FleetNotFoundError):
                await fleet_service.get_fleet(db, fleet.fleet_id, principal=principal_b)
            with pytest.raises(DriverNotFoundError):
                await driver_service.get_driver(
                    db, driver.driver_id, principal=principal_b
                )
            assert (
                await vehicle_service.list_vehicles(db, principal=principal_b)
            ).total == 0
            assert (
                await vehicle_service.list_vehicles(db, principal=principal_a)
            ).total == 1
            assert (
                await fleet_service.list_fleets(db, principal=principal_b)
            ).total == 0
            assert (
                await driver_service.list_drivers(db, principal=principal_b)
            ).total == 0
            assert (
                await driver_service.list_drivers(db, principal=principal_a)
            ).total == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ownership_transfer_moves_battery_closes_fleet_and_warranties_follow_on_postgres(
    temporary_database: str,
) -> None:
    """WP3 on real rows: fit a battery, enter warranties, then transfer the truck.

    The transfer (VH-12) changes the truck's owner and date in one transaction,
    closes the seller's fleet membership, moves the seller's battery to the
    buyer; the ownership and installation periods come from the views; a
    warranty follows its object, so the buyer sees it and the seller does not.
    """
    import app.api.vehicle_transfer as vehicle_transfer
    import app.domains.batteries.service as battery_service
    import app.domains.warranties.service as warranty_service
    from app.domains.batteries.exceptions import (
        BatteryConflictError,
        BatteryNotFoundError,
    )
    from app.domains.batteries.schemas import (
        BatteryCreateRequest,
        BatteryInstallRequest,
        BatteryModelCreateRequest,
    )
    from app.domains.batteries.types import BatteryChemistry
    from app.domains.fleet.schemas import FleetVehicleAddRequest
    from app.domains.vehicles.schemas import (
        VehicleCreateRequest,
        VehicleOwnershipTransferRequest,
    )
    from app.domains.warranties.exceptions import (
        WarrantyConflictError,
        WarrantyLimitsInvalidError,
    )
    from app.domains.warranties.schemas import (
        WarrantyCreateRequest,
        WarrantyUpdateRequest,
    )

    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    staff = build_internal_principal()
    first_handover = datetime.now(timezone.utc) - timedelta(days=90)
    sale_date = datetime.now(timezone.utc) - timedelta(days=1)
    today = datetime.now(timezone.utc).date()
    try:
        async with session_factory.begin() as db:
            seller_id, vehicle_model_id = await _insert_vehicle_parents(db)
            buyer_id = await _integration_organization(db)
        seller = build_principal(organization_id=seller_id)
        buyer = build_principal(organization_id=buyer_id)

        async with session_factory.begin() as db:
            vehicle = await vehicle_service.create_vehicle(
                db,
                VehicleCreateRequest(
                    license_plate="WP3-001",
                    vin=f"W3{uuid4().hex[:15]}".upper(),
                    vehicle_model_id=vehicle_model_id,
                    year=2026,
                    acquired_at=first_handover,
                ),
                principal=seller,
            )
            fleet = await fleet_service.create_fleet(
                db, FleetCreateRequest(name="WP3 fleet"), principal=seller
            )
            await fleet_service.add_vehicle_to_fleet(
                db,
                fleet.fleet_id,
                FleetVehicleAddRequest(vehicle_vin=vehicle.vin),
                principal=seller,
            )
            battery_model = await battery_service.create_battery_model(
                db,
                BatteryModelCreateRequest(
                    manufacturer="CATL",
                    model_name="LFP-WP3",
                    chemistry=BatteryChemistry.LFP,
                    design_capacity_kwh=300.0,
                ),
            )
            battery = await battery_service.create_battery(
                db,
                BatteryCreateRequest(
                    serial_number="WP3-BAT-1",
                    battery_model_id=battery_model.battery_model_id,
                    organization_id=seller_id,
                    acquired_at=first_handover,
                ),
                principal=staff,
            )
            await battery_service.install_battery(
                db,
                battery.battery_id,
                BatteryInstallRequest(
                    vehicle_id=vehicle.vehicle_id, installed_at=first_handover
                ),
                principal=staff,
            )
            vehicle_warranty = await warranty_service.create_warranty(
                db,
                WarrantyCreateRequest(
                    vehicle_id=vehicle.vehicle_id,
                    starts_on=today - timedelta(days=30),
                    ends_on=today + timedelta(days=10),
                    limits={"distance_km": 200000},
                ),
                principal=staff,
            )
            battery_warranty = await warranty_service.create_warranty(
                db,
                WarrantyCreateRequest(
                    battery_id=battery.battery_id,
                    starts_on=today - timedelta(days=30),
                    ends_on=today + timedelta(days=400),
                    limits={"charge_cycles": 3000},
                ),
                principal=staff,
            )

        # Rules proved on the real tables.
        async with session_factory() as db:
            with pytest.raises(BatteryConflictError):
                await battery_service.install_battery(
                    db,
                    battery.battery_id,
                    BatteryInstallRequest(vehicle_id=vehicle.vehicle_id),
                    principal=staff,
                )
            with pytest.raises(WarrantyLimitsInvalidError):
                await warranty_service.create_warranty(
                    db,
                    WarrantyCreateRequest(
                        vehicle_id=vehicle.vehicle_id,
                        warranty_type=WarrantyType.EXTENDED,
                        starts_on=today,
                        ends_on=today + timedelta(days=5),
                        limits={"charge_cycles": 1},
                    ),
                    principal=staff,
                )
            with pytest.raises(WarrantyConflictError):
                await warranty_service.create_warranty(
                    db,
                    WarrantyCreateRequest(
                        vehicle_id=vehicle.vehicle_id,
                        starts_on=today,
                        ends_on=today + timedelta(days=5),
                    ),
                    principal=staff,
                )
            expiring = await warranty_service.list_warranties(
                db, principal=seller, expiring_within_days=30
            )
            assert [item.warranty_id for item in expiring.items] == [
                vehicle_warranty.warranty_id
            ]
            assert (
                await battery_service.resolve_installed_battery_capacity_kwh(
                    db, vehicle.vehicle_id
                )
                == 300.0
            )

        # The transfer, through the orchestrating endpoint.
        async with session_factory.begin() as db:
            transfer = await vehicle_transfer.transfer_vehicle_ownership_endpoint(
                vehicle.vehicle_id,
                VehicleOwnershipTransferRequest(
                    organization_id=buyer_id, acquired_at=sale_date, reason="Sold"
                ),
                staff,
                db,
            )
        assert transfer.vehicle.organization_id == buyer_id
        assert transfer.previous_organization_id == seller_id
        assert transfer.closed_fleet_id == fleet.fleet_id
        assert transfer.moved_battery_id == battery.battery_id
        assert transfer.ended_driving_session_id is None

        async with session_factory() as db:
            periods = await vehicle_service.list_vehicle_ownership_periods(
                db, vehicle.vehicle_id, principal=staff
            )
            assert [
                (item.organization_id, item.owned_from, item.owned_until)
                for item in periods.items
            ] == [
                (seller_id, first_handover, sale_date),
                (buyer_id, sale_date, None),
            ]
            # The seller no longer reaches the truck, its battery or its warranties.
            with pytest.raises(BatteryNotFoundError):
                await battery_service.get_battery(
                    db, battery.battery_id, principal=seller
                )
            assert (
                await warranty_service.list_warranties(db, principal=seller)
            ).total == 0
            buyer_warranties = await warranty_service.list_warranties(
                db, principal=buyer
            )
            assert {item.warranty_id for item in buyer_warranties.items} == {
                vehicle_warranty.warranty_id,
                battery_warranty.warranty_id,
            }
            moved_battery = await battery_service.get_battery(
                db, battery.battery_id, principal=buyer
            )
            assert moved_battery.organization_id == buyer_id
            assert moved_battery.vehicle_id == vehicle.vehicle_id
            installation = await battery_service.list_battery_installation_periods(
                db, battery.battery_id, principal=buyer
            )
            assert [
                (item.vehicle_id, item.installed_until) for item in installation.items
            ] == [(vehicle.vehicle_id, None)]
            memberships = await fleet_service.list_fleet_membership_history(
                db, fleet.fleet_id, principal=staff
            )
            assert memberships.items[0].removed_at is not None

        # Void once; a voided warranty is final.
        async with session_factory.begin() as db:
            voided = await warranty_service.void_warranty(
                db,
                vehicle_warranty.warranty_id,
                principal=staff,
                reason="Charging policy violated",
            )
        assert voided.status == "VOIDED"
        async with session_factory() as db:
            with pytest.raises(WarrantyConflictError):
                await warranty_service.void_warranty(
                    db,
                    vehicle_warranty.warranty_id,
                    principal=staff,
                    reason="Again",
                )
            with pytest.raises(WarrantyConflictError):
                await warranty_service.update_warranty(
                    db,
                    vehicle_warranty.warranty_id,
                    WarrantyUpdateRequest(contract_reference="X"),
                    principal=staff,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_status_reports_health_activation_and_silence_alert_on_postgres(
    temporary_database: str,
) -> None:
    """WP4: status reports are stored and read back as device health, the truck
    activation follows the mounted device's data, and a silent device alerts
    the organization's administrators and fleet managers (DEV-03/04/05, VEH-05)."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    now = datetime.now(timezone.utc)
    staff = build_internal_principal()
    try:
        async with session_factory() as db:
            vehicle = await _integration_vehicle(db, license_plate="IT-WP4-ON")
            vehicle.acquired_at = now - timedelta(days=1)
            silent_vehicle = VehicleModel(
                vehicle_id=uuid4(),
                organization_id=vehicle.organization_id,
                vehicle_model_id=vehicle.vehicle_model_id,
                license_plate="IT-WP4-OFF",
                vin=f"IT{uuid4().hex[:15]}".upper(),
                year=2026,
                status=VehicleStatus.ACTIVE,
                acquired_at=now - timedelta(days=1),
            )
            db.add_all([vehicle, silent_vehicle])
            await db.flush()
            devices = [
                TelematicModel(
                    telematic_id=uuid4(),
                    telematic_serial=f"IT-TBOX-{uuid4().hex[:12]}",
                    organization_id=vehicle.organization_id,
                    acquired_at=now - timedelta(days=1),
                    vehicle_id=mounted_vehicle_id,
                    installed_at=now - timedelta(hours=2)
                    if mounted_vehicle_id
                    else None,
                    status=TelematicStatus.ACTIVE,
                )
                for mounted_vehicle_id in (
                    vehicle.vehicle_id,
                    silent_vehicle.vehicle_id,
                    None,
                )
            ]
            db.add_all(devices)
            await db.flush()
            device, silent_device, stock_device = devices
            for sent_device, received_at in (
                (device, now - timedelta(seconds=30)),
                (
                    silent_device,
                    now
                    - timedelta(
                        minutes=settings.TELEMATICS_SILENT_THRESHOLD_MINUTES + 5
                    ),
                ),
            ):
                await telemetry_repository.insert_telemetry(
                    db,
                    {
                        "organization_id": sent_device.organization_id,
                        "device_message_id": uuid4(),
                        "telematic_id": sent_device.telematic_id,
                        "vehicle_id": sent_device.vehicle_id,
                        "recorded_at": received_at,
                        "received_at": received_at,
                        "location": coordinates_to_location(10.8, 106.7),
                        "soc_percent": 80.0,
                        "raw_payload": {"source": "postgres-integration"},
                    },
                )

            # Two reports: the newest one shows an antenna fault.
            for reported_at, payload in (
                (
                    now - timedelta(days=2),
                    {
                        "firmware_version": "1.2.3",
                        "signal_dbm": -70,
                        "gnss_status": "FIX",
                    },
                ),
                (
                    now - timedelta(hours=1),
                    {
                        "firmware_version": "1.2.4",
                        "telemetry_interval_seconds": 10,
                        "supply_voltage_v": 24.1,
                        "gnss_status": "ANTENNA_FAULT",
                        "sim": {
                            "iccid": "8984049000001234567",
                            "data_status": "ACTIVE",
                        },
                    },
                ),
            ):
                result = await telematics_service.record_status_report(
                    db,
                    TelematicStatusEnvelope(
                        device.telematic_serial,
                        TelematicStatusMessage.model_validate(
                            payload | {"timestamp": reported_at.isoformat()}
                        ),
                    ),
                )
                assert result["processed"] == 1
            notice = await telematics_service.record_status_report(
                db,
                TelematicStatusEnvelope(
                    device.telematic_serial,
                    TelematicStatusMessage.model_validate({"status": "offline"}),
                ),
            )
            assert notice["skipped"] == 1

            health = await telematics_service.get_telematic_health(
                db, device.telematic_id, principal=staff
            )
            assert health.health_state is TelematicHealthState.ATTENTION
            assert health.latest_report is not None
            assert health.latest_report.firmware_version == "1.2.4"
            assert float(health.latest_report.supply_voltage_v or 0) == 24.1
            detail = await telematics_service.get_telematic(
                db, device.telematic_id, principal=staff
            )
            assert detail.firmware_version == "1.2.4"
            assert detail.telemetry_interval_seconds == 10
            reports = await telematics_service.list_telematic_status_reports(
                db, device.telematic_id, principal=staff, since=None, limit=10
            )
            assert [report.firmware_version for report in reports.items] == [
                "1.2.4",
                "1.2.3",
            ]
            summary = await telematics_service.get_device_health_summary(
                db, principal=staff
            )
            assert summary.total_count == 3
            assert summary.attention_count == 1
            assert summary.silent_count == 1
            assert summary.not_mounted_count == 1
            assert stock_device.vehicle_id is None

            # Activation: the mounted device delivered data after the handover.
            activation = await telemetry_service.get_vehicle_activation_response(
                db, vehicle.vehicle_id, principal=staff
            )
            assert activation.activation_status is VehicleActivationStatus.ACTIVATED
            assert activation.telematic_serial == device.telematic_serial
            assert activation.handover_at is not None
            activations = await telemetry_service.list_vehicle_activations(
                db, principal=staff
            )
            assert activations.summary.total_count == 2
            # The silent truck's only sample predates its device's mounting,
            # so it is still waiting for data.
            assert activations.summary.activated_count == 1
            assert activations.summary.awaiting_data_count == 1
            assert activations.summary.activation_rate_percent == 50.0

            # The silent device alerts the organization's fleet manager, not a
            # driver or a locked member.
            people = {}
            for label, role, membership_status in (
                ("manager", UserRole.FLEET_MANAGER, MembershipStatus.ACTIVE),
                ("driver", UserRole.DRIVER, MembershipStatus.ACTIVE),
                ("locked_admin", UserRole.ORG_ADMIN, MembershipStatus.LOCKED),
            ):
                user = UserModel(
                    phone_number=f"+84{uuid4().hex[:9]}",
                    full_name=label,
                    status=UserStatus.ACTIVE.value,
                )
                db.add(user)
                await db.flush()
                membership = MembershipModel(
                    organization_id=vehicle.organization_id,
                    user_id=user.user_id,
                    status=membership_status.value,
                )
                db.add(membership)
                await db.flush()
                await identity_repository.insert_role_assignment(
                    db, membership_record=membership, role=role.value, granted_by=None
                )
                people[label] = user.user_id
            recipients = await identity_service.list_organization_role_holder_user_ids(
                db,
                vehicle.organization_id,
                (UserRole.ORG_ADMIN, UserRole.FLEET_MANAGER),
            )
            assert recipients == [people["manager"]]

            await device_health_monitor.check_devices_for_silence(db)
            manager_unread = await notifications_service.count_unread_notifications(
                db, principal=build_principal(user_id=people["manager"])
            )
            driver_unread = await notifications_service.count_unread_notifications(
                db, principal=build_principal(user_id=people["driver"])
            )
            assert manager_unread.unread_count == 1
            assert driver_unread.unread_count == 0
            # A second sweep in the same silence episode does not alert again.
            await device_health_monitor.check_devices_for_silence(db)
            again = await notifications_service.count_unread_notifications(
                db, principal=build_principal(user_id=people["manager"])
            )
            assert again.unread_count == 1
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fleet_limit_visible_set_tree_move_and_deleted_fleet_ends_assignments(
    temporary_database: str,
) -> None:
    """FL-10 / FLT-01 on real rows: a manager limited to a parent fleet sees the
    parent and everything below (fleets, trucks, tree), moving a sub-fleet
    changes it, a deleted fleet ends its assignments (system, NULL actor) and
    taking away the last one lifts the limit."""
    import app.domains.fleet.assignment_service as assignment_service
    from app.domains.fleet.exceptions import (
        FleetNotFoundError,
        FleetUserAssignmentOrganizationMismatchError,
    )
    from app.domains.vehicles.exceptions import VehicleNotFoundError

    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory() as db:
            organization_id, vehicle_model_id = await _insert_vehicle_parents(db)
            other_organization_id = await _integration_organization(db)
            user = UserModel(
                phone_number=f"+84{uuid4().hex[:9]}",
                full_name="Regional Manager",
                status=UserStatus.ACTIVE.value,
            )
            db.add(user)
            await db.flush()
            membership = MembershipModel(
                organization_id=organization_id,
                user_id=user.user_id,
                status=MembershipStatus.ACTIVE.value,
            )
            db.add(membership)
            await db.flush()
            admin = build_principal(organization_id=organization_id)
            manager = dataclasses.replace(
                build_principal(
                    roles=frozenset({UserRole.FLEET_MANAGER}),
                    organization_id=organization_id,
                    user_id=user.user_id,
                ),
                membership_id=membership.membership_id,
            )

            async def make_fleet(name: str, parent_id: UUID | None) -> UUID:
                created = await fleet_service.create_fleet(
                    db,
                    FleetCreateRequest(name=name, parent_fleet_id=parent_id),
                    principal=admin,
                )
                return created.fleet_id

            region = await make_fleet("Region", None)
            branch = await make_fleet("Branch", region)
            depot = await make_fleet("Depot", branch)
            other = await make_fleet("Other region", None)

            async def truck_in(fleet_id: UUID, plate: str) -> UUID:
                vehicle = VehicleModel(
                    vehicle_id=uuid4(),
                    organization_id=organization_id,
                    vehicle_model_id=vehicle_model_id,
                    license_plate=plate,
                    vin=f"FL{uuid4().hex[:15]}".upper(),
                    year=2026,
                    status=VehicleStatus.ACTIVE,
                )
                db.add(vehicle)
                await db.flush()
                await fleet_repository.insert_membership(
                    db,
                    fleet_id=fleet_id,
                    vehicle_id=vehicle.vehicle_id,
                    added_at=datetime.now(timezone.utc),
                )
                return vehicle.vehicle_id

            truck_region = await truck_in(region, "FL-REGION")
            truck_depot = await truck_in(depot, "FL-DEPOT")
            truck_other = await truck_in(other, "FL-OTHER")

            # No assignment: no limit, and the limited-role caller sees all.
            assert (
                await fleet_service.resolve_visible_fleet_ids(
                    db, membership.membership_id, organization_id
                )
                is None
            )
            assert (await fleet_service.list_fleets(db, principal=manager)).total == 4

            # Parent assigned: the union is the parent and everything below it.
            first = await assignment_service.assign_fleet_to_membership(
                db, membership.membership_id, region, principal=admin
            )
            assert first.warnings == [] and first.assigned_by == admin.user_id
            # A child of an assigned parent is allowed, with a warning.
            second = await assignment_service.assign_fleet_to_membership(
                db, membership.membership_id, branch, principal=admin
            )
            assert second.warnings == [
                assignment_service.WARNING_COVERED_BY_ASSIGNED_PARENT
            ]
            assert await fleet_service.resolve_visible_fleet_ids(
                db, membership.membership_id, organization_id
            ) == frozenset({region, branch, depot})
            assert await fleet_service.resolve_visible_vehicle_ids(
                db, membership.membership_id, organization_id
            ) == frozenset({truck_region, truck_depot})

            # The limit applies to fleets, the tree, trucks and fleet-wide views.
            assert (await fleet_service.list_fleets(db, principal=manager)).total == 3
            with pytest.raises(FleetNotFoundError):
                await fleet_service.get_fleet(db, other, principal=manager)
            tree = await fleet_service.get_fleet_tree(db, principal=manager)
            assert [node.fleet_id for node in tree.items] == [region]
            assert tree.total_fleets == 3
            assert tree.items[0].vehicle_count == 1
            assert tree.items[0].vehicle_count_with_descendants == 2
            admin_tree = await fleet_service.get_fleet_tree(db, principal=admin)
            assert {node.fleet_id for node in admin_tree.items} == {region, other}
            assert admin_tree.total_fleets == 4
            visible_trucks = await fleet_service.resolve_principal_visible_vehicle_ids(
                db, manager
            )
            assert (
                await vehicle_service.list_vehicles(
                    db, principal=manager, visible_vehicle_ids=visible_trucks
                )
            ).total == 2
            with pytest.raises(VehicleNotFoundError):
                await vehicle_service.get_vehicle(
                    db,
                    truck_other,
                    principal=manager,
                    visible_vehicle_ids=visible_trucks,
                )
            assert set(
                await fleet_service.list_active_member_vehicle_ids(
                    db, region, principal=manager
                )
            ) == {truck_region}
            assert set(
                await fleet_service.list_active_member_vehicle_ids(
                    db, region, principal=manager, include_descendants=True
                )
            ) == {truck_region, truck_depot}
            with pytest.raises(FleetNotFoundError):
                await fleet_service.list_active_member_vehicle_ids(
                    db, other, principal=manager
                )
            below_region = await fleet_service.list_fleets(
                db,
                principal=admin,
                parent_fleet_id=region,
                include_descendants=True,
            )
            assert {item.fleet_id for item in below_region.items} == {branch, depot}
            children_only = await fleet_service.list_fleets(
                db, principal=admin, parent_fleet_id=region
            )
            assert [item.fleet_id for item in children_only.items] == [branch]

            # Another organization's fleet cannot be given to this membership.
            foreign = await fleet_service.create_fleet(
                db,
                FleetCreateRequest(
                    name="Foreign", organization_id=other_organization_id
                ),
                principal=build_internal_principal(),
            )
            with pytest.raises(FleetUserAssignmentOrganizationMismatchError):
                await assignment_service.assign_fleet_to_membership(
                    db,
                    membership.membership_id,
                    foreign.fleet_id,
                    principal=build_internal_principal(),
                )

            # Moving the depot under the other region changes what is visible.
            await fleet_service.update_fleet(
                db, depot, FleetUpdateRequest(parent_fleet_id=other), principal=admin
            )
            assert await fleet_service.resolve_visible_fleet_ids(
                db, membership.membership_id, organization_id
            ) == frozenset({region, branch})
            assert await fleet_service.resolve_visible_vehicle_ids(
                db, membership.membership_id, organization_id
            ) == frozenset({truck_region})
            assert set(
                await fleet_service.list_active_member_vehicle_ids(
                    db, other, principal=admin, include_descendants=True
                )
            ) == {truck_other, truck_depot}
            rolled_up = await fleet_service.get_fleet(
                db, other, principal=admin, include_descendants=True
            )
            assert rolled_up.vehicle_count == 2

            # A manager limited to fleets cannot delete a fleet given to someone.
            with pytest.raises(AccessDeniedError):
                await fleet_service.soft_delete_fleet(db, branch, principal=manager)

            # Deleting the branch ends its assignment: the system closed it.
            await fleet_service.soft_delete_fleet(db, branch, principal=admin)
            history = await assignment_service.list_assignments_by_membership(
                db, membership.membership_id, principal=admin, include_closed=True
            )
            by_fleet = {item.fleet_id: item for item in history.items}
            assert by_fleet[branch].unassigned_at is not None
            assert by_fleet[branch].unassigned_by is None
            assert by_fleet[region].unassigned_at is None
            open_rows = await assignment_service.list_assignments_by_fleet(
                db, region, principal=admin
            )
            assert open_rows.total == 1

            # Taking away the last fleet lifts the limit.
            await assignment_service.unassign_fleet_from_membership(
                db, membership.membership_id, region, principal=admin
            )
            assert (
                await fleet_service.resolve_visible_fleet_ids(
                    db, membership.membership_id, organization_id
                )
                is None
            )
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_private_visibility_grants_commands_connection_and_status_on_postgres(
    temporary_database: str,
) -> None:
    """WP7 on real PostgreSQL: list visibility, grant expiry, commands and reads.

    A private location is listed for its owner and for a granted organization
    in the view scope (and for nobody else); a grant past its last day gives
    nothing and is closed when the same organization is granted again (CS-13);
    a command can be cancelled while queued and only then; the connection and
    status reads follow the state rows; the message log hides the frame text.
    """
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory.begin() as db:
            owner = build_organization_record()
            partner = build_organization_record()
            stranger = build_organization_record()
            db.add_all([owner, partner, stranger])
            await db.flush()
            owner_id, partner_id, stranger_id = (
                owner.organization_id,
                partner.organization_id,
                stranger.organization_id,
            )
        staff = build_internal_principal()
        async with session_factory.begin() as db:
            location = await charging_stations_service.create_charging_location(
                db,
                ChargingLocationCreateRequest(
                    organization_id=owner_id,
                    display_name="Mine depot",
                    address="Km 9",
                    latitude=10.0,
                    longitude=106.0,
                    is_public=False,
                ),
                principal=staff,
            )
            station = await charging_stations_service.create_charging_station(
                db,
                ChargingStationCreateRequest(
                    location_id=location.location_id,
                    ocpp_identity="IT-WP7-1",
                    registered_serial_number="SN-WP7-1",
                    physical_reference="Tru 1",
                    max_power_kw=240,
                ),
                principal=staff,
            )
            evse = await charging_stations_service.create_charging_evse(
                db,
                station.station_id,
                ChargingEvseCreateRequest(ocpp_evse_id=1, emi3_evse_id="VN*G3N*E-WP7"),
                principal=staff,
            )
            connector = await charging_stations_service.create_charging_connector(
                db,
                evse.evse_id,
                ChargingConnectorCreateRequest(
                    ocpp_connector_id=1,
                    standard=ConnectorStandard.IEC_62196_T2_COMBO,
                    max_power_kw=120,
                    max_voltage_v=1000,
                    max_current_a=250,
                ),
                principal=staff,
            )

        async def listed(
            organization_id: UUID, scope: StationListScope, **filters: object
        ) -> tuple[int, int]:
            """Count the locations and chargers a customer's list returns."""
            principal = build_principal(organization_id=organization_id)
            async with session_factory() as db:
                locations = await charging_stations_service.list_charging_locations(
                    db,
                    principal=principal,
                    scope=scope,
                    **filters,  # type: ignore[arg-type]
                )
                stations = await charging_stations_service.list_charging_stations(
                    db,
                    principal=principal,
                    scope=scope,
                    **filters,  # type: ignore[arg-type]
                )
            return locations.total, stations.total

        visible, managed = StationListScope.VISIBLE, StationListScope.MANAGED
        assert await listed(owner_id, managed) == (1, 1)
        assert await listed(owner_id, visible) == (1, 1)
        assert await listed(partner_id, visible) == (0, 0)
        assert await listed(stranger_id, visible) == (0, 0)
        assert await listed(owner_id, managed, search_text="mine") == (1, 1)
        assert await listed(owner_id, managed, search_text="zzz") == (0, 0)
        assert await listed(owner_id, managed, is_public=True) == (0, 0)

        today = charging_stations_service._today_in_report_zone()
        async with session_factory.begin() as db:
            await charging_stations_service.grant_charging_location_access(
                db,
                location.location_id,
                ChargingLocationAccessCreateRequest(
                    allowed_organization_id=partner_id, valid_until=today
                ),
                principal=staff,
            )
            # A grant whose last day was yesterday (inserted the way an old
            # contract would be): it blocks nothing and shows nothing.
            await db.execute(
                text(
                    "INSERT INTO charging_location_access (access_id, location_id, "
                    "allowed_organization_id, granted_at, granted_by, valid_until) "
                    "VALUES (:a, :l, :o, now(), :u, :d)"
                ),
                {
                    "a": uuid4(),
                    "l": location.location_id,
                    "o": stranger_id,
                    "u": ACTOR_USER_ID,
                    "d": today - timedelta(days=1),
                },
            )
        assert await listed(partner_id, visible) == (1, 1)
        assert await listed(partner_id, managed) == (0, 0)
        assert await listed(stranger_id, visible) == (0, 0)
        async with session_factory.begin() as db:
            regrant = await charging_stations_service.grant_charging_location_access(
                db,
                location.location_id,
                ChargingLocationAccessCreateRequest(
                    allowed_organization_id=stranger_id
                ),
                principal=staff,
            )
        assert regrant.revoked_at is None
        assert await listed(stranger_id, visible) == (1, 1)
        async with engine.connect() as connection:
            closed_reason = (
                await connection.execute(
                    text(
                        "SELECT revoke_reason FROM charging_location_access "
                        "WHERE allowed_organization_id = :o AND revoked_at IS NOT NULL"
                    ),
                    {"o": stranger_id},
                )
            ).scalar_one()
        assert closed_reason == "Access expired"

        # The charger connected, boot data arrived and the gun reported.
        async with session_factory.begin() as db:
            await db.execute(
                text(
                    "UPDATE charging_station_state SET last_seen_at = now(), "
                    "ocpp_protocol_version = 'ocpp1.6', serial_number = 'SN-OTHER' "
                    "WHERE station_id = :s"
                ),
                {"s": station.station_id},
            )
            await db.execute(
                text(
                    "UPDATE charging_connector_state SET status = 'Available', "
                    "status_updated_at = now() WHERE connector_id = :c"
                ),
                {"c": connector.connector_id},
            )
            for direction, action in (
                (OcppMessageDirection.CP_TO_CSMS, "BootNotification"),
                (OcppMessageDirection.CSMS_TO_CP, None),
            ):
                await ocpp_state_repository.insert_ocpp_message(
                    db,
                    station_id=station.station_id,
                    occurred_at=datetime.now(timezone.utc),
                    ocpp_subprotocol="ocpp1.6",
                    direction=direction,
                    raw_frame='[2,"m1","BootNotification",{"idTag":"SECRET"}]',
                    action=action,
                    ocpp_message_id="m1",
                )
        owner_principal = build_principal(organization_id=owner_id)
        partner_principal = build_principal(organization_id=partner_id)
        async with session_factory() as db:
            connection_facts = (
                await charging_stations_service.get_charging_station_connection(
                    db, station.station_id, principal=owner_principal
                )
            )
            status = await charging_stations_service.get_charging_station_status(
                db, station.station_id, principal=partner_principal
            )
            board = (
                await charging_stations_service.list_charging_station_status_summaries(
                    db, principal=partner_principal
                )
            )
            log = await charging_stations_service.list_charging_station_ocpp_messages(
                db,
                station.station_id,
                principal=staff,
                action="BootNotification",
            )
        assert connection_facts.is_online is True
        assert connection_facts.is_serial_matching is False
        assert connection_facts.ocpp_protocol_version == "ocpp1.6"
        assert status.available_connector_count == 1
        assert status.status_counts == {"Available": 1}
        assert status.connectors[0].is_status_stale is False
        assert [item.station_id for item in board.items] == [station.station_id]
        assert (log.total, log.items[0].raw_frame) == (1, None)

        # Commands: a queued one can be cancelled once; the log filters by outcome.
        async with session_factory.begin() as db:
            queued = await charging_stations_service.create_charging_station_command(
                db,
                station.station_id,
                ChargingStationCommandCreateRequest(
                    command_type=StationCommandType.RESET, reason="Hung charger"
                ),
                principal=owner_principal,
            )
        assert queued.outcome is StationCommandOutcome.PENDING
        async with session_factory.begin() as db:
            cancelled = await charging_stations_service.cancel_charging_station_command(
                db, station.station_id, queued.command_id, principal=owner_principal
            )
        assert cancelled.outcome is StationCommandOutcome.NOT_SENT
        assert cancelled.response_status == "Cancelled"
        async with session_factory() as db:
            with pytest.raises(ChargingStationCommandConflictError):
                await charging_stations_service.cancel_charging_station_command(
                    db, station.station_id, queued.command_id, principal=owner_principal
                )
            pending = await charging_stations_service.list_charging_station_commands(
                db,
                station.station_id,
                principal=owner_principal,
                outcome=StationCommandOutcome.PENDING,
            )
            not_sent = await charging_stations_service.list_charging_station_commands(
                db,
                station.station_id,
                principal=owner_principal,
                outcome=StationCommandOutcome.NOT_SENT,
            )
        assert (pending.total, not_sent.total) == (0, 1)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sos_routing_channel_switches_and_inbox_seen_read_on_postgres(
    temporary_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NTF-06 / NTF-05 / NTF-01 on real rows: an SOS reaches the owner's
    ORG_ADMIN and FLEET_MANAGER and our customer care (not a driver), the
    default sends push and e-mail, an organization switch-off suppresses the
    push but not the inbox, the setting's history records the reason, and the
    inbox keeps seen and read apart (NT-10, NT-12, NT-15)."""
    from app.api.startup import register_all_hooks

    register_all_hooks()
    pushed: list[str] = []
    emailed: list[str] = []

    class RecordingPush:
        async def send_push(
            self, push_token: str, message: notification_providers.PushMessage
        ) -> notification_providers.PushOutcome:
            pushed.append(push_token)
            return notification_providers.PushOutcome.SENT

    class RecordingEmail:
        async def send_email(self, email: str, subject: str, body: str) -> None:
            emailed.append(email)

    monkeypatch.setattr(notification_delivery, "get_push_sender", RecordingPush)
    monkeypatch.setattr(notification_delivery, "get_email_sender", RecordingEmail)
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory() as db:
            vehicle = await _integration_vehicle(db, license_plate="IT-NTF-001")
            db.add(vehicle)
            internal_organization = build_organization_record()
            internal_organization.is_internal = True
            db.add(internal_organization)
            await db.flush()

            async def add_person(
                label: str,
                organization_id: UUID,
                role: UserRole,
                *,
                push_token: str | None = None,
            ) -> UUID:
                user = UserModel(
                    phone_number=f"+84{uuid4().hex[:9]}",
                    full_name=label,
                    email=f"{label}-{uuid4().hex[:6]}@example.com",
                    status=UserStatus.ACTIVE.value,
                )
                db.add(user)
                await db.flush()
                membership = MembershipModel(
                    organization_id=organization_id,
                    user_id=user.user_id,
                    status=MembershipStatus.ACTIVE.value,
                )
                db.add(membership)
                await db.flush()
                await identity_repository.insert_role_assignment(
                    db, membership_record=membership, role=role.value, granted_by=None
                )
                if push_token is not None:
                    db.add(
                        UserSessionModel(
                            user_id=user.user_id,
                            platform="ANDROID",
                            refresh_token_hash=uuid4().hex,
                            push_token=push_token,
                            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                        )
                    )
                    await db.flush()
                return user.user_id

            owner_id = vehicle.organization_id
            admin_id = await add_person(
                "admin", owner_id, UserRole.ORG_ADMIN, push_token="push-admin"
            )
            manager_id = await add_person(
                "manager", owner_id, UserRole.FLEET_MANAGER, push_token="push-manager"
            )
            driver_id = await add_person(
                "driver", owner_id, UserRole.DRIVER, push_token="push-driver"
            )
            care_id = await add_person(
                "care",
                internal_organization.organization_id,
                UserRole.CUSTOMER_CARE,
                push_token="push-care",
            )
            owner_admin = build_principal(user_id=admin_id, organization_id=owner_id)

            async def raise_sos() -> int:
                case = await support_service.create_support_sos(
                    db,
                    SupportSosCreateRequest.model_validate(
                        {
                            "vehicle_vin": vehicle.vin,
                            "latitude": 10.8,
                            "longitude": 106.7,
                        }
                    ),
                    principal=build_internal_principal(),
                )
                alerts = await notifications_service.list_notifications(
                    db,
                    principal=build_internal_principal(),
                    after_id=0,
                    limit=1,
                    notification_type=NotificationType.SOS_ALERT,
                    order=NotificationListOrder.DESC,
                )
                assert alerts.notifications[0].subject_id == case.case_id
                return alerts.notifications[0].notification_id

            async def recipient_ids(notification_id: int) -> set[UUID]:
                rows = await db.execute(
                    text(
                        "SELECT user_id FROM notification_recipients "
                        "WHERE notification_id = :id"
                    ),
                    {"id": notification_id},
                )
                return {row.user_id for row in rows}

            # Defaults: push on, e-mail on for an SOS.
            first_sos = await raise_sos()
            assert await recipient_ids(first_sos) == {admin_id, manager_id, care_id}
            assert set(pushed) == {"push-admin", "push-manager", "push-care"}
            assert len(emailed) == 3

            # The ORG_ADMIN switches the push off, then the e-mail off too.
            pushed.clear()
            emailed.clear()
            await (
                notification_settings_service.update_organization_notification_setting(
                    db,
                    NotificationType.SOS_ALERT,
                    NotificationSettingUpdateRequest(
                        push_enabled=False, email_enabled=True
                    ),
                    principal=owner_admin,
                )
            )
            second_sos = await raise_sos()
            assert await recipient_ids(second_sos) == {admin_id, manager_id, care_id}
            assert pushed == []
            assert len(emailed) == 3
            await (
                notification_settings_service.update_organization_notification_setting(
                    db,
                    NotificationType.SOS_ALERT,
                    NotificationSettingUpdateRequest(
                        push_enabled=False, email_enabled=False, reason="Daytime desk"
                    ),
                    principal=owner_admin,
                )
            )
            history = (
                await db.execute(
                    text(
                        "SELECT push_enabled, email_enabled, changed_by, change_reason "
                        "FROM organization_notification_setting_history"
                    )
                )
            ).all()
            # The first save created the row (no history); the second one
            # copied the old row with the actor and the typed reason.
            assert [tuple(row) for row in history] == [
                (False, True, admin_id, "Daytime desk")
            ]
            listing = await notification_settings_service.list_organization_notification_settings(
                db, principal=owner_admin
            )
            sos_setting = next(
                entry
                for entry in listing.settings
                if entry.notification_type is NotificationType.SOS_ALERT
            )
            assert (sos_setting.push_enabled, sos_setting.email_enabled) == (
                False,
                False,
            )
            assert sos_setting.is_default is False

            # Inbox: both alerts are unseen and unread; opening the centre
            # sees them, opening one reads it.
            admin_principal = build_principal(
                user_id=admin_id, organization_id=owner_id
            )
            counts = await notifications_service.count_unread_notifications(
                db, principal=admin_principal
            )
            assert (counts.unread_count, counts.unseen_count) == (2, 2)
            centre = await notifications_service.list_notifications(
                db,
                principal=admin_principal,
                after_id=0,
                limit=10,
                mine_only=True,
                order=NotificationListOrder.DESC,
            )
            assert [item.notification_id for item in centre.notifications] == [
                second_sos,
                first_sos,
            ]
            assert all(item.seen_at is not None for item in centre.notifications)
            assert all(item.read_at is None for item in centre.notifications)
            counts = await notifications_service.count_unread_notifications(
                db, principal=admin_principal
            )
            assert (counts.unread_count, counts.unseen_count) == (2, 0)
            opened = await notifications_service.mark_notification_read(
                db, first_sos, principal=admin_principal
            )
            assert opened.read_at is not None and opened.seen_at is not None
            unread_page = await notifications_service.list_notifications(
                db,
                principal=admin_principal,
                after_id=0,
                limit=10,
                mine_only=True,
                unread_only=True,
                order=NotificationListOrder.DESC,
            )
            assert [item.notification_id for item in unread_page.notifications] == [
                second_sos
            ]
            older_page = await notifications_service.list_notifications(
                db,
                principal=admin_principal,
                after_id=0,
                limit=10,
                before_id=second_sos,
                mine_only=True,
                order=NotificationListOrder.DESC,
            )
            assert [item.notification_id for item in older_page.notifications] == [
                first_sos
            ]
            # The driver was not a recipient.
            driver_counts = await notifications_service.count_unread_notifications(
                db, principal=build_principal(user_id=driver_id)
            )
            assert driver_counts.unread_count == 0
            await db.rollback()
    finally:
        await engine.dispose()
