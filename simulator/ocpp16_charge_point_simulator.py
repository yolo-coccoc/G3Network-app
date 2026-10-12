"""OCPP 1.6J charge-point simulator for local happy-path runs.

Speaks OCPP 1.6J (JSON over WebSocket, subprotocol ``ocpp1.6``) as a charger
would, against an already pre-provisioned station. It mimics the Willdigits
DC charger only as far as the spec summary describes: a dual-gun unit whose
connectors are numbered 0 (whole charger), 1 and 2.

It also answers CSMS-initiated calls: ``GetConfiguration`` is served from a
configuration key table modelled on the spec's section 4.3 keys, including
``SupportedFeatureProfiles`` and read-only entries, and
``RemoteStartTransaction`` (a QR scan) is accepted and its idTag handed to the
``session`` scenario.

Scenarios are added one message group at a time as the gateway learns them
(see ``docs/planners/backend-ocpp16-charger-integration.md``); currently:

* ``boot`` - BootNotification, then ``--heartbeats`` Heartbeats spaced
  ``--heartbeat-interval`` seconds apart.
* ``status`` - the ``boot`` scenario, then StatusNotification for connector 0
  (the whole charger) and every gun: a full Available -> Preparing -> Charging
  -> SuspendedEV -> Finishing -> Available cycle on gun 1 (one report without a
  timestamp, which 1.6 allows), then a fault on gun 2 and on the charger.
* ``session`` - ``boot``, all connectors Available, then waits for the gateway's
  ``RemoteStartTransaction`` (scan the charger in the app meanwhile; only a
  scanned token starts a charge, CE-11) unless ``--id-tag`` names the token,
  then a charging session on gun 1 with that idTag: Authorize, Preparing, StartTransaction (the gateway assigns the
  transactionId), Charging, two MeterValues messages (an energy reading in
  **kWh**, SoC, power, voltage, current, temperature, ``Power.Offered`` and a
  vendor-specific measurand), StopTransaction with the closing meter reading, a
  stop reason and ``transactionData``, Finishing, Available.
  With ``--samples N --sample-interval S`` it charges longer (the meter rises
  ``--energy-step-wh`` per sample); with ``--stop-on-remote`` it keeps charging
  until the gateway's ``RemoteStopTransaction`` (a stop from the app) or until
  ``--samples`` readings were sent. ``--sessions 2`` runs two sessions at
  once, each on the gun its remote start names (two drivers, two guns).

Real-firmware behaviours the gateway must tolerate (CO-16, CO-17, CE-28), for
the test-day rehearsal (``docs/planners/backend-charging-test-day.md``):

* ``--naive-timestamps`` - every timestamp without a timezone (local style).
* ``--uppercase-id-tag`` - the idTag is sent upper-cased.
* ``--long-texts`` - a model, serial and firmware longer than the 1.6 schema
  allows, and a long status ``info``.
* ``--float-meter-start`` - ``meterStart`` sent as ``12345.0``.
* ``--resend-stop`` - StopTransaction sent twice (the answer was "lost").
* ``--no-stop`` - the connection drops after charging, with no
  StopTransaction: an orphan session for the staff force-close (CE-29).

Any of these flags sends every CALL without the client-side schema check.
Otherwise it does not simulate retries, reconnects, delays or random errors. A
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
    ConfigurationStatus,
    Reason,
    RemoteStartStopStatus,
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
        id_tag: idTag presented in the ``session`` scenario; ``None`` waits
            for the gateway's ``RemoteStartTransaction`` and uses its idTag.
        remote_start_timeout_seconds: How long the ``session`` scenario waits
            for that remote start.
        meter_start_wh: Meter reading (Wh) sent in StartTransaction.
        meter_stop_wh: Meter reading (Wh) sent in StopTransaction.
        heartbeats: How many Heartbeats the ``boot`` scenario sends.
        heartbeat_interval_seconds: Pause between those Heartbeats.
        configuration: Key table served for ``GetConfiguration``.
        linger_seconds: How long to stay connected after the scenario so
            CSMS-initiated calls (for example the post-boot
            ``GetConfiguration``) can arrive and be answered.
        timeout_seconds: Timeout for connecting and for each call.
        sessions: How many sessions the ``session`` scenario runs side by
            side, each waiting for its own remote start (two guns, two drivers).
        samples: MeterValues messages sent during the session.
        sample_interval_seconds: Pause between them.
        energy_step_wh: How much the meter rises per sample.
        stop_on_remote: Charge until ``RemoteStopTransaction`` arrives (or the
            samples run out) instead of stopping after the samples.
        naive_timestamps: Send every timestamp without a timezone.
        uppercase_id_tag: Send the idTag upper-cased.
        long_texts: Send boot and status texts longer than the 1.6 limits.
        float_meter_start: Send ``meterStart`` as a float.
        resend_stop: Send StopTransaction twice.
        no_stop: Drop the connection after charging, with no StopTransaction.
    """

    url: str
    identity: str
    connectors: int = 2
    scenario: str = "boot"
    vendor: str = "Willdigits"
    model: str = "DC-240kW-Dual-CCS2"
    firmware_version: str = "OCPP_L4.05_SIM"
    id_tag: str | None = None
    remote_start_timeout_seconds: float = 120.0
    meter_start_wh: int = 1000
    meter_stop_wh: int = 1500
    heartbeats: int = 1
    heartbeat_interval_seconds: float = 1.0
    configuration: tuple[tuple[str, str, bool], ...] = DEFAULT_CONFIGURATION
    linger_seconds: float = 1.0
    timeout_seconds: float = 5.0
    sessions: int = 1
    samples: int = 2
    sample_interval_seconds: float = 0.0
    energy_step_wh: int = 250
    stop_on_remote: bool = False
    naive_timestamps: bool = False
    uppercase_id_tag: bool = False
    long_texts: bool = False
    float_meter_start: bool = False
    resend_stop: bool = False
    no_stop: bool = False

    @property
    def is_lenient(self) -> bool:
        """Whether any flag sends what the 1.6 schema would refuse."""
        return (
            self.naive_timestamps
            or self.long_texts
            or self.float_meter_start
            or self.uppercase_id_tag
        )


