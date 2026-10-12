"""Shared pieces of the charging test-day tools (planner backend-charging-test-day).

Used by ``seed_test_drivers.py``, ``driver_app.py`` and ``station_console.py``:

* the test-driver numbering scheme (driver ``N`` -> phone, default password),
  so the seed and the app stand-in agree without repeating the numbers;
* token masking (rule IS-07): a QR token / idTag / access token is never
  printed or written in full, only its first two characters;
* the JSON-lines evidence file every test-day run appends to;
* ``ToolSession``: an API client that logs every call as evidence and logs in
  again when the 15-minute access token expires during a long watch;
* ``run_tool``: prints an HTTP error as status + the backend's ``detail`` and
  exits non-zero instead of showing a traceback;
* small table and time formatting helpers.

The default password is a documented **test** value for a local or test
database, not a secret; set ``CHARGING_TEST_DRIVER_PASSWORD`` (or pass
``--password``) to use another one. Standard library only (plus the shared
``api_client``).
"""

import io
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api_client import ApiClient, ApiError

# Driver N's phone is this prefix plus N on two digits: +84999000001 for
# driver 1. Nine digits after +84, as the backend's normalization requires
# (security.normalize_phone_number); a range apart from the example admin
# number (+84900000001 in backend/.env.example).
TEST_DRIVER_PHONE_PREFIX = "+849990000"
TEST_DRIVER_NUMBER_DIGITS = 2
TEST_DRIVER_PASSWORD_ENV = "CHARGING_TEST_DRIVER_PASSWORD"
# Documented test default (only for local/test databases), see module docstring.
TEST_DRIVER_DEFAULT_PASSWORD = "TestDay-2026"

# JSON keys whose value is a credential: masked wherever a tool prints or logs
# a body (compared lower-case). ``idTag`` / ``idToken`` / ``parentIdTag`` are
# OCPP tokens; ``id_token`` is the QR token of a scan.
SECRET_KEYS = frozenset(
    {"id_token", "idtag", "idtoken", "parentidtag", "access_token", "refresh_token"}
)

# Evidence files go under backend/logs/, which backend/.gitignore already
# ignores (``logs/``), so a test day's evidence is never committed by accident.
EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "backend" / "logs" / "test-day"


def build_test_driver_phone(
    driver_number: int, prefix: str = TEST_DRIVER_PHONE_PREFIX
) -> str:
    """Build the phone number of test driver ``driver_number``.

    Args:
        driver_number: The driver's number, 1-based.
        prefix: Phone prefix; the number is appended on two digits.

    Returns:
        The phone number, e.g. ``+84999000001`` for driver 1.

    Raises:
        ValueError: If the number is not between 1 and 99.
    """
    if not 1 <= driver_number < 10**TEST_DRIVER_NUMBER_DIGITS:
        raise ValueError("a test driver number is between 1 and 99")
    return f"{prefix}{driver_number:0{TEST_DRIVER_NUMBER_DIGITS}d}"


def resolve_test_driver_password(password: str | None) -> str:
    """Pick the test drivers' password: argument, then env, then the default.

    Args:
        password: The ``--password`` value, or ``None``.

    Returns:
        The password to set or to log in with.
    """
    return (
        password
        or os.environ.get(TEST_DRIVER_PASSWORD_ENV)
        or TEST_DRIVER_DEFAULT_PASSWORD
    )


def mask_token(value: str | None) -> str:
    """Mask a token for display: its first two characters and an ellipsis.

    Args:
        value: The token, or ``None``.

    Returns:
        ``"AB…"`` for ``"ABCDEF..."``; ``"-"`` for ``None`` or empty.
    """
    if not value:
        return "-"
    return f"{value[:2]}…"


