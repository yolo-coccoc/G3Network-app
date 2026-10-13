"""Stand-in for the driver's mobile app on a charging test day.

Decisions D8/D9 of ``docs/planners/backend-charging-test-day.md``: everything
goes through the HTTP API (``/api/v1``), logged in as a test driver created by
``seed_test_drivers.py`` (driver ``N`` -> the same phone scheme, see
``test_day_common``), with platform ``ANDROID`` like the app.

Subcommands:

* ``scan --driver N (--charger <code> | --qr "<raw text>") [--gun N]``:
  ``POST /charging-sessions/scan``. ``--charger`` sends the OCPP identity (or
  the registered serial); ``--qr`` sends the text read from the QR code
  unchanged as ``charger_code``: the tool never parses it (D9). Prints the
  session, then watches it.
* ``watch <session_id>``: every ``--interval`` seconds one line (local time,
  status, energy, power, SoC, elapsed) from ``GET /charging-sessions/{id}``
  until the session is COMPLETED or ABANDONED; then the receipt (COMPLETED
  only), the bill and the wallet balance. Ctrl+C asks whether to stop the
  charge: ``y`` queues the stop and keeps watching; ``N`` (or Enter) leaves
  the charge running and prints the command to stop it later; a second Ctrl+C
  at the prompt exits.
* ``stop <session_id>``: ``POST /charging-sessions/{id}/stop``.
* ``sessions``: the driver's own sessions (``GET /charging-sessions/mine``).

Every call is appended as a JSON line to the evidence file (default
``logs/test-day/driver_app-<UTC date>.jsonl`` in this folder, git-ignored). The
QR token and the access tokens are masked everywhere (IS-07). An HTTP error
prints its status and the backend's message and exits 1. Without ``--charger``
or ``--qr`` the scan uses the ``TEST_DAY_CHARGER_ID`` setting. Standalone: run
it with any Python 3.12 from this folder (``python driver_app.py ...``; see
``CLAUDE.md``), or on the VM with ``make driver-app ARGS="..."``.
"""

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from g3_api import ApiClient, ApiError, login, resolve_api_url, tool_setting
from test_day_common import (
    TEST_DRIVER_PASSWORD_ENV,
    TEST_DRIVER_PHONE_PREFIX,
    EvidenceLog,
    ToolSession,
    build_test_driver_phone,
    format_duration,
    format_local_time,
    mask_token,
    parse_time,
    print_table,
    resolve_test_driver_password,
    run_tool,
    to_float,
)

# The app's platform: decides the session lifetime on the backend.
APP_PLATFORM = "ANDROID"
DEFAULT_INTERVAL_SECONDS = 5.0
FINAL_STATUSES = frozenset({"COMPLETED", "ABANDONED"})
SESSIONS_PATH = "/api/v1/charging-sessions"


def open_session(args: argparse.Namespace) -> ToolSession:
    """Log in as the test driver the arguments name.

    Args:
        args: Parsed CLI arguments (driver number or phone, password, API URL,
            evidence file).

    Returns:
        The logged-in tool session.

    Raises:
        ApiError: The login was refused (wrong phone/password, locked).
        RuntimeError: The backend is unreachable.
    """
    api_url = resolve_api_url(args.api_url)
    phone_number = args.phone or build_test_driver_phone(args.driver, args.phone_prefix)
    password = resolve_test_driver_password(args.password)
    evidence = EvidenceLog("driver_app", args.log_file)

    def log_in() -> ApiClient:
        """Log the driver in with the app's platform.

        Returns:
            A client holding the driver's token.
        """
        return login(api_url, phone_number, password, platform=APP_PLATFORM)

    print(f"Logging in as {phone_number} ({api_url})")
    return ToolSession(log_in, evidence)


def stop_command_hint(args: argparse.Namespace, session_id: str) -> str:
    """Build the command that stops the session later.

    Args:
        args: Parsed CLI arguments (driver number or phone).
        session_id: The session to stop.

    Returns:
        The ``make driver-app`` command line.
    """
    who = f"--phone {args.phone}" if args.phone else f"--driver {args.driver}"
    return f'make driver-app ARGS="stop {session_id} {who}"'


def print_stop_answer(answer: dict[str, Any]) -> None:
    """Print the answer of a stop request.

    Args:
        answer: ``ChargingSessionStopResponse`` body.
    """
    command_id = answer.get("command_id")
    print(
        f"Stop accepted: session {answer.get('session_id')} is "
        f"{answer.get('status')}; "
        + (
            f"REMOTE_STOP command {command_id} queued (the session ends when the "
            "charger confirms)"
            if command_id
            else "the pending scan was cancelled"
        )
    )


