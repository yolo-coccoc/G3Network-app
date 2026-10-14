"""Reads and changes a charger's OCPP configuration through the backend API.

Everything goes through the backend's command channel, the way the admin
portal will do it:

* **Get:** queue a ``GET_CONFIGURATION`` command
  (``POST /api/v1/charging-stations/{id}/commands``); the gateway asks the
  charger and stores what it reports as a snapshot; once the command is
  answered, read the newest snapshot (``GET .../configuration``).
* **Send:** OCPP 1.6 changes one key per request, so each filled field becomes
  one ``CHANGE_CONFIGURATION`` command, sent one after the other, each waited
  for. The charger's own answer (``Accepted``, ``Rejected``, ``NotSupported``,
  ``RebootRequired``) is reported per key. When at least one change was
  accepted, a fresh snapshot is taken (reason ``AFTER_CHANGE``).

A command waits up to 30 s for the gateway to pick it up and up to 30 s for
the charger's answer (the backend's defaults), so each wait here allows a
little more than both.
"""

from __future__ import annotations

import time
from typing import Any

from api_client import ApiClient, ApiError

STATIONS_PATH = "/api/v1/charging-stations"

# Longest wait for one command's outcome: gateway pickup + charger answer.
COMMAND_WAIT_SECONDS = 70.0
COMMAND_POLL_SECONDS = 1.0

# The ``reason`` stored with each command, so staff can tell where it came from.
COMMAND_REASON = "Charger monitor"


def to_page_configuration(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Turn a configuration snapshot into what the page shows.

    Args:
        snapshot: A ``ChargingStationConfigurationResponse``.

    Returns:
        ``captured_at`` (``None`` if the charger never reported one) and the
        keys: name (prefixed with the component for OCPP 2.0.1), value and
        whether it is read-only.
    """
    items = []
    for entry in snapshot.get("items", []):
        name = entry["variable_name"]
        if entry.get("component_name"):
            name = f"{entry['component_name']}.{name}"
        items.append(
            {
                "key": name,
                "value": entry.get("value"),
                "readonly": entry.get("mutability") == "READ_ONLY",
            }
        )
    return {"captured_at": snapshot.get("captured_at"), "items": items}


class ConfigurationService:
    """Gets and changes chargers' configuration through the backend API.

    Attributes:
        client: The logged-in API client.
    """

    def __init__(self, client: ApiClient) -> None:
        """Keep the client.

        Args:
            client: The logged-in API client.
        """
        self.client = client

    def latest(self, station_id: str) -> dict[str, Any]:
        """Return the newest configuration snapshot the backend has stored.

        Asks the charger nothing; the snapshot may be from its last boot.

        Args:
            station_id: The charger's ID.

        Returns:
            The page's configuration (see ``to_page_configuration``).
        """
        snapshot = self.client.get(f"{STATIONS_PATH}/{station_id}/configuration")
        return to_page_configuration(snapshot)

    def request(self, station_id: str, reason: str = "ON_DEMAND") -> dict[str, Any]:
        """Ask the charger for its configuration now and return it.

        Args:
            station_id: The charger's ID.
            reason: Why the snapshot is taken (``ON_DEMAND`` or
                ``AFTER_CHANGE``).

        Returns:
            The page's configuration, plus ``outcome`` and ``answer`` of the
            ``GET_CONFIGURATION`` command.

        Raises:
            ApiError: The backend refused the command, or the charger did not
                answer it (offline, timed out, refused).
        """
        command = self._run(station_id, "GET_CONFIGURATION", {"capture_reason": reason})
        if command.get("outcome") != "ACCEPTED":
            raise ApiError(
                None,
                "the charger did not report its configuration "
                f"(outcome {command.get('outcome')}, "
                f"answer {command.get('response_status') or '-'})",
            )
        configuration = self.latest(station_id)
        configuration["outcome"] = command.get("outcome")
        return configuration

    def change(self, station_id: str, changes: dict[str, str]) -> dict[str, Any]:
        """Send each change as its own command and report each answer.

        Args:
            station_id: The charger's ID.
            changes: Key -> new value; only the keys to change.

        Returns:
            ``results`` (per key: value, outcome, the charger's answer, and an
            error when the backend refused the command) and, when any change
            was accepted, the ``configuration`` read back afterwards.
        """
        results = []
        for key, value in changes.items():
            try:
                command = self._run(
                    station_id, "CHANGE_CONFIGURATION", {"key": key, "value": value}
                )
                results.append(
                    {
                        "key": key,
                        "value": value,
                        "outcome": command.get("outcome"),
                        "answer": command.get("response_status"),
                        "error": None,
                    }
                )
            except ApiError as error:
                results.append(
                    {
                        "key": key,
                        "value": value,
                        "outcome": "ERROR",
                        "answer": None,
                        "error": error.detail,
                    }
                )
        answer: dict[str, Any] = {"results": results, "configuration": None}
        if any(result["outcome"] == "ACCEPTED" for result in results):
            try:
                answer["configuration"] = self.request(station_id, "AFTER_CHANGE")
            except ApiError as error:
                answer["configuration_error"] = error.detail
        return answer

    def _run(
        self, station_id: str, command_type: str, parameters: dict[str, object]
    ) -> dict[str, Any]:
        """Queue one command and wait until it has an outcome.

        Args:
            station_id: The charger's ID.
            command_type: The backend's ``StationCommandType`` value.
            parameters: The command's parameters.

        Returns:
            The final ``ChargingStationCommandResponse`` (still ``PENDING`` if
            the wait ran out).

        Raises:
            ApiError: The backend refused to queue or read the command.
        """
        path = f"{STATIONS_PATH}/{station_id}/commands"
        command: dict[str, Any] = self.client.post(
            path,
            {
                "command_type": command_type,
                "parameters": parameters,
                "reason": COMMAND_REASON,
            },
        )
        deadline = time.monotonic() + COMMAND_WAIT_SECONDS
        while command.get("outcome") == "PENDING" and time.monotonic() < deadline:
            time.sleep(COMMAND_POLL_SECONDS)
            command = self.client.get(f"{path}/{command['command_id']}")
        return command
