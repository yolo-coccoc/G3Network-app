"""Backend FastAPI application entry point.

Builds the FastAPI app: mounts every domain router under ``/api/v1``, maps
the shared domain-exception bases (``app.libs.common.errors``) to HTTP
status codes once for all routers, and disposes the database engine on
shutdown. Run with ``make backend-dev``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.domains.charging_sessions.router import router as charging_sessions_router
from app.domains.charging_stations.router import router as charging_stations_router
from app.domains.drivers.router import router as drivers_router
from app.domains.fleet.router import router as fleet_router
from app.domains.notifications.router import router as notifications_router
from app.domains.support.router import router as support_router
from app.domains.telematics.router import router as telematics_router
from app.domains.telemetry.router import router as telemetry_router
from app.domains.vehicles.router import router as vehicles_router
from app.libs.common.config import settings
from app.libs.common.errors import (
    ConflictError,
    DomainError,
    InvalidInputError,
    NotFoundError,
    UpstreamUnavailableError,
)
from app.libs.db.session import close_db


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run the application's startup and shutdown work.

    Startup does nothing: the schema comes from Alembic (``make db-reset``),
    never from the app.

    Args:
        _app: The FastAPI application (unused).

    Yields:
        Control to FastAPI while the application serves requests.

    Side Effects:
        On shutdown, disposes the shared database engine's connection pool.
    """
    yield
    await close_db()


app = FastAPI(
    title=settings.APP_NAME,
    description=settings.APP_DESCRIPTION,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Report that the API process is up. Does not touch the database.

    Returns:
        The status, application version and name.
    """
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "app_name": settings.APP_NAME,
    }


# HTTP status for each shared domain-exception base. A router keeps its own
# try/except only for an exception that needs a different code than this.
_DOMAIN_ERROR_STATUS: dict[type[DomainError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    InvalidInputError: status.HTTP_400_BAD_REQUEST,
    UpstreamUnavailableError: status.HTTP_502_BAD_GATEWAY,
}


async def domain_error_handler(_request: Request, error: Exception) -> JSONResponse:
    """Convert an uncaught domain exception into its HTTP error response.

    The body has the same shape as FastAPI's ``HTTPException`` response
    (``{"detail": message}``), so clients see no difference from the former
    per-router handling.

    Args:
        _request: The request being served (unused).
        error: The domain exception that escaped the endpoint.

    Returns:
        A JSON response with the status of the exception's base class.
    """
    status_code = next(
        code for base, code in _DOMAIN_ERROR_STATUS.items() if isinstance(error, base)
    )
    return JSONResponse(status_code=status_code, content={"detail": str(error)})


for _error_base in _DOMAIN_ERROR_STATUS:
    app.add_exception_handler(_error_base, domain_error_handler)

# Include routers
app.include_router(vehicles_router, prefix="/api/v1/vehicles")
app.include_router(telematics_router, prefix="/api/v1/telematics")
app.include_router(telemetry_router, prefix="/api/v1/telemetry")
app.include_router(charging_stations_router, prefix="/api/v1")
app.include_router(charging_sessions_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1/notifications")
app.include_router(drivers_router, prefix="/api/v1/drivers")
app.include_router(support_router, prefix="/api/v1/support")
app.include_router(fleet_router, prefix="/api/v1/fleets")
