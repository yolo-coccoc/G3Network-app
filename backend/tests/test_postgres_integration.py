"""PostgreSQL integration test for the baseline migrations and telemetry repository."""

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.domains.telemetry.repository as telemetry_repository
import app.domains.vehicles.repository as vehicle_repository
from app.domains.telematics.models import TelematicModel
from app.domains.telematics.types import TelematicStatus
from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.vehicles.models import VehicleModel
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings

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
            tables = set((await connection.execute(text("""
                            SELECT table_name
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                            AND table_name IN (
                                'vehicles', 'telematics', 'vehicle_telemetry',
                                'charging_stations', 'charging_evses',
                                'charging_connectors', 'charging_sessions',
                                'charging_session_events',
                                'charging_session_meter_values'
                            )
                            """))).scalars())

        # Pinned to the current Alembic head. This assertion was stale
        # (hardcoded to an old head) until F-B2 - it went unnoticed only
        # because this suite is skipped unless RUN_DB_INTEGRATION=1.
        assert version == "0017_charging_ingest_fields"
        assert len(tables) == 9
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
                    "latitude": 10.8,
                    "longitude": 106.7,
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
