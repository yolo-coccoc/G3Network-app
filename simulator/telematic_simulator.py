"""Simulate Telematics continuously publishing telemetry over MQTT."""

import asyncio
import json
import random
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from uuid import uuid4

import aiomqtt

from app.domains.telemetry.schemas import TelemetryMessage

# Only edit this block to change the API, broker, coordinates, or send interval.
API_BASE_URL = "http://localhost:8000"
MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_USERNAME: str | None = None
MQTT_PASSWORD: str | None = None
PUBLISH_INTERVAL_SECONDS = 5
START_LATITUDE = 10.762622
START_LONGITUDE = 106.660172
REQUEST_TIMEOUT_SECONDS = 10


def get_telematic_serials() -> list[str]:
    """Get the serials of existing Telematics from the CRUD API.

    Returns:
        List of device serials that have not been soft-deleted.

    Raises:
        RuntimeError: When the API is unreachable or returns an error response.
    """
    request = Request(
        f"{API_BASE_URL}/api/v1/telematics/?page=1&page_size=100",
        method="GET",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise RuntimeError(f"Failed to get the Telematic list: {error}") from error

    serials = [item["telematic_serial"] for item in payload["items"]]
    if not serials:
        raise RuntimeError("API did not return any Telematics to simulate")
    return serials


async def publish_for_device(client: aiomqtt.Client, serial: str, index: int) -> None:
    """Generate and publish telemetry for a single Telematic.

    Args:
        client: Connected MQTT client.
        serial: Serial of the simulated device.
        index: Index used to give each device a different position.
    """
    odometer = 12500.0 + index * 100
    while True:
        payload = {
            "message_uuid": str(uuid4()),
            "telematic_serial": serial,
            "recorded_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "location": {
                "latitude": START_LATITUDE + random.uniform(-0.001, 0.001),
                "longitude": START_LONGITUDE + random.uniform(-0.001, 0.001),
            },
            "vehicle_state": {
                "speed": random.uniform(20, 80),
                "heading": random.uniform(0, 360),
                "odometer": odometer,
            },
            "battery": {
                "soc": random.uniform(20.0, 95.0),
                "voltage": 650.0,
                "current": -120.0,
                "temperature": random.uniform(25.0, 40.0),
            },
            "motor": {"temperature": 45.0},
            "signal": {"strength": -70},
            "errors": [],
        }
        message = TelemetryMessage.model_validate(payload)
        topic = f"g3network/telematics/{serial}/telemetry"
        await client.publish(
            topic, json.dumps(message.model_dump(mode="json")), qos=0, retain=False
        )
        print(f"Sent telemetry: {serial}")
        odometer += 0.2
        await asyncio.sleep(PUBLISH_INTERVAL_SECONDS)


async def main() -> None:
    """Connect to the broker and run a task for every device."""
    serials = get_telematic_serials()
    print(f"Found {len(serials)} Telematics: {', '.join(serials)}")
    async with aiomqtt.Client(
        hostname=MQTT_HOST,
        port=MQTT_PORT,
        username=MQTT_USERNAME,
        password=MQTT_PASSWORD,
        identifier="g3network-telematic-simulator",
    ) as client:
        tasks = [
            asyncio.create_task(publish_for_device(client, serial, index))
            for index, serial in enumerate(serials, start=1)
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped simulator")
