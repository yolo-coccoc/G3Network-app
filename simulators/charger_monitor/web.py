"""The monitor's web page: a small HTTP server in its own thread.

Serves ``static/index.html`` at ``/`` and a small JSON API the page calls:

* ``GET /api/chargers`` - the chargers the staff account manages.
* ``GET /api/chargers/<id>/connection`` - online, last seen, last boot.
* ``GET /api/chargers/<id>/messages[?after=<time>]`` - the OCPP frames.
* ``GET /api/chargers/<id>/startup`` - the latest BootNotification + answer.
* ``GET /api/chargers/<id>/heartbeats`` - the heartbeat interval check.
* ``GET /api/chargers/<id>/configuration`` - the newest stored configuration.
* ``POST /api/chargers/<id>/configuration/get`` - ask the charger for it now.
* ``POST /api/chargers/<id>/configuration/change`` - body
  ``{"changes": {"<key>": "<value>", ...}}``: change those keys.

Each one calls the backend API; the bearer token stays in this process, never
in the browser. A backend error becomes a ``502`` with ``{"error": ...}`` so
the page can show it.

The POST routes change chargers, so they only accept a JSON body
(``Content-Type: application/json``) from this page's own origin: a browser
will not send that cross-site without a CORS preflight, which this server
never answers, so another site open in the same browser cannot use them.
Standard library only.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from api_client import ApiError
from configuration import ConfigurationService
from settings import MonitorSettings
from traffic import TrafficReader

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Limits on a change request, matching the backend's (key <= 50 characters,
# value <= 500) and keeping one request small.
MAX_CHANGES = 50
MAX_KEY_LENGTH = 50
MAX_VALUE_LENGTH = 500
MAX_BODY_BYTES = 64 * 1024


class BadRequestError(Exception):
    """The page sent a request the monitor will not forward."""


def parse_changes(body: Any) -> dict[str, str]:
    """Validate a change request body and return the changes to send.

    Blank values are dropped: a field left empty means "keep it as it is".

    Args:
        body: The decoded JSON body.

    Returns:
        Key -> new value, in the order sent.

    Raises:
        BadRequestError: The body is not ``{"changes": {str: str}}``, or a
            key or value is too long, or there are too many keys.
    """
    changes = body.get("changes") if isinstance(body, dict) else None
    if not isinstance(changes, dict):
        raise BadRequestError('expected {"changes": {"<key>": "<value>"}}')
    cleaned: dict[str, str] = {}
    for key, value in changes.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise BadRequestError("every key and value must be text")
        if not value.strip():
            continue
        if not 1 <= len(key) <= MAX_KEY_LENGTH or len(value) > MAX_VALUE_LENGTH:
            raise BadRequestError(f"{key[:60]!r}: key or value too long")
        cleaned[key] = value.strip()
    if len(cleaned) > MAX_CHANGES:
        raise BadRequestError(f"at most {MAX_CHANGES} changes at a time")
    return cleaned


def build_handler(
    reader: TrafficReader, configurations: ConfigurationService
) -> type[BaseHTTPRequestHandler]:
    """Create the request handler class bound to the monitor's services.

    Args:
        reader: Reads the traffic from the backend API.
        configurations: Gets and changes chargers' configuration.

    Returns:
        A handler class for ``ThreadingHTTPServer``.
    """
    get_routes: dict[str, Callable[[str, dict[str, list[str]]], Any]] = {
        "connection": lambda station_id, _: reader.connection(station_id),
        "messages": lambda station_id, query: reader.messages(
            station_id, next(iter(query.get("after", [])), None)
        ),
        "startup": lambda station_id, _: reader.startup(station_id),
        "heartbeats": lambda station_id, _: reader.heartbeats(station_id),
        "configuration": lambda station_id, _: configurations.latest(station_id),
    }

    class PageHandler(BaseHTTPRequestHandler):
        """Answers the page and its JSON API, and 404 for anything else."""

        def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
            """Route one GET request."""
            url = urlsplit(self.path)
            parts = [part for part in url.path.split("/") if part]
            if not parts or parts == ["index.html"]:
                body = (STATIC_DIR / "index.html").read_bytes()
                self._reply(HTTPStatus.OK, "text/html; charset=utf-8", body)
                return
            if parts == ["api", "chargers"]:
                self._reply_json(reader.list_chargers)
                return
            if len(parts) == 4 and parts[:2] == ["api", "chargers"]:
                station_id, route = parts[2], get_routes.get(parts[3])
                if route is not None and _is_uuid(station_id):
                    query = parse_qs(url.query)
                    self._reply_json(lambda: route(station_id, query))
                    return
            self._reply(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")

        def do_POST(self) -> None:  # noqa: N802 - name fixed by http.server
            """Route one POST request (configuration get / change)."""
            parts = [part for part in urlsplit(self.path).path.split("/") if part]
            if not (
                len(parts) == 5
                and parts[:2] == ["api", "chargers"]
                and parts[3] == "configuration"
                and parts[4] in ("get", "change")
                and _is_uuid(parts[2])
            ):
                self._reply(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")
                return
            try:
                body = self._read_json_body()
                station_id = parts[2]
                if parts[4] == "get":
                    self._reply_json(lambda: configurations.request(station_id))
                else:
                    changes = parse_changes(body)
                    if not changes:
                        raise BadRequestError("no field was filled in")
                    self._reply_json(lambda: configurations.change(station_id, changes))
            except BadRequestError as error:
                answer = json.dumps({"error": str(error)}).encode("utf-8")
                self._reply(HTTPStatus.BAD_REQUEST, "application/json", answer)

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
            host = self.headers.get("Host", "")
            if origin is not None and urlsplit(origin).netloc != host:
                raise BadRequestError("requests from another site are refused")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                raise BadRequestError("body too large")
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except ValueError as error:
                raise BadRequestError("the body is not valid JSON") from error

        def _reply_json(self, produce: Callable[[], Any]) -> None:
            """Answer with the JSON ``produce`` returns, or a 502 on a backend error.

            Args:
                produce: Calls the backend and returns the answer.
            """
            try:
                status, answer = HTTPStatus.OK, produce()
            except ApiError as error:
                status = HTTPStatus.BAD_GATEWAY
                answer = {"error": error.detail, "status": error.status}
            body = json.dumps(answer).encode("utf-8")
            self._reply(status, "application/json", body)

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
    """Tell whether a path part is a UUID (a charger ID).

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

    def __init__(
        self,
        settings: MonitorSettings,
        reader: TrafficReader,
        configurations: ConfigurationService,
    ) -> None:
        """Bind the server to the configured address (raises if it is taken).

        Args:
            settings: Where to listen.
            reader: Reads the traffic from the backend API.
            configurations: Gets and changes chargers' configuration.

        Raises:
            OSError: The address or port is not available.
        """
        self._server = ThreadingHTTPServer(
            (settings.ui_host, settings.ui_port),
            build_handler(reader, configurations),
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
