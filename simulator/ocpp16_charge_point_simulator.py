"""OCPP 1.6J charge-point simulator for local happy-path runs.

Speaks OCPP 1.6J (JSON over WebSocket, subprotocol ``ocpp1.6``) as a charger
would, against an already pre-provisioned station. It mimics the Willdigits
DC charger only as far as the spec summary describes: a dual-gun unit whose
connectors are numbered 0 (whole charger), 1 and 2.

It also answers CSMS-initiated calls: ``GetConfiguration`` is served from a
configuration key table modelled on the spec's section 4.3 keys, including
``SupportedFeatureProfiles`` and read-only entries.

Scenarios are added one message group at a time as the gateway learns them
(see ``docs/planners/backend-ocpp16-charger-integration.md``); currently:

* ``boot`` - BootNotification, then ``--heartbeats`` Heartbeats spaced
  ``--heartbeat-interval`` seconds apart.
* ``status`` - the ``boot`` scenario, then StatusNotification for connector 0
  (the whole charger) and every gun: a full Available -> Preparing -> Charging
  -> SuspendedEV -> Finishing -> Available cycle on gun 1 (one report without a
  timestamp, which 1.6 allows), then a fault on gun 2 and on the charger.
* ``session`` - ``boot``, all connectors Available, then a charging session on
  gun 1: Authorize, Preparing, StartTransaction (the gateway assigns the
  transactionId), Charging, two MeterValues messages (an energy reading in
  **kWh**, SoC, power, voltage, current, temperature, ``Power.Offered`` and a
  vendor-specific measurand), StopTransaction with the closing meter reading, a
  stop reason and ``transactionData``, Finishing, Available.

Like the 2.0.1 simulator this only generates valid happy-path traffic. It does
not simulate retries, duplicates, reconnects, delays, or random errors. A
CALLERROR from the gateway is printed and makes the exit code non-zero, so a
message the gateway does not handle yet is visible rather than hidden.
"""

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote

from ocpp.exceptions import OCPPError
from ocpp.routing import on
from ocpp.v16 import ChargePoint, call, call_result
from ocpp.v16.enums import (
    Action,
    ChargePointErrorCode,
    ChargePointStatus,
    Reason,
)
from websockets.asyncio.client import connect
from websockets.typing import Subprotocol

OCPP_SUBPROTOCOL = "ocpp1.6"
SCENARIOS = ("boot", "status", "session")

# (key, value, readonly). Modelled on the spec summary's section 4.3 keys; the
# defaults are what a vendor might ship, not what G3 wants (for example
# MeterValueSampleInterval is deliberately above the <= 30 s target).
DEFAULT_CONFIGURATION: tuple[tuple[str, str, bool], ...] = (
    ("SupportedFeatureProfiles", "Core,SmartCharging,RemoteTrigger", True),
    ("NumberOfConnectors", "2", True),
    ("MeterValueSampleInterval", "60", False),
    (
        "MeterValuesSampledData",
        "Energy.Active.Import.Register,Power.Active.Import,SoC",
        False,
    ),
    ("ClockAlignedDataInterval", "0", False),
    ("MeterValuesAlignedData", "Energy.Active.Import.Register", False),
    ("HeartbeatInterval", "60", False),
    ("WebSocketPingInterval", "30", False),
    ("ConnectionTimeOut", "60", False),
    ("AuthorizeRemoteTxRequests", "false", False),
    ("LocalAuthorizeOffline", "true", False),
    ("StopTransactionOnEVSideDisconnect", "true", False),
    ("StopTransactionOnInvalidId", "true", False),
    ("TransactionMessageAttempts", "3", False),
    ("TransactionMessageRetryInterval", "60", False),
    ("UnlockConnectorOnEVSideDisconnect", "true", False),
)