def request_stop(session: ToolSession, session_id: str) -> None:
    """Queue the stop of a session and print the answer.

    Args:
        session: The driver's tool session.
        session_id: The session to stop.

    Raises:
        ApiError: The backend refused the stop (404 unknown, 409 not running).
        RuntimeError: The backend is unreachable.
    """
    answer = session.call("stop", "POST", f"{SESSIONS_PATH}/{session_id}/stop", {})
    print_stop_answer(answer)


# What a gun's live status means for the driver (the session itself stays
# ACTIVE through all of these; SuspendedEV/EVSE are pauses, not faults).
GUN_STATUS_TEXT = {
    "Preparing": "plugged in, not started",
    "Charging": "charging",
    "SuspendedEV": "paused: car full or car paused",
    "SuspendedEVSE": "paused by the charger",
    "Finishing": "finished, unplug",
    "Available": "free",
    "Faulted": "FAULT",
    "Unavailable": "out of service",
}


def resolve_gun_status(session: ToolSession, detail: dict[str, Any]) -> str | None:
    """Read the live status of the session's gun, as the app would show it.

    Args:
        session: The driver's tool session.
        detail: ``ChargingSessionDetailResponse`` body.

    Returns:
        A short text such as ``paused: car full or car paused``, or ``None``
        when the session has no gun yet or the status cannot be read (a read
        failure never stops the watch).
    """
    connector_id = detail.get("connector_id")
    if not connector_id or detail.get("status") != "ACTIVE":
        return None
    try:
        board = session.call(
            "gun_status",
            "GET",
            f"/api/v1/charging-stations/{detail['station_id']}/status",
        )
    except (ApiError, RuntimeError):
        return None
    for connector in board.get("connectors", []):
        if str(connector.get("connector_id")) == str(connector_id):
            status = str(connector.get("status"))
            text = GUN_STATUS_TEXT.get(status, status)
            if status == "Faulted" and connector.get("error_code"):
                text = f"FAULT {connector['error_code']}"
            return text
    return None


def format_watch_line(detail: dict[str, Any], gun_status: str | None = None) -> str:
    """Build the one-line live view of a session.

    Args:
        detail: ``ChargingSessionDetailResponse`` body.
        gun_status: What the gun is doing now (``resolve_gun_status``), if known.

    Returns:
        ``time  status  energy  power  SoC  elapsed  [gun status]``.
    """
    status = str(detail.get("status"))
    energy_wh = to_float(detail.get("energy_delivered_wh"))
    power_kw = to_float(detail.get("current_power_kw"))
    soc = to_float(detail.get("soc_end_percent"))
    if detail.get("started_at"):
        elapsed = f"charging {format_duration(detail.get('duration_seconds') or 0)}"
    else:
        scanned_at = parse_time(detail.get("created_at"))
        waited = (datetime.now(UTC) - scanned_at).total_seconds() if scanned_at else 0.0
        elapsed = f"waiting {format_duration(waited)}"
    return "  ".join(
        [
            datetime.now().strftime("%H:%M:%S"),
            status.ljust(9),
            "energy " + ("-" if energy_wh is None else f"{energy_wh / 1000:.3f} kWh"),
            "power " + ("-" if power_kw is None else f"{power_kw:.1f} kW"),
            "SoC " + ("-" if soc is None else f"{soc:.0f}%"),
            elapsed,
        ]
        + ([f"gun: {gun_status}"] if gun_status else [])
    )


def ask_to_stop() -> bool | None:
    """Ask on the terminal whether to stop the charge.

    Returns:
        ``True`` for yes, ``False`` for no (the default), ``None`` when a
        second Ctrl+C or end of input interrupted the prompt.
    """
    try:
        answer = input("\nStop the charge? [y/N] ")
    except (KeyboardInterrupt, EOFError):
        print()
        return None
    return answer.strip().lower() in {"y", "yes"}


def watch_session(
    session: ToolSession, args: argparse.Namespace, session_id: str
) -> None:
    """Poll a session until it ends, then print the receipt, bill and balance.

    A network error or a server error during a poll is printed and the poll
    is retried at the next interval (the API may be restarted during a test);
    a 4xx answer ends the watch with the error.

    Args:
        session: The driver's tool session.
        args: Parsed CLI arguments (interval, driver).
        session_id: The session to watch.

    Raises:
        ApiError: A poll was refused with a 4xx status.
    """
    print(f"Watching session {session_id} every {args.interval:g} s (Ctrl+C: stop?)")
    while True:
        try:
            try:
                detail = session.call("poll", "GET", f"{SESSIONS_PATH}/{session_id}")
            except ApiError as error:
                if error.status_code < 500:
                    raise
                print(f"warning: HTTP {error.status_code}: {error.detail}; retrying")
                time.sleep(args.interval)
                continue
            except RuntimeError as error:
                print(f"warning: {error}; retrying")
                time.sleep(args.interval)
                continue
            print(format_watch_line(detail, resolve_gun_status(session, detail)))
            if detail.get("status") in FINAL_STATUSES:
                break
            time.sleep(args.interval)
        except KeyboardInterrupt:
            decision = ask_to_stop()
            if not decision:
                print("The charge keeps running. To stop it later:")
                print(f"  {stop_command_hint(args, session_id)}")
                return
            try:
                request_stop(session, session_id)
            except ApiError as error:
                # E.g. 409: the session ended meanwhile; the next poll shows it.
                print(f"Stop refused: HTTP {error.status_code}: {error.detail}")
    print_session_end(session, detail)


