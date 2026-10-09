"""PostgreSQL integration test for the baseline migrations and telemetry repository."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
import pytest_asyncio
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.domains.charging_sessions.repository as charging_repository
import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.service as charging_stations_service
import app.domains.drivers.repository as driver_repository
import app.domains.fleet.repository as fleet_repository
import app.domains.fleet.service as fleet_service
import app.domains.notifications.repository as notification_repository
import app.domains.notifications.service as notifications_service
import app.domains.support.repository as support_repository
import app.domains.support.service as support_service
import app.domains.telematics.repository as telematics_repository
import app.domains.telematics.service as telematics_service
import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.repository as vehicle_repository
import app.domains.vehicles.service as vehicle_service
from app.domains.batteries.models import BatteryModel, BatteryModelModel
from app.domains.batteries.types import BatteryStatus
from app.domains.charging_sessions.types import EnergySeriesGranularity, SessionStatus
from app.domains.fleet.exceptions import (
    FleetConflictError,
    FleetHasSubFleetsError,
    FleetHierarchyLoopError,
    FleetOrganizationNotFoundError,
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
from app.domains.identity.models import OrganizationModel, UserModel
from app.domains.identity.types import OrganizationStatus, UserStatus
from app.domains.notifications.models import NotificationModel
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
from app.domains.telematics.schemas import TelematicCreateRequest
from app.domains.telematics.types import TelematicStatus
from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.telemetry.schemas import TelemetryEnvelope, TelemetryMessage
from app.domains.telemetry.types import ReportGranularity
from app.domains.vehicles.models import VehicleModel, VehicleModelModel
from app.domains.vehicles.types import VehicleStatus
from app.domains.warranties.models import WarrantyModel
from app.domains.warranties.types import WarrantyStatus, WarrantyType
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location
from app.libs.db.history import UNSPECIFIED_CHANGE_REASON, set_change_context
from tests.builders import build_organization_record

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
                                'vehicles', 'telematics', 'vehicle_telemetry',
                                'charging_stations', 'charging_evses',
                                'charging_connectors', 'charging_sessions',
                                'charging_session_events',
                                'charging_session_measurements',
                                'charging_ocpp_messages',
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
        assert len(tables) == 11
        # The raw OCPP message log must be a real TimescaleDB hypertable
        # partitioned on occurred_at, not just an ordinary table.
        # Exactly five hypertables: the OCPP 1.6J work replaced the session
        # meter-values hypertable with measurements and added the raw message
        # log; the identity work added the access audit log.
        assert hypertables == {
            "vehicle_telemetry",
            "charging_session_events",
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
                vehicle_id=vehicle_id,
                status=TelematicStatus.ACTIVE,
                firmware_version="integration-test",
            )
            session.add(vehicle)
            await session.flush()
            session.add(telematic)
            await session.flush()

            inserted = await telemetry_repository.insert_telemetry(
                session,
                {
                    "message_uuid": uuid4(),
                    "telematic_id": telematic_id,
                    "telematic_serial": serial,
                    "vehicle_id": vehicle_id,
                    "recorded_at": recorded_at,
                    "received_at": recorded_at,
                    "location": coordinates_to_location(10.8, 106.7),
                    "speed": 42.0,
                    "heading": 180.0,
                    "soc": 80.0,
                    "battery_voltage": 650.0,
                    "battery_current": -120.0,
                    "battery_temperature": 30.0,
                    "motor_temperature": 45.0,
                    "odometer": 12500.5,
                    "signal_strength": -70,
                    "error_codes": {"codes": []},
                    "raw_payload": {"source": "postgres-integration"},
                },
            )
            latest = await telemetry_repository.get_latest_vehicle_telemetry(
                session, vehicle_id
            )

            assert inserted == 1
            assert latest is not None
            assert latest.telematic_serial == serial
            assert latest.soc == 80.0

            await session.rollback()

        async with session_factory() as session:
            vehicle_after_rollback = await vehicle_repository.get_by_id(
                session, vehicle_id
            )
            telemetry_after_rollback = await session.scalar(
                select(VehicleTelemetryModel.message_id).where(
                    VehicleTelemetryModel.vehicle_id == vehicle_id
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
            "message_uuid": uuid4(),
            "telematic_id": telematic_id,
            "telematic_serial": serial,
            "vehicle_id": vehicle_id,
            "recorded_at": recorded_at,
            "received_at": received_at,
            "location": coordinates_to_location(10.8, 106.7),
            "soc": 80.0,
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


async def _provision_station(
    engine: object, identity: str, evse_ids: list[int]
) -> None:
    """Insert a station with one EVSE (and one connector) per given EVSE number."""
    now = datetime.now(timezone.utc)
    station_id = uuid4()
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        await connection.execute(
            text(
                "INSERT INTO charging_stations (station_id, ocpp_identity, display_name, "
                "maintenance_status, created_at, updated_at) "
                "VALUES (:s, :i, 'e2e', 'OPERATIONAL', :t, :t)"
            ),
            {"s": station_id, "i": identity, "t": now},
        )
        for evse_number in evse_ids:
            evse_id = uuid4()
            await connection.execute(
                text(
                    "INSERT INTO charging_evses (evse_id, station_id, ocpp_evse_id, "
                    "created_at, updated_at) VALUES (:e, :s, :n, :t, :t)"
                ),
                {"e": evse_id, "s": station_id, "n": evse_number, "t": now},
            )
            await connection.execute(
                text(
                    "INSERT INTO charging_connectors (connector_id, evse_id, "
                    "ocpp_connector_id, created_at, updated_at) "
                    "VALUES (:c, :e, 1, :t, :t)"
                ),
                {"c": uuid4(), "e": evse_id, "t": now},
            )


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
                        "SELECT station_id, ocpp_protocol_version, vendor, model, "
                        "firmware_version, last_boot_at IS NOT NULL AS booted, "
                        "last_seen_at IS NOT NULL AS seen, charger_status::text AS charger_status, "
                        "charger_error_code FROM charging_stations WHERE ocpp_identity = 'E2E-16'"
                    )
                )
            ).one()
            gun_statuses = (
                await connection.execute(
                    text(
                        "SELECT e.ocpp_evse_id, c.status::text, c.error_code, "
                        "c.updated_at = c.created_at AS updated_at_kept "
                        "FROM charging_connectors c JOIN charging_evses e USING (evse_id) "
                        "WHERE e.station_id = :s ORDER BY e.ocpp_evse_id"
                    ),
                    {"s": station.station_id},
                )
            ).all()
            session = (
                await connection.execute(
                    text(
                        "SELECT session_id, ocpp_transaction_id, status::text, id_tag, "
                        "stop_reason, meter_start_wh, meter_stop_wh, meter_end_wh, "
                        "energy_delivered_wh FROM charging_sessions WHERE station_id = :s"
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
                        "SELECT count(DISTINCT capture_id) AS captures, count(*) AS keys, "
                        "max(value) FILTER (WHERE config_key = 'SupportedFeatureProfiles') AS profiles "
                        "FROM charging_station_configuration_entries WHERE station_id = :s"
                    ),
                    {"s": station.station_id},
                )
            ).one()
            legacy = (
                await connection.execute(
                    text(
                        "SELECT s.ocpp_protocol_version, x.status::text, x.meter_end_wh, "
                        "x.energy_delivered_wh FROM charging_stations s "
                        "JOIN charging_sessions x ON x.station_id = s.station_id "
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
        # Device-reported status never bumps updated_at (last admin edit).
        assert [tuple(row) for row in gun_statuses] == [
            (1, "Available", "NoError", True),
            (2, "Available", "NoError", True),
        ]
        # The session: allocated ID, idTag, reason, closing meter, energy.
        assert (session.ocpp_transaction_id, session.status) == ("1", "completed")
        assert (session.id_tag, session.stop_reason) == ("SIMTAG001", "EVDisconnected")
        assert (session.meter_start_wh, session.meter_stop_wh) == (1000, 1500)
        assert (session.meter_end_wh, session.energy_delivered_wh) == (1500, 500)
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
        # One configuration capture with the charger's supported profiles.
        assert (captures.captures, captures.keys) == (1, 16)
        assert captures.profiles == "Core,SmartCharging,RemoteTrigger"
        # The 2.0.1 path is unaffected.
        assert tuple(legacy) == ("ocpp2.0.1", "completed", 1500, 500)
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

    The identity ID comes back from ``INSERT ... RETURNING`` and
    ``created_at``/``read_at`` are client-side values, so the service builds
    its response straight from the object; mark-read stays idempotent.
    """
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory.begin() as db:
            notification_record = await notification_repository.insert(
                db,
                notification_type=NotificationType.BATTERY_ALERT,
                severity=NotificationSeverity.WARNING,
                vehicle_id=None,
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

        async with session_factory.begin() as db:
            first_read = await notifications_service.mark_notification_read(
                db, notification_record.notification_id
            )
        async with session_factory.begin() as db:
            second_read = await notifications_service.mark_notification_read(
                db, notification_record.notification_id
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
        assert stored.read_at == first_read.read_at
        assert second_read.read_at == first_read.read_at
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
                    vehicle_vin=vin,
                    firmware_version=None,
                ),
            )
        async with session_factory.begin() as db:
            await telematics_service.soft_delete_telematic(
                db, first_device.telematic_id
            )
        async with session_factory.begin() as db:
            replacement_device = await telematics_service.create_telematic(
                db,
                TelematicCreateRequest(
                    telematic_serial=f"{serial_prefix}-B",
                    vehicle_vin=vin,
                    firmware_version=None,
                ),
            )
        assert replacement_device.vehicle_id == vehicle_id

        # Pre-check path: the service sees the live replacement device.
        with pytest.raises(TelematicConflictError):
            async with session_factory.begin() as db:
                await telematics_service.create_telematic(
                    db,
                    TelematicCreateRequest(
                        telematic_serial=f"{serial_prefix}-C",
                        vehicle_vin=vin,
                        firmware_version=None,
                    ),
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
                        vehicle_vin=vin,
                        firmware_version=None,
                    ),
                )
        monkeypatch.undo()

        async with session_factory.begin() as db:
            live_mapping = await telematics_service.resolve_mapping_by_serial(
                db, f"{serial_prefix}-B"
            )
            await vehicle_service.soft_delete_vehicle(db, vehicle_id)
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
        vehicle_id=vehicle.vehicle_id,
        status=TelematicStatus.ACTIVE,
    )
    db.add(telematic)
    await db.flush()
    for reading in readings:
        await telemetry_repository.insert_telemetry(
            db,
            {
                "message_uuid": uuid4(),
                "telematic_id": telematic.telematic_id,
                "telematic_serial": telematic.telematic_serial,
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
                        "soc": 80.0,
                        "soh_percent": 95.0,
                        "cycle_count": 100,
                    },
                    {
                        "recorded_at": _september_2026(1, 17, 30),
                        "soc": 79.0,
                        "soh_percent": 94.5,
                        "cycle_count": 100,
                    },
                    {
                        "recorded_at": _september_2026(1, 20),
                        "soc": 78.0,
                        "cycle_count": 101,
                    },
                    {"recorded_at": _september_2026(3, 5), "soc": 70.0},
                ],
            )

            trend = await telemetry_service.get_vehicle_battery_health_response(
                session,
                vehicle_id=vehicle.vehicle_id,
                start_time=datetime(2026, 8, 31, tzinfo=timezone.utc),
                end_time=datetime(2026, 9, 4, tzinfo=timezone.utc),
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
                        "soc": 90.0,
                        "odometer": 1000.0,
                    },
                    {
                        "recorded_at": _september_2026(1, 10),
                        "soc": 80.0,
                        "odometer": 1050.0,
                    },
                    {
                        "recorded_at": _september_2026(1, 18),
                        "soc": 75.0,
                        "odometer": 1070.0,
                    },
                    {
                        "recorded_at": _september_2026(2, 10),
                        "soc": 60.0,
                        "odometer": 1120.0,
                    },
                    {"recorded_at": end_time, "soc": 55.0, "odometer": 1130.0},
                ],
            )

            daily_report = await telemetry_service.get_vehicle_operating_report(
                session,
                vehicle_id=vehicle.vehicle_id,
                start_time=start_time,
                end_time=end_time,
                granularity=ReportGranularity.DAY,
            )
            monthly_report = await telemetry_service.get_vehicle_operating_report(
                session,
                vehicle_id=vehicle.vehicle_id,
                start_time=start_time,
                end_time=end_time,
                granularity=ReportGranularity.MONTH,
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
    maintenance_status: str = "OPERATIONAL",
) -> tuple[UUID, list[UUID]]:
    """Insert a located station with one EVSE/connector per given gun status.

    Returns the station ID and the connector IDs, in EVSE-number order.
    """
    now = datetime.now(timezone.utc)
    station_id = uuid4()
    connector_ids: list[UUID] = []
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        await connection.execute(
            text(
                "INSERT INTO charging_stations (station_id, ocpp_identity, display_name, "
                "location, maintenance_status, created_at, updated_at) VALUES "
                "(:s, :i, :i, ST_GeogFromText(:p), "
                "CAST(:m AS chargingstationmaintenancestatus), :t, :t)"
            ),
            {
                "s": station_id,
                "i": identity,
                "p": f"SRID=4326;POINT({longitude} 10.0)",
                "m": maintenance_status,
                "t": now,
            },
        )
        for evse_number, connector_status in enumerate(connector_statuses, start=1):
            evse_id, connector_id = uuid4(), uuid4()
            await connection.execute(
                text(
                    "INSERT INTO charging_evses (evse_id, station_id, ocpp_evse_id, "
                    "created_at, updated_at) VALUES (:e, :s, :n, :t, :t)"
                ),
                {"e": evse_id, "s": station_id, "n": evse_number, "t": now},
            )
            await connection.execute(
                text(
                    "INSERT INTO charging_connectors (connector_id, evse_id, "
                    "ocpp_connector_id, status, status_updated_at, created_at, "
                    "updated_at) VALUES (:c, :e, 1, "
                    "CAST(:st AS chargingconnectorstatus), :t, :t, :t)"
                ),
                {
                    "c": connector_id,
                    "e": evse_id,
                    "st": connector_status,
                    "t": now,
                },
            )
            connector_ids.append(connector_id)
    return station_id, connector_ids


