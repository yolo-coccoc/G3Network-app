"""OCPP 2.0.1 simulator for a local happy-path charging session.

This simulator only generates valid traffic for an already pre-provisioned
station, EVSE, and connector. It does not simulate retries, duplicates,
reconnects, delays, or random errors; those branches belong to the
reliability path, which is not part of the MVP yet.
"""

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from websockets.asyncio.client import ClientConnection, connect
from websockets.typing import Subprotocol

OCPP_SUBPROTOCOL = "ocpp2.0.1"
DEFAULT_METER_VALUES_WH = (Decimal("1250"), Decimal("1500"))


@dataclass(frozen=True, slots=True)
class SimulatorConfig:
    """Configuration for a single run of the charging session simulator.

    Attributes:
        url: Base WebSocket URL, e.g. ``ws://localhost:9000``.
        identity: OCPP identity of the pre-provisioned station.
        evse_id: Valid OCPP EVSE ID of the station.
        connector_id: Valid OCPP connector ID of the EVSE.
        transaction_id: Transaction identity assigned by the simulator.
        meter_start_wh: Starting meter value sent in ``Started``.
        meter_values_wh: The meter values to send, each as its own
            ``MeterValues`` message.
        timeout_seconds: Timeout for the entire flow.
    """

    url: str
    identity: str
    evse_id: int = 1
    connector_id: int = 1
    transaction_id: str = "SIM-TRANSACTION-001"
    meter_start_wh: Decimal = Decimal("1000")
    meter_values_wh: tuple[Decimal, ...] = DEFAULT_METER_VALUES_WH
    timeout_seconds: float = 5.0


class OCPPChargingSessionSimulator:
    """A small OCPP client that sends a session lifecycle and waits for ACKs in order.

    Attributes:
        config: Identity, topology, and transaction used in the flow.
        _sequence_number: Sequence number of the TransactionEvents sent so far.

    The connection only exists inside :meth:`run`; the simulator therefore
    keeps no registry or state to recover after a reconnect.
    """

    def __init__(self, config: SimulatorConfig) -> None:
        """Initialize the simulator with the immutable configuration of a session.

        Args:
            config: Gateway configuration and the provisioned topology.
        """
        self.config = config
        self._sequence_number = 0

    @staticmethod
    def _timestamp() -> str:
        """Build an OCPP UTC timestamp with timezone and millisecond precision.

        Returns:
            ISO-8601 timestamp ending in ``Z``.
        """
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    def _next_sequence_number(self) -> int:
        """Increment and return the sequence number for the next TransactionEvent.

        Returns:
            Positive sequence number following the Started, Updated, Ended order.
        """
        self._sequence_number += 1
        return self._sequence_number

    def _transaction_event_payload(
        self,
        *,
        event_type: str,
        trigger_reason: str,
        meter_wh: Decimal | None = None,
        stopped_reason: str | None = None,
    ) -> dict[str, Any]:
        """Build the wire payload for an OCPP TransactionEvent.

        Args:
            event_type: ``Started``, ``Updated``, or ``Ended``.
            trigger_reason: A trigger reason valid under OCPP 2.0.1.
            meter_wh: Optional meter value, sent in the transaction event.
            stopped_reason: Stop reason, used only for ``Ended``.

        Returns:
            Dictionary used directly in the OCPP CALL frame.
        """
        transaction_info: dict[str, str] = {"transactionId": self.config.transaction_id}
        if stopped_reason is not None:
            transaction_info["stoppedReason"] = stopped_reason
        payload: dict[str, Any] = {
            "eventType": event_type,
            "timestamp": self._timestamp(),
            "triggerReason": trigger_reason,
            "seqNo": self._next_sequence_number(),
            "transactionInfo": transaction_info,
            "evse": {
                "id": self.config.evse_id,
                "connectorId": self.config.connector_id,
            },
        }
        if meter_wh is not None:
            payload["meterValue"] = [self._meter_value_payload(meter_wh)]
        return payload

    def _meter_value_payload(self, value_wh: Decimal) -> dict[str, Any]:
        """Build a meter value group containing exactly one Wh sample.

        Args:
            value_wh: Canonical energy value in Wh.

        Returns:
            ``MeterValueType`` dictionary following OCPP wire naming.
        """
        # The OCPP schema declares SampledValue.value as a JSON number. Decimal is
        # kept in the config to avoid rounding before the boundary; it is only
        # converted to a JSON number at this wire-serialization step.
        wire_value: int | float
        if value_wh == value_wh.to_integral_value():
            wire_value = int(value_wh)
        else:
            wire_value = float(value_wh)
        return {
            "timestamp": self._timestamp(),
            "sampledValue": [
                {
                    "value": wire_value,
                    "measurand": "Energy.Active.Import.Register",
                    "unitOfMeasure": {"unit": "Wh"},
                }
            ],
        }

    async def _send_call(
        self,
        websocket: ClientConnection,
        *,
        action: str,
        payload: dict[str, Any],
    ) -> None:
        """Send an OCPP CALL and wait for the CALLRESULT with the matching unique ID.

        Args:
            websocket: OCPP connection that has negotiated the subprotocol.
            action: OCPP action name.
            payload: Payload already using wire naming.

        Side Effects:
            Sends one frame and waits for the corresponding response, assuming
            the happy path; no retries or error-handling branches.
        """
        unique_id = uuid4().hex
        frame = json.dumps([2, unique_id, action, payload], separators=(",", ":"))
        await websocket.send(frame)
        await websocket.recv()
        print(f"ACK action={action} unique_id={unique_id}")

    async def run(self) -> None:
        """Run Started → MeterValues → Updated → Ended, then close the socket.

        Side Effects:
            Creates a session on the gateway and waits for a successful ACK for
            each CALL. The context manager closes the connection right after
            the ``Ended`` ACK.
        """
        uri = (
            f"{self.config.url.rstrip('/')}/ocpp/{quote(self.config.identity, safe='')}"
        )
        async with asyncio.timeout(self.config.timeout_seconds):
            async with connect(
                uri,
                subprotocols=[Subprotocol(OCPP_SUBPROTOCOL)],
                open_timeout=self.config.timeout_seconds,
                close_timeout=self.config.timeout_seconds,
            ) as websocket:
                print(
                    f"CONNECTED identity={self.config.identity} "
                    f"subprotocol={websocket.subprotocol}"
                )
                await self._send_call(
                    websocket,
                    action="TransactionEvent",
                    payload=self._transaction_event_payload(
                        event_type="Started",
                        trigger_reason="CablePluggedIn",
                        meter_wh=self.config.meter_start_wh,
                    ),
                )
                for meter_value in self.config.meter_values_wh:
                    await self._send_call(
                        websocket,
                        action="MeterValues",
                        payload={
                            "evseId": self.config.evse_id,
                            "meterValue": [self._meter_value_payload(meter_value)],
                        },
                    )
                await self._send_call(
                    websocket,
                    action="TransactionEvent",
                    payload=self._transaction_event_payload(
                        event_type="Updated",
                        trigger_reason="MeterValuePeriodic",
                    ),
                )
                final_meter = self.config.meter_values_wh[-1]
                await self._send_call(
                    websocket,
                    action="TransactionEvent",
                    payload=self._transaction_event_payload(
                        event_type="Ended",
                        trigger_reason="EVDeparted",
                        meter_wh=final_meter,
                        stopped_reason="EVDisconnected",
                    ),
                )
                print(
                    f"COMPLETED identity={self.config.identity} "
                    f"transaction_id={self.config.transaction_id}"
                )


