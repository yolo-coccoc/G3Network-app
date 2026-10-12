"""Minimal authenticated JSON client the simulator seed scripts share.

Every API route except login needs a bearer token, so the scripts log in as
the first HEAD_ADMIN that ``make identity-bootstrap`` creates: the phone and
password come from ``IDENTITY_BOOTSTRAP_ADMIN_PHONE`` and
``IDENTITY_BOOTSTRAP_ADMIN_PASSWORD`` in ``backend/.env`` (read through the
backend settings, so the scripts must run from ``backend/`` with its venv).
Standard library only; no retry, no token refresh (a seed run is shorter than
the access-token lifetime).
"""

import json
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.libs.common.config import settings

DEFAULT_API_URL = "http://localhost:8000"
REQUEST_TIMEOUT_SECONDS = 10


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

    def get(self, path: str) -> Any:
        """Send an authenticated GET and return the JSON response.

        Args:
            path: Path relative to the base URL, query string included.

        Returns:
            The decoded JSON body.

        Raises:
            RuntimeError: The request failed or returned an HTTP error.
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
            RuntimeError: The request failed, returned an HTTP error, or the
                body is not a JSON object.
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
        RuntimeError: The bootstrap phone or password is not set in
            ``backend/.env``, or the login failed (run
            ``make identity-bootstrap`` first).
    """
    phone_number = settings.IDENTITY_BOOTSTRAP_ADMIN_PHONE
    password = settings.IDENTITY_BOOTSTRAP_ADMIN_PASSWORD
    if not phone_number or not password:
        raise RuntimeError(
            "Set IDENTITY_BOOTSTRAP_ADMIN_PHONE and IDENTITY_BOOTSTRAP_ADMIN_PASSWORD "
            "in backend/.env and run `make identity-bootstrap` first"
        )
    response = _send(
        api_url.rstrip("/"),
        "POST",
        "/api/v1/auth/login",
        {"phone_number": phone_number, "password": password, "platform": "WEB"},
        None,
    )
    return ApiClient(api_url, response["access_token"])


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
        RuntimeError: The backend is unreachable, answered an HTTP error, or
            returned invalid JSON.
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
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8")
        raise RuntimeError(
            f"{method} {path} returned HTTP {error.code}: {detail}"
        ) from error
    except URLError as error:
        raise RuntimeError(f"Could not call {method} {path}: {error.reason}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{method} {path} returned invalid JSON") from error
