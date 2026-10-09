"""Alembic environment for the backend's async migrations.

Scope: builds the async engine from the same ``settings.DATABASE_URL`` the
API and workers use, registers every domain's models on ``Base.metadata``
(autogenerate only sees modules imported here) and filters out
extension-owned objects. Limitation: during the bootstrap phase there is a
single baseline revision (see ``.claude/rules/database.md``); this module
holds no schema logic of its own.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Every model module must be imported so its tables join ``Base.metadata``;
# the names themselves are unused here.
from app.domains.batteries.models import (  # noqa: F401
    BatteryModel,
    BatteryModelModel,
)
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
    DrivingSessionModel,
    TripModel,
)
from app.domains.fleet.models import (  # noqa: F401
    FleetModel,
    FleetUserAssignmentModel,
    FleetVehicleMembershipModel,
    GeofenceModel,
)
from app.domains.identity.models import (  # noqa: F401
    AccessAuditLogModel,
    LegalDocumentModel,
    MembershipModel,
    OneTimeCodeModel,
    OrganizationModel,
    OrganizationSettingModel,
    UserConsentModel,
    UserCredentialModel,
    UserModel,
    UserRoleAssignmentModel,
    UserSessionModel,
    UserStateModel,
)
from app.domains.notifications.models import NotificationModel  # noqa: F401
from app.domains.support.models import SupportCaseModel  # noqa: F401
from app.domains.telematics.models import (  # noqa: F401
    TelematicModel,
    TelematicStatusReportModel,
)
from app.domains.telemetry.models import TelemetryModel  # noqa: F401
from app.domains.vehicles.models import (  # noqa: F401
    VehicleModel,
    VehicleModelModel,
)
from app.domains.warranties.models import WarrantyModel  # noqa: F401
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
    """Run migrations in 'offline' mode, emitting SQL instead of executing it.

    The context is configured with only the URL, so no engine or DBAPI
    connection is needed.

    Side Effects:
        ``context.execute()`` calls write their SQL to the script output.
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


# Objects in ``public`` that extensions own or create on their own. They are
# not in the application metadata, so without this filter autogenerate and
# ``alembic check`` would propose dropping them.
_EXTENSION_TABLES = frozenset({"spatial_ref_sys"})  # PostGIS


def _include_object(
    object_: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    """Tell autogenerate which database objects belong to the application.

    Skips, only when they exist in the database but not in the models:
    PostGIS's ``spatial_ref_sys`` table, and the single-column
    ``<table>_<time column>_idx`` index TimescaleDB's ``create_hypertable``
    adds to every hypertable, and every ``*_history`` table (change history,
    built by ``app.libs.db.history_ddl`` and not modelled). Everything else
    is compared normally.

    Args:
        object_: The SQLAlchemy schema object (table, index, column...).
        name: The object's name.
        type_: ``"table"``, ``"index"``, ``"column"``...
        reflected: Whether the object was reflected from the database.
        compare_to: The matching metadata object, or ``None`` if the models
            don't declare it.

    Returns:
        ``False`` for the extension-managed and history objects above, ``True`` otherwise.
    """
    if not reflected or compare_to is not None:
        return True
    if type_ == "table":
        if name is not None and name.endswith("_history"):
            return False
        return name not in _EXTENSION_TABLES
    if type_ == "index":
        columns = list(getattr(object_, "columns", []))
        table = getattr(object_, "table", None)
        if len(columns) == 1 and table is not None:
            return name != f"{table.name}_{columns[0].name}_idx"
    return True


def do_run_migrations(connection: Connection) -> None:
    """Configure the migration context on a connection and run the migrations.

    Args:
        connection: A synchronous connection handed over by
            ``AsyncConnection.run_sync``.

    Side Effects:
        Applies the pending migrations inside one transaction.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create a throwaway async engine and run the migrations on it.

    ``NullPool`` is used because the engine lives only for this run; it is
    disposed of before returning.

    Raises:
        RuntimeError: If ``sqlalchemy.url`` is missing from the Alembic
            configuration.
    """
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
    """Run migrations in 'online' mode against a live database.

    Raises:
        RuntimeError: Propagated from ``run_async_migrations`` when the
            database URL is not configured.
    """
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
