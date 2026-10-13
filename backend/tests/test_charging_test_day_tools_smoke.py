"""Smoke tests keeping the charging test-day tools standalone.

``simulator/charging_test_day/`` is copied to a laptop and run without the
backend's code (planner ``docs/planners/backend-charging-test-day.md``). These
tests fail when a change would break that: a module importing ``app``, the
written-out plug standards drifting from the backend's enum, or the pinned
libraries drifting from the backend's lock file.
"""

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from app.domains.charging_stations.types import ConnectorStandard

TOOLS_DIR = Path(__file__).resolve().parents[2] / "simulator" / "charging_test_day"
LOCK_FILE = Path(__file__).resolve().parents[1] / "uv.lock"


def _imported_top_level_modules(source_file: Path) -> set[str]:
    """Return the top-level names a module imports.

    Args:
        source_file: A Python file.

    Returns:
        The first component of every ``import x`` / ``from x import`` target.
    """
    tree = ast.parse(source_file.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules.add(node.module.split(".")[0])
    return modules


@pytest.mark.parametrize(
    "source_file", sorted(TOOLS_DIR.glob("*.py")), ids=lambda path: path.name
)
def test_no_test_day_tool_imports_the_backend(source_file: Path) -> None:
    """Every module of the folder runs without the backend's ``app`` package."""
    assert "app" not in _imported_top_level_modules(source_file)


def test_the_folder_holds_the_tools_its_guide_describes() -> None:
    """The guide, the pins and the settings template travel with the tools."""
    expected = {
        "CLAUDE.md",
        "requirements.txt",
        ".env.example",
        ".gitignore",
        "g3_api.py",
        "test_day_common.py",
        "driver_app.py",
        "station_console.py",
        "ocpp16_charge_point_simulator.py",
        "seed_charging_topology.py",
    }
    assert expected <= {path.name for path in TOOLS_DIR.iterdir()}


def test_written_out_plug_standards_match_the_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seed's ``CONNECTOR_STANDARDS`` equals ``ConnectorStandard``'s values."""
    monkeypatch.syspath_prepend(str(TOOLS_DIR))
    spec = importlib.util.spec_from_file_location(
        "seed_charging_topology", TOOLS_DIR / "seed_charging_topology.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "seed_charging_topology", module)
    spec.loader.exec_module(module)

    assert tuple(module.CONNECTOR_STANDARDS) == tuple(
        standard.value for standard in ConnectorStandard
    )


@pytest.mark.parametrize("package", ["ocpp", "websockets"])
def test_pinned_libraries_match_the_backend_lock_file(package: str) -> None:
    """``requirements.txt`` pins the versions the backend is tested with."""
    lock_text = LOCK_FILE.read_text(encoding="utf-8")
    locked = re.search(
        rf'\[\[package\]\]\nname = "{package}"\nversion = "([^"]+)"', lock_text
    )
    assert locked is not None
    requirements = (TOOLS_DIR / "requirements.txt").read_text(encoding="utf-8")
    assert f"{package}=={locked.group(1)}" in requirements.split()
