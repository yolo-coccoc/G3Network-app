"""Smoke test cho graph migration baseline hiện tại."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def _backend_root() -> Path:
    """Trả về thư mục backend chứa alembic.ini."""
    return Path(__file__).resolve().parents[1]


def test_migration_graph_has_one_current_head() -> None:
    """Graph migration chỉ có một head baseline charging MVP."""
    backend_root = _backend_root()
    script = ScriptDirectory.from_config(Config(str(backend_root / "alembic.ini")))

    assert script.get_heads() == ["0005_create_monitoring_api_schema"]


def test_reset_migration_uses_application_allowlist() -> None:
    """Reset migration không được xóa alembic_version hoặc extension."""
    migration = (
        _backend_root()
        / "app/libs/db/migrations/versions/0001_reset_application_schema.py"
    )
    source = migration.read_text(encoding="utf-8")

    assert "_APPLICATION_TABLES" in source
    assert 'DROP TABLE IF EXISTS "alembic_version"' not in source
    assert "DROP EXTENSION" not in source