@pytest.mark.asyncio
async def test_station_availability_counts_only_available_connectors_on_postgres(
    temporary_database: str,
) -> None:
    """D3 on real PostGIS: OPERATIONAL + >=1 active Available connector, online not needed.

    Query point at longitude 106.0020. From nearest to farthest:
    deleted-gun (0.0002 away, its only Available gun is soft-deleted), maintenance
    (0.0005, Available but UNDER_MAINTENANCE), busy (0.0010, Charging only) and
    free (0.0020, one Charging and one Available gun). Only "free" is available.
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
            maintenance_status="UNDER_MAINTENANCE",
        )
        deleted_gun_id, deleted_connectors = await _provision_located_station(
            engine,
            identity="AV-DELETED",
            longitude=106.0018,
            connector_statuses=["Available", None],
        )
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE charging_connectors SET deleted_at = now() "
                    "WHERE connector_id = :c"
                ),
                {"c": deleted_connectors[0]},
            )

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
                )
            )
            operational = await charging_stations_service.list_nearby_charging_stations(
                db, latitude=10.0, longitude=106.002, radius_km=5
            )
            free_detail = await charging_stations_service.get_charging_station(
                db, free_id
            )
            maintenance_detail = await charging_stations_service.get_charging_station(
                db, maintenance_id
            )
            free_status = await charging_stations_service.get_charging_station_status(
                db, free_id
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
            await connection.execute(
                text(
                    "INSERT INTO charging_sessions (session_id, station_id, evse_id, "
                    "connector_id, ocpp_transaction_id, status, started_at, ended_at, "
                    "meter_start_wh, meter_end_wh, meter_end_sampled_at, "
                    "energy_delivered_wh, created_at, updated_at) VALUES "
                    "(:x, :s, :e, :c, 'SERIES-TX', 'completed', :start, :end, "
                    "1000, 4000, :end, 3000, :start, :end)"
                ),
                {
                    "x": session_id,
                    "s": station_id,
                    "e": evse_id,
                    "c": connector_ids[0],
                    "start": t0 + timedelta(minutes=40),
                    "end": t0 + timedelta(hours=1, minutes=20),
                },
            )
            for minutes, value_wh in ((55, 2000), (70, 3500), (80, 4000)):
                await connection.execute(
                    text(
                        "INSERT INTO charging_session_measurements (measurement_id, "
                        "sampled_at, session_id, measurand, value, unit) VALUES "
                        "(:m, :t, :x, 'Energy.Active.Import.Register', :v, 'Wh')"
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
                db, start_time=t0, end_time=t0 + timedelta(days=1)
            )
            completed = await charging_sessions_service.list_charging_sessions(
                db,
                page=1,
                page_size=10,
                station_id=station_id,
                status=SessionStatus.COMPLETED,
            )
            active = await charging_sessions_service.list_charging_sessions(
                db,
                page=1,
                page_size=10,
                station_id=station_id,
                status=SessionStatus.ACTIVE,
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
                        "SELECT ocpp_protocol_version, vendor, model, firmware_version, "
                        "last_boot_at IS NOT NULL AS booted, "
                        "last_seen_at IS NOT NULL AS seen "
                        "FROM charging_stations WHERE ocpp_identity = 'BOOT-201'"
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


@pytest.mark.asyncio
async def test_driver_assignment_indexes_and_list_filters_on_postgres(
    temporary_database: str,
) -> None:
    """One open assignment per vehicle and per driver; q/vehicle filters (F-E4, #85)."""
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
            alice = await driver_repository.insert(
                db,
                {
                    "full_name": "Alice Nguyen",
                    "phone_number": "0901000001",
                    "license_number": "LIC-50%-A",
                },
            )
            bob = await driver_repository.insert(
                db,
                {
                    "full_name": "Bob Tran",
                    "phone_number": "0901000002",
                    "license_number": "LIC-B",
                },
            )
            now = datetime.now(timezone.utc)
            await driver_repository.insert_assignment(
                db,
                driver_id=alice.driver_id,
                vehicle_id=first_vehicle.vehicle_id,
                assigned_at=now,
            )

            # A second open assignment on the same vehicle, or for the same
            # driver, breaks one of the two partial unique indexes.
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await driver_repository.insert_assignment(
                        db,
                        driver_id=bob.driver_id,
                        vehicle_id=first_vehicle.vehicle_id,
                        assigned_at=now,
                    )
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await driver_repository.insert_assignment(
                        db,
                        driver_id=alice.driver_id,
                        vehicle_id=second_vehicle.vehicle_id,
                        assigned_at=now,
                    )

            # Closed history rows are unlimited: close, then reopen.
            open_assignment = await driver_repository.find_active_assignment_by_driver(
                db, alice.driver_id
            )
            assert open_assignment is not None
            await driver_repository.close_assignment(
                db, open_assignment, unassigned_at=now + timedelta(minutes=1)
            )
            await driver_repository.insert_assignment(
                db,
                driver_id=bob.driver_id,
                vehicle_id=first_vehicle.vehicle_id,
                assigned_at=now + timedelta(minutes=2),
            )

            by_vehicle = await driver_repository.list_all(
                db, offset=0, limit=10, vehicle_id=first_vehicle.vehicle_id
            )
            by_name = await driver_repository.list_all(
                db, offset=0, limit=10, search_text="alice"
            )
            by_phone = await driver_repository.list_all(
                db, offset=0, limit=10, search_text="000002"
            )
            # "%" is matched literally, never as a wildcard.
            by_percent = await driver_repository.list_all(
                db, offset=0, limit=10, search_text="50%"
            )
            no_wildcard = await driver_repository.count(db, search_text="%")
            unassigned_vehicle = await driver_repository.count(
                db, vehicle_id=second_vehicle.vehicle_id
            )

            assert [driver.driver_id for driver in by_vehicle] == [bob.driver_id]
            assert [driver.driver_id for driver in by_name] == [alice.driver_id]
            assert [driver.driver_id for driver in by_phone] == [bob.driver_id]
            assert [driver.driver_id for driver in by_percent] == [alice.driver_id]
            assert no_wildcard == 1
            assert unassigned_vehicle == 0
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
                db, hanoi.fleet_id, geofence.geofence_id
            )
            assert (
                await fleet_service.list_geofences_containing(
                    db, hanoi.fleet_id, latitude=10.775, longitude=107.705
                )
                == []
            )
            listed = await fleet_service.list_geofences(db, hanoi.fleet_id)
            assert listed.total == 0

            # #84: close the membership by ID; a second close is a 404.
            await fleet_service.close_fleet_membership(
                db, hanoi.fleet_id, membership.fleet_vehicle_membership_id
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
                db, FleetCreateRequest(organization_id=organization_id, name="South")
            )
            depot = await fleet_service.create_fleet(
                db,
                FleetCreateRequest(
                    organization_id=organization_id,
                    fleet_code="SG-D1",
                    parent_fleet_id=region.fleet_id,
                ),
            )
            assert depot.parent_fleet_id == region.fleet_id
            assert (depot.name, region.fleet_code) == (None, None)

            with pytest.raises(FleetHierarchyLoopError):
                await fleet_service.update_fleet(
                    db,
                    region.fleet_id,
                    FleetUpdateRequest(parent_fleet_id=depot.fleet_id),
                )
            with pytest.raises(FleetHasSubFleetsError):
                await fleet_service.soft_delete_fleet(db, region.fleet_id)

            await fleet_service.soft_delete_fleet(db, depot.fleet_id)
            await fleet_service.soft_delete_fleet(db, region.fleet_id)
            with pytest.raises(FleetParentNotFoundError):
                await fleet_service.create_fleet(
                    db,
                    FleetCreateRequest(
                        organization_id=organization_id,
                        name="Orphan",
                        parent_fleet_id=region.fleet_id,
                    ),
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
            )
            # The same code in another organization is fine.
            await fleet_service.create_fleet(
                db,
                FleetCreateRequest(
                    organization_id=second_organization_id, fleet_code="HQ"
                ),
            )
            with pytest.raises(FleetConflictError):
                await fleet_service.create_fleet(
                    db,
                    FleetCreateRequest(
                        organization_id=first_organization_id, fleet_code="HQ"
                    ),
                )
            with pytest.raises(FleetParentOrganizationMismatchError):
                await fleet_service.create_fleet(
                    db,
                    FleetCreateRequest(
                        organization_id=second_organization_id,
                        name="Wrong tree",
                        parent_fleet_id=first.fleet_id,
                    ),
                )
            await db.rollback()

        async with session_factory() as db:
            with pytest.raises(FleetOrganizationNotFoundError):
                await fleet_service.create_fleet(
                    db, FleetCreateRequest(organization_id=uuid4(), name="Ghost")
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
            driver = await driver_repository.insert(
                db,
                {
                    "full_name": "Chi Le",
                    "phone_number": "0901000003",
                    "license_number": "LIC-C",
                },
            )
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
            )
            assert sos.channel is SupportCaseChannel.IN_APP

            sos_alerts = await notifications_service.list_notifications(
                db,
                after_id=0,
                limit=10,
                unread_only=True,
                vehicle_id=vehicle.vehicle_id,
                notification_type=NotificationType.SOS_ALERT,
                severity=NotificationSeverity.CRITICAL,
                order=NotificationListOrder.DESC,
            )
            assert sos_alerts.count == 1
            sos_alert = sos_alerts.notifications[0]
            assert sos_alert.payload["case_id"] == str(sos.case_id)
            assert sos_alert.payload["vehicle_vin"] == vehicle.vin
            assert sos_alert.payload["latitude"] == pytest.approx(10.8)
            assert sos_alert.payload["error_code"] == "E-042"

            unread = await notifications_service.count_unread_notifications(
                db, vehicle.vehicle_id
            )
            first_mark = await notifications_service.mark_all_notifications_read(
                db, vehicle.vehicle_id
            )
            second_mark = await notifications_service.mark_all_notifications_read(
                db, vehicle.vehicle_id
            )
            stored = await notifications_service.get_notification(
                db, sos_alert.notification_id
            )
            assert unread.unread_count == 1
            assert first_mark.marked_count == 1
            assert second_mark.marked_count == 0
            assert stored.read_at is not None
            assert (
                await notifications_service.count_unread_notifications(db, None)
            ).unread_count == 0
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
                        "message_uuid": uuid4(),
                        "telematic_id": device.telematic_id,
                        "telematic_serial": device.telematic_serial,
                        "vehicle_id": device.vehicle_id,
                        "recorded_at": received_at,
                        "received_at": received_at,
                        "location": coordinates_to_location(10.8, 106.7),
                        "soc": 80.0,
                        "signal_strength": signal_strength,
                        "raw_payload": {"source": "postgres-integration"},
                    },
                )

            online = await telematics_service.get_telematic(
                db, devices["online"].telematic_id
            )
            silent = await telematics_service.get_telematic(
                db, devices["silent"].telematic_id
            )
            unmounted = await telematics_service.get_telematic(
                db, devices["unmounted"].telematic_id
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
                db, [{"recorded_at": _september_2026(1, 1), "soc": 80.0}]
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
                        "soc": 90.0,
                        "odometer": 1000.0,
                    },
                    {
                        "recorded_at": _september_2026(1, 5),
                        "soc": 80.0,
                        "odometer": 1050.0,
                    },
                ],
            )
            second_vehicle = await _seed_vehicle_readings(
                db,
                [
                    {
                        "recorded_at": _september_2026(1, 2),
                        "soc": 70.0,
                        "odometer": 0.0,
                    },
                    {
                        "recorded_at": _september_2026(1, 6),
                        "soc": 40.0,
                        "odometer": 250.0,
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