def print_session_end(session: ToolSession, detail: dict[str, Any]) -> None:
    """Print the receipt (COMPLETED only), the bill and the wallet balance.

    A failed read is printed and the next one is still tried, so one missing
    piece (e.g. no bill) does not hide the others.

    Args:
        session: The driver's tool session.
        detail: The final ``ChargingSessionDetailResponse`` body.
    """
    session_id = detail["session_id"]
    print(f"\nSession {session_id} ended: {detail.get('status')}")
    if detail.get("status") == "COMPLETED":
        try:
            receipt = session.call(
                "receipt", "GET", f"{SESSIONS_PATH}/{session_id}/receipt"
            )
            print_receipt(receipt)
        except ApiError as error:
            print(f"Receipt: HTTP {error.status_code}: {error.detail}")
    try:
        bill = session.call("bill", "GET", f"{SESSIONS_PATH}/{session_id}/bill")
        print_bill(bill)
    except ApiError as error:
        print(f"Bill: HTTP {error.status_code}: {error.detail}")
    try:
        wallet = session.call("wallet", "GET", "/api/v1/wallets/me")
        print(f"\nWallet balance: {wallet['balance']:,} {wallet.get('currency', '')}")
    except ApiError as error:
        print(f"Wallet: HTTP {error.status_code}: {error.detail}")


def print_receipt(receipt: dict[str, Any]) -> None:
    """Print the receipt of a finished charge.

    Args:
        receipt: ``ChargingSessionReceiptResponse`` body.
    """
    energy_wh = to_float(receipt.get("energy_delivered_wh"))
    print("\nReceipt")
    print_table(
        ["field", "value"],
        [
            ["charger", receipt.get("charger_reference")],
            ["gun", receipt.get("gun_number")],
            ["connector", receipt.get("connector_standard")],
            ["location", receipt.get("location_name")],
            ["started", format_local_time(receipt.get("started_at"))],
            ["ended", format_local_time(receipt.get("ended_at"))],
            ["duration", format_duration(receipt.get("duration_seconds") or 0)],
            ["meter start Wh", receipt.get("meter_start_wh")],
            ["meter stop Wh", receipt.get("meter_stop_wh")],
            ["energy kWh", None if energy_wh is None else f"{energy_wh / 1000:.3f}"],
            ["stop reason", receipt.get("stop_reason")],
            ["bill status", receipt.get("bill_status")],
            ["total", receipt.get("total_amount")],
        ],
    )


def print_bill(bill: dict[str, Any]) -> None:
    """Print a session's bill, with the hold reason when ON_HOLD.

    Args:
        bill: ``SessionBillResponse`` body.
    """
    energy_wh = to_float(bill.get("energy_wh"))
    print(f"\nBill {bill.get('bill_id')}")
    print_table(
        ["field", "value"],
        [
            ["status", bill.get("status")],
            ["reason", bill.get("status_reason")],
            [
                "energy kWh",
                None if energy_wh is None else f"{energy_wh / 1000:.3f}",
            ],
            ["energy source", bill.get("energy_source")],
            ["price/kWh", bill.get("price_per_kwh")],
            ["before VAT", bill.get("amount_before_vat")],
            ["VAT %", bill.get("vat_rate_percent")],
            ["VAT", bill.get("vat_amount")],
            ["total", bill.get("total_amount")],
            ["paid", bill.get("is_paid")],
        ],
    )
    if bill.get("status") == "ON_HOLD":
        print(f"ON HOLD: {bill.get('status_reason')}")


