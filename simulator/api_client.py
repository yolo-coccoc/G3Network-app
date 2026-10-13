"""API client for the simulator scripts that run with the backend's code.

The client itself lives in the standalone test-day folder
(``charging_test_day/g3_api.py``), which runs without the backend; this module
only makes it importable as ``api_client`` for the scripts that stay here
because they need the backend's code or database (``seed_simulator_devices.py``,
``telematic_simulator.py``, ``seed_test_drivers.py``). Run them from
``backend/`` with its venv. The admin login settings are looked up as
described in ``g3_api.py`` (environment, then ``backend/.env``).
"""

import sys
from pathlib import Path

# The standalone folder is a sibling of this file; putting it first on the
# import path lets ``g3_api`` (and the test-day helpers) be imported by name.
sys.path.insert(0, str(Path(__file__).resolve().parent / "charging_test_day"))

from g3_api import (  # noqa: E402
    DEFAULT_API_URL,
    ApiClient,
    ApiError,
    login,
    login_as_bootstrap_admin,
    resolve_api_url,
)

__all__ = [
    "DEFAULT_API_URL",
    "ApiClient",
    "ApiError",
    "login",
    "login_as_bootstrap_admin",
    "resolve_api_url",
]
