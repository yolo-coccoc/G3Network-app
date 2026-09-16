"""Quickly create a set of vehicles and Telematics for the local simulator."""

import json
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Only edit this block when you want to generate a different simulator dataset.
API_BASE_URL = "http://localhost:8000"
DEVICE_COUNT = 1
TELEMATIC_SERIAL_PREFIX = "TBOX-SIM-"
VIN_PREFIX = "SIMULATORVIN"
LICENSE_PLATE_PREFIX = "SIM-"
REQUEST_TIMEOUT_SECONDS = 10


def post(path: str, payload: dict[str, object]) -> dict[str, object]:
    """Send a JSON POST to the API and return the JSON response.

    Args:
        path: Relative API path.
        payload: JSON request content.

    Returns:
        JSON response content.

    Raises:
        RuntimeError: When the API call fails or returns an HTTP error.
    """
    request = Request(
        f"{API_BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            response_data = json.loads(response.read().decode("utf-8"))
            return cast(dict[str, object], response_data)
    except (HTTPError, URLError) as error:
        detail = (
            error.read().decode("utf-8") if isinstance(error, HTTPError) else str(error)
        )
        raise RuntimeError(f"POST {path} failed: {detail}") from error


def main() -> None:
    """Create the vehicle first, then create the Telematic with the matching VIN."""
    for index in range(1, DEVICE_COUNT + 1):
        suffix = f"{index:05d}"
        vin = f"{VIN_PREFIX}{suffix}"
        vehicle_payload = {
            "license_plate": f"{LICENSE_PLATE_PREFIX}{index:03d}",
            "vin": vin,
            "make": "G3Network",
            "model": "E-Truck Simulator",
            "year": 2026,
            "status": "ACTIVE",
            "fleet_id": None,
        }
        vehicle = post("/api/v1/vehicles/", vehicle_payload)
        telematic_serial = f"{TELEMATIC_SERIAL_PREFIX}{suffix}"
        telematic = post(
            "/api/v1/telematics/",
            {
                "telematic_serial": telematic_serial,
                "vehicle_vin": vin,
                "status": "ACTIVE",
                "firmware_version": "simulator-1.0.0",
            },
        )
        print(
            f"Created {telematic['telematic_serial']} -> "
            f"vehicle_id={vehicle['vehicle_id']} -> VIN={vin}"
        )


if __name__ == "__main__":
    main()
