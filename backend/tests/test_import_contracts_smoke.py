"""Guard the domain-boundary contracts against new modules being forgotten.

Each domain's import-linter contract (``[tool.importlinter]`` in
pyproject.toml) must forbid every module of that domain except its public
surface (``service``, ``types``, ``exceptions``, and identity's ``dependencies``). A new internal module or
subpackage that is not listed would silently become importable by other
domains; this test fails instead.
"""

import tomllib
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOMAINS_ROOT = BACKEND_ROOT / "app" / "domains"
# `dependencies` is the FastAPI authentication surface only the identity
# domain has (`get_current_principal`, `require_roles`).
PUBLIC_MODULES = frozenset({"service", "types", "exceptions", "dependencies"})


def _domain_names() -> list[str]:
    """Return every domain package under ``app/domains``.

    Returns:
        Sorted domain directory names.
    """
    return sorted(
        path.name
        for path in DOMAINS_ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    )


def _internal_modules(domain: str) -> set[str]:
    """List a domain's top-level modules and subpackages that are not public.

    Args:
        domain: Domain package name.

    Returns:
        Dotted module paths such as ``app.domains.telemetry.alerting``.
    """
    modules = set()
    for path in (DOMAINS_ROOT / domain).iterdir():
        if path.name.startswith("__"):
            continue
        if path.is_dir() and (path / "__init__.py").exists():
            modules.add(path.name)
        elif path.suffix == ".py":
            modules.add(path.stem)
    return {f"app.domains.{domain}.{name}" for name in modules - PUBLIC_MODULES}


def _forbidden_modules_by_domain() -> dict[str, set[str]]:
    """Read each domain contract's ``forbidden_modules`` from pyproject.toml.

    Returns:
        Forbidden module paths keyed by the domain that owns them.
    """
    config = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text())
    forbidden: dict[str, set[str]] = {}
    for contract in config["tool"]["importlinter"]["contracts"]:
        for module in contract.get("forbidden_modules", []):
            domain = module.split(".")[2]
            forbidden.setdefault(domain, set()).update([module])
    return forbidden


@pytest.mark.parametrize("domain", _domain_names())
def test_every_internal_module_is_forbidden_to_other_domains(domain: str) -> None:
    """A domain's contract lists all of its non-public modules."""
    missing = _internal_modules(domain) - _forbidden_modules_by_domain().get(
        domain, set()
    )

    assert not missing, (
        f"Add {sorted(missing)} to the '{domain}' import-linter contract's "
        "forbidden_modules in backend/pyproject.toml"
    )
