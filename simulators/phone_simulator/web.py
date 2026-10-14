"""The phone's page: a small HTTP server in its own thread.

Serves ``static/index.html`` at ``/`` and the app's calls as a small JSON
API; the driver's tokens stay in this process, never in the browser:

* ``GET  /api/state`` - logged in or not, the profile, the login to prefill.
* ``POST /api/login`` ``{"phone": ..., "password": ...}`` / ``POST /api/logout``.
* ``GET  /api/home`` - wallet, recent charges, a running charge.
* ``POST /api/scan`` ``{"charger_code": ..., "gun": n | null}``.
* ``GET  /api/charges/<id>`` - the live charge; ``POST /api/charges/<id>/stop``;
  ``GET /api/charges/<id>/receipt``.
* ``GET  /api/wallet``; ``POST /api/wallet/top-up`` ``{"amount": n}``;
  ``POST /api/wallet/bank-pay`` ``{"transfer_code": ...}`` (simulated bank).

A refusal by the backend is answered ``400`` (``401`` when the session ended)
with ``{"error": <the backend's message>}``; an unreachable backend ``502``.
The POST routes accept only a JSON body from this page's own origin: a
browser will not send that cross-site without a CORS preflight, which this
server never answers. Standard library only.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from api_client import ApiError
from phone import NotDriverError, PhoneApp

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 8192


class BadRequestError(Exception):
    """The page sent a request the phone will not forward."""


def text_field(body: Any, name: str, max_length: int) -> str:
    """Read a non-empty text field from a JSON body.

    Args:
        body: The decoded body.
        name: The field.
        max_length: Longest accepted value.

    Returns:
        The text, stripped.

    Raises:
        BadRequestError: Missing, not text, empty or too long.
    """
    value = body.get(name) if isinstance(body, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise BadRequestError(f"{name} is required")
    if len(value) > max_length:
        raise BadRequestError(f"{name} is too long")
    return value.strip()


def build_handler(app: PhoneApp) -> type[BaseHTTPRequestHandler]:
    """Create the request handler class bound to one phone.

    Args:
        app: The phone's app logic.

    Returns:
        A handler class for ``ThreadingHTTPServer``.
    """

    class PageHandler(BaseHTTPRequestHandler):
        """Answers the page and the app's calls, and 404 otherwise."""

        def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
            """Route one GET request."""
            parts = [part for part in urlsplit(self.path).path.split("/") if part]
            if not parts or parts == ["index.html"]:
                body = (STATIC_DIR / "index.html").read_bytes()
                self._reply(HTTPStatus.OK, "text/html; charset=utf-8", body)
            elif parts == ["api", "state"]:
                self._answer(app.state)
            elif parts == ["api", "home"]:
                self._answer(app.home)
            elif parts == ["api", "wallet"]:
                self._answer(app.wallet)
            elif (
                len(parts) >= 3
                and parts[:2] == ["api", "charges"]
                and _is_uuid(parts[2])
            ):
                session_id = parts[2]
                if len(parts) == 3:
                    self._answer(lambda: app.charge(session_id))
                elif parts[3:] == ["receipt"]:
                    self._answer(lambda: app.receipt(session_id))
                else:
                    self._not_found()
            else:
                self._not_found()

        def do_POST(self) -> None:  # noqa: N802 - name fixed by http.server
            """Route one POST request."""
            parts = [part for part in urlsplit(self.path).path.split("/") if part]
            try:
                body = self._read_json_body()
                action = self._post_action(parts, body)
            except BadRequestError as error:
                self._reply_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            if action is None:
                self._not_found()
                return
            self._answer(action)

        def _post_action(self, parts: list[str], body: Any) -> Callable[[], Any] | None:
            """Find the app call a POST path asks for.

            Args:
                parts: The path's parts.
                body: The decoded JSON body.

            Returns:
                The call, or ``None`` for an unknown path.

            Raises:
                BadRequestError: A body field is missing or invalid.
            """
            if parts == ["api", "login"]:
                phone = text_field(body, "phone", 20)
                password = text_field(body, "password", 128)
                return lambda: app.login(phone, password)
            if parts == ["api", "logout"]:
                return app.logout
            if parts == ["api", "scan"]:
                code = text_field(body, "charger_code", 255)
                gun = body.get("gun")
                if gun is not None and (not isinstance(gun, int) or gun < 1):
                    raise BadRequestError("gun must be a number from 1")
                return lambda: app.scan(code, gun)
            if parts == ["api", "wallet", "top-up"]:
                amount = body.get("amount") if isinstance(body, dict) else None
                if (
                    not isinstance(amount, int)
                    or isinstance(amount, bool)
                    or amount < 1
                ):
                    raise BadRequestError("amount must be a whole number of dong")
                return lambda: app.top_up(amount)
            if parts == ["api", "wallet", "bank-pay"]:
                code = text_field(body, "transfer_code", 64)
                return lambda: app.bank_pay(code)
            if (
                len(parts) == 4
                and parts[:2] == ["api", "charges"]
                and _is_uuid(parts[2])
                and parts[3] == "stop"
            ):
                session_id = parts[2]
                return lambda: app.stop(session_id)
            return None

        def _answer(self, produce: Callable[[], Any]) -> None:
            """Answer with what ``produce`` returns, or the backend's refusal.

            Args:
                produce: Calls the backend and returns the answer.
            """
            try:
                answer = produce()
            except NotDriverError as error:
                self._reply_json(HTTPStatus.FORBIDDEN, {"error": str(error)})
                return
            except ApiError as error:
                if error.status is None or error.status >= 500:
                    status = HTTPStatus.BAD_GATEWAY
                elif error.status == 401:
                    status = HTTPStatus.UNAUTHORIZED
                else:
                    status = HTTPStatus.BAD_REQUEST
                self._reply_json(
                    status, {"error": error.detail, "status": error.status}
                )
                return
            self._reply_json(HTTPStatus.OK, answer)

        def _read_json_body(self) -> Any:
            """Read a POST body, refusing anything but same-origin JSON.

            Returns:
                The decoded body.

            Raises:
                BadRequestError: Not ``application/json``, from another
                    origin, too large, or not valid JSON.
            """
            content_type = self.headers.get("Content-Type", "")
            if content_type.split(";")[0].strip() != "application/json":
                raise BadRequestError("only application/json is accepted")
            origin = self.headers.get("Origin")
            if origin is not None and urlsplit(origin).netloc != self.headers.get(
                "Host", ""
            ):
                raise BadRequestError("requests from another site are refused")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                raise BadRequestError("body too large")
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except ValueError as error:
                raise BadRequestError("the body is not valid JSON") from error

        def _not_found(self) -> None:
            """Answer 404."""
            self._reply(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")

        def _reply_json(self, status: HTTPStatus, answer: Any) -> None:
            """Send a JSON response.

            Args:
                status: The HTTP status.
                answer: The JSON-ready answer.
            """
            self._reply(status, "application/json", json.dumps(answer).encode("utf-8"))

        def _reply(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
            """Send a complete response that the browser must not cache.

            Args:
                status: The HTTP status.
                content_type: The ``Content-Type`` header.
                body: The response body.
            """
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Keep the console quiet (no access log).

            Args:
                format: The library's format string.
                *args: Its arguments.
            """

    return PageHandler


def _is_uuid(text: str) -> bool:
    """Tell whether a path part is a UUID (a session ID).

    Args:
        text: The path part.

    Returns:
        ``True`` for a valid UUID.
    """
    try:
        UUID(text)
    except ValueError:
        return False
    return True


class WebPage:
    """The running web server.

    Attributes:
        url: The address to open in a browser.
    """

    def __init__(self, app: PhoneApp) -> None:
        """Bind the server to the configured address (raises if it is taken).

        Args:
            app: The phone's app logic.

        Raises:
            OSError: The address or port is not available.
        """
        settings = app.settings
        self._server = ThreadingHTTPServer(
            (settings.ui_host, settings.ui_port), build_handler(app)
        )
        self._server.daemon_threads = True
        shown_host = "127.0.0.1" if settings.ui_host == "0.0.0.0" else settings.ui_host
        self.url = f"http://{shown_host}:{settings.ui_port}/"
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="web-page", daemon=True
        )

    def start(self) -> None:
        """Start serving in the background."""
        self._thread.start()

    def stop(self) -> None:
        """Stop serving and release the port."""
        self._server.shutdown()
        self._server.server_close()
