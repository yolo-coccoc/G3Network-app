"""Provision the minimal charging topology for the local OCPP simulators.

Logs in as the bootstrap HEAD_ADMIN (see ``api_client.py``), then creates in
foreign-key order: a public location (unless ``--location-id`` names one), the
station, its EVSEs and connectors, and a location tariff so a QR scan can be
priced (a charger with no tariff in force refuses the scan). The rows belong
to the internal organization. The script does not recreate topology that
already exists, does not retry, and contains no charging business logic; each
run should use a new ``ocpp_identity`` if the database already has that
identity.

Two layouts are supported:

* ``--protocol 2.0.1`` (default): one EVSE and one connector, with the IDs given
  by ``--evse-id`` and ``--connector-id``.
* ``--protocol 1.6``: OCPP 1.6J has no EVSE level, so each gun ``n`` (1..
  ``--connectors``) becomes EVSE ``n`` holding connector ``1`` (decision D3 of
  ``docs/planners/backend-ocpp16-charger-integration.md``). Connector ``0``
  (the whole charger) needs no topology row.

Every gun is a 240 kW DC connector, the shape of the Willdigits charger the
1.6J gateway targets; its plug standard is CCS2 (``IEC_62196_T2_COMBO``, the
delivered unit, confirmed 2026-10-12) unless ``--connector-standard`` names
another value of the backend's ``ConnectorStandard`` (e.g. ``GBT_DC``). The tariff price
is ``--price-per-kwh`` (default ``DEFAULT_PRICE_PER_KWH``).
"""

import argparse
from collections.abc import Mapping
from typing import Any

from api_client import ApiClient, login_as_bootstrap_admin, resolve_api_url

from app.domains.charging_stations.types import ConnectorStandard

DEFAULT_IDENTITY = "SIM-OCPP-001"
DEFAULT_DISPLAY_NAME = "Charging Simulator"
# Price of the location tariff the seed publishes: whole dong per kWh, VAT %.
DEFAULT_PRICE_PER_KWH = 3500
DEFAULT_VAT_RATE_PERCENT = "8"
# The connector every gun gets; ``standard`` is the default of
# ``--connector-standard``.
DEFAULT_CONNECTOR_STANDARD = ConnectorStandard.IEC_62196_T2_COMBO.value
CONNECTOR_SPEC = {
    "standard": DEFAULT_CONNECTOR_STANDARD,
    "max_power_kw": 240,
    "max_voltage_v": 1000,
    "max_current_a": 250,
}


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


def provision_location(client: ApiClient, display_name: str) -> str:
    """Create a public location for the simulated charger.

    Args:
        client: Logged-in API client.
        display_name: Name shown for the place.

    Returns:
        The new location's internal ID.

    Raises:
        RuntimeError: If the request fails or the response lacks the ID.
    """
    location = client.post(
        "/api/v1/charging-locations",
        {
            "display_name": display_name,
            "address": "Simulator depot",
            "latitude": 10.762622,
            "longitude": 106.660172,
            "is_public": True,
        },
    )
    location_id = required_id(location, "location_id")
    print(f"Provisioned location: {display_name} ({location_id})")
    return location_id


def provision_tariff(
    client: ApiClient, location_id: str, price_per_kwh: int, vat_rate_percent: str
) -> None:
    """Create a location tariff and publish its first version, in force now.

    Args:
        client: Logged-in API client.
        location_id: The location the tariff prices.
        price_per_kwh: Price per kWh before VAT, whole dong.
        vat_rate_percent: VAT rate as a decimal string.

    Raises:
        RuntimeError: If a request fails or a response lacks the expected ID.
    """
    tariff = client.post(
        "/api/v1/tariffs",
        {"name": "Simulator tariff", "location_id": location_id},
    )
    tariff_id = required_id(tariff, "tariff_id")
    client.post(
        f"/api/v1/tariffs/{tariff_id}/versions",
        {
            "price_per_kwh": price_per_kwh,
            "vat_rate_percent": vat_rate_percent,
            "change_reason": "Simulator seed",
        },
    )
    print(f"Provisioned tariff: {price_per_kwh} VND/kWh + {vat_rate_percent}% VAT")


