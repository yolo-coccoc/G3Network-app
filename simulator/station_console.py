"""Operator console for one charger on a charging test day.

Decision D8 of ``docs/planners/backend-charging-test-day.md``: everything goes
through the HTTP API (``/api/v1``), logged in as the bootstrap HEAD_ADMIN
(``IDENTITY_BOOTSTRAP_ADMIN_*`` in ``backend/.env``). The charger is named by
``--charger <OCPP identity>`` (resolved through ``GET /charging-stations?q=``,
exact identity first, then the registered serial) or by ``--station-id``.

Subcommands:

* ``status``: connection facts (online, last seen, boot data) and the status
  of the whole charger (connector 0) and of every gun, with error codes.
* ``messages [--follow]``: the OCPP message log, oldest of the page first:
  time, direction (``<-`` received from the charger, ``->`` sent to it),
  message type, action and a short payload summary; a CALLERROR line starts
  with ``!!``. ``--follow`` keeps polling and prints only new frames until
  Ctrl+C. The frame text is read with ``include_raw_frame=true``, which writes
  one access-audit row per request on the backend (frames can hold RFID
  tags); ``--no-frames`` avoids it but then shows no type or summary.
  ``idTag`` / ``idToken`` values are masked in everything printed (IS-07).
* ``config``: the latest configuration the charger reported.
* ``set <key> <value>``: queues a ``CHANGE_CONFIGURATION`` command and waits
  until the gateway records the charger's answer (or ``--timeout``).
* ``commands``: recent commands with their outcome.
* ``force-close <session_id> --reason "..."``: closes a session the charger
  will never stop (``POST /charging-sessions/{id}/force-close``, D5). The
  charger is optional; when ``--charger`` / ``--station-id`` is given, the
  session must belong to it, so a mistyped ID cannot close another charger's
  session.
* ``sessions [--status ACTIVE]``: this charger's sessions (the session list's
  ``station_id`` filter, server side).

Every call is appended to the evidence file (default
``backend/logs/test-day/station_console-<UTC date>.jsonl``, git-ignored;
message-feed bodies are not copied there, the backend already stores them).
An HTTP error prints its status and the backend's message and exits 1. Run
from ``backend/`` with its venv (or ``make station-console ARGS="..."``).
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from api_client import ApiClient, ApiError, login_as_bootstrap_admin, resolve_api_url
from test_day_common import (
    EvidenceLog,
    ToolSession,
    format_local_time,
    mask_secrets,
    parse_time,
    print_table,
    run_tool,
    to_float,
)

STATIONS_PATH = "/api/v1/charging-stations"
SESSIONS_PATH = "/api/v1/charging-sessions"
# The API's largest page (API_MAX_PAGE_SIZE).
MAX_PAGE_SIZE = 100
# A follow poll reads at most this many pages of new frames (100 each) before
# printing; a larger burst between two polls is cut and says so.
MAX_FOLLOW_PAGES = 20
DIRECTION_ARROWS = {"CP_TO_CSMS": "<-", "CSMS_TO_CP": "->"}
OCPP_MESSAGE_TYPES = {2: "CALL", 3: "RESULT", 4: "CALLERROR"}
# Session statuses the list filter accepts (the backend's SessionStatus).
SESSION_STATUSES = ("PENDING", "ACTIVE", "COMPLETED", "ABANDONED")


class StationTarget:
    """The charger a console command works on.

    Attributes:
        station_id: Internal ID of the station.
        label: How to show it: the OCPP identity, or the ID.
    """

    def __init__(self, station_id: str, label: str) -> None:
        """Keep the station ID and its display label.

        Args:
            station_id: Internal ID of the station.
            label: The OCPP identity, or the ID when unknown.
        """
        self.station_id = station_id
        self.label = label

    @property
    def path(self) -> str:
        """The station's API path.

        Returns:
            ``/api/v1/charging-stations/{station_id}``.
        """
        return f"{STATIONS_PATH}/{self.station_id}"


def open_session(args: argparse.Namespace) -> ToolSession:
    """Log in as the bootstrap HEAD_ADMIN.

    Args:
        args: Parsed CLI arguments (API URL, evidence file).

    Returns:
        The logged-in tool session.

    Raises:
        ApiError: The login was refused.
        RuntimeError: The bootstrap credentials are missing or the backend is
            unreachable.
    """
    api_url = resolve_api_url(args.api_url)

    def log_in() -> ApiClient:
        """Log the bootstrap administrator in.

        Returns:
            A client holding the administrator's token.
        """
        return login_as_bootstrap_admin(api_url)

    return ToolSession(log_in, EvidenceLog("station_console", args.log_file))


def resolve_station(session: ToolSession, args: argparse.Namespace) -> StationTarget:
    """Find the station the arguments name.

    Args:
        session: The administrator's tool session.
        args: Parsed CLI arguments (``--station-id`` or ``--charger``).

    Returns:
        The station.

    Raises:
        RuntimeError: Neither argument was given, or no active station has
            that OCPP identity or registered serial number.
    """
    if args.station_id:
        return StationTarget(args.station_id, args.station_id)
    if not args.charger:
        raise RuntimeError("Name the charger with --charger or --station-id")
    query = urlencode({"q": args.charger, "page_size": MAX_PAGE_SIZE})
    page = session.call("resolve_station", "GET", f"{STATIONS_PATH}?{query}")
    items = page.get("items", [])
    for field_name in ("ocpp_identity", "registered_serial_number"):
        for item in items:
            if item.get(field_name) == args.charger:
                return StationTarget(str(item["station_id"]), item["ocpp_identity"])
    raise RuntimeError(f"No active charging station has the code {args.charger!r}")


def yes_no(value: object) -> str:
    """Show a boolean as ``yes`` / ``no`` (``-`` for none).

    Args:
        value: A boolean or ``None``.

    Returns:
        The text.
    """
    if value is None:
        return "-"
    return "yes" if value else "no"


def command_status(args: argparse.Namespace) -> None:
    """Print the connection facts and the status of the charger and its guns.

    Args:
        args: Parsed CLI arguments.
    """
    session = open_session(args)
    station = resolve_station(session, args)
    connection = session.call("connection", "GET", f"{station.path}/connection")
    status = session.call("status", "GET", f"{station.path}/status")
    print(
        f"Charger {connection.get('ocpp_identity')} ({station.station_id})  "
        f"online: {yes_no(connection.get('is_online'))}  "
        f"last seen: {format_local_time(connection.get('last_seen_at'))}  "
        f"(offline after {connection.get('offline_after_seconds')} s)"
    )
    print(
        f"OCPP {connection.get('ocpp_protocol_version') or '-'}  "
        f"vendor {connection.get('vendor') or '-'}  "
        f"model {connection.get('model') or '-'}  "
        f"firmware {connection.get('firmware_version') or '-'}  "
        f"last boot {format_local_time(connection.get('last_boot_at'))}"
    )
    print(
        f"Serial reported {connection.get('serial_number') or '-'} / registered "
        f"{connection.get('registered_serial_number')}  "
        f"match: {yes_no(connection.get('is_serial_matching'))}"
    )
    print(
        f"Connector 0 (whole charger): {status.get('charger_status') or '-'}  "
        f"error {status.get('charger_error_code') or '-'}  "
        f"vendor error {status.get('charger_vendor_error_code') or '-'}  "
        f"at {format_local_time(status.get('charger_status_updated_at'))}"
    )
    rows = [
        [
            connector.get("ocpp_evse_id"),
            connector.get("ocpp_connector_id"),
            connector.get("standard"),
            connector.get("max_power_kw"),
            connector.get("status"),
            "stale" if connector.get("is_status_stale") else "",
            connector.get("error_code"),
            connector.get("vendor_error_code"),
            connector.get("status_info"),
            format_local_time(connector.get("status_updated_at")),
        ]
        for connector in status.get("connectors", [])
    ]
    print()
    print_table(
        [
            "gun",
            "conn",
            "standard",
            "max_kW",
            "status",
            "stale",
            "error",
            "vendor_error",
            "info",
            "updated",
        ],
        rows,
    )


def summarize_frame(raw_frame: str | None) -> tuple[str, str]:
    """Read an OCPP frame's message type and a masked one-line summary.

    Args:
        raw_frame: The frame text, or ``None`` when frames were not asked for.

    Returns:
        ``(message type, summary)``: ``CALL`` / ``RESULT`` / ``CALLERROR`` and
        the compact payload (a CALLERROR: its code and description), tokens
        masked. ``("?", ...)`` when the frame is missing or not OCPP-shaped.
    """
    if raw_frame is None:
        return "?", ""
    try:
        frame = json.loads(raw_frame)
    except json.JSONDecodeError:
        return "?", "<unparsable frame withheld>"
    if not isinstance(frame, list) or not frame:
        return "?", "<unexpected frame shape>"
    message_type = OCPP_MESSAGE_TYPES.get(frame[0], str(frame[0]))
    if frame[0] == 4:
        error_code = frame[2] if len(frame) > 2 else "-"
        description = frame[3] if len(frame) > 3 else ""
        details = mask_secrets(frame[4]) if len(frame) > 4 else {}
        summary = f"{error_code}: {description}"
        if details:
            summary += f" {json.dumps(details, ensure_ascii=False)}"
        return message_type, summary
    payload = frame[3] if frame[0] == 2 and len(frame) > 3 else frame[-1]
    return message_type, json.dumps(
        mask_secrets(payload), ensure_ascii=False, separators=(",", ":")
    )


def format_message_line(
    message: dict[str, Any], width: int, request_actions: dict[str, str]
) -> str:
    """Build the one-line view of a logged OCPP frame.

    The log stores the action on requests only; an answer (RESULT or
    CALLERROR) shows its request's action in brackets, found by the OCPP
    message ID among the requests printed before it.

    Args:
        message: ``ChargingOcppMessageResponse`` body.
        width: Longest line to print; the summary is cut to fit.
        request_actions: OCPP message ID -> action of the requests printed so
            far; a request's action is added to it.

    Returns:
        ``[!!] time  arrow  type  action  summary``.
    """
    message_type, summary = summarize_frame(message.get("raw_frame"))
    ocpp_message_id = str(message.get("ocpp_message_id"))
    action = message.get("action")
    if action:
        request_actions[ocpp_message_id] = str(action)
    elif ocpp_message_id in request_actions:
        action = f"({request_actions[ocpp_message_id]})"
    occurred_at = parse_time(message.get("occurred_at"))
    clock = (
        occurred_at.astimezone().strftime("%H:%M:%S.%f")[:-3] if occurred_at else "-"
    )
    line = "  ".join(
        [
            "!!" if message_type == "CALLERROR" else "  ",
            clock,
            DIRECTION_ARROWS.get(str(message.get("direction")), "??"),
            message_type.ljust(9),
            str(action or "-").ljust(26),
            summary,
        ]
    )
    return line if len(line) <= width else line[: width - 1] + "…"


def fetch_messages(
    session: ToolSession,
    station: StationTarget,
    args: argparse.Namespace,
    *,
    occurred_from: str | None,
    page_size: int,
    max_pages: int,
) -> tuple[list[dict[str, Any]], int]:
    """Read logged frames, newest first, over one or more pages.

    Args:
        session: The administrator's tool session.
        station: The charger.
        args: Parsed CLI arguments (``--action``, ``--no-frames``).
        occurred_from: Inclusive lower time bound (ISO, with zone), or ``None``.
        page_size: Rows per page.
        max_pages: Pages to read at most.

    Returns:
        The frames read (newest first) and the total the API reported.
    """
    collected: list[dict[str, Any]] = []
    total = 0
    for page_number in range(1, max_pages + 1):
        query: dict[str, object] = {
            "page": page_number,
            "page_size": page_size,
            "include_raw_frame": "false" if args.no_frames else "true",
        }
        if args.action:
            query["action"] = args.action
        if occurred_from:
            query["occurred_from"] = occurred_from
        page = session.call(
            "messages",
            "GET",
            f"{station.path}/ocpp-messages?{urlencode(query)}",
            log_body=False,
        )
        items = page.get("items", [])
        total = int(page.get("total", 0))
        collected.extend(items)
        if not items or len(collected) >= total:
            break
    return collected, total


def command_messages(args: argparse.Namespace) -> None:
    """Print the newest logged frames; with ``--follow`` keep printing new ones.

    While following, a network error or a 5xx answer is printed and the poll
    retried; Ctrl+C ends the feed.

    Args:
        args: Parsed CLI arguments.

    Raises:
        ApiError: A read was refused with a 4xx status.
    """
    session = open_session(args)
    station = resolve_station(session, args)
    messages, total = fetch_messages(
        session,
        station,
        args,
        occurred_from=None,
        page_size=min(args.limit, MAX_PAGE_SIZE),
        max_pages=1,
    )
    print(f"OCPP log of {station.label}: last {len(messages)} of {total} frame(s)")
    seen_ids: set[str] = set()
    request_actions: dict[str, str] = {}
    newest_at: str | None = None
    for message in reversed(messages):
        print(format_message_line(message, args.width, request_actions))
    for message in messages:
        seen_ids.add(str(message["message_id"]))
    if messages:
        newest_at = messages[0]["occurred_at"]
    if not args.follow:
        return
    print(f"-- following every {args.interval:g} s, Ctrl+C to quit --")
    try:
        while True:
            time.sleep(args.interval)
            # Inclusive lower bound on the newest time seen: frames logged in
            # the same instant are read again and dropped by ID.
            try:
                fresh, total = fetch_messages(
                    session,
                    station,
                    args,
                    occurred_from=newest_at,
                    page_size=MAX_PAGE_SIZE,
                    max_pages=MAX_FOLLOW_PAGES,
                )
            except ApiError as error:
                if error.status_code < 500:
                    raise
                print(f"-- HTTP {error.status_code}: {error.detail}; retrying --")
                continue
            except RuntimeError as error:
                # The API may be restarted during a test day: keep following.
                print(f"-- {error}; retrying --")
                continue
            new_messages = [
                message
                for message in fresh
                if str(message["message_id"]) not in seen_ids
            ]
            if len(fresh) < total:
                print(
                    f"-- {total - len(fresh)} older frame(s) of this burst skipped --"
                )
            for message in reversed(new_messages):
                print(format_message_line(message, args.width, request_actions))
                seen_ids.add(str(message["message_id"]))
            if fresh:
                newest_at = fresh[0]["occurred_at"]
    except KeyboardInterrupt:
        print()


def command_config(args: argparse.Namespace) -> None:
    """Print the latest configuration snapshot as a key/value table.

    Args:
        args: Parsed CLI arguments.
    """
    session = open_session(args)
    station = resolve_station(session, args)
    capture = session.call("config", "GET", f"{station.path}/configuration")
    print(
        f"Configuration of {station.label} captured "
        f"{format_local_time(capture.get('captured_at'))}"
    )
    rows = [
        [
            item.get("variable_name"),
            item.get("value"),
            "RO" if item.get("mutability") == "READ_ONLY" else "",
            item.get("component_name"),
        ]
        for item in capture.get("items", [])
    ]
    print_table(["key", "value", "readonly", "component"], rows)


def command_set(args: argparse.Namespace) -> None:
    """Queue a ChangeConfiguration and wait for the charger's answer.

    Args:
        args: Parsed CLI arguments (key, value, reason, timeout).

    Raises:
        ApiError: The command was refused (404 charger, 400/422 parameters).
        SystemExit: ``1`` when the outcome is not ``ACCEPTED`` (rejected,
            error, timeout, not sent, or still pending at ``--timeout``).
    """
    session = open_session(args)
    station = resolve_station(session, args)
    command = session.call(
        "set",
        "POST",
        f"{station.path}/commands",
        {
            "command_type": "CHANGE_CONFIGURATION",
            "parameters": {"key": args.key, "value": args.value},
            "reason": args.reason,
        },
    )
    command_id = command["command_id"]
    print(f"Queued CHANGE_CONFIGURATION {args.key}={args.value} ({command_id})")
    deadline = time.monotonic() + args.timeout
    while command.get("outcome") == "PENDING" and time.monotonic() < deadline:
        time.sleep(1)
        command = session.call(
            "command_poll", "GET", f"{station.path}/commands/{command_id}"
        )
    print(
        f"Outcome: {command.get('outcome')}  charger answer: "
        f"{command.get('response_status') or '-'}  "
        f"answered {format_local_time(command.get('answered_at'))}"
    )
    if command.get("outcome") == "PENDING":
        print(
            f"Still PENDING after {args.timeout:g} s; check later with "
            '`make station-console ARGS="commands ..."`'
        )
    if command.get("outcome") != "ACCEPTED":
        sys.exit(1)


def command_commands(args: argparse.Namespace) -> None:
    """List the charger's recent commands, newest first.

    Args:
        args: Parsed CLI arguments.
    """
    session = open_session(args)
    station = resolve_station(session, args)
    query = urlencode({"page_size": args.limit})
    page = session.call("commands", "GET", f"{station.path}/commands?{query}")
    rows = [
        [
            format_local_time(command.get("requested_at")),
            command.get("command_type"),
            command.get("outcome"),
            command.get("response_status"),
            format_local_time(command.get("answered_at")),
            json.dumps(mask_secrets(command.get("parameters")), ensure_ascii=False)
            if command.get("parameters")
            else None,
            command.get("command_id"),
        ]
        for command in page.get("items", [])
    ]
    print_table(
        [
            "requested",
            "type",
            "outcome",
            "answer",
            "answered",
            "parameters",
            "command_id",
        ],
        rows,
    )


def command_force_close(args: argparse.Namespace) -> None:
    """Close a session the charger will never stop (staff, D5/D6).

    Args:
        args: Parsed CLI arguments (session ID, reason, optional charger).

    Raises:
        ApiError: 404 unknown session (or the endpoint is not deployed), 409
            the session is not ACTIVE, 403 not internal staff.
        RuntimeError: A charger was named and the session is not on it.
    """
    session = open_session(args)
    if args.charger or args.station_id:
        station = resolve_station(session, args)
        detail = session.call(
            "session_check", "GET", f"{SESSIONS_PATH}/{args.session_id}"
        )
        if str(detail.get("station_id")) != station.station_id:
            raise RuntimeError(
                f"Session {args.session_id} is not on charger {station.label}; "
                "nothing was closed"
            )
    closed = session.call(
        "force_close",
        "POST",
        f"{SESSIONS_PATH}/{args.session_id}/force-close",
        {"reason": args.reason},
    )
    energy_wh = to_float(closed.get("energy_delivered_wh"))
    print(
        f"Session {closed.get('session_id')}: {closed.get('status')}  "
        f"stop reason {closed.get('stop_reason') or '-'}  "
        f"ended {format_local_time(closed.get('ended_at'))}  "
        "energy " + ("-" if energy_wh is None else f"{energy_wh / 1000:.3f} kWh")
    )


def command_sessions(args: argparse.Namespace) -> None:
    """List the charger's sessions, newest first.

    Args:
        args: Parsed CLI arguments (status filter, limit).
    """
    session = open_session(args)
    station = resolve_station(session, args)
    query: dict[str, object] = {"station_id": station.station_id}
    query["page_size"] = args.limit
    if args.status:
        query["status"] = args.status
    page = session.call("sessions", "GET", f"{SESSIONS_PATH}?{urlencode(query)}")
    rows = []
    for item in page.get("items", []):
        energy_wh = to_float(item.get("energy_delivered_wh"))
        rows.append(
            [
                item.get("session_id"),
                item.get("status"),
                item.get("ocpp_transaction_id"),
                format_local_time(item.get("created_at")),
                format_local_time(item.get("started_at")),
                format_local_time(item.get("ended_at")),
                None if energy_wh is None else f"{energy_wh / 1000:.3f}",
                item.get("stop_reason"),
                item.get("started_by"),
            ]
        )
    print_table(
        [
            "session_id",
            "status",
            "tx",
            "scanned",
            "started",
            "ended",
            "kWh",
            "stop",
            "started_by",
        ],
        rows,
    )
    print(f"{len(rows)} of {page.get('total')} session(s) on {station.label}")


def positive_int(value: str) -> int:
    """Parse a positive integer CLI value.

    Args:
        value: Numeric string received from the CLI.

    Returns:
        Positive integer.

    Raises:
        argparse.ArgumentTypeError: If value is not a positive integer.
    """
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def positive_float(value: str) -> float:
    """Parse a positive number of seconds.

    Args:
        value: Numeric string received from the CLI.

    Returns:
        Positive float.

    Raises:
        argparse.ArgumentTypeError: If value is not a positive number.
    """
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive number") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI with its subcommands.

    Returns:
        The argument parser.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--api-url", default=None, help="Backend URL; default $G3_API_URL or localhost."
    )
    common.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Evidence file (JSON lines); default "
        "backend/logs/test-day/station_console-<date>.jsonl.",
    )
    station = argparse.ArgumentParser(add_help=False)
    target = station.add_mutually_exclusive_group(required=True)
    target.add_argument("--charger", help="OCPP identity (or registered serial).")
    target.add_argument("--station-id", help="Internal station ID.")

    parser = argparse.ArgumentParser(
        description="Operator console for one charger (logs in as the bootstrap "
        "HEAD_ADMIN)."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser(
        "status", parents=[common, station], help="Connection and gun status."
    )
    status.set_defaults(handler=command_status)

    messages = commands.add_parser(
        "messages", parents=[common, station], help="OCPP message log."
    )
    messages.add_argument("--follow", action="store_true", help="Keep printing.")
    messages.add_argument(
        "--limit", type=positive_int, default=30, help="Frames shown first (≤100)."
    )
    messages.add_argument(
        "--interval", type=positive_float, default=3.0, help="Follow poll seconds."
    )
    messages.add_argument("--action", default=None, help="Only this OCPP action.")
    messages.add_argument(
        "--no-frames",
        action="store_true",
        help="Do not read frame text (no audit row; no type or summary).",
    )
    messages.add_argument(
        "--width", type=positive_int, default=200, help="Longest printed line."
    )
    messages.set_defaults(handler=command_messages)

    config = commands.add_parser(
        "config", parents=[common, station], help="Latest configuration snapshot."
    )
    config.set_defaults(handler=command_config)

    set_parser = commands.add_parser(
        "set", parents=[common, station], help="ChangeConfiguration of one key."
    )
    set_parser.add_argument("key")
    set_parser.add_argument("value")
    set_parser.add_argument("--reason", default="Test-day console")
    set_parser.add_argument(
        "--timeout", type=positive_float, default=60.0, help="Seconds to wait."
    )
    set_parser.set_defaults(handler=command_set)

    command_list = commands.add_parser(
        "commands", parents=[common, station], help="Recent commands."
    )
    command_list.add_argument("--limit", type=positive_int, default=10)
    command_list.set_defaults(handler=command_commands)

    force_close = commands.add_parser(
        "force-close", parents=[common], help="Close a session the charger never stops."
    )
    force_close.add_argument("session_id")
    force_target = force_close.add_mutually_exclusive_group()
    force_target.add_argument(
        "--charger", help="Optional: refuse unless the session is on this charger."
    )
    force_target.add_argument("--station-id", help="Optional: same, by station ID.")
    force_close.add_argument("--reason", required=True, help="1 to 200 characters.")
    force_close.set_defaults(handler=command_force_close)

    sessions = commands.add_parser(
        "sessions", parents=[common, station], help="This charger's sessions."
    )
    sessions.add_argument("--status", choices=SESSION_STATUSES, default=None)
    sessions.add_argument("--limit", type=positive_int, default=20)
    sessions.set_defaults(handler=command_sessions)
    return parser


def main() -> None:
    """Parse the arguments and run the subcommand (exit 1 on an API error)."""
    args = build_parser().parse_args()
    run_tool(lambda: args.handler(args))


if __name__ == "__main__":
    main()
