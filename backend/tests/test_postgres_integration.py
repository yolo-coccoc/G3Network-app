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
from pathlib import Path
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.domains.charging_sessions.repository as charging_repository
import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.repository as vehicle_repository
from app.domains.telematics.models import TelematicModel
from app.domains.telematics.types import TelematicStatus
from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.vehicles.models import VehicleModel
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location

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
        # Exactly four hypertables: the OCPP 1.6J work replaced the session
        # meter-values hypertable with measurements and added the raw message log.
        assert hypertables == {
            "vehicle_telemetry",
            "charging_session_events",
            "charging_ocpp_messages",
            "charging_session_measurements",
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
            vehicle = VehicleModel(
                vehicle_id=vehicle_id,
                license_plate=license_plate,
                vin=vin,
                make="G3Network",
                model="Integration Test",
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
            session.add(
                VehicleModel(
                    vehicle_id=vehicle_id,
                    license_plate=f"IT-{uuid4().hex[:12]}",
                    vin=f"1{uuid4().hex[:16]}",
                    make="G3Network",
                    model="Integration Test",
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
                        "SELECT e.ocpp_evse_id, c.status::text, c.error_code "
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
        assert [tuple(row) for row in gun_statuses] == [
            (1, "Available", "NoError"),
            (2, "Available", "NoError"),
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
