"""Provision the minimal charging topology for the local OCPP simulator.

The script calls the station, EVSE, and connector creation APIs in sequence.
It does not recreate topology that already exists, does not retry, and
contains no charging business logic; each run should use a new
``ocpp_identity`` if the database already has that identity.
"""

import argparse
import json
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_IDENTITY = "SIM-OCPP-001"
DEFAULT_DISPLAY_NAME = "Charging Simulator"
REQUEST_TIMEOUT_SECONDS = 10


def post_json(
    api_url: str,
    path: str,
    payload: Mapping[str, object],
) -> dict[str, Any]:
    """Send a JSON POST to the backend and return the response object.

    Args:
        api_url: Base URL of the backend; a trailing ``/`` is optional.
        path: Relative API path.
        payload: JSON object sent in the request body.

    Returns:
        JSON response as an object.

    Raises:
        RuntimeError: If the request fails, the response is not a JSON
            object, or the backend returns an error HTTP status.
    """
    request = Request(
        f"{api_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw_response = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8")
        raise RuntimeError(f"POST {path} returned HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Could not call POST {path}: {error.reason}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"POST {path} returned invalid JSON") from error

    if not isinstance(raw_response, dict):
        raise RuntimeError(f"POST {path} did not return a JSON object")
    return raw_response


def required_id(response: Mapping[str, Any], field_name: str) -> str:
    """Get an internal ID from the provisioning response and check its type.

    Args:
        response: JSON object returned by the API.
        field_name: Name of the ID field to retrieve.

    Returns:
        ID as a string, to embed in the URL of the next request.

    Raises:
        RuntimeError: If the response is missing the ID or it is not a string.
    """
    value = response.get(field_name)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Response is missing a valid {field_name}: {response}")
    return value


def positive_int(value: str) -> int:
    """Parse a positive integer for an OCPP topology parameter.

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


def parse_args() -> argparse.Namespace:
    """Read the CLI parameters for a topology simulator.

    Returns:
        Namespace containing the backend URL, identity, and OCPP IDs.
    """
    parser = argparse.ArgumentParser(
        description="Create a station, EVSE, and connector for the OCPP simulator."
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--identity", default=DEFAULT_IDENTITY)
    parser.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
    parser.add_argument("--evse-id", type=positive_int, default=1)
    parser.add_argument("--connector-id", type=positive_int, default=1)
    return parser.parse_args()


def main() -> None:
    """Create the station, EVSE, and connector in correct foreign-key order."""
    args = parse_args()
    station = post_json(
        args.api_url,
        "/api/v1/charging-stations",
        {
            "ocpp_identity": args.identity,
            "display_name": args.display_name,
        },
    )
    station_id = required_id(station, "station_id")

    evse = post_json(
        args.api_url,
        f"/api/v1/charging-stations/{station_id}/evses",
        {"ocpp_evse_id": args.evse_id},
    )
    evse_id = required_id(evse, "evse_id")

    connector = post_json(
        args.api_url,
        f"/api/v1/charging-evses/{evse_id}/connectors",
        {"ocpp_connector_id": args.connector_id},
    )
    connector_id = required_id(connector, "connector_id")

    print(f"Provisioned station: {args.identity} ({station_id})")
    print(f"Provisioned EVSE: {args.evse_id} ({evse_id})")
    print(f"Provisioned connector: {args.connector_id} ({connector_id})")


if __name__ == "__main__":
    main()