@dataclass(frozen=True, slots=True)
class SimulatorConfig:
    """Configuration for one run of the 1.6J simulator.

    Attributes:
        url: Base WebSocket URL, e.g. ``ws://localhost:9000``.
        identity: OCPP identity (the charger's "Charger ID") of the
            pre-provisioned station.
        connectors: Number of guns; connector IDs 1..connectors, plus 0 for
            the whole charger.
        scenario: One of ``SCENARIOS``.
        vendor: ``chargePointVendor`` sent in BootNotification.
        model: ``chargePointModel`` sent in BootNotification.
        firmware_version: ``firmwareVersion`` sent in BootNotification.
        id_tag: idTag presented in the ``session`` scenario.
        meter_start_wh: Meter reading (Wh) sent in StartTransaction.
        meter_stop_wh: Meter reading (Wh) sent in StopTransaction.
        heartbeats: How many Heartbeats the ``boot`` scenario sends.
        heartbeat_interval_seconds: Pause between those Heartbeats.
        configuration: Key table served for ``GetConfiguration``.
        linger_seconds: How long to stay connected after the scenario so
            CSMS-initiated calls (for example the post-boot
            ``GetConfiguration``) can arrive and be answered.
        timeout_seconds: Timeout for connecting and for each call.
    """

    url: str
    identity: str
    connectors: int = 2
    scenario: str = "boot"
    vendor: str = "Willdigits"
    model: str = "DC-240kW-Dual-CCS2"
    firmware_version: str = "OCPP_L4.05_SIM"
    id_tag: str = "SIMTAG001"
    meter_start_wh: int = 1000
    meter_stop_wh: int = 1500
    heartbeats: int = 1
    heartbeat_interval_seconds: float = 1.0
    configuration: tuple[tuple[str, str, bool], ...] = DEFAULT_CONFIGURATION
    linger_seconds: float = 1.0
    timeout_seconds: float = 5.0


@dataclass(slots=True)
class RunReport:
    """What happened during a run.

    Attributes:
        errors: Human-readable descriptions of every CALLERROR received.
    """

    errors: list[str] = field(default_factory=list)