def _decimal_argument(value: str) -> Decimal:
    """Parse a CLI meter value into a finite, non-negative Decimal.

    Args:
        value: Decimal string from argparse.

    Returns:
        Meter value in Wh.

    Raises:
        argparse.ArgumentTypeError: If the string is not a valid Decimal.
    """
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(
            f"Meter is not a Decimal: {value}"
        ) from error
    if not parsed.is_finite() or parsed < 0:
        raise argparse.ArgumentTypeError("Meter must be a finite, non-negative Decimal")
    return parsed


def _parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the simulator's CLI arguments.

    Args:
        arguments: Optional arguments; ``None`` uses ``sys.argv``.

    Returns:
        Namespace validated by argparse.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://localhost:9000")
    parser.add_argument("--identity", default="SIM-OCPP-001")
    parser.add_argument("--evse-id", type=int, default=1)
    parser.add_argument("--connector-id", type=int, default=1)
    parser.add_argument("--transaction-id", default="SIM-TRANSACTION-001")
    parser.add_argument(
        "--meter-start-wh", type=_decimal_argument, default=Decimal("1000")
    )
    parser.add_argument(
        "--meter-value",
        dest="meter_values_wh",
        type=_decimal_argument,
        action="append",
        help="May be repeated; each value creates one MeterValues message.",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    return parser.parse_args(arguments)


async def main(arguments: Sequence[str] | None = None) -> None:
    """Run a single happy-path OCPP charging session."""
    args = _parse_args(arguments)
    config = SimulatorConfig(
        url=args.url,
        identity=args.identity,
        evse_id=args.evse_id,
        connector_id=args.connector_id,
        transaction_id=args.transaction_id,
        meter_start_wh=args.meter_start_wh,
        meter_values_wh=tuple(args.meter_values_wh or DEFAULT_METER_VALUES_WH),
        timeout_seconds=args.timeout,
    )
    await OCPPChargingSessionSimulator(config).run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped OCPP charging session simulator")
