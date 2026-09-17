"""Smoke test for the current baseline migration graph."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def _backend_root() -> Path:
    """Return the backend directory containing alembic.ini."""
    return Path(__file__).resolve().parents[1]


def test_migration_graph_has_one_current_head() -> None:
    """The migration graph has exactly one baseline head for the charging MVP."""
    backend_root = _backend_root()
    script = ScriptDirectory.from_config(Config(str(backend_root / "alembic.ini")))

    assert script.get_heads() == ["0007_telemetry_schema_version"]


def test_reset_migration_uses_application_allowlist() -> None:
    """The reset migration must never drop alembic_version or an extension."""
    migration = (
        _backend_root()
        / "app/libs/db/migrations/versions/0001_reset_application_schema.py"
    )
    source = migration.read_text(encoding="utf-8")

    assert "_APPLICATION_TABLES" in source
    assert 'DROP TABLE IF EXISTS "alembic_version"' not in source
    assert "DROP EXTENSION" not in source
