"""A logged-in session against the backend's HTTP API, as a phone app keeps one.

Logs in with a phone number and password (platform ``ANDROID``), sends the
bearer token with every request, and when the backend refuses an expired
access token (``401``) trades the refresh token for a new pair
(``/auth/refresh``) and repeats the request once, as the real app will. A
``WEB`` session gets no refresh token in the body (the backend sets it as an
``HttpOnly`` cookie, which this client does not keep), so it holds only the
access token and must log in again once that expires.
Thread-safe: the page's requests are answered from several threads. Standard
library only.
"""

from __future__ import annotations

import json
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REQUEST_TIMEOUT_SECONDS = 15


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


class ApiSession:
    """One person's logged-in session (a driver on the phone, or the bank's admin).

    Attributes:
        api_url: Base URL of the backend API, without a trailing slash.
        platform: The session platform sent at login (``ANDROID``, ``WEB``).
    """

    def __init__(self, api_url: str, platform: str) -> None:
        """Prepare a session that is not logged in yet.

        Args:
            api_url: Base URL of the backend API.
            platform: The session platform sent at login.
        """
        self.api_url = api_url
        self.platform = platform
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._lock = threading.Lock()

    @property
    def is_logged_in(self) -> bool:
        """Whether the session holds tokens."""
        return self._access_token is not None

    def login(self, phone_number: str, password: str) -> None:
        """Log in and keep the token pair.

        Args:
            phone_number: The login phone number.
            password: The password.

        Raises:
            ApiError: Wrong credentials (401), lockout (423), locked account
                (403), or the backend is unreachable.
        """
        answer = send(
            self.api_url,
            "POST",
            "/api/v1/auth/login",
            {
                "phone_number": phone_number,
                "password": password,
                "platform": self.platform,
            },
            None,
        )
        self._keep_tokens(answer)

    def logout(self) -> None:
        """Log out this device on the backend (best effort) and forget the tokens."""
        with self._lock:
            token = self._access_token
            self._access_token = self._refresh_token = None
        if token is not None:
            try:
                send(self.api_url, "POST", "/api/v1/auth/logout", {}, token)
            except ApiError:
                pass  # the tokens are forgotten either way

    def get(self, path: str, params: dict[str, object] | None = None) -> Any:
        """Send an authenticated GET and return the decoded JSON body.

        Args:
            path: Path under the API, e.g. ``/api/v1/wallets/me``.
            params: Query parameters; ``None`` values are left out.

        Returns:
            The decoded JSON body.

        Raises:
            ApiError: The backend refused the request, or is unreachable.
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
            The decoded JSON body (``None`` for an empty answer).

        Raises:
            ApiError: The backend refused the request, or is unreachable.
        """
        return self._authorized("POST", path, payload)

    def _authorized(
        self, method: str, path: str, payload: dict[str, object] | None
    ) -> Any:
        """Send with the access token; on 401 refresh the pair once and retry.

        Args:
            method: HTTP method.
            path: Path (with query string) under the API.
            payload: JSON body, or ``None``.

        Returns:
            The decoded JSON body.

        Raises:
            ApiError: Not logged in (401), the refresh failed, the backend
                refused the request, or it is unreachable.
        """
        with self._lock:
            token = self._access_token
        if token is None:
            raise ApiError(401, "not logged in")
        try:
            return send(self.api_url, method, path, payload, token)
        except ApiError as error:
            if error.status != 401:
                raise
        return send(self.api_url, method, path, payload, self._refresh())

    def _refresh(self) -> str:
        """Trade the refresh token for a new pair.

        Returns:
            The new access token.

        Raises:
            ApiError: No refresh token, or the backend refused it (the session
                ended; the phone must log in again).
        """
        with self._lock:
            refresh_token = self._refresh_token
        if refresh_token is None:
            raise ApiError(401, "the session ended; log in again")
        try:
            answer = send(
                self.api_url,
                "POST",
                "/api/v1/auth/refresh",
                {"refresh_token": refresh_token},
                None,
            )
        except ApiError as error:
            with self._lock:
                self._access_token = self._refresh_token = None
            raise ApiError(
                401, f"the session ended; log in again ({error.detail})"
            ) from error
        return self._keep_tokens(answer)

    def _keep_tokens(self, answer: Any) -> str:
        """Store a ``TokenResponse``'s pair.

        Args:
            answer: The decoded login or refresh answer.

        Returns:
            The access token.

        Raises:
            ApiError: The answer carries no access token.
        """
        access = answer.get("access_token") if isinstance(answer, dict) else None
        refresh = answer.get("refresh_token") if isinstance(answer, dict) else None
        if not isinstance(access, str):
            raise ApiError(None, "the backend answered without an access token")
        if not isinstance(refresh, str):
            refresh = None  # a WEB session: the backend set it as a cookie
        with self._lock:
            self._access_token, self._refresh_token = access, refresh
        return access


def send(
    api_url: str,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    token: str | None,
) -> Any:
    """Send one request and decode the JSON answer.

    Args:
        api_url: Base URL of the backend API.
        method: HTTP method.
        path: Path (with query string) under the API.
        payload: JSON body, or ``None``.
        token: Bearer token, or ``None``.

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
    request = Request(f"{api_url}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
    except HTTPError as error:
        raise ApiError(error.code, error_detail(error.read())) from error
    except (URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise ApiError(
            None, f"the backend API is unreachable at {api_url} ({reason})"
        ) from error
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError as error:
        raise ApiError(
            None, "the backend answered something that is not JSON"
        ) from error


def error_detail(body: bytes) -> str:
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
