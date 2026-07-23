"""SQLAlchemy Base class for models.

This file exists for Alembic to import Base.metadata.
The actual Base class is defined in session.py to avoid circular imports.
"""

from app.libs.db.session import Base  # noqa: F401

__all__ = ["Base"]
