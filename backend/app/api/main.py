"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.domains.vehicles.router import router as vehicles_router
from app.domains.telematics.router import router as telematics_router
from app.libs.common.config import settings
from app.libs.db.session import close_db


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan events."""
    # Startup
    # Note: Tables are created via Alembic migrations
    yield
    # Shutdown
    await close_db()


app = FastAPI(
    title=settings.APP_NAME,
    description="Backend for G3Network - Electric truck driver support system",
    version=settings.APP_VERSION,
    lifespan=lifespan,
)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "app_name": settings.APP_NAME,
    }


# Include routers
app.include_router(vehicles_router, prefix="/api/v1/vehicles")
app.include_router(telematics_router, prefix="/api/v1/telematics")