def mask_frame_text(frame_text: str) -> str:
    """Mask the tokens inside a raw OCPP frame (a JSON array as text).

    Args:
        frame_text: The frame as logged by the gateway.

    Returns:
        The frame re-serialized with every ``SECRET_KEYS`` value masked, or a
        placeholder when it is not valid JSON (it is then withheld, since its
        tokens cannot be found reliably).
    """
    try:
        frame = json.loads(frame_text)
    except json.JSONDecodeError:
        return "<unparsable frame withheld>"
    return json.dumps(mask_secrets(frame), ensure_ascii=False, separators=(",", ":"))


def mask_secrets(value: Any) -> Any:
    """Copy a decoded JSON value with every credential masked.

    A string under a key of ``SECRET_KEYS`` is masked with ``mask_token``; a
    dict or list under such a key (the 2.0.1 ``idToken`` object) is walked so
    its inner string is masked. A ``raw_frame`` string (an OCPP frame from the
    message log) is parsed and masked with ``mask_frame_text``.

    Args:
        value: Any decoded JSON value.

    Returns:
        The same structure, credentials masked; the input is not changed.
    """
    if isinstance(value, Mapping):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SECRET_KEYS and isinstance(item, str):
                masked[key] = mask_token(item)
            elif key == "raw_frame" and isinstance(item, str):
                masked[key] = mask_frame_text(item)
            else:
                masked[key] = mask_secrets(item)
        return masked
    if isinstance(value, list):
        return [mask_secrets(item) for item in value]
    return value