def command_scan(args: argparse.Namespace) -> None:
    """Scan a charger, print the pending session, then watch it.

    Args:
        args: Parsed CLI arguments.

    Raises:
        ApiError: The scan was refused (404 unknown charger/gun, 403 not
            allowed, 409 busy gun / open charge / low balance, 400 no tariff).
        RuntimeError: The backend is unreachable.
    """
    session = open_session(args)
    charger_code = args.charger or args.qr or tool_setting("TEST_DAY_CHARGER_ID")
    if not charger_code:
        raise RuntimeError(
            "Name the charger with --charger or --qr (or set TEST_DAY_CHARGER_ID)"
        )
    scan_request: dict[str, object] = {"charger_code": charger_code}
    if args.gun is not None:
        scan_request["connector_number"] = args.gun
    scan = session.call("scan", "POST", f"{SESSIONS_PATH}/scan", scan_request)
    print_table(
        ["field", "value"],
        [
            ["session_id", scan.get("session_id")],
            ["status", scan.get("status")],
            ["station_id", scan.get("station_id")],
            ["gun", args.gun if args.gun is not None else "pick on the charger"],
            [
                "price",
                f"{scan.get('price_per_kwh')} {scan.get('currency')}/kWh "
                f"+ {scan.get('vat_rate_percent')}% VAT",
            ],
            ["token", mask_token(scan.get("id_token"))],
            ["command_id", scan.get("command_id")],
            ["expires", format_local_time(scan.get("expires_at"))],
        ],
    )
    watch_session(session, args, str(scan["session_id"]))


def command_watch(args: argparse.Namespace) -> None:
    """Watch an existing session.

    Args:
        args: Parsed CLI arguments.
    """
    watch_session(open_session(args), args, args.session_id)


def command_stop(args: argparse.Namespace) -> None:
    """Stop a session from any terminal.

    Args:
        args: Parsed CLI arguments.
    """
    request_stop(open_session(args), args.session_id)


def command_sessions(args: argparse.Namespace) -> None:
    """List the driver's own sessions, newest first.

    Args:
        args: Parsed CLI arguments.
    """
    session = open_session(args)
    query = urlencode({"page_size": args.limit})
    page = session.call("sessions", "GET", f"{SESSIONS_PATH}/mine?{query}")
    rows = []
    for item in page.get("items", []):
        energy_wh = to_float(item.get("energy_delivered_wh"))
        rows.append(
            [
                item.get("session_id"),
                item.get("status"),
                format_local_time(item.get("created_at")),
                format_local_time(item.get("started_at")),
                format_local_time(item.get("ended_at")),
                None if energy_wh is None else f"{energy_wh / 1000:.3f}",
                item.get("stop_reason"),
            ]
        )
    print_table(
        ["session_id", "status", "scanned", "started", "ended", "kWh", "stop"],
        rows,
    )
    print(f"{len(rows)} of {page.get('total')} session(s)")


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
        "--driver",
        type=positive_int,
        default=1,
        help="Test driver number from seed_test_drivers.py (default 1).",
    )
    common.add_argument(
        "--phone", default=None, help="Log in with this phone instead of --driver."
    )
    common.add_argument(
        "--phone-prefix",
        default=TEST_DRIVER_PHONE_PREFIX,
        help="Phone prefix of the test drivers (as given to the seed).",
    )
    common.add_argument(
        "--password",
        default=None,
        help=f"Default: ${TEST_DRIVER_PASSWORD_ENV}, else the test default.",
    )
    common.add_argument(
        "--api-url", default=None, help="Backend URL; default $G3_API_URL or localhost."
    )
    common.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Evidence file (JSON lines); default "
        "logs/test-day/driver_app-<date>.jsonl in this folder.",
    )
    watching = argparse.ArgumentParser(add_help=False)
    watching.add_argument(
        "--interval",
        type=positive_float,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"Seconds between polls (default {DEFAULT_INTERVAL_SECONDS:g}).",
    )

    parser = argparse.ArgumentParser(
        description="Stand-in for the driver app: scan, watch, stop, history."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser(
        "scan", parents=[common, watching], help="Scan a charger and watch the charge."
    )
    target = scan.add_mutually_exclusive_group()
    target.add_argument("--charger", help="Charger code: OCPP identity or serial.")
    target.add_argument("--qr", help="Raw QR text, sent unchanged as charger_code.")
    scan.add_argument(
        "--gun",
        type=positive_int,
        default=None,
        help="Gun number; default: the driver picks it on the charger.",
    )
    scan.set_defaults(handler=command_scan)

    watch = commands.add_parser(
        "watch", parents=[common, watching], help="Watch an existing session."
    )
    watch.add_argument("session_id")
    watch.set_defaults(handler=command_watch)

    stop = commands.add_parser("stop", parents=[common], help="Stop a session.")
    stop.add_argument("session_id")
    stop.set_defaults(handler=command_stop)

    sessions = commands.add_parser(
        "sessions", parents=[common], help="List my sessions."
    )
    sessions.add_argument("--limit", type=positive_int, default=10)
    sessions.set_defaults(handler=command_sessions)
    return parser


def main() -> None:
    """Parse the arguments and run the subcommand (exit 1 on an API error)."""
    args = build_parser().parse_args()
    run_tool(lambda: args.handler(args))


if __name__ == "__main__":
    main()