def provision_evse(
    client: ApiClient,
    station_id: str,
    identity: str,
    evse_id: int,
    connector_id: int,
    connector_standard: str = DEFAULT_CONNECTOR_STANDARD,
) -> None:
    """Create one EVSE under a station and one connector under that EVSE.

    Args:
        client: Logged-in API client.
        station_id: Internal ID of the station that owns the EVSE.
        identity: The station's OCPP identity, used to build a unique eMI3 ID.
        evse_id: OCPP EVSE ID to create.
        connector_id: OCPP connector ID to create under the new EVSE.
        connector_standard: Plug standard of the connector
            (``ConnectorStandard`` value).

    Raises:
        RuntimeError: If a request fails or a response lacks the expected ID.
    """
    evse = client.post(
        f"/api/v1/charging-stations/{station_id}/evses",
        {"ocpp_evse_id": evse_id, "emi3_evse_id": f"VN*G3N*E{identity}*{evse_id}"},
    )
    evse_uuid = required_id(evse, "evse_id")
    connector = client.post(
        f"/api/v1/charging-evses/{evse_uuid}/connectors",
        {
            "ocpp_connector_id": connector_id,
            **CONNECTOR_SPEC,
            "standard": connector_standard,
        },
    )
    connector_uuid = required_id(connector, "connector_id")
    print(f"Provisioned EVSE: {evse_id} ({evse_uuid})")
    print(
        f"Provisioned connector: {connector_id} {connector_standard} ({connector_uuid})"
    )


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
        Namespace containing the backend URL, identity, location, price,
        connector standard and OCPP IDs.
    """
    parser = argparse.ArgumentParser(
        description="Create a location, station, EVSEs, connectors and a tariff "
        "for the OCPP simulator."
    )
    parser.add_argument(
        "--api-url", default=None, help="Backend URL; default $G3_API_URL or localhost."
    )
    parser.add_argument("--identity", default=DEFAULT_IDENTITY)
    parser.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
    parser.add_argument(
        "--location-id",
        default=None,
        help="Existing location to put the charger in; default: create one "
        "(with its own tariff).",
    )
    parser.add_argument(
        "--price-per-kwh",
        type=positive_int,
        default=DEFAULT_PRICE_PER_KWH,
        help="Tariff price before VAT, whole dong per kWh (only when a "
        f"location is created; default {DEFAULT_PRICE_PER_KWH}).",
    )
    parser.add_argument(
        "--connector-standard",
        choices=[standard.value for standard in ConnectorStandard],
        default=DEFAULT_CONNECTOR_STANDARD,
        help=f"Plug standard of every gun (default {DEFAULT_CONNECTOR_STANDARD}).",
    )
    parser.add_argument(
        "--protocol",
        choices=("2.0.1", "1.6"),
        default="2.0.1",
        help="Topology layout to create; 1.6 makes one EVSE per gun.",
    )
    parser.add_argument(
        "--evse-id",
        type=positive_int,
        default=1,
        help="2.0.1 only: OCPP EVSE ID.",
    )
    parser.add_argument(
        "--connector-id",
        type=positive_int,
        default=1,
        help="2.0.1 only: OCPP connector ID.",
    )
    parser.add_argument(
        "--connectors",
        type=positive_int,
        default=2,
        help="1.6 only: number of guns (EVSE n / connector 1 for each).",
    )
    return parser.parse_args()


def main() -> None:
    """Create the location, tariff, station, EVSEs and connectors in FK order."""
    args = parse_args()
    client = login_as_bootstrap_admin(resolve_api_url(args.api_url))
    location_id = args.location_id
    if location_id is None:
        location_id = provision_location(client, f"{args.display_name} depot")
        provision_tariff(
            client, location_id, args.price_per_kwh, DEFAULT_VAT_RATE_PERCENT
        )
    station = client.post(
        "/api/v1/charging-stations",
        {
            "location_id": location_id,
            "ocpp_identity": args.identity,
            "registered_serial_number": f"{args.identity}-SN",
            "max_power_kw": CONNECTOR_SPEC["max_power_kw"],
        },
    )
    station_id = required_id(station, "station_id")

    print(f"Provisioned station: {args.identity} ({station_id})")
    if args.protocol == "1.6":
        for gun in range(1, args.connectors + 1):
            provision_evse(
                client, station_id, args.identity, gun, 1, args.connector_standard
            )
    else:
        provision_evse(
            client,
            station_id,
            args.identity,
            args.evse_id,
            args.connector_id,
            args.connector_standard,
        )


if __name__ == "__main__":
    main()
