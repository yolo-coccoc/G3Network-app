"""The charger's web page: a small HTTP server in its own thread.

Serves ``static/index.html`` at ``/`` and the live state as JSON at
``/api/state``; the page polls it. Standard library only. It listens on
``CHARGER_UI_HOST:CHARGER_UI_PORT`` (``127.0.0.1:8090`` by default, so only
this computer can open it). Read-only for now: the buttons that operate the
charger come later.
"""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from state import SimulatorState

STATIC_DIR = Path(__file__).resolve().parent / "static"


def build_handler(state: SimulatorState) -> type[BaseHTTPRequestHandler]:
    """Create the request handler class bound to one simulator's state.

    Args:
        state: The state the page shows.

    Returns:
        A handler class for ``ThreadingHTTPServer``.
    """

    class PageHandler(BaseHTTPRequestHandler):
        """Answers the page, its state endpoint, and 404 for anything else."""

        def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
            """Serve the page or the state."""
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                body = (STATIC_DIR / "index.html").read_bytes()
                self._reply(HTTPStatus.OK, "text/html; charset=utf-8", body)
            elif path == "/api/state":
                body = json.dumps(state.snapshot()).encode("utf-8")
                self._reply(HTTPStatus.OK, "application/json", body)
            else:
                self._reply(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")

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

    def __init__(self, state: SimulatorState) -> None:
        """Bind the server to the configured address (raises if it is taken).

        Args:
            state: The state the page shows.

        Raises:
            OSError: The address or port is not available.
        """
        settings = state.settings
        self._server = ThreadingHTTPServer(
            (settings.ui_host, settings.ui_port), build_handler(state)
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
