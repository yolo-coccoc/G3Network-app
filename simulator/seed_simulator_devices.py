"""Quickly create a set of vehicles and Telematics for the local simulator.

Logs in as the bootstrap HEAD_ADMIN (see ``api_client.py``), creates one truck
model, then each truck and the T-Box mounted on it. The rows belong to the
internal organization. Not idempotent: on a database that already has these
VINs, run ``make db-reset`` (and ``make identity-bootstrap``) first.
"""

from api_client import login_as_bootstrap_admin

# Only edit this block when you want to generate a different simulator dataset.
DEVICE_COUNT = 1
TELEMATIC_SERIAL_PREFIX = "TBOX-SIM-"
VIN_PREFIX = "SIMULATORVIN"
LICENSE_PLATE_PREFIX = "SIM-"


def main() -> None:
    """Create the truck model, then each vehicle and its Telematic by VIN."""
    client = login_as_bootstrap_admin()
    vehicle_model = client.post(
        "/api/v1/vehicle-models/",
        {
            "make": "G3Network",
            "model_name": "E-Truck Simulator",
            "nominal_battery_capacity_kwh": 282,
        },
    )
    for index in range(1, DEVICE_COUNT + 1):
        suffix = f"{index:05d}"
        vin = f"{VIN_PREFIX}{suffix}"
        vehicle = client.post(
            "/api/v1/vehicles/",
            {
                "license_plate": f"{LICENSE_PLATE_PREFIX}{index:03d}",
                "vin": vin,
                "vehicle_model_id": vehicle_model["vehicle_model_id"],
                "year": 2026,
            },
        )
        telematic = client.post(
            "/api/v1/telematics/",
            {
                "telematic_serial": f"{TELEMATIC_SERIAL_PREFIX}{suffix}",
                "vehicle_vin": vin,
                "status": "ACTIVE",
            },
        )
        print(
            f"Created {telematic['telematic_serial']} -> "
            f"vehicle_id={vehicle['vehicle_id']} -> VIN={vin}"
        )


if __name__ == "__main__":
    main()
