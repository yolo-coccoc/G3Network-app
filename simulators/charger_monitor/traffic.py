"""Reads a charger's OCPP traffic from the backend API, for the page.

The backend stores every frame it receives or sends in its OCPP message log
(``GET /api/v1/charging-stations/{id}/ocpp-messages``, internal staff only).
Asking for the frame text writes an access-audit row on the backend, so this
module asks for it only when it is needed:

* **New messages:** a cheap probe without frame text first; the frames are
  fetched only when something new arrived.
* **Startup message:** the latest ``BootNotification`` and its answer are
  fetched once per boot and kept until the charger boots again.
* **Heartbeats:** only their times are needed, so no frame text at all.

Frames can hold RFID / QR tokens (``idTag``); they are masked here, before
anything reaches the browser.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from api_client import ApiClient, ApiError

STATIONS_PATH = "/api/v1/charging-stations"

# How many frames the page gets on first load, and at most per refresh.
INITIAL_MESSAGES = 50
MAX_NEW_MESSAGES = 100

# How many heartbeats the interval check covers.
HEARTBEAT_HISTORY = 30

# How long after a BootNotification its answer is searched for.
BOOT_ANSWER_WINDOW = timedelta(seconds=60)

# A heartbeat counts as on time when its gap from the previous one is within
# this many seconds of the interval, or this share of it, whichever is larger.
ON_TIME_TOLERANCE_SECONDS = 2.0
ON_TIME_TOLERANCE_SHARE = 0.1

# Keys whose string values are tokens: RFID cards and QR codes (1.6 idTag,
# parentIdTag; 2.0.1 idToken).
SECRET_KEYS = frozenset({"idTag", "parentIdTag", "idToken"})

MESSAGE_TYPES = {2: "CALL", 3: "RESULT", 4: "CALLERROR"}


def mask_secrets(value: Any) -> Any:
    """Return a copy with every token masked to its first two characters.

    Args:
        value: A decoded JSON value.

    Returns:
        The same structure, with the string under each secret key masked.
    """
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            if key in SECRET_KEYS and isinstance(item, str):
                masked[key] = f"{item[:2]}…" if item else item
            else:
                masked[key] = mask_secrets(item)
        return masked
    if isinstance(value, list):
        return [mask_secrets(item) for item in value]
    return value


def parse_time(text: str | None) -> datetime | None:
    """Parse an API timestamp.

    Args:
        text: ISO 8601 text, or ``None``.

    Returns:
        The timezone-aware time, or ``None``.
    """
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def to_page_message(item: dict[str, Any]) -> dict[str, Any]:
    """Turn one logged frame into what the page shows (tokens masked).

    Args:
        item: One ``ChargingOcppMessageResponse`` with its ``raw_frame``.

    Returns:
        Time, direction, message type, action, the masked frame and, for a
        CALLERROR, its code and description. The raw text is never passed on.
    """
    frame: Any = None
    if item.get("raw_frame"):
        try:
            frame = mask_secrets(json.loads(item["raw_frame"]))
        except ValueError:
            frame = "<not JSON>"
    message_type = ""
    error = None
    if isinstance(frame, list) and frame:
        message_type = MESSAGE_TYPES.get(frame[0], str(frame[0]))
        if frame[0] == 4:
            error = " ".join(str(part) for part in frame[2:4])
    return {
        "message_id": item["message_id"],
        "occurred_at": item["occurred_at"],
        "direction": item["direction"],
        "action": item.get("action"),
        "ocpp_message_id": item.get("ocpp_message_id"),
        "type": message_type,
        "frame": frame,
        "error": error,
    }


def judge_gap(gap_s: float | None, interval_s: float | None) -> str:
    """Judge one heartbeat's gap from the previous one against the interval.

    Args:
        gap_s: Seconds since the previous heartbeat; ``None`` for the oldest.
        interval_s: The interval the backend gave at boot; ``None`` if unknown.

    Returns:
        ``first``, ``unknown interval``, ``on time``, ``late`` or ``early``.
    """
    if gap_s is None:
        return "first"
    if not interval_s:
        return "unknown interval"
    tolerance = max(ON_TIME_TOLERANCE_SECONDS, interval_s * ON_TIME_TOLERANCE_SHARE)
    deviation = gap_s - interval_s
    if abs(deviation) <= tolerance:
        return "on time"
    return "late" if deviation > 0 else "early"


class TrafficReader:
    """Answers the page's questions about chargers and their traffic.

    Attributes:
        client: The logged-in API client.
    """

    def __init__(self, client: ApiClient) -> None:
        """Start with an empty startup cache.

        Args:
            client: The logged-in API client.
        """
        self.client = client
        self._startup_cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def list_chargers(self) -> list[dict[str, Any]]:
        """List the chargers the staff account manages (all of them).

        Returns:
            One entry per charger: ID, identity, label, online state, last
            seen, vendor and model.
        """
        page = self.client.get(
            STATIONS_PATH, {"scope": "MANAGED", "page": 1, "page_size": 100}
        )
        return [
            {
                "station_id": item["station_id"],
                "ocpp_identity": item["ocpp_identity"],
                "location": item.get("location_display_name"),
                "is_online": item.get("is_online"),
                "last_seen_at": item.get("last_seen_at"),
                "vendor": item.get("vendor"),
                "model": item.get("model"),
                "status": item.get("status"),
            }
            for item in page.get("items", [])
        ]

    def connection(self, station_id: str) -> Any:
        """Return the charger's connection facts (online, last seen, boot...).

        Args:
            station_id: The charger's ID.

        Returns:
            The backend's ``ChargingStationConnectionResponse``.
        """
        return self.client.get(f"{STATIONS_PATH}/{station_id}/connection")

    def _messages(self, station_id: str, **params: object) -> list[dict[str, Any]]:
        """Read one page of the charger's message log, newest first.

        Args:
            station_id: The charger's ID.
            **params: Query parameters of the message-log endpoint.

        Returns:
            The logged frames.
        """
        page = self.client.get(f"{STATIONS_PATH}/{station_id}/ocpp-messages", params)
        items: list[dict[str, Any]] = page.get("items", [])
        return items

    def messages(self, station_id: str, after: str | None) -> list[dict[str, Any]]:
        """Return the frames newer than ``after`` (or the latest ones), masked.

        Without ``after``, the latest ``INITIAL_MESSAGES`` frames. With it, a
        probe without frame text checks for anything newer first, so the
        frames (and their audit row) are fetched only when there is traffic.

        Args:
            station_id: The charger's ID.
            after: The newest ``occurred_at`` the page already has.

        Returns:
            The frames for the page, newest first.
        """
        if after is None:
            items = self._messages(
                station_id, include_raw_frame="true", page_size=INITIAL_MESSAGES
            )
            return [to_page_message(item) for item in items]
        newest = parse_time(after)
        if newest is None:
            return []
        # The lower bound is inclusive; one microsecond later skips the frame
        # the page already has (the log stores microseconds).
        since = (newest + timedelta(microseconds=1)).isoformat()
        if not self._messages(station_id, occurred_from=since, page_size=1):
            return []
        items = self._messages(
            station_id,
            occurred_from=since,
            include_raw_frame="true",
            page_size=MAX_NEW_MESSAGES,
        )
        return [to_page_message(item) for item in items]

    def startup(self, station_id: str) -> dict[str, Any] | None:
        """Return the latest BootNotification and the backend's answer.

        Fetched with frames once per boot: a probe without frame text finds
        the latest boot, and the cached exchange is reused while it is the
        same one.

        Args:
            station_id: The charger's ID.

        Returns:
            ``request`` and ``answer`` (page messages; the answer may be
            ``None``), the boot ``status`` and heartbeat ``interval`` from the
            answer; ``None`` if the charger never booted.
        """
        latest = self._messages(
            station_id,
            action="BootNotification",
            direction="CP_TO_CSMS",
            page_size=1,
        )
        if not latest:
            return None
        boot_id = latest[0]["message_id"]
        with self._lock:
            cached = self._startup_cache.get(station_id)
        if cached is not None and cached["boot_message_id"] == boot_id:
            return cached

        request = self._messages(
            station_id,
            action="BootNotification",
            direction="CP_TO_CSMS",
            include_raw_frame="true",
            page_size=1,
        )[0]
        sent_at = parse_time(request["occurred_at"])
        answer = None
        if sent_at is not None:
            candidates = self._messages(
                station_id,
                direction="CSMS_TO_CP",
                occurred_from=sent_at.isoformat(),
                occurred_to=(sent_at + BOOT_ANSWER_WINDOW).isoformat(),
                include_raw_frame="true",
                page_size=100,
            )
            answer = next(
                (
                    item
                    for item in candidates
                    if item.get("ocpp_message_id") == request.get("ocpp_message_id")
                ),
                None,
            )
        payload: dict[str, Any] = {}
        page_answer = to_page_message(answer) if answer else None
        if page_answer and page_answer["type"] == "RESULT":
            frame = page_answer["frame"]
            if (
                isinstance(frame, list)
                and len(frame) > 2
                and isinstance(frame[2], dict)
            ):
                payload = frame[2]
        result = {
            "boot_message_id": request["message_id"],
            "request": to_page_message(request),
            "answer": page_answer,
            "status": payload.get("status") or ("CALLERROR" if page_answer else None),
            "interval": payload.get("interval"),
        }
        with self._lock:
            self._startup_cache[station_id] = result
        return result

    def _configured_interval(
        self, station_id: str, booted_at: datetime | None
    ) -> tuple[float | None, datetime | None]:
        """Find a ``HeartbeatInterval`` the charger reported after its boot.

        A ``ChangeConfiguration`` can change the interval after the boot; the
        newest configuration snapshot then holds the value in force.

        Args:
            station_id: The charger's ID.
            booted_at: When the latest BootNotification arrived.

        Returns:
            ``(interval, captured_at)`` from a snapshot taken after the boot,
            or ``(None, None)`` when there is none (or it cannot be read).
        """
        try:
            snapshot = self.client.get(f"{STATIONS_PATH}/{station_id}/configuration")
        except ApiError:
            return None, None
        captured_at = parse_time(snapshot.get("captured_at"))
        if captured_at is None or (booted_at is not None and captured_at <= booted_at):
            return None, None
        for entry in snapshot.get("items", []):
            if entry.get("variable_name") == "HeartbeatInterval":
                value = str(entry.get("value") or "")
                if value.isdigit():
                    return float(value), captured_at
        return None, None

    def heartbeats(self, station_id: str) -> dict[str, Any]:
        """Check the latest heartbeats' gaps against the interval in force.

        Only the times are read (no frame text). Gaps are measured between
        the times the backend received consecutive heartbeats. Each gap is
        judged against the boot interval, or, for a heartbeat received after a
        newer configuration snapshot, against that snapshot's
        ``HeartbeatInterval``.

        Args:
            station_id: The charger's ID.

        Returns:
            The current interval, where it came from (``boot`` or
            ``configuration``), and the recent heartbeats, newest first, each
            with its gap, the interval it was judged against, and the check.
        """
        startup = self.startup(station_id)
        boot_interval = startup.get("interval") if startup else None
        booted_at = parse_time(startup["request"]["occurred_at"]) if startup else None
        configured, configured_at = self._configured_interval(station_id, booted_at)
        items = self._messages(
            station_id,
            action="Heartbeat",
            direction="CP_TO_CSMS",
            page_size=HEARTBEAT_HISTORY,
        )
        times = [parse_time(item["occurred_at"]) for item in items]
        rows = []
        for index, item in enumerate(items):
            current = times[index]
            previous = times[index + 1] if index + 1 < len(times) else None
            gap = (
                (current - previous).total_seconds()
                if current is not None and previous is not None
                else None
            )
            # A heartbeat that arrived after the newer snapshot was sent under
            # its interval (the snapshot is read back a few seconds after the
            # change itself, so this errs towards the new value).
            interval = boot_interval
            if configured is not None and configured_at is not None:
                if current is not None and current >= configured_at:
                    interval = configured
            rows.append(
                {
                    "received_at": item["occurred_at"],
                    "gap_s": gap,
                    "interval_s": interval,
                    "check": judge_gap(gap, interval),
                }
            )
        if configured is not None:
            return {"interval_s": configured, "source": "configuration", "recent": rows}
        return {"interval_s": boot_interval, "source": "boot", "recent": rows}
