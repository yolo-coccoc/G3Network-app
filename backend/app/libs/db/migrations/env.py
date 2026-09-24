"""Alembic configuration for the backend's async migrations."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Only load the active charging models; technical status history is not
# part of the MVP metadata and the corresponding legacy source only remains
# as a comment in the domain.
from app.domains.charging_sessions.models import (  # noqa: F401
    ChargingSessionEventModel,
    ChargingSessionMeasurementModel,
    ChargingSessionModel,
)
from app.domains.charging_stations.models import (  # noqa: F401
    ChargingConnectorModel,
    ChargingEvseModel,
    ChargingOcppMessageModel,
    ChargingStationConfigurationEntryModel,
    ChargingStationModel,
)
from app.domains.drivers.models import (  # noqa: F401
    DriverModel,
    DriverVehicleAssignmentModel,
)
from app.domains.fleet.models import (  # noqa: F401
    FleetModel,
    FleetVehicleMembershipModel,
)
from app.domains.support.models import SupportCaseModel  # noqa: F401
from app.domains.telematics.models import TelematicModel  # noqa: F401
from app.domains.telemetry.models import VehicleTelemetryModel  # noqa: F401
from app.domains.vehicles.models import VehicleModel  # noqa: F401
from app.libs.common.config import settings
from app.libs.db.base import Base

# The Alembic object provides the context and configuration read from alembic.ini.
config = context.config

# Use the same Settings as the API and worker so a second source of .env
# reading doesn't exist. ConfigParser uses % interpolation, so this character
# must be escaped before writing it into the ini.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

# Load Alembic's logger configuration if the ini file has a corresponding logging section.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata used for autogenerate migrations.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in async mode."""
    configuration = config.get_section(config.config_ini_section, {})
    sqlalchemy_url = config.get_main_option("sqlalchemy.url")
    if sqlalchemy_url is None:
        raise RuntimeError("Alembic sqlalchemy.url is not configured")
    configuration["sqlalchemy.url"] = sqlalchemy_url

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
