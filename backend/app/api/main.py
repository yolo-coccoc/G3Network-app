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

import app.api.startup as startup
from app.api.charging_session_flow import router as charging_session_flow_router
from app.api.vehicle_transfer import router as vehicle_transfer_router
from app.domains.batteries.router import battery_models_router
from app.domains.batteries.router import router as batteries_router
from app.domains.billing.router import router as billing_router
from app.domains.charging_sessions.router import router as charging_sessions_router
from app.domains.charging_stations.router import router as charging_stations_router
from app.domains.drivers.router import driving_sessions_router
from app.domains.drivers.router import router as drivers_router
from app.domains.drivers.trip_router import router as trips_router
from app.domains.fleet.router import membership_fleets_router
from app.domains.fleet.router import router as fleet_router
from app.domains.identity.compliance_router import (
    audit_router as access_audit_router,
)
from app.domains.identity.compliance_router import (
    consents_router,
    legal_documents_router,
)
from app.domains.identity.membership_router import router as memberships_router
from app.domains.identity.organization_router import router as organizations_router
from app.domains.identity.router import auth_router, users_router
from app.domains.notifications.router import router as notifications_router
from app.domains.notifications.router import (
    settings_router as notification_settings_router,
)
from app.domains.support.router import router as support_router
from app.domains.telematics.router import router as telematics_router
from app.domains.telemetry.router import router as telemetry_router
from app.domains.vehicles.router import router as vehicles_router
from app.domains.vehicles.router import vehicle_models_router
from app.domains.warranties.router import router as warranties_router
from app.libs.common.config import settings
from app.libs.common.errors import (
    ConflictError,
    DomainError,
    InvalidInputError,
    LockedError,
    NotFoundError,
    PermissionDeniedError,
    TooManyRequestsError,
    UnauthenticatedError,
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
    UnauthenticatedError: status.HTTP_401_UNAUTHORIZED,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    LockedError: status.HTTP_423_LOCKED,
    TooManyRequestsError: status.HTTP_429_TOO_MANY_REQUESTS,
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
    headers = (
        {"WWW-Authenticate": "Bearer"}
        if status_code == status.HTTP_401_UNAUTHORIZED
        else None
    )
    return JSONResponse(
        status_code=status_code, content={"detail": str(error)}, headers=headers
    )


for _error_base in _DOMAIN_ERROR_STATUS:
    app.add_exception_handler(_error_base, domain_error_handler)

# Cross-domain reactions wired from above the domains: ending a membership
# closes the driver profile (DR-10), the fleet limit reaches the vehicle
# endpoints (FL-10), the end of a charging session bills it (BL-19).
startup.register_api_hooks()

# Include routers
app.include_router(vehicles_router, prefix="/api/v1/vehicles")
app.include_router(vehicle_transfer_router, prefix="/api/v1/vehicles")
app.include_router(vehicle_models_router, prefix="/api/v1/vehicle-models")
app.include_router(batteries_router, prefix="/api/v1/batteries")
app.include_router(battery_models_router, prefix="/api/v1/battery-models")
app.include_router(warranties_router, prefix="/api/v1/warranties")
app.include_router(telematics_router, prefix="/api/v1/telematics")
app.include_router(telemetry_router, prefix="/api/v1/telemetry")
app.include_router(charging_stations_router, prefix="/api/v1")
app.include_router(charging_sessions_router, prefix="/api/v1")
app.include_router(charging_session_flow_router, prefix="/api/v1")
app.include_router(billing_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1/notifications")
app.include_router(
    notification_settings_router,
    prefix="/api/v1/organizations",
)
app.include_router(drivers_router, prefix="/api/v1/drivers")
app.include_router(driving_sessions_router, prefix="/api/v1/driving-sessions")
app.include_router(trips_router, prefix="/api/v1/trips")
app.include_router(support_router, prefix="/api/v1/support")
app.include_router(fleet_router, prefix="/api/v1/fleets")
app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(users_router, prefix="/api/v1/users")
app.include_router(organizations_router, prefix="/api/v1/organizations")
app.include_router(memberships_router, prefix="/api/v1/memberships")
app.include_router(membership_fleets_router, prefix="/api/v1/memberships")
app.include_router(legal_documents_router, prefix="/api/v1/legal-documents")
app.include_router(consents_router, prefix="/api/v1/consents")
app.include_router(access_audit_router, prefix="/api/v1/access-audit-logs")