def utc_timestamp() -> str:
    """Build an OCPP UTC timestamp with milliseconds, ending in ``Z``.

    Returns:
        ISO-8601 timestamp such as ``2026-09-24T10:00:00.000Z``.
    """
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class SimulatedChargePoint(ChargePoint):  # type: ignore[misc]
    """A 1.6J charge point that also answers CSMS-initiated calls.

    Attributes:
        id: The charger's identity (its "Charger ID").
        config: Immutable configuration of this run.
        report: Collects CALLERRORs so the process can exit non-zero.
    """

    def __init__(self, connection: object, config: SimulatorConfig) -> None:
        """Initialize the simulated charge point on an open WebSocket.

        Args:
            connection: Client WebSocket that negotiated ``ocpp1.6``.
            config: Configuration for this run.
        """
        super().__init__(
            config.identity, connection, response_timeout=config.timeout_seconds
        )
        self.config = config
        self.report = RunReport()

    @on(Action.get_configuration)  # type: ignore[untyped-decorator]
    async def on_get_configuration(
        self, key: list[str] | None = None, **_: object
    ) -> call_result.GetConfiguration:
        """Answer ``GetConfiguration`` from the key table.

        Args:
            key: Requested keys. ``None`` or empty means "return everything",
                which is how the gateway asks right after boot.
            **_: Other optional OCPP fields (none are defined for this call).

        Returns:
            All matching configuration keys, and the unknown requested keys in
            ``unknown_key``.
        """
        table = {
            name: (value, readonly)
            for name, value, readonly in self.config.configuration
        }
        if not key:
            wanted = list(table)
            unknown: list[str] = []
        else:
            wanted = [name for name in key if name in table]
            unknown = [name for name in key if name not in table]
        print(f"CSMS asked GetConfiguration key={key or 'ALL'} -> {len(wanted)} keys")
        return call_result.GetConfiguration(
            configuration_key=[
                {"key": name, "readonly": table[name][1], "value": table[name][0]}
                for name in wanted
            ],
            unknown_key=unknown or None,
        )

    async def send_call(
        self, request: object, *, skip_schema_validation: bool = False
    ) -> object | None:
        """Send one CALL, print the outcome, and remember a CALLERROR.

        Args:
            request: An ``ocpp.v16.call`` payload dataclass.
            skip_schema_validation: Send without validating against the 1.6
                JSON schema. Needed to send a vendor-specific measurand, which
                the standard schema forbids (real chargers do send them).

        Returns:
            The CALLRESULT payload, or ``None`` if the gateway answered with a
            CALLERROR (which is recorded in ``report``).
        """
        action = type(request).__name__
        try:
            # python-ocpp's call() swallows a CALLERROR by default and returns
            # None; suppress=False makes it raise so failures are never hidden.
            response: object = await self.call(
                request,
                suppress=False,
                skip_schema_validation=skip_schema_validation,
            )
        except OCPPError as error:
            description = f"{action} -> CALLERROR {type(error).__name__}: {error}"
            self.report.errors.append(description)
            print(f"CALLERROR action={action} {type(error).__name__}: {error}")
            return None
        print(f"ACK action={action} response={response}")
        return response

    async def run_boot_scenario(self) -> None:
        """Send BootNotification, then the configured number of Heartbeats.

        Side Effects:
            Sends ``1 + heartbeats`` CALLs; each is answered by the gateway or
            reported as a CALLERROR.
        """
        await self.send_call(
            call.BootNotification(
                charge_point_vendor=self.config.vendor,
                charge_point_model=self.config.model,
                firmware_version=self.config.firmware_version,
            )
        )
        for index in range(self.config.heartbeats):
            if index:
                await asyncio.sleep(self.config.heartbeat_interval_seconds)
            await self.send_call(call.Heartbeat())

    async def send_status(
        self,
        connector_id: int,
        status: str,
        *,
        error_code: str = "NoError",
        vendor_error_code: str | None = None,
        info: str | None = None,
        with_timestamp: bool = True,
    ) -> None:
        """Send one StatusNotification.

        Args:
            connector_id: ``0`` for the whole charger, otherwise the gun number.
            status: An OCPP 1.6 ``ChargePointStatus`` label.
            error_code: An OCPP 1.6 ``ChargePointErrorCode`` label.
            vendor_error_code: Vendor-specific error code, if any.
            info: Free-text information, if any.
            with_timestamp: Set to ``False`` to omit the (optional) timestamp.
        """
        await self.send_call(
            call.StatusNotification(
                connector_id=connector_id,
                error_code=ChargePointErrorCode(error_code),
                status=ChargePointStatus(status),
                timestamp=utc_timestamp() if with_timestamp else None,
                info=info,
                vendor_error_code=vendor_error_code,
            )
        )

    async def run_status_scenario(self) -> None:
        """Boot, then report the charger and every gun through a charging cycle.

        Side Effects:
            Sends the boot messages and then one StatusNotification per step;
            each is answered by the gateway or reported as a CALLERROR.
        """
        await self.run_boot_scenario()
        await self.send_status(0, "Available")
        for gun in range(1, self.config.connectors + 1):
            await self.send_status(gun, "Available")
        await self.send_status(1, "Preparing")
        await self.send_status(1, "Charging", with_timestamp=False)
        await self.send_status(1, "SuspendedEV")
        await self.send_status(1, "Finishing")
        await self.send_status(1, "Available")
        if self.config.connectors >= 2:
            await self.send_status(
                2,
                "Faulted",
                error_code="ConnectorLockFailure",
                vendor_error_code="3",
                info="gun lock failed",
            )
        await self.send_status(
            0, "Faulted", error_code="PowerMeterFailure", vendor_error_code="23"
        )

    async def run_session_scenario(self) -> None:
        """Boot, then run one charging session on gun 1.

        Side Effects:
            Sends the boot messages, then Authorize -> StartTransaction ->
            StopTransaction with the StatusNotification cycle around them. The
            transactionId in StopTransaction is the one the gateway returned in
            StartTransaction; if StartTransaction fails the run stops there.
        """
        await self.run_boot_scenario()
        await self.send_status(0, "Available")
        for gun in range(1, self.config.connectors + 1):
            await self.send_status(gun, "Available")
        await self.send_call(call.Authorize(id_tag=self.config.id_tag))
        await self.send_status(1, "Preparing")
        started = await self.send_call(
            call.StartTransaction(
                connector_id=1,
                id_tag=self.config.id_tag,
                meter_start=self.config.meter_start_wh,
                timestamp=utc_timestamp(),
            )
        )
        if not isinstance(started, call_result.StartTransaction):
            return
        print(f"TRANSACTION assigned transaction_id={started.transaction_id}")
        await self.send_status(1, "Charging")
        for energy_kwh, soc in (("1.25", "40"), ("1.45", "90")):
            await self.send_call(
                call.MeterValues(
                    connector_id=1,
                    transaction_id=started.transaction_id,
                    meter_value=[
                        {
                            "timestamp": utc_timestamp(),
                            "sampled_value": [
                                {
                                    "value": energy_kwh,
                                    "measurand": "Energy.Active.Import.Register",
                                    "unit": "kWh",
                                    "context": "Sample.Periodic",
                                },
                                {"value": soc, "measurand": "SoC", "unit": "Percent"},
                                {
                                    "value": "120000",
                                    "measurand": "Power.Active.Import",
                                    "unit": "W",
                                },
                                {"value": "650.5", "measurand": "Voltage", "unit": "V"},
                                {
                                    "value": "184",
                                    "measurand": "Current.Import",
                                    "unit": "A",
                                },
                                {
                                    "value": "31",
                                    "measurand": "Temperature",
                                    "unit": "Celsius",
                                },
                                {
                                    "value": "120000",
                                    "measurand": "Power.Offered",
                                    "unit": "W",
                                },
                                # Not an OCPP 1.6 measurand: vendors send these anyway.
                                {
                                    "value": "600.0",
                                    "measurand": "Voltage.Demand",
                                    "unit": "V",
                                },
                            ],
                        }
                    ],
                ),
                skip_schema_validation=True,
            )
        await self.send_call(
            call.StopTransaction(
                meter_stop=self.config.meter_stop_wh,
                timestamp=utc_timestamp(),
                transaction_id=started.transaction_id,
                reason=Reason.ev_disconnected,
                id_tag=self.config.id_tag,
                transaction_data=[
                    {
                        "timestamp": utc_timestamp(),
                        "sampled_value": [
                            {
                                "value": str(self.config.meter_stop_wh),
                                "context": "Transaction.End",
                            },
                            {
                                "value": "95",
                                "measurand": "SoC",
                                "unit": "Percent",
                                "context": "Transaction.End",
                            },
                        ],
                    }
                ],
            )
        )
        await self.send_status(1, "Finishing")
        await self.send_status(1, "Available")