@dataclass(slots=True)
class RunReport:
    """What happened during a run.

    Attributes:
        errors: Human-readable descriptions of every CALLERROR received.
    """

    errors: list[str] = field(default_factory=list)


def utc_timestamp(*, naive: bool = False) -> str:
    """Build an OCPP UTC timestamp with milliseconds, ending in ``Z``.

    Args:
        naive: Leave out the timezone (``2026-09-24T10:00:00.000``), as some
            firmware does.

    Returns:
        ISO-8601 timestamp such as ``2026-09-24T10:00:00.000Z``.
    """
    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return timestamp.replace("+00:00", "" if naive else "Z")


class SimulatedChargePoint(ChargePoint):  # type: ignore[misc]
    """A 1.6J charge point that also answers CSMS-initiated calls.

    Attributes:
        id: The charger's identity (its "Charger ID").
        config: Immutable configuration of this run.
        report: Collects CALLERRORs so the process can exit non-zero.
        remote_starts: ``(idTag, connectorId)`` of the accepted
            ``RemoteStartTransaction`` calls, in arrival order, for the
            ``session`` scenario.
        remote_stops: One event per running transaction, set when
            ``RemoteStopTransaction`` arrives for it.
        configuration: The live key table: ``ChangeConfiguration`` edits it,
            ``GetConfiguration`` reads it.
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
        self.remote_starts: asyncio.Queue[tuple[str, int | None]] = asyncio.Queue()
        self.remote_stops: dict[int, asyncio.Event] = {}
        self.configuration: dict[str, tuple[str, bool]] = {
            name: (value, readonly) for name, value, readonly in config.configuration
        }

    def timestamp(self) -> str:
        """Build a timestamp in this run's style (naive with ``--naive-timestamps``).

        Returns:
            The current time as an OCPP timestamp.
        """
        return utc_timestamp(naive=self.config.naive_timestamps)

    @on(Action.remote_stop_transaction)  # type: ignore[untyped-decorator]
    async def on_remote_stop_transaction(
        self, transaction_id: int, **_: object
    ) -> call_result.RemoteStopTransaction:
        """Accept a stop from the app for the running transaction.

        Args:
            transaction_id: The transaction the gateway wants stopped.
            **_: No other fields are defined.

        Returns:
            ``Accepted`` for a running transaction, otherwise ``Rejected``.
        """
        is_running = transaction_id in self.remote_stops
        print(
            f"CSMS asked RemoteStopTransaction transaction_id={transaction_id} "
            f"-> {'Accepted' if is_running else 'Rejected'}"
        )
        if not is_running:
            return call_result.RemoteStopTransaction(
                status=RemoteStartStopStatus.rejected
            )
        self.remote_stops[transaction_id].set()
        return call_result.RemoteStopTransaction(status=RemoteStartStopStatus.accepted)

    @on(Action.change_configuration)  # type: ignore[untyped-decorator]
    async def on_change_configuration(
        self, key: str, value: str, **_: object
    ) -> call_result.ChangeConfiguration:
        """Change one writable key of the table, as the operator console asks.

        Args:
            key: The configuration key.
            value: Its new value.
            **_: No other fields are defined.

        Returns:
            ``Accepted`` for a writable key, ``Rejected`` for a read-only one,
            ``NotSupported`` for an unknown one.
        """
        current = self.configuration.get(key)
        if current is None:
            status = ConfigurationStatus.not_supported
        elif current[1]:
            status = ConfigurationStatus.rejected
        else:
            self.configuration[key] = (value, False)
            status = ConfigurationStatus.accepted
        print(f"CSMS asked ChangeConfiguration {key}={value} -> {status.value}")
        return call_result.ChangeConfiguration(status=status)

    @on(Action.remote_start_transaction)  # type: ignore[untyped-decorator]
    async def on_remote_start_transaction(
        self, id_tag: str, connector_id: int | None = None, **_: object
    ) -> call_result.RemoteStartTransaction:
        """Accept a remote start (a QR scan) and queue its idTag.

        Args:
            id_tag: The single-use token the scan issued.
            connector_id: The gun the scan named, if any; the session then
                charges on that gun.
            **_: Other optional OCPP fields (``chargingProfile``).

        Returns:
            ``Accepted``; the ``session`` scenario then starts with this idTag.
        """
        print(f"CSMS asked RemoteStartTransaction connector={connector_id}")
        self.remote_starts.put_nowait((id_tag, connector_id))
        return call_result.RemoteStartTransaction(status=RemoteStartStopStatus.accepted)

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
        table = self.configuration
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
                skip_schema_validation=(
                    skip_schema_validation or self.config.is_lenient
                ),
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
        if self.config.long_texts:
            # Over the 1.6 limits (model 20, serial 25, firmware 50).
            boot = call.BootNotification(
                charge_point_vendor=self.config.vendor,
                charge_point_model=f"{self.config.model}-LIQUID-COOLED-EDITION",
                firmware_version=f"{self.config.firmware_version}-" + "B" * 50,
                charge_point_serial_number="WD-SN-" + "0" * 34,
            )
        else:
            boot = call.BootNotification(
                charge_point_vendor=self.config.vendor,
                charge_point_model=self.config.model,
                firmware_version=self.config.firmware_version,
            )
        await self.send_call(boot)
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
                timestamp=self.timestamp() if with_timestamp else None,
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
        """Boot, then run ``--sessions`` charging sessions side by side.

        Side Effects:
            Sends the boot messages, then runs one ``run_one_session`` per
            session at the same time (two drivers on the two guns).
        """
        await self.run_boot_scenario()
        await self.send_status(
            0,
            "Available",
            info=("Charger ready after power-on self test " * 3)
            if self.config.long_texts
            else None,
        )
        for gun in range(1, self.config.connectors + 1):
            await self.send_status(gun, "Available")
        await asyncio.gather(
            *(self.run_one_session(index) for index in range(self.config.sessions))
        )

    async def run_one_session(self, index: int) -> None:
        """Run one charging session, on the gun the remote start names.

        Args:
            index: Which of the run's sessions this is (0-based); its gun is
                ``index + 1`` when the remote start names none.

        Side Effects:
            Without ``--id-tag``, waits for a ``RemoteStartTransaction``, then
            Authorize -> StartTransaction -> MeterValues -> StopTransaction
            with the StatusNotification cycle around them. The transactionId in
            StopTransaction is the one the gateway returned in
            StartTransaction; if no remote start arrives in time or
            StartTransaction fails, this session stops there (a missing remote
            start is recorded in ``report``).
        """
        id_tag = self.config.id_tag
        gun = index + 1
        if id_tag is None:
            print(
                "WAITING for RemoteStartTransaction: scan "
                f"{self.config.identity} in the app "
                f"(up to {self.config.remote_start_timeout_seconds:.0f} s)"
            )
            try:
                id_tag, remote_gun = await asyncio.wait_for(
                    self.remote_starts.get(),
                    timeout=self.config.remote_start_timeout_seconds,
                )
            except TimeoutError:
                self.report.errors.append("no RemoteStartTransaction arrived")
                print("NO RemoteStartTransaction arrived; session not started")
                return
            gun = remote_gun or gun
        if self.config.uppercase_id_tag:
            id_tag = id_tag.upper()
        await self.send_call(call.Authorize(id_tag=id_tag))
        await self.send_status(gun, "Preparing")
        started = await self.send_call(
            call.StartTransaction(
                connector_id=gun,
                id_tag=id_tag,
                meter_start=(
                    float(self.config.meter_start_wh)
                    if self.config.float_meter_start
                    else self.config.meter_start_wh
                ),
                timestamp=self.timestamp(),
            )
        )
        if not isinstance(started, call_result.StartTransaction):
            return
        print(f"TRANSACTION gun={gun} assigned transaction_id={started.transaction_id}")
        remote_stop = self.remote_stops.setdefault(
            started.transaction_id, asyncio.Event()
        )
        await self.send_status(gun, "Charging")
        meter_wh = self.config.meter_start_wh
        sample = 0
        while sample < self.config.samples:
            if sample and self.config.sample_interval_seconds:
                try:
                    await asyncio.wait_for(
                        remote_stop.wait(),
                        timeout=self.config.sample_interval_seconds,
                    )
                except TimeoutError:
                    pass
            if remote_stop.is_set():
                break
            sample += 1
            meter_wh += self.config.energy_step_wh
            soc = str(min(40 + sample * 5, 100))
            energy_kwh = f"{meter_wh / 1000:.3f}"
            await self.send_call(
                call.MeterValues(
                    connector_id=gun,
                    transaction_id=started.transaction_id,
                    meter_value=[
                        {
                            "timestamp": self.timestamp(),
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
        if self.config.stop_on_remote and not remote_stop.is_set():
            print("WAITING for RemoteStopTransaction (stop the charge in the app)")
            await remote_stop.wait()
        if self.config.no_stop:
            print("DROPPING the connection without StopTransaction (orphan session)")
            return
        # The closing reading: the configured one, or where the meter got to.
        meter_stop_wh = max(self.config.meter_stop_wh, meter_wh)
        stop = call.StopTransaction(
            meter_stop=meter_stop_wh,
            timestamp=self.timestamp(),
            transaction_id=started.transaction_id,
            reason=Reason.remote if remote_stop.is_set() else Reason.ev_disconnected,
            id_tag=id_tag,
            transaction_data=[
                {
                    "timestamp": self.timestamp(),
                    "sampled_value": [
                        {
                            "value": str(meter_stop_wh),
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
        await self.send_call(stop)
        if self.config.resend_stop:
            # The gateway's answer was "lost" on 4G: a real charger sends the
            # same stop again and must get the same confirmation (CO-16).
            print("RESENDING StopTransaction (simulating a lost answer)")
            await self.send_call(stop)
        self.remote_stops.pop(started.transaction_id, None)
        await self.send_status(gun, "Finishing")
        await self.send_status(gun, "Available")


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
    parser.add_argument(
        "--id-tag",
        default=None,
        help="session: token to start with; default: wait for the remote start "
        "of a QR scan and use its token.",
    )
    parser.add_argument("--remote-start-timeout", type=float, default=120.0)
    parser.add_argument("--meter-start", type=int, default=1000)
    parser.add_argument("--meter-stop", type=int, default=1500)
    parser.add_argument("--heartbeats", type=int, default=1)
    parser.add_argument("--heartbeat-interval", type=float, default=1.0)
    parser.add_argument("--linger", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--sessions",
        type=int,
        default=1,
        help="session: sessions to run side by side, each on the gun its "
        "remote start names (default: the next gun)",
    )
    parser.add_argument(
        "--samples", type=int, default=2, help="session: MeterValues to send"
    )
    parser.add_argument(
        "--sample-interval",
        type=float,
        default=0.0,
        help="session: seconds between MeterValues",
    )
    parser.add_argument(
        "--energy-step-wh", type=int, default=250, help="session: Wh per sample"
    )
    parser.add_argument(
        "--stop-on-remote",
        action="store_true",
        help="session: keep charging until the app stops it (RemoteStopTransaction)",
    )
    parser.add_argument("--naive-timestamps", action="store_true")
    parser.add_argument("--uppercase-id-tag", action="store_true")
    parser.add_argument("--long-texts", action="store_true")
    parser.add_argument("--float-meter-start", action="store_true")
    parser.add_argument("--resend-stop", action="store_true")
    parser.add_argument(
        "--no-stop",
        action="store_true",
        help="session: drop the connection without StopTransaction (orphan)",
    )
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
            remote_start_timeout_seconds=args.remote_start_timeout,
            meter_start_wh=args.meter_start,
            meter_stop_wh=args.meter_stop,
            heartbeats=args.heartbeats,
            heartbeat_interval_seconds=args.heartbeat_interval,
            linger_seconds=args.linger,
            timeout_seconds=args.timeout,
            sessions=args.sessions,
            samples=args.samples,
            sample_interval_seconds=args.sample_interval,
            energy_step_wh=args.energy_step_wh,
            stop_on_remote=args.stop_on_remote,
            naive_timestamps=args.naive_timestamps,
            uppercase_id_tag=args.uppercase_id_tag,
            long_texts=args.long_texts,
            float_meter_start=args.float_meter_start,
            resend_stop=args.resend_stop,
            no_stop=args.no_stop,
        )
    )
    return 1 if report.errors else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("Stopped OCPP 1.6J simulator")
