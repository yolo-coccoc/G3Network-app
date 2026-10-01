"""Process-wide async database engine, session factory and HTTP session dependency.

Scope: every entry boundary of the backend gets its ``AsyncSession`` from
here - the FastAPI app through ``get_db``, background workers and CLIs
through ``async_session_factory``. No other module creates an engine or a
session factory (see ``.claude/rules/backend-runtime-conventions.md``).

Limitations: one engine per process, configured only from
``settings.DATABASE_URL``; there is no read replica, per-request engine
selection or retry on a lost connection. ``Base`` lives in
``app.libs.db.base``, not here.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.libs.common.config import settings

# Module-level, created at import time and shared by the whole process: the
# engine owns the connection pool, so one instance per process keeps the pool
# bounded. It lives until ``close_db`` disposes of it at shutdown.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.APP_DEBUG,
)

# Bound to ``engine`` above. ``expire_on_commit=False`` keeps ORM objects
# readable after the entry boundary commits (a response may be serialized
# after the commit); ``autoflush=False`` makes every flush explicit in the
# repositories.
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide one transactional ``AsyncSession`` per HTTP request.

    This dependency is the HTTP entry boundary and owns the transaction:
    services and repositories never commit or roll back. The session is
    committed after the endpoint returns without raising, and rolled back
    if anything raised while the endpoint ran (including a domain exception
    that ``app/api/main.py`` later turns into an error response), so a
    failed request never leaves partial writes behind.

    Always declare it with ``scope="function"``: FastAPI's default scope
    runs the code after ``yield`` (the commit) only after the response has
    been sent, so the client could see success before the data is
    committed, or for a commit that then fails.

    Usage:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db, scope="function")):
            ...

    Yields:
        A new session from ``async_session_factory``, closed when the
        request finishes.

    Raises:
        Exception: Whatever the endpoint (or the commit itself) raised is
            re-raised unchanged after the rollback.
    """
    async with async_session_factory() as db_session:
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            # Process/request boundary: roll back whatever failed, then let the
            # original error propagate to FastAPI's exception handlers.
            await db_session.rollback()
            raise


async def close_db() -> None:
    """Dispose of the shared engine at process shutdown.

    Side Effects:
        Closes every pooled connection of ``engine``. A session opened after
        this call transparently creates a new pool, so call it only once the
        process stops serving work.
    """
    await engine.dispose()
