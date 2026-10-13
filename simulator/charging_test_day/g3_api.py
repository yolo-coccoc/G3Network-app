"""Standalone JSON client and settings of the charging test-day tools.

This folder (``simulator/charging_test_day/``) runs on its own: on the
development VM inside the repository, or copied to a laptop with only Python
3.12 and ``requirements.txt``. Nothing here imports the backend's code; the
tools reach the backend only over HTTP (this client) and OCPP (the charger
simulator).

**Settings** (``tool_setting``) are read, in this order, from: the process
environment; ``backend/.env`` when the folder still sits inside the
repository; this folder's own ``.env`` (``KEY=VALUE`` lines, ``export`` and
quotes allowed; see ``.env.example``). So on the VM the developer's
``backend/.env`` (or a sourced settings file) wins, and on a laptop the
folder's ``.env`` is used.

Every API route except login needs a bearer token. The operator console and
the topology seed log in as the bootstrap HEAD_ADMIN: ``G3_ADMIN_PHONE`` /
``G3_ADMIN_PASSWORD``, or the backend's own ``IDENTITY_BOOTSTRAP_ADMIN_PHONE``
/ ``IDENTITY_BOOTSTRAP_ADMIN_PASSWORD``. The driver tool logs in as a test
driver with ``login``.

An HTTP error answer raises ``ApiError``, which keeps the status code and the
backend's ``detail`` message so a tool can print it instead of a traceback.
Standard library only; no retry, no token refresh (a seed run is shorter than
the access-token lifetime; a long-running test-day tool logs in again when it
is restarted).
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_API_URL = "http://localhost:8000"
# Setting a test-day tool reads when ``--api-url`` is not given.
API_URL_ENV = "G3_API_URL"
REQUEST_TIMEOUT_SECONDS = 10
# This folder, its own settings file, and the backend's settings file when the
# folder is still inside the repository (absent on a laptop copy).
TOOLS_DIR = Path(__file__).resolve().parent
TOOLS_ENV_FILE = TOOLS_DIR / ".env"
BACKEND_ENV_FILE = TOOLS_DIR.parents[1] / "backend" / ".env"


def read_env_file(path: Path) -> dict[str, str]:
    """Read ``KEY=VALUE`` lines from a settings file.

    Blank lines and ``#`` comments are skipped; an ``export`` prefix and
    surrounding single or double quotes are removed.

    Args:
        path: The file; a missing one gives no settings.

    Returns:
        The settings by name.
    """
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        if text.startswith("export "):
            text = text[len("export ") :].strip()
        key, _, value = text.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


@cache
def _file_settings() -> tuple[dict[str, str], dict[str, str]]:
    """Read both settings files once per process.

    Returns:
        The backend's ``.env`` (empty outside the repository) and this
        folder's ``.env``.
    """
    return read_env_file(BACKEND_ENV_FILE), read_env_file(TOOLS_ENV_FILE)


def tool_setting(*names: str, default: str | None = None) -> str | None:
    """Look a setting up: environment, then ``backend/.env``, then ``.env`` here.

    Args:
        *names: Setting names to try, in order (e.g. the tool's own name, then
            the backend's name for the same value).
        default: What to return when no source has any of the names.

    Returns:
        The first non-empty value found, or ``default``.
    """
    backend_file, tools_file = _file_settings()
    for source in (os.environ, backend_file, tools_file):
        for name in names:
            value = source.get(name)
            if value:
                return value
    return default


class ApiError(RuntimeError):
    """The backend answered with an HTTP error status.

    Subclasses ``RuntimeError`` so callers written before it (which only catch
    or propagate ``RuntimeError``) keep working.

    Attributes:
        method: HTTP method of the failed request.
        path: Path of the failed request.
        status_code: HTTP status of the answer.
        detail: The backend's ``detail`` message, flattened to one line (a
            validation error's list is joined), or the raw body text.
        body: The decoded JSON body, or ``None`` when it was not JSON.
    """

    def __init__(
        self, method: str, path: str, status_code: int, body_text: str
    ) -> None:
        """Keep the request, the status and the decoded answer.

        Args:
            method: HTTP method of the request.
            path: Path of the request.
            status_code: HTTP status of the answer.
            body_text: The raw answer body.
        """
        self.method = method
        self.path = path
        self.status_code = status_code
        try:
            self.body: Any = json.loads(body_text) if body_text else None
        except json.JSONDecodeError:
            self.body = None
        self.detail = _flatten_detail(self.body, body_text)
        super().__init__(f"{method} {path} returned HTTP {status_code}: {body_text}")


@dataclass(frozen=True)
class ApiResponse:
    """A successful answer with its status code.

    Attributes:
        status_code: HTTP status of the answer (2xx).
        body: The decoded JSON body.
    """

    status_code: int
    body: Any


def resolve_api_url(api_url: str | None) -> str:
    """Pick the backend URL: the argument, then ``G3_API_URL``, then the default.

    Args:
        api_url: The ``--api-url`` value, or ``None`` when not given.

    Returns:
        The base URL without a trailing slash.
    """
    return (api_url or tool_setting(API_URL_ENV) or DEFAULT_API_URL).rstrip("/")


class ApiClient:
    """A logged-in session against the backend API.

    Attributes:
        api_url: Base URL of the backend, without a trailing slash.
        access_token: Bearer token of the logged-in administrator.
    """

    def __init__(self, api_url: str, access_token: str) -> None:
        """Keep the base URL and the token.

        Args:
            api_url: Base URL of the backend.
            access_token: Bearer token returned by the login.
        """
        self.api_url = api_url.rstrip("/")
        self.access_token = access_token

    def request(
        self, method: str, path: str, payload: Mapping[str, object] | None = None
    ) -> ApiResponse:
        """Send an authenticated request and return the status and JSON body.

        Args:
            method: HTTP method.
            path: Path relative to the base URL, query string included.
            payload: JSON request body, or ``None`` for none.

        Returns:
            The status code and the decoded body.

        Raises:
            ApiError: The backend answered an HTTP error.
            RuntimeError: The backend is unreachable or returned invalid JSON.
        """
        return _send_with_status(self.api_url, method, path, payload, self.access_token)

    def get(self, path: str) -> Any:
        """Send an authenticated GET and return the JSON response.

        Args:
            path: Path relative to the base URL, query string included.

        Returns:
            The decoded JSON body.

        Raises:
            ApiError: The backend answered an HTTP error.
            RuntimeError: The request failed or returned invalid JSON.
        """
        return _send(self.api_url, "GET", path, None, self.access_token)

    def post(self, path: str, payload: Mapping[str, object]) -> dict[str, Any]:
        """Send an authenticated JSON POST and return the response object.

        Args:
            path: Path relative to the base URL.
            payload: JSON request body.

        Returns:
            The decoded JSON object.

        Raises:
            ApiError: The backend answered an HTTP error.
            RuntimeError: The request failed, or the body is not a JSON object.
        """
        response = _send(self.api_url, "POST", path, payload, self.access_token)
        if not isinstance(response, dict):
            raise RuntimeError(f"POST {path} did not return a JSON object")
        return response


def login_as_bootstrap_admin(api_url: str = DEFAULT_API_URL) -> ApiClient:
    """Log in as the bootstrap HEAD_ADMIN and return a client for it.

    Args:
        api_url: Base URL of the backend.

    Returns:
        A client that sends the admin's bearer token.

    Raises:
        RuntimeError: No admin phone or password is set (``G3_ADMIN_PHONE`` /
            ``G3_ADMIN_PASSWORD`` in this folder's ``.env``, or the backend's
            ``IDENTITY_BOOTSTRAP_ADMIN_*``), or the login failed.
    """
    phone_number = tool_setting("G3_ADMIN_PHONE", "IDENTITY_BOOTSTRAP_ADMIN_PHONE")
    password = tool_setting("G3_ADMIN_PASSWORD", "IDENTITY_BOOTSTRAP_ADMIN_PASSWORD")
    if not phone_number or not password:
        raise RuntimeError(
            "Set G3_ADMIN_PHONE and G3_ADMIN_PASSWORD (in this folder's .env, "
            "see .env.example) or the backend's IDENTITY_BOOTSTRAP_ADMIN_*"
        )
    return login(api_url, phone_number, password, platform="WEB")


def login(
    api_url: str, phone_number: str, password: str, *, platform: str = "WEB"
) -> ApiClient:
    """Log in as any person with phone and password and return a client.

    Args:
        api_url: Base URL of the backend.
        phone_number: The login phone number (the backend normalizes it).
        password: The person's password.
        platform: ``WEB``, ``ANDROID`` or ``IOS`` (the backend's
            ``SessionPlatform``); it decides the session lifetime.

    Returns:
        A client that sends the person's bearer token.

    Raises:
        ApiError: The backend refused the login (401 wrong credentials, 423
            temporary lockout, 403 locked account, 422 invalid phone).
        RuntimeError: The backend is unreachable or the answer has no token.
    """
    response = _send(
        api_url.rstrip("/"),
        "POST",
        "/api/v1/auth/login",
        {"phone_number": phone_number, "password": password, "platform": platform},
        None,
    )
    if not isinstance(response, dict) or not isinstance(
        response.get("access_token"), str
    ):
        raise RuntimeError("The login answer carries no access token")
    return ApiClient(api_url, response["access_token"])


def _flatten_detail(body: Any, body_text: str) -> str:
    """Turn the backend's error ``detail`` into one readable line.

    A domain error carries ``{"detail": "message"}``; a request validation
    error (422) carries a list of ``{"loc": [...], "msg": "..."}`` entries.

    Args:
        body: The decoded error body, or ``None`` when it was not JSON.
        body_text: The raw body, used when there is no ``detail``.

    Returns:
        The message, or the raw body text (``"(empty body)"`` when blank).
    """
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts = []
        for entry in detail:
            if isinstance(entry, dict):
                location = ".".join(str(part) for part in entry.get("loc", []))
                parts.append(f"{location}: {entry.get('msg', entry)}")
            else:
                parts.append(str(entry))
        return "; ".join(parts)
    return body_text.strip() or "(empty body)"


def _send(
    api_url: str,
    method: str,
    path: str,
    payload: Mapping[str, object] | None,
    access_token: str | None,
) -> Any:
    """Send one request and decode its JSON response.

    Args:
        api_url: Base URL of the backend.
        method: HTTP method.
        path: Path relative to the base URL.
        payload: JSON body, or ``None`` for none.
        access_token: Bearer token, or ``None`` for the login itself.

    Returns:
        The decoded JSON body.

    Raises:
        ApiError: The backend answered an HTTP error.
        RuntimeError: The backend is unreachable or returned invalid JSON.
    """
    return _send_with_status(api_url, method, path, payload, access_token).body


def _send_with_status(
    api_url: str,
    method: str,
    path: str,
    payload: Mapping[str, object] | None,
    access_token: str | None,
) -> ApiResponse:
    """Send one request and return its status and decoded JSON body.

    Args:
        api_url: Base URL of the backend.
        method: HTTP method.
        path: Path relative to the base URL.
        payload: JSON body, or ``None`` for none.
        access_token: Bearer token, or ``None`` for the login itself.

    Returns:
        The status code and the decoded body (``None`` for an empty body).

    Raises:
        ApiError: The backend answered an HTTP error.
        RuntimeError: The backend is unreachable or returned invalid JSON.
    """
    headers = {"Content-Type": "application/json"}
    if access_token is not None:
        headers["Authorization"] = f"Bearer {access_token}"
    request = Request(
        f"{api_url}{path}",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body_text = response.read().decode("utf-8")
            return ApiResponse(
                status_code=response.status,
                body=json.loads(body_text) if body_text else None,
            )
    except HTTPError as error:
        raise ApiError(
            method, path, error.code, error.read().decode("utf-8")
        ) from error
    except URLError as error:
        raise RuntimeError(f"Could not call {method} {path}: {error.reason}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{method} {path} returned invalid JSON") from error
