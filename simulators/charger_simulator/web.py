"""The charger's web page: a small HTTP server in its own thread.

Serves ``static/index.html`` at ``/``, the live state as JSON at
``GET /api/state`` (the page polls it), and the buttons:

* ``POST /api/guns/<n>/plug`` - body ``{"start_soc": <percent>}``: plug the
  sample car into gun ``n`` (a physical action).
* ``POST /api/guns/<n>/unplug`` - take the gun out of the car.
* ``POST /api/guns/<n>/stop`` - press Stop on the charger's screen.
* ``POST /api/speed`` - body ``{"speed": 1 | 10 | 60}``: how fast the charge
  runs (simulator only).

A button runs in the charger's asyncio loop (``run_coroutine_threadsafe``),
where every change of state happens; it answers ``{"message": ...}``, or 409
with ``{"error": ...}`` when the gun's state does not allow it.

The POST routes accept only a JSON body from this page's own origin: a
browser will not send that cross-site without a CORS preflight, which this
server never answers. It listens on ``CHARGER_UI_HOST:CHARGER_UI_PORT``
(``127.0.0.1:8090`` by default, so only this computer can open it). Standard
library only.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from state import SimulatorState
from station import ChargingStation, StationError

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Longest wait for a button to run in the charger's loop.
ACTION_TIMEOUT_SECONDS = 5.0
MAX_BODY_BYTES = 4096


class BadRequestError(Exception):
    """The page sent a request the simulator will not run."""


def number_field(body: Any, name: str, low: float, high: float) -> float:
    """Read a number from a JSON body and check its range.

    Args:
        body: The decoded body.
        name: The field.
        low: Smallest accepted value.
        high: Largest accepted value.

    Returns:
        The number.

    Raises:
        BadRequestError: Missing, not a number, or out of range.
    """
    value = body.get(name) if isinstance(body, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BadRequestError(f"{name} must be a number")
    if not low <= value <= high:
        raise BadRequestError(f"{name} must be between {low:g} and {high:g}")
    return float(value)


def build_handler(
    state: SimulatorState, station: ChargingStation
) -> type[BaseHTTPRequestHandler]:
    """Create the request handler class bound to one simulator.

    Args:
        state: The connection state the page shows.
        station: The charger's guns; the buttons act on it.

    Returns:
        A handler class for ``ThreadingHTTPServer``.
    """

    class PageHandler(BaseHTTPRequestHandler):
        """Answers the page, its state, its buttons, and 404 otherwise."""

        def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
            """Serve the page or the state."""
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                body = (STATIC_DIR / "index.html").read_bytes()
                self._reply(HTTPStatus.OK, "text/html; charset=utf-8", body)
            elif path == "/api/state":
                snapshot = state.snapshot() | {"station": station.snapshot()}
                self._reply_json(HTTPStatus.OK, snapshot)
            else:
                self._reply(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")

        def do_POST(self) -> None:  # noqa: N802 - name fixed by http.server
            """Run one button."""
            parts = [part for part in urlsplit(self.path).path.split("/") if part]
            try:
                body = self._read_json_body()
                action = self._action(parts, body)
            except BadRequestError as error:
                self._reply_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            if action is None:
                self._reply(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")
                return
            loop = station.loop
            if loop is None:
                self._reply_json(
                    HTTPStatus.SERVICE_UNAVAILABLE, {"error": "the charger is starting"}
                )
                return

            async def run() -> str:
                return action()

            try:
                message = asyncio.run_coroutine_threadsafe(run(), loop).result(
                    ACTION_TIMEOUT_SECONDS
                )
            except StationError as error:
                self._reply_json(HTTPStatus.CONFLICT, {"error": str(error)})
                return
            self._reply_json(HTTPStatus.OK, {"message": message})

        def _action(self, parts: list[str], body: Any) -> Callable[[], str] | None:
            """Find the station method a POST path asks for.

            Args:
                parts: The path's parts.
                body: The decoded JSON body.

            Returns:
                A call that runs the button, or ``None`` for an unknown path.

            Raises:
                BadRequestError: A gun number or a body field is invalid.
            """
            if parts == ["api", "speed"]:
                speed = int(number_field(body, "speed", 1, 1000))
                return lambda: station.set_speed(speed)
            if len(parts) != 4 or parts[:2] != ["api", "guns"]:
                return None
            if not parts[2].isdigit():
                raise BadRequestError("the gun must be a number")
            number = int(parts[2])
            if parts[3] == "plug":
                start_soc = number_field(body, "start_soc", 0, 99)
                return lambda: station.plug(number, start_soc)
            if parts[3] == "unplug":
                return lambda: station.unplug(number)
            if parts[3] == "stop":
                return lambda: station.stop(number)
            return None

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

        def _reply_json(self, status: HTTPStatus, answer: Any) -> None:
            """Send a JSON response.

            Args:
                status: The HTTP status.
                answer: The JSON-ready answer.
            """
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
            """Keep the console for the charger's own log (no access log).

            Args:
                format: The library's format string.
                *args: Its arguments.
            """

    return PageHandler


class WebPage:
    """The running web server.

    Attributes:
        url: The address to open in a browser.
    """

    def __init__(self, state: SimulatorState, station: ChargingStation) -> None:
        """Bind the server to the configured address (raises if it is taken).

        Args:
            state: The connection state the page shows.
            station: The charger's guns; the buttons act on it.

        Raises:
            OSError: The address or port is not available.
        """
        settings = state.settings
        self._server = ThreadingHTTPServer(
            (settings.ui_host, settings.ui_port), build_handler(state, station)
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