async def run(config: SimulatorConfig) -> RunReport:
    """Connect as a 1.6J charger, run the scenario, and disconnect.

    Args:
        config: Configuration for this run.

    Returns:
        The report with every CALLERROR received.

    Side Effects:
        Opens one WebSocket to the gateway with subprotocol ``ocpp1.6`` and
        closes it at the end. A background task serves CSMS-initiated calls
        for the whole time the connection is open.
    """
    uri = f"{config.url.rstrip('/')}/ocpp/{quote(config.identity, safe='')}"
    async with connect(
        uri,
        subprotocols=[Subprotocol(OCPP_SUBPROTOCOL)],
        open_timeout=config.timeout_seconds,
        close_timeout=config.timeout_seconds,
    ) as websocket:
        print(
            f"CONNECTED identity={config.identity} subprotocol={websocket.subprotocol}"
        )
        charge_point = SimulatedChargePoint(websocket, config)
        # start() reads frames forever and dispatches CSMS-initiated calls;
        # the scenario runs beside it so both directions work at once.
        listener = asyncio.create_task(charge_point.start())
        try:
            if config.scenario == "boot":
                await charge_point.run_boot_scenario()
            elif config.scenario == "status":
                await charge_point.run_status_scenario()
            elif config.scenario == "session":
                await charge_point.run_session_scenario()
            await asyncio.sleep(config.linger_seconds)
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        print(f"FINISHED identity={config.identity} scenario={config.scenario}")
        return charge_point.report


def _parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the simulator's CLI arguments.

    Args:
        arguments: Optional arguments; ``None`` uses ``sys.argv``.

    Returns:
        Namespace validated by argparse.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="ws://localhost:9000")
    parser.add_argument("--identity", default="SIM-OCPP16-001")
    parser.add_argument("--connectors", type=int, default=2)
    parser.add_argument("--scenario", choices=SCENARIOS, default="boot")
    parser.add_argument("--firmware", default="OCPP_L4.05_SIM")
    parser.add_argument("--id-tag", default="SIMTAG001")
    parser.add_argument("--meter-start", type=int, default=1000)
    parser.add_argument("--meter-stop", type=int, default=1500)
    parser.add_argument("--heartbeats", type=int, default=1)
    parser.add_argument("--heartbeat-interval", type=float, default=1.0)
    parser.add_argument("--linger", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    return parser.parse_args(arguments)


async def main(arguments: Sequence[str] | None = None) -> int:
    """Run one scenario and return the process exit code.

    Args:
        arguments: Optional CLI arguments.

    Returns:
        ``0`` if every CALL was answered, ``1`` if the gateway answered any
        CALL with a CALLERROR.
    """
    args = _parse_args(arguments)
    report = await run(
        SimulatorConfig(
            url=args.url,
            identity=args.identity,
            connectors=args.connectors,
            scenario=args.scenario,
            firmware_version=args.firmware,
            id_tag=args.id_tag,
            meter_start_wh=args.meter_start,
            meter_stop_wh=args.meter_stop,
            heartbeats=args.heartbeats,
            heartbeat_interval_seconds=args.heartbeat_interval,
            linger_seconds=args.linger,
            timeout_seconds=args.timeout,
        )
    )
    return 1 if report.errors else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("Stopped OCPP 1.6J simulator")
