"""Browser dashboard for a charging test day: one page instead of many terminals.

Decision D12 of ``docs/planners/backend-charging-test-day.md`` (TT-22). A small
local web server, standard library only, that serves ``dashboard.html`` and
answers its JSON calls by calling the backend API with the same code as the
command-line tools (``g3_api``, ``test_day_common``). The browser never talks
to the backend: the API has no CORS setting and the credentials stay in this
process. The page shows, for one charger at a time:

* the charger and its guns (``GET /charging-stations/{id}/connection`` and
  ``/status``), as ``station_console.py status``;
* the live OCPP message feed (``/ocpp-messages``), as ``station_console.py
  messages --follow``; tokens masked (IS-07);
* two driver panels (test drivers 1 and 2): scan, live session, stop, then the
  receipt, the bill and the wallet, as ``driver_app.py``;
* the operator's settings (configuration, ChangeConfiguration), commands and
  sessions, with force-close, as ``station_console.py``;
* the OCPP 1.6J charger simulator in its interactive ``charger`` scenario,
  started as a child process and driven by buttons (``plug 1``, ``start 1``...
  written to its standard input); its output is shown on the page.

Safety: the server listens on ``127.0.0.1`` only (it acts as the HEAD_ADMIN);
a POST must carry the ``X-Test-Day-Dashboard`` header (a browser cannot add it
from another site without a CORS preflight, which this server never allows),
and the ``Host`` header must be a local name (no DNS rebinding). The simulator
never starts as the test day's charger (``TEST_DAY_CHARGER_ID``), online or
not, nor as any charger online with a firmware other than the simulator's, so
it never takes over a real charger's connection.

Routes: every ``get_*`` / ``post_*`` function may raise ``BadRequestError``
(answered 400), ``LookupError`` (an unknown charger, 404), ``ApiError`` (the
backend's own status and message) or ``RuntimeError`` (backend unreachable,
502); ``DashboardRequestHandler._run`` turns each into a JSON error.

Every backend call is appended to the evidence files
``logs/test-day/dashboard-<UTC date>.jsonl`` (operator) and
``dashboard_driver-<UTC date>.jsonl`` (drivers), as the other tools do. One
lock serializes the backend calls: the sessions are not thread-safe and the
page polls only every few seconds. Run it with the folder's venv Python
(``.venv\\Scripts\\python dashboard.py`` on Windows), then open the printed URL.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import webbrowser
from collections import deque
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from driver_app import APP_PLATFORM, FINAL_STATUSES
from g3_api import (
    TOOLS_DIR,
    ApiClient,
    ApiError,
    login,
    login_as_bootstrap_admin,
    resolve_api_url,
    tool_setting,
)
from station_console import (
    MAX_PAGE_SIZE,
    SESSIONS_PATH,
    STATIONS_PATH,
    summarize_frame,
)
from test_day_common import (
    EvidenceLog,
    ToolSession,
    build_test_driver_phone,
    mask_secrets,
    resolve_test_driver_password,
)

DEFAULT_PORT = 8080
PAGE_FILE = TOOLS_DIR / "dashboard.html"
SIMULATOR_FILE = TOOLS_DIR / "ocpp16_charge_point_simulator.py"
# The simulated charger the page offers by default: the laptop's own identity,
# never the real charger's (CLAUDE.md rule 1).
DEFAULT_SIM_IDENTITY = "SIM-LAPTOP-01"
# The firmware the simulator reports by default (its ``--firmware``); a
# charger online with any other firmware is taken to be the real one.
SIMULATOR_FIRMWARE = "OCPP_L4.05_SIM"
# Header every POST must carry (see the module docstring, "Safety").
CSRF_HEADER = "X-Test-Day-Dashboard"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
# Largest POST body accepted; the page sends a few hundred bytes at most.
MAX_BODY_BYTES = 64 * 1024
# Test drivers the page has a panel for (seed_test_drivers.py numbering).
DRIVER_NUMBERS = (1, 2)
# Pages of new frames one feed poll reads at most (100 frames each).
MAX_FEED_PAGES = 5
# Simulator output lines kept in memory for the page.
SIM_OUTPUT_LINES = 2000
# How long a "quit" may take before the simulator is terminated.
SIM_QUIT_SECONDS = 10.0
SESSION_STATUSES = frozenset({"PENDING", "ACTIVE", "COMPLETED", "ABANDONED"})


class BadRequestError(ValueError):
    """The page sent a request this server cannot act on (answered 400)."""


class DashboardBackend:
    """The logged-in backend sessions of the dashboard.

    Logs in lazily: the HEAD_ADMIN on the first operator call, each test
    driver on that driver's first call; a session logs in again by itself when
    its token expires (``ToolSession``).

    Attributes:
        api_url: Base URL of the backend API (through the SSH tunnel).
    """

    def __init__(self, api_url: str) -> None:
        """Keep the URL; no login happens yet.

        Args:
            api_url: Base URL of the backend API.
        """
        self.api_url = api_url
        self._lock = threading.Lock()
        self._admin: ToolSession | None = None
        self._drivers: dict[int, ToolSession] = {}
        self._station_ids: dict[str, str] = {}
        self._admin_evidence = EvidenceLog("dashboard", None)
        self._driver_evidence = EvidenceLog("dashboard_driver", None)

    def admin_call(
        self,
        event: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        log_body: bool = True,
    ) -> Any:
        """Call the API as the bootstrap HEAD_ADMIN.

        Args:
            event: Evidence event name.
            method: HTTP method.
            path: API path, query string included.
            payload: JSON body, or ``None``.
            log_body: Whether the answer's body goes into the evidence.

        Returns:
            The decoded JSON body.

        Raises:
            ApiError: The backend answered an HTTP error.
            RuntimeError: Missing admin credentials, or the backend is
                unreachable.
        """
        with self._lock:
            if self._admin is None:
                api_url = self.api_url
                self._admin = ToolSession(
                    lambda: login_as_bootstrap_admin(api_url), self._admin_evidence
                )
            return self._admin.call(event, method, path, payload, log_body=log_body)

    def driver_call(
        self,
        driver_number: int,
        event: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> Any:
        """Call the API as test driver ``driver_number``, with the app's platform.

        Args:
            driver_number: The test driver (1-based).
            event: Evidence event name.
            method: HTTP method.
            path: API path, query string included.
            payload: JSON body, or ``None``.

        Returns:
            The decoded JSON body.

        Raises:
            ApiError: The backend answered an HTTP error (401: wrong password).
            RuntimeError: The backend is unreachable.
        """
        with self._lock:
            session = self._drivers.get(driver_number)
            if session is None:
                api_url = self.api_url
                phone_number = build_test_driver_phone(driver_number)
                password = resolve_test_driver_password(None)

                def log_in() -> ApiClient:
                    """Log the test driver in as the app does.

                    Returns:
                        A client holding the driver's token.
                    """
                    return login(api_url, phone_number, password, platform=APP_PLATFORM)

                session = ToolSession(log_in, self._driver_evidence)
                self._drivers[driver_number] = session
            return session.call(event, method, path, payload)

    def resolve_station_id(self, charger: str) -> str:
        """Find a charger's internal ID by OCPP identity or registered serial.

        The answer is cached for the process: a station's ID never changes.

        Args:
            charger: The OCPP identity (or the registered serial number).

        Returns:
            The station ID.

        Raises:
            BadRequestError: No charger was named.
            LookupError: No active station has that code.
            ApiError: The backend refused the search.
            RuntimeError: The backend is unreachable.
        """
        if not charger:
            raise BadRequestError("Name a charger")
        cached = self._station_ids.get(charger)
        if cached:
            return cached
        query = urlencode({"q": charger, "page_size": MAX_PAGE_SIZE})
        page = self.admin_call("resolve_station", "GET", f"{STATIONS_PATH}?{query}")
        items = page.get("items", [])
        for field_name in ("ocpp_identity", "registered_serial_number"):
            for item in items:
                if item.get(field_name) == charger:
                    station_id = str(item["station_id"])
                    self._station_ids[charger] = station_id
                    return station_id
        raise LookupError(f"No active charging station has the code {charger!r}")


class SimulatorProcess:
    """The OCPP 1.6J simulator, run as a child process in its ``charger`` scenario.

    One simulator at a time. Its standard output is read line by line by a
    background thread into a bounded buffer; each line gets a running number
    so the page asks only for the lines after the last one it showed.

    Attributes:
        identity: The OCPP identity it runs as, or ``None`` before the first
            start.
    """

    def __init__(self) -> None:
        """Create the controller; no process runs yet."""
        self.identity: str | None = None
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._lines: deque[tuple[int, str]] = deque(maxlen=SIM_OUTPUT_LINES)
        self._next_line_number = 1

    @property
    def is_running(self) -> bool:
        """Whether the simulator process is alive.

        Returns:
            ``True`` while it runs.
        """
        return self._process is not None and self._process.poll() is None

    def start(self, identity: str, start_mode: str, sample_interval: float) -> None:
        """Start the simulator as ``identity``, connected to ``G3_OCPP_URL``.

        Args:
            identity: OCPP identity of an already provisioned station.
            start_mode: ``auto`` (charge as soon as plugged and authorized) or
                ``screen`` (only after ``start N``).
            sample_interval: Seconds between meter readings.

        Raises:
            BadRequestError: A simulator already runs, or an argument is
                invalid.
            OSError: The process could not be started.
        """
        if start_mode not in {"auto", "screen"}:
            raise BadRequestError("start mode is auto or screen")
        if sample_interval <= 0:
            raise BadRequestError("the sample interval must be positive")
        with self._lock:
            if self.is_running:
                raise BadRequestError(f"The simulator already runs as {self.identity}")
            command = [
                sys.executable,
                "-u",
                str(SIMULATOR_FILE),
                "--scenario",
                "charger",
                "--identity",
                identity,
                "--start",
                start_mode,
                "--sample-interval",
                f"{sample_interval:g}",
            ]
            # UTF-8 both ways: the simulator prints "…" and a Windows console
            # code page would otherwise fail on it.
            environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            self._process = subprocess.Popen(
                command,
                cwd=TOOLS_DIR,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=environment,
            )
            self.identity = identity
            self._append(f"--- simulator started as {identity} ({start_mode}) ---")
            threading.Thread(
                target=self._read_output, args=(self._process,), daemon=True
            ).start()

    def send(self, text: str) -> None:
        """Type one command into the simulator (e.g. ``plug 1``).

        Args:
            text: The command line, without the newline.

        Raises:
            BadRequestError: No simulator runs, or the text is empty.
        """
        command = " ".join(text.split())
        if not command:
            raise BadRequestError("Empty command")
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise BadRequestError("The simulator is not running")
            self._append(f"> {command}")
            stdin = process.stdin
        # Written outside the lock: should the pipe ever fill, a blocked write
        # must not also block the output reader, which needs the lock.
        try:
            stdin.write(command + "\n")
            stdin.flush()
        except OSError as error:
            raise BadRequestError("The simulator is not running") from error

    def quit(self) -> None:
        """Ask the simulator to quit; terminate, then kill it if it lingers.

        Side Effects:
            On a clean ``quit`` the simulator saves its meters itself. When it
            does not exit within ``SIM_QUIT_SECONDS`` it is terminated, then
            killed after the same delay; the meters are then not saved.
        """
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write("quit\n")
                process.stdin.flush()
            process.wait(timeout=SIM_QUIT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=SIM_QUIT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def output(self, after: int) -> dict[str, Any]:
        """Return the output lines after line number ``after``.

        Args:
            after: The last line number the page already shows (0: all).

        Returns:
            ``running``, ``identity`` and ``lines`` (``[number, text]`` pairs).
        """
        with self._lock:
            lines = [[number, text] for number, text in self._lines if number > after]
        return {"running": self.is_running, "identity": self.identity, "lines": lines}

    def _append(self, text: str) -> None:
        """Add one numbered line to the buffer (caller may hold the lock).

        Args:
            text: The line, without its newline.
        """
        self._lines.append((self._next_line_number, text))
        self._next_line_number += 1

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        """Copy the simulator's output into the buffer until it exits.

        Args:
            process: The simulator process.
        """
        assert process.stdout is not None
        for line in process.stdout:
            with self._lock:
                self._append(line.rstrip("\n"))
        code = process.wait()
        with self._lock:
            self._append(f"--- simulator exited (code {code}) ---")


def _text(value: object, name: str) -> str:
    """Read a required text field from a request body.

    Args:
        value: The JSON value.
        name: The field's name, for the error message.

    Returns:
        The stripped text.

    Raises:
        BadRequestError: The field is missing or empty.
    """
    if not isinstance(value, str) or not value.strip():
        raise BadRequestError(f"{name} is required")
    return value.strip()


def _driver_number(value: object) -> int:
    """Read a test driver number the page offers.

    Args:
        value: The JSON or query value.

    Returns:
        The driver number.

    Raises:
        BadRequestError: Not one of ``DRIVER_NUMBERS``.
    """
    try:
        number = int(str(value))
    except ValueError as error:
        raise BadRequestError("driver must be a number") from error
    if number not in DRIVER_NUMBERS:
        raise BadRequestError(f"driver must be one of {DRIVER_NUMBERS}")
    return number


def _station_path(backend: DashboardBackend, charger: str) -> str:
    """Build the API path of the named charger.

    Args:
        backend: The dashboard's backend sessions.
        charger: OCPP identity or registered serial.

    Returns:
        ``/api/v1/charging-stations/{station_id}``.
    """
    return f"{STATIONS_PATH}/{backend.resolve_station_id(charger)}"


def _summarize_message(message: dict[str, Any]) -> dict[str, Any]:
    """Reduce a logged OCPP frame to what the feed shows, tokens masked.

    Args:
        message: ``ChargingOcppMessageResponse`` body (with ``raw_frame``).

    Returns:
        ``message_id``, ``occurred_at``, ``direction``, ``action``,
        ``ocpp_message_id``, ``message_type`` and the masked ``summary``.
    """
    message_type, summary = summarize_frame(message.get("raw_frame"))
    return {
        "message_id": message.get("message_id"),
        "occurred_at": message.get("occurred_at"),
        "direction": message.get("direction"),
        "action": message.get("action"),
        "ocpp_message_id": message.get("ocpp_message_id"),
        "message_type": message_type,
        "summary": summary,
    }


def get_settings(context: "DashboardContext", query: dict[str, str]) -> Any:
    """Answer the page's start-up settings.

    Args:
        context: The dashboard state.
        query: Unused.

    Returns:
        The API URL, the default charger, the simulator identity and drivers.
    """
    return {
        "api_url": context.backend.api_url,
        "default_charger": tool_setting("TEST_DAY_CHARGER_ID") or "",
        "sim_identity": context.sim_identity,
        "drivers": list(DRIVER_NUMBERS),
    }


def get_station(context: "DashboardContext", query: dict[str, str]) -> Any:
    """Answer the charger's connection facts and gun statuses.

    Args:
        context: The dashboard state.
        query: ``charger``.

    Returns:
        ``station_id``, ``connection`` and ``status`` bodies.
    """
    path = _station_path(context.backend, query.get("charger", ""))
    return {
        "station_id": path.rsplit("/", 1)[-1],
        "connection": context.backend.admin_call(
            "connection", "GET", f"{path}/connection", log_body=False
        ),
        "status": context.backend.admin_call(
            "status", "GET", f"{path}/status", log_body=False
        ),
    }


def get_messages(context: "DashboardContext", query: dict[str, str]) -> Any:
    """Answer logged OCPP frames, oldest first, summarized and masked.

    Without ``since``: the newest ``limit`` frames. With ``since`` (an ISO
    time): every frame at or after it, up to ``MAX_FEED_PAGES`` pages; the
    page drops the ones it already shows by ``message_id``.

    Args:
        context: The dashboard state.
        query: ``charger``, optional ``since`` and ``limit``.

    Returns:
        ``items`` (oldest first) and ``total``.
    """
    path = _station_path(context.backend, query.get("charger", ""))
    since = query.get("since") or None
    page_size = MAX_PAGE_SIZE if since else min(int(query.get("limit", 50)), 100)
    collected: list[dict[str, Any]] = []
    total = 0
    for page_number in range(1, (MAX_FEED_PAGES if since else 1) + 1):
        parameters: dict[str, object] = {
            "page": page_number,
            "page_size": page_size,
            "include_raw_frame": "true",
        }
        if since:
            parameters["occurred_from"] = since
        page = context.backend.admin_call(
            "messages",
            "GET",
            f"{path}/ocpp-messages?{urlencode(parameters)}",
            log_body=False,
        )
        items = page.get("items", [])
        total = int(page.get("total", 0))
        collected.extend(items)
        if not items or len(collected) >= total:
            break
    return {
        "items": [_summarize_message(item) for item in reversed(collected)],
        "total": total,
    }


def get_config(context: "DashboardContext", query: dict[str, str]) -> Any:
    """Answer the latest configuration the charger reported.

    Args:
        context: The dashboard state.
        query: ``charger``.

    Returns:
        The ``configuration`` capture body.
    """
    path = _station_path(context.backend, query.get("charger", ""))
    return context.backend.admin_call("config", "GET", f"{path}/configuration")


def post_config(context: "DashboardContext", body: dict[str, Any]) -> Any:
    """Queue a ChangeConfiguration; the page follows it in the command list.

    Args:
        context: The dashboard state.
        body: ``charger``, ``key``, ``value``.

    Returns:
        The queued command.
    """
    path = _station_path(context.backend, str(body.get("charger", "")))
    key = _text(body.get("key"), "key")
    value = body.get("value")
    if not isinstance(value, str):
        raise BadRequestError("value is required")
    return context.backend.admin_call(
        "set",
        "POST",
        f"{path}/commands",
        {
            "command_type": "CHANGE_CONFIGURATION",
            "parameters": {"key": key, "value": value.strip()},
            "reason": "Test-day dashboard",
        },
    )


def get_commands(context: "DashboardContext", query: dict[str, str]) -> Any:
    """Answer the charger's recent commands, newest first.

    Args:
        context: The dashboard state.
        query: ``charger``, optional ``limit``.

    Returns:
        The command list page.
    """
    path = _station_path(context.backend, query.get("charger", ""))
    parameters = urlencode({"page_size": min(int(query.get("limit", 10)), 100)})
    return context.backend.admin_call(
        "commands", "GET", f"{path}/commands?{parameters}", log_body=False
    )


def get_sessions(context: "DashboardContext", query: dict[str, str]) -> Any:
    """Answer the charger's sessions, newest first.

    Args:
        context: The dashboard state.
        query: ``charger``, optional ``status`` and ``limit``.

    Returns:
        The session list page.
    """
    station_id = context.backend.resolve_station_id(query.get("charger", ""))
    parameters: dict[str, object] = {
        "station_id": station_id,
        "page_size": min(int(query.get("limit", 20)), 100),
    }
    if query.get("status") in SESSION_STATUSES:
        parameters["status"] = query["status"]
    return context.backend.admin_call(
        "sessions", "GET", f"{SESSIONS_PATH}?{urlencode(parameters)}", log_body=False
    )


def post_force_close(context: "DashboardContext", body: dict[str, Any]) -> Any:
    """Close a session the charger will never stop (staff, D5/D6).

    The session must belong to the charger the page shows, so a mistaken
    click cannot close another charger's session.

    Args:
        context: The dashboard state.
        body: ``charger``, ``session_id``, ``reason``.

    Returns:
        The closed session.

    Raises:
        BadRequestError: The session is not on that charger.
    """
    station_id = context.backend.resolve_station_id(str(body.get("charger", "")))
    session_id = _text(body.get("session_id"), "session_id")
    reason = _text(body.get("reason"), "reason")
    detail = context.backend.admin_call(
        "session_check", "GET", f"{SESSIONS_PATH}/{session_id}"
    )
    if str(detail.get("station_id")) != station_id:
        raise BadRequestError("That session is not on this charger; nothing closed")
    return context.backend.admin_call(
        "force_close",
        "POST",
        f"{SESSIONS_PATH}/{session_id}/force-close",
        {"reason": reason},
    )


def post_scan(context: "DashboardContext", body: dict[str, Any]) -> Any:
    """Scan a charger as a test driver (``POST /charging-sessions/scan``).

    Args:
        context: The dashboard state.
        body: ``driver``, then ``qr`` (raw QR text, sent unchanged) or
            ``charger``, and an optional ``gun``.

    Returns:
        The scan answer (the token is masked before it reaches the page).
    """
    driver_number = _driver_number(body.get("driver"))
    qr_text = body.get("qr")
    charger_code = (
        qr_text if isinstance(qr_text, str) and qr_text.strip() else body.get("charger")
    )
    scan_request: dict[str, object] = {
        "charger_code": _text(charger_code, "charger or QR text")
    }
    gun = body.get("gun")
    if gun not in (None, ""):
        scan_request["connector_number"] = int(str(gun))
    return context.backend.driver_call(
        driver_number, "scan", "POST", f"{SESSIONS_PATH}/scan", scan_request
    )


def get_driver_session(context: "DashboardContext", query: dict[str, str]) -> Any:
    """Answer one session as the driver app shows it, with its gun's state.

    Once the session is COMPLETED or ABANDONED the receipt (COMPLETED only),
    the bill and the wallet come with it; a failed read of one of them is
    returned as its error text, so the others still show.

    Args:
        context: The dashboard state.
        query: ``driver``, ``session_id``.

    Returns:
        ``detail``, ``gun`` (number, status, error code), and when final
        ``receipt`` / ``bill`` / ``wallet``.
    """
    driver_number = _driver_number(query.get("driver"))
    session_id = _text(query.get("session_id"), "session_id")
    backend = context.backend
    detail = backend.driver_call(
        driver_number, "poll", "GET", f"{SESSIONS_PATH}/{session_id}"
    )
    answer: dict[str, Any] = {"detail": detail, "gun": None}
    if detail.get("connector_id"):
        try:
            board = backend.driver_call(
                driver_number,
                "gun_status",
                "GET",
                f"{STATIONS_PATH}/{detail['station_id']}/status",
            )
        except (ApiError, RuntimeError):
            board = {}
        for connector in board.get("connectors", []):
            if str(connector.get("connector_id")) == str(detail["connector_id"]):
                answer["gun"] = {
                    "number": connector.get("ocpp_evse_id"),
                    "status": connector.get("status"),
                    "error_code": connector.get("error_code"),
                }
    if detail.get("status") in FINAL_STATUSES:
        reads = [("bill", f"{SESSIONS_PATH}/{session_id}/bill")]
        if detail.get("status") == "COMPLETED":
            reads.insert(0, ("receipt", f"{SESSIONS_PATH}/{session_id}/receipt"))
        reads.append(("wallet", "/api/v1/wallets/me"))
        for name, path in reads:
            try:
                answer[name] = backend.driver_call(driver_number, name, "GET", path)
            except ApiError as error:
                answer[name] = {"error": f"HTTP {error.status_code}: {error.detail}"}
    return answer


def get_driver_overview(context: "DashboardContext", query: dict[str, str]) -> Any:
    """Answer a driver's wallet and recent sessions (to resume an open one).

    Args:
        context: The dashboard state.
        query: ``driver``.

    Returns:
        ``phone``, ``wallet`` and ``sessions`` (the 10 newest).
    """
    driver_number = _driver_number(query.get("driver"))
    backend = context.backend
    return {
        "phone": build_test_driver_phone(driver_number),
        "wallet": backend.driver_call(
            driver_number, "wallet", "GET", "/api/v1/wallets/me"
        ),
        "sessions": backend.driver_call(
            driver_number,
            "sessions",
            "GET",
            f"{SESSIONS_PATH}/mine?{urlencode({'page_size': 10})}",
        ),
    }


def post_stop(context: "DashboardContext", body: dict[str, Any]) -> Any:
    """Stop a session from the app (queues a RemoteStopTransaction).

    Args:
        context: The dashboard state.
        body: ``driver``, ``session_id``.

    Returns:
        The stop answer.
    """
    driver_number = _driver_number(body.get("driver"))
    session_id = _text(body.get("session_id"), "session_id")
    return context.backend.driver_call(
        driver_number, "stop", "POST", f"{SESSIONS_PATH}/{session_id}/stop", {}
    )


def get_simulator(context: "DashboardContext", query: dict[str, str]) -> Any:
    """Answer the simulator's state and its new output lines.

    Args:
        context: The dashboard state.
        query: ``after``: the last line number the page shows.

    Returns:
        ``running``, ``identity`` and ``lines``.
    """
    return context.simulator.output(int(query.get("after", 0)))


def post_simulator_start(context: "DashboardContext", body: dict[str, Any]) -> Any:
    """Start the simulator, never as the real charger.

    The test day's charger (``TEST_DAY_CHARGER_ID``) is refused even while it
    is offline: it may come back at any moment (a power-cycle test), and two
    connections with one identity take the line from each other. The command
    line simulator still allows it for a rehearsal with the charger off.

    Args:
        context: The dashboard state.
        body: ``identity``, ``start_mode``, ``sample_interval``.

    Returns:
        The simulator's state.

    Raises:
        BadRequestError: The identity is the test day's charger, or is online
            with a non-simulator firmware (another real charger), or the
            arguments are invalid.
        LookupError: The identity is not provisioned (seed it first).
    """
    identity = _text(body.get("identity"), "identity")
    try:
        sample_interval = float(body.get("sample_interval") or 5)
    except (TypeError, ValueError) as error:
        raise BadRequestError("sample_interval must be a number") from error
    path = _station_path(context.backend, identity)
    # Compared by station, not by text: the real charger can also be named by
    # its registered serial number.
    test_day_charger = tool_setting("TEST_DAY_CHARGER_ID")
    if test_day_charger:
        try:
            test_day_station_id = context.backend.resolve_station_id(test_day_charger)
        except LookupError:
            test_day_station_id = None
        if identity == test_day_charger or path.endswith(f"/{test_day_station_id}"):
            raise BadRequestError(
                f"{identity} is the real charger: the simulator never runs as it "
                f"here. Use another identity (e.g. {DEFAULT_SIM_IDENTITY})."
            )
    connection = context.backend.admin_call(
        "connection", "GET", f"{path}/connection", log_body=False
    )
    firmware = connection.get("firmware_version")
    if connection.get("is_online") and firmware and firmware != SIMULATOR_FIRMWARE:
        raise BadRequestError(
            f"{identity} is online with firmware {firmware}: a real charger is "
            "connected. Use another identity (e.g. SIM-LAPTOP-01)."
        )
    context.simulator.start(
        identity,
        str(body.get("start_mode") or "screen"),
        sample_interval,
    )
    return context.simulator.output(0)


def post_simulator_send(context: "DashboardContext", body: dict[str, Any]) -> Any:
    """Type one command into the simulator.

    Args:
        context: The dashboard state.
        body: ``text``, e.g. ``plug 1``.

    Returns:
        ``{"sent": text}``.
    """
    text = _text(body.get("text"), "text")
    context.simulator.send(text)
    return {"sent": text}


def post_simulator_quit(context: "DashboardContext", body: dict[str, Any]) -> Any:
    """Quit the simulator (its connection closes; the charger goes offline).

    Args:
        context: The dashboard state.
        body: Unused.

    Returns:
        The simulator's state.
    """
    context.simulator.quit()
    return context.simulator.output(0)


class DashboardContext:
    """Process-wide state the request handlers share.

    Attributes:
        backend: The logged-in backend sessions.
        simulator: The simulator controller.
        sim_identity: The identity the page offers for the simulator.
    """

    def __init__(self, backend: DashboardBackend, sim_identity: str) -> None:
        """Create the shared state.

        Args:
            backend: The backend sessions.
            sim_identity: The simulator identity offered by default.
        """
        self.backend = backend
        self.simulator = SimulatorProcess()
        self.sim_identity = sim_identity


GetRoute = Callable[[DashboardContext, dict[str, str]], Any]
PostRoute = Callable[[DashboardContext, dict[str, Any]], Any]

GET_ROUTES: dict[str, GetRoute] = {
    "/api/settings": get_settings,
    "/api/station": get_station,
    "/api/messages": get_messages,
    "/api/config": get_config,
    "/api/commands": get_commands,
    "/api/sessions": get_sessions,
    "/api/driver/session": get_driver_session,
    "/api/driver/overview": get_driver_overview,
    "/api/simulator": get_simulator,
}
POST_ROUTES: dict[str, PostRoute] = {
    "/api/config": post_config,
    "/api/force-close": post_force_close,
    "/api/driver/scan": post_scan,
    "/api/driver/stop": post_stop,
    "/api/simulator/start": post_simulator_start,
    "/api/simulator/send": post_simulator_send,
    "/api/simulator/quit": post_simulator_quit,
}


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Serves the page and its JSON routes.

    A backend error is answered with the backend's status and ``{"error":
    "HTTP <status>: <detail>"}``; an unreachable backend with 502; a bad
    request with 400. Every JSON answer is masked (``mask_secrets``).

    Attributes:
        context: The shared dashboard state (set on the class at start-up).
    """

    context: DashboardContext

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silence the per-request access log (the page polls every few seconds).

        Args:
            format: The log format (unused).
            *args: Its arguments (unused).
        """

    def do_GET(self) -> None:
        """Serve the page or a GET route.

        Side Effects:
            Sends exactly one answer: 403 for a non-local ``Host``, the page,
            404 for an unknown route, or the route's JSON answer.
        """
        if not self._is_local_host():
            return
        url = urlsplit(self.path)
        if url.path in {"/", "/index.html"}:
            self._send_page()
            return
        route = GET_ROUTES.get(url.path)
        if route is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown route"})
            return
        query = {key: values[-1] for key, values in parse_qs(url.query).items()}
        self._run(lambda: route(self.context, query))

    def do_POST(self) -> None:
        """Serve a POST route (JSON body, ``X-Test-Day-Dashboard`` header).

        Side Effects:
            Sends exactly one answer: 403 for a non-local ``Host`` or a
            missing header, 404 for an unknown route, 400 for a body that is
            not a JSON object of at most ``MAX_BODY_BYTES``, or the route's
            JSON answer.
        """
        if not self._is_local_host():
            return
        if self.headers.get(CSRF_HEADER) != "1":
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Missing page header"})
            return
        route = POST_ROUTES.get(urlsplit(self.path).path)
        if route is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown route"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY_BYTES:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid body length"})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})
            return
        if not isinstance(body, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Expected an object"})
            return
        self._run(lambda: route(self.context, body))

    def _is_local_host(self) -> bool:
        """Refuse a request whose ``Host`` is not a local name (DNS rebinding).

        Returns:
            ``True`` when the request may go on; otherwise a 403 was sent.
        """
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host in LOCAL_HOSTS:
            return True
        self._send_json(HTTPStatus.FORBIDDEN, {"error": "Open the page via localhost"})
        return False

    def _run(self, handler: Callable[[], Any]) -> None:
        """Run a route and answer its result or its error as JSON.

        Args:
            handler: The bound route.
        """
        try:
            result = handler()
        except ApiError as error:
            self._send_json(
                error.status_code,
                {"error": f"HTTP {error.status_code}: {error.detail}"},
            )
        except (BadRequestError, ValueError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except LookupError as error:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(error)})
        except (RuntimeError, OSError) as error:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(error)})
        else:
            self._send_json(HTTPStatus.OK, result)

    def _send_page(self) -> None:
        """Send ``dashboard.html``, read from disk on every request.

        Side Effects:
            Reads the page file (so an edit shows on the next reload).
        """
        content = PAGE_FILE.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, status: int, body: Any) -> None:
        """Send a masked JSON answer.

        Args:
            status: HTTP status code.
            body: JSON-serializable body.
        """
        content = json.dumps(mask_secrets(body), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI.

    Returns:
        The argument parser.
    """
    parser = argparse.ArgumentParser(
        description="Test-day dashboard: charger, drivers, OCPP log and simulator "
        "in one browser page."
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="Local port (default 8080)."
    )
    parser.add_argument(
        "--api-url", default=None, help="Backend URL; default $G3_API_URL or localhost."
    )
    parser.add_argument(
        "--sim-identity",
        default=DEFAULT_SIM_IDENTITY,
        help=f"Simulator identity offered on the page (default {DEFAULT_SIM_IDENTITY}).",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open the browser."
    )
    return parser


def main() -> None:
    """Serve the dashboard until Ctrl+C, then quit the simulator if it runs."""
    args = build_parser().parse_args()
    context = DashboardContext(
        DashboardBackend(resolve_api_url(args.api_url)), args.sim_identity
    )
    DashboardRequestHandler.context = context
    # 127.0.0.1 only: this process acts as the HEAD_ADMIN (module docstring).
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardRequestHandler)
    url = f"http://localhost:{args.port}/"
    print(
        f"Test-day dashboard on {url} (API {context.backend.api_url}); Ctrl+C to quit"
    )
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
        context.simulator.quit()


if __name__ == "__main__":
    main()
