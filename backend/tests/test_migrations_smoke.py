"""Smoke test for the single bootstrap-phase baseline migration."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def _backend_root() -> Path:
    """Return the backend directory containing alembic.ini."""
    return Path(__file__).resolve().parents[1]


def test_migration_graph_is_one_baseline_revision() -> None:
    """The bootstrap phase keeps exactly one migration, which is also the head."""
    script = ScriptDirectory.from_config(Config(str(_backend_root() / "alembic.ini")))

    assert script.get_heads() == ["0001_baseline_schema"]
    assert [revision.revision for revision in script.walk_revisions()] == [
        "0001_baseline_schema"
    ]


def test_baseline_clear_step_spares_extensions_and_alembic_version() -> None:
    """The clear step must never drop alembic_version or an extension object."""
    migration = (
        _backend_root() / "app/libs/db/migrations/versions/0001_baseline_schema.py"
    )
    source = migration.read_text(encoding="utf-8")

    assert "c.relname <> 'alembic_version'" in source
    assert "d.deptype = 'e'" in source
    assert "DROP EXTENSION" not in source
    assert "DROP SCHEMA" not in source