class EvidenceLog:
    """Append-only JSON-lines evidence of a test-day tool run.

    Each line: ``at`` (UTC ISO time), ``tool``, ``event`` and the extra fields
    of the call (typically ``method``, ``path``, ``status``, ``body``). Bodies
    are masked with ``mask_secrets`` before they are written. Writing errors
    are reported once on stderr and then ignored: a full disk must not stop a
    charge being watched.

    Attributes:
        path: The file the lines are appended to.
        tool: Name of the tool written on every line.
    """

    def __init__(self, tool: str, path: Path | None) -> None:
        """Open (lazily) the evidence file, creating its directory.

        Args:
            tool: Name of the tool, e.g. ``driver_app``.
            path: File to append to; ``None`` means
                ``backend/logs/test-day/<tool>-<UTC date>.jsonl``.
        """
        self.tool = tool
        self.path = path or EVIDENCE_DIR / (
            f"{tool}-{datetime.now(UTC).date().isoformat()}.jsonl"
        )
        self._is_broken = False

    def write(self, event: str, **fields: Any) -> None:
        """Append one evidence line.

        Args:
            event: What happened, e.g. ``scan`` or ``poll``.
            **fields: Extra JSON-serializable fields; ``body`` is masked.
        """
        if self._is_broken:
            return
        if "body" in fields:
            fields["body"] = mask_secrets(fields["body"])
        line = {
            "at": datetime.now(UTC).isoformat(),
            "tool": self.tool,
            "event": event,
            **fields,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as evidence_file:
                evidence_file.write(json.dumps(line, default=str) + "\n")
        except OSError as error:
            self._is_broken = True
            print(f"warning: cannot write evidence to {self.path}: {error}")


class ToolSession:
    """A logged-in API session of a test-day tool, with evidence and re-login.

    Every call is written to the evidence log (method, path, status, masked
    body). An answer ``401`` (the access token lives 15 minutes by default,
    ``IDENTITY_ACCESS_TOKEN_TTL_SECONDS``) triggers one new login and one
    retry, so a watch over a long charge keeps working.

    Attributes:
        evidence: The evidence log the calls are written to.
    """

    def __init__(self, login: Callable[[], ApiClient], evidence: EvidenceLog) -> None:
        """Log in once and keep the login function for a later re-login.

        Args:
            login: Function returning a freshly logged-in client.
            evidence: The evidence log.

        Raises:
            ApiError: The login was refused.
            RuntimeError: The backend is unreachable.
        """
        self.evidence = evidence
        self._login = login
        self._client = self._log_in()

    def _log_in(self) -> ApiClient:
        """Log in and record the outcome (never the credentials or tokens).

        Returns:
            The logged-in client.

        Raises:
            ApiError: The login was refused.
            RuntimeError: The backend is unreachable.
        """
        try:
            client = self._login()
        except ApiError as error:
            self.evidence.write("login", status=error.status_code, detail=error.detail)
            raise
        self.evidence.write("login", status=200)
        return client

    def call(
        self,
        event: str,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
        *,
        log_body: bool = True,
    ) -> Any:
        """Send one request, log it as evidence, and return the JSON body.

        Args:
            event: Evidence event name, e.g. ``scan`` or ``poll``.
            method: HTTP method.
            path: Path relative to the base URL, query string included.
            payload: JSON body, or ``None``.
            log_body: Whether a successful answer's body goes into the
                evidence (off for bulky reads such as a message-log feed whose
                content is already stored by the backend); errors always log
                their body.

        Returns:
            The decoded JSON body.

        Raises:
            ApiError: The backend answered an HTTP error (after one re-login
                for a 401).
            RuntimeError: The backend is unreachable or returned invalid JSON.
        """
        try:
            try:
                response = self._client.request(method, path, payload)
            except ApiError as error:
                if error.status_code != 401:
                    raise
                self._client = self._log_in()
                response = self._client.request(method, path, payload)
        except ApiError as error:
            self.evidence.write(
                event,
                method=method,
                path=path,
                status=error.status_code,
                body=error.body if error.body is not None else error.detail,
            )
            raise
        if log_body:
            self.evidence.write(
                event,
                method=method,
                path=path,
                status=response.status_code,
                body=response.body,
            )
        else:
            self.evidence.write(
                event, method=method, path=path, status=response.status_code
            )
        return response.body


def run_tool(handler: Callable[[], None]) -> None:
    """Run a tool's command and turn failures into a message and exit code.

    Args:
        handler: The command to run.

    Raises:
        SystemExit: ``1`` with ``HTTP <status>: <detail>`` for an HTTP error
            or the message for an unreachable backend; ``130`` on Ctrl+C.

    Side Effects:
        Makes stdout line-buffered, so a live view piped to ``tee`` or a file
        (the test-day evidence) shows each line as it is printed.
    """
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    try:
        handler()
    except ApiError as error:
        sys.exit(
            f"error: {error.method} {error.path} -> HTTP {error.status_code}: "
            f"{error.detail}"
        )
    except RuntimeError as error:
        sys.exit(f"error: {error}")
    except KeyboardInterrupt:
        print()
        sys.exit(130)


def parse_time(value: object) -> datetime | None:
    """Parse an ISO 8601 time from the API.

    Args:
        value: The JSON value (a string or ``None``).

    Returns:
        The aware datetime, or ``None`` when absent or unparsable.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_local_time(value: object) -> str:
    """Format an API time in the machine's local time zone.

    Args:
        value: An ISO 8601 string or ``None``.

    Returns:
        ``YYYY-MM-DD HH:MM:SS`` local time, or ``"-"``.
    """
    parsed = parse_time(value)
    if parsed is None:
        return "-"
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def format_duration(seconds: float) -> str:
    """Format a duration as ``H:MM:SS``.

    Args:
        seconds: Duration in seconds; a negative value counts as zero.

    Returns:
        The formatted duration.
    """
    total = max(0, int(seconds))
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def to_float(value: object) -> float | None:
    """Read a number the API may send as a JSON number or a decimal string.

    Args:
        value: The JSON value.

    Returns:
        The float, or ``None`` when absent or not a number.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def print_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    """Print rows as a left-aligned text table.

    Args:
        headers: Column titles.
        rows: One sequence of cells per row (``None`` prints as ``-``).
    """
    cells = [[("-" if cell is None else str(cell)) for cell in row] for row in rows]
    widths = [
        max([len(header)] + [len(row[index]) for row in cells])
        for index, header in enumerate(headers)
    ]
    print("  ".join(header.ljust(width) for header, width in zip(headers, widths)))
    print("  ".join("-" * width for width in widths))
    for row in cells:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)))
    if not rows:
        print("(none)")
