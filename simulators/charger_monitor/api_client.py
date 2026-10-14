"""A logged-in client of the backend's HTTP API.

Logs in with the staff phone and password from the settings and sends the
bearer token with every request. An access token lives about 15 minutes, so
a ``401`` answer triggers one fresh login and a retry. Thread-safe: the web
server answers the page from several threads. Standard library only.
"""

from __future__ import annotations

import json
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from settings import MonitorSettings

REQUEST_TIMEOUT_SECONDS = 10

# The session platform sent at login; WEB is the staff portal's.
LOGIN_PLATFORM = "WEB"


class ApiError(Exception):
    """The backend refused a request, or could not be reached.

    Attributes:
        status: The HTTP status, or ``None`` when the backend is unreachable.
        detail: The backend's message, or why it could not be reached.
    """

    def __init__(self, status: int | None, detail: str) -> None:
        """Keep the status and the message.

        Args:
            status: The HTTP status, or ``None`` when unreachable.
            detail: The message.
        """
        super().__init__(f"{status or 'no answer'}: {detail}")
        self.status = status
        self.detail = detail


class ApiClient:
    """A staff session against the backend API.

    Attributes:
        settings: Where the API is and who to log in as.
    """

    def __init__(self, settings: MonitorSettings) -> None:
        """Prepare the client; the first request logs in.

        Args:
            settings: The monitor's settings.
        """
        self.settings = settings
        self._token: str | None = None
        self._lock = threading.Lock()

    def get(self, path: str, params: dict[str, object] | None = None) -> Any:
        """Send an authenticated GET and return the decoded JSON body.

        Args:
            path: Path under the API, e.g. ``/api/v1/charging-stations``.
            params: Query parameters; ``None`` values are left out.

        Returns:
            The decoded JSON body.

        Raises:
            ApiError: The backend refused the request or the login, or is
                unreachable.
        """
        query = {k: v for k, v in (params or {}).items() if v is not None}
        if query:
            path = f"{path}?{urlencode(query)}"
        return self._authorized("GET", path, None)

    def post(self, path: str, payload: dict[str, object]) -> Any:
        """Send an authenticated JSON POST and return the decoded JSON body.

        Args:
            path: Path under the API.
            payload: The JSON body.

        Returns:
            The decoded JSON body.

        Raises:
            ApiError: The backend refused the request or the login, or is
                unreachable.
        """
        return self._authorized("POST", path, payload)

    def _authorized(
        self, method: str, path: str, payload: dict[str, object] | None
    ) -> Any:
        """Send a request with the bearer token, logging in again once on 401.

        Logs in when there is no token yet, and once more when the token is
        refused (expired), then repeats the request.

        Args:
            method: HTTP method.
            path: Path (with query string) under the API.
            payload: JSON body, or ``None``.

        Returns:
            The decoded JSON body.

        Raises:
            ApiError: The backend refused the request or the login, or is
                unreachable.
        """
        try:
            return self._send(method, path, payload, self._current_token())
        except ApiError as error:
            if error.status != 401:
                raise
        return self._send(method, path, payload, self._current_token(force_login=True))

    def _current_token(self, *, force_login: bool = False) -> str:
        """Return a valid access token, logging in when needed.

        Args:
            force_login: Log in again even if a token is held.

        Returns:
            The bearer token.

        Raises:
            ApiError: The login was refused or the backend is unreachable.
        """
        with self._lock:
            if self._token is None or force_login:
                answer = self._send(
                    "POST",
                    "/api/v1/auth/login",
                    {
                        "phone_number": self.settings.admin_phone,
                        "password": self.settings.admin_password,
                        "platform": LOGIN_PLATFORM,
                    },
                    None,
                )
                token = answer.get("access_token") if isinstance(answer, dict) else None
                if not isinstance(token, str):
                    raise ApiError(None, "the login answer carries no access token")
                self._token = token
            return self._token

    def _send(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None,
        token: str | None,
    ) -> Any:
        """Send one request and decode the JSON answer.

        Args:
            method: HTTP method.
            path: Path (with query string) under the API.
            payload: JSON body, or ``None``.
            token: Bearer token, or ``None`` for the login itself.

        Returns:
            The decoded JSON body (``None`` for an empty body).

        Raises:
            ApiError: An HTTP error status, or the backend is unreachable or
                answered something that is not JSON.
        """
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(
            f"{self.settings.api_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read()
        except HTTPError as error:
            raise ApiError(error.code, _error_detail(error.read())) from error
        except (URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise ApiError(
                None, f"backend API unreachable at {self.settings.api_url} ({reason})"
            ) from error
        if not body:
            return None
        try:
            return json.loads(body)
        except ValueError as error:
            raise ApiError(
                None, "the backend answered something that is not JSON"
            ) from error


def _error_detail(body: bytes) -> str:
    """Pull the backend's ``detail`` message out of an error body.

    Args:
        body: The raw error body.

    Returns:
        The message, flattened to one line.
    """
    text = body.decode("utf-8", errors="replace")
    try:
        detail = json.loads(text).get("detail", text)
    except (ValueError, AttributeError):
        return text.strip()[:300]
    if isinstance(detail, list):
        return "; ".join(str(item.get("msg", item)) for item in detail)[:300]
    return str(detail)[:300]
