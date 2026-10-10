"""Every process must register every cross-domain hook (CV-21).

A hook slot is a module-level variable, so each process starts with it empty,
and an empty slot is skipped without an error: a charging session ended in a
process that did not register the billing hook stays unbilled, an alert raised
there skips the checked-in driver and the fleet limit. These tests fail when
`register_all_hooks` leaves a slot empty, or when a module that is run as a
process (the API, an ``entrypoint.py``, the identity bootstrap) does not call
it.
"""

import ast
from pathlib import Path

import pytest

import app.api.billing_hooks as billing_hooks
import app.api.startup as startup
import app.domains.charging_sessions.service as charging_session_service
import app.domains.drivers.service as driver_service
import app.domains.fleet.service as fleet_service
import app.domains.identity.dependencies as identity_dependencies
import app.domains.identity.member_service as member_service
import app.domains.notifications.recipient_service as recipient_service

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
# The API is served by uvicorn from this module, so it has no `__main__` guard.
API_MODULE = APP_ROOT / "api" / "main.py"
# Today: the API, five worker entrypoints and the identity bootstrap. A lower
# count means the discovery below stopped finding processes.
MINIMUM_PROCESS_MODULES = 7


def _empty_every_hook_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset every hook slot to its state in a fresh process.

    Args:
        monkeypatch: Restores the slots after the test.
    """
    monkeypatch.setattr(charging_session_service, "_session_ended_hooks", [])
    monkeypatch.setattr(member_service, "_membership_end_hooks", [])
    monkeypatch.setattr(identity_dependencies, "_visible_vehicle_resolver", None)
    monkeypatch.setattr(recipient_service, "_vehicle_audience_hooks", None)


def _process_modules() -> list[Path]:
    """List the modules that are started as a process.

    Returns:
        The API module plus every module under ``app/`` with a
        ``if __name__ == "__main__":`` guard, sorted.
    """
    guarded = [
        path
        for path in APP_ROOT.rglob("*.py")
        if 'if __name__ == "__main__":' in path.read_text(encoding="utf-8")
    ]
    return sorted([API_MODULE, *guarded])


def _calls_register_all_hooks(path: Path) -> bool:
    """Tell whether a module calls `register_all_hooks` anywhere.

    Args:
        path: The module's source file.

    Returns:
        True when the module contains a call to ``register_all_hooks`` or
        ``<alias>.register_all_hooks``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "register_all_hooks":
            return True
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "register_all_hooks"
        ):
            return True
    return False


def test_register_all_hooks_fills_every_hook_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the call, billing, membership end, fleet visibility and alert
    routing are all wired."""
    _empty_every_hook_slot(monkeypatch)

    startup.register_all_hooks()

    assert charging_session_service._session_ended_hooks == [
        billing_hooks.bill_ended_session
    ]
    assert member_service._membership_end_hooks == [
        driver_service.handle_membership_end
    ]
    assert (
        identity_dependencies._visible_vehicle_resolver
        is fleet_service.resolve_principal_visible_vehicle_ids
    )
    assert recipient_service._vehicle_audience_hooks is not None


def test_register_all_hooks_twice_registers_each_hook_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling it again (a test, a reload) adds no duplicate listener."""
    _empty_every_hook_slot(monkeypatch)

    startup.register_all_hooks()
    startup.register_all_hooks()

    assert len(charging_session_service._session_ended_hooks) == 1
    assert len(member_service._membership_end_hooks) == 1


def test_every_process_module_calls_register_all_hooks() -> None:
    """The API, each entrypoint and the bootstrap register every hook."""
    process_modules = _process_modules()
    missing = [
        str(path.relative_to(APP_ROOT.parent))
        for path in process_modules
        if not _calls_register_all_hooks(path)
    ]

    assert len(process_modules) >= MINIMUM_PROCESS_MODULES
    assert missing == []
