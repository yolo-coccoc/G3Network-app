"""OCPP 1.6J charge-point simulator, modelled on how a real charger behaves.

Speaks OCPP 1.6J (JSON over WebSocket, subprotocol ``ocpp1.6``) as a charger
would, against an already pre-provisioned station. It mimics the Willdigits
DC charger as far as the spec summary describes: a dual-gun unit whose
connectors are numbered 0 (whole charger), 1 and 2.

**The charger model.** Each gun has a plug, a status (Available -> Preparing ->
Charging <-> SuspendedEV -> Finishing -> Available), a meter and the car's
state of charge. As on a real 1.6 charger:

* A ``RemoteStartTransaction`` (the backend's answer to a QR scan) only
  *authorizes* a charge. With ``AuthorizeRemoteTxRequests=true`` the charger
  first sends ``Authorize`` for the idTag. The charge starts once the gun is
  plugged in, either at once (``--start auto``) or when the driver presses
  start on the screen (``--start screen``, or the ``start`` command). Pressing
  start sends no message of its own: it is what makes the charger send
  ``StartTransaction``. An authorization not used within ``ConnectionTimeOut``
  seconds expires (the charger forgets it).
* A remote start for a busy gun or an unknown gun is ``Rejected``; with
  ``--remote-start-requires-gun`` one that names no gun is rejected too.
* A ``StartTransaction`` answered ``Invalid`` is ended at once with a
  ``StopTransaction`` for the transactionId the CSMS gave (``0``) and reason
  ``DeAuthorized`` (``StopTransactionOnInvalidId=true``).
* ``MeterValues`` are sent every ``MeterValueSampleInterval`` seconds (the
  configuration key, so a ``ChangeConfiguration`` takes effect), or every
  ``--sample-interval`` seconds. The meter rises with the power; the car's
  state of charge rises with the energy, and at ``--full-soc`` the gun goes to
  ``SuspendedEV`` with no power, as a full car does.
* Unplugging during a charge ends it with ``EVDisconnected``
  (``StopTransactionOnEVSideDisconnect=true``); a stop at the screen is
  ``Local``; a stop from the app (``RemoteStopTransaction``) is ``Remote``.
  After a stop the gun stays ``Finishing`` until it is unplugged.

Configuration keys (``GetConfiguration`` / ``ChangeConfiguration``) come from a
table modelled on the spec's section 4.3 keys; ``--config KEY=VALUE`` overrides
one at start-up.

Scenarios:

* ``boot`` - BootNotification, then ``--heartbeats`` Heartbeats spaced
  ``--heartbeat-interval`` seconds apart.
* ``status`` - the ``boot`` scenario, then StatusNotification for connector 0
  and every gun: a full Available -> Preparing -> Charging -> SuspendedEV ->
  Finishing -> Available cycle on gun 1 (one report without a timestamp, which
  1.6 allows), then a fault on gun 2 and on the charger.
* ``session`` - a scripted driver: boot, all connectors Available, then
  ``--sessions`` sessions side by side (session ``n`` uses gun ``n`` unless the
  remote start names another). Each one, without ``--id-tag``, waits for the
  QR scan's remote start; with ``--id-tag`` the driver presents that tag at
  the charger instead (``Authorize`` then ``StartTransaction``, like a card).
  ``--plug before-scan|after-scan|never`` says when the driver plugs in
  (``--plug-delay`` seconds after the remote start for ``after-scan``),
  ``--start auto|screen`` who starts the charge (``--press-start-delay``), and
  ``--end`` how it ends: ``unplug`` or ``local`` after ``--samples`` readings,
  ``remote`` when the app stops it, ``full`` when the car is full (then
  unplugged), ``power-loss`` after ``--samples`` readings with no
  StopTransaction (an orphan for the staff force-close, CE-29).
* ``charger`` - a charger you operate by typing commands (or piping them):
  ``plug N``, ``unplug N``, ``start N`` (press start), ``stop N`` (press stop),
  ``card N TAG`` (present a card), ``full N`` (the car is full), ``fault N
  [code]``, ``clear N``, ``status``, ``wait S``, ``power-loss``, ``quit``. The
  backend's remote starts, stops and configuration changes are answered as
  they arrive. Type ``help`` for the list.

Real-firmware behaviours the gateway must tolerate (CO-16, CO-17, CE-28), for
the test-day rehearsal (``docs/planners/backend-charging-test-day.md``):

* ``--naive-timestamps`` - every timestamp without a timezone (local style).
* ``--uppercase-id-tag`` - the idTag is sent upper-cased.
* ``--long-texts`` - a model, serial and firmware longer than the 1.6 schema
  allows, and a long status ``info``.
* ``--float-meter-start`` - ``meterStart`` sent as ``12345.0``.
* ``--resend-stop`` - StopTransaction sent twice (the answer was "lost").

Any of these flags sends every CALL without the client-side schema check. Not
simulated (``docs/decisions/deferred.md`` item 103): network loss with an
offline message queue and reconnect, clock drift, and vendor messages such as
``DataTransfer``. A CALLERROR from the gateway is printed and makes the exit
code non-zero, so a message the gateway does not handle yet is visible rather
than hidden.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote

from ocpp.exceptions import OCPPError
from ocpp.routing import on
from ocpp.v16 import ChargePoint, call, call_result
from ocpp.v16.enums import (
    Action,
    AuthorizationStatus,
    ChargePointErrorCode,
    ChargePointStatus,
    ConfigurationStatus,
    Reason,
    RemoteStartStopStatus,
)
from websockets.asyncio.client import connect
from websockets.typing import Subprotocol

OCPP_SUBPROTOCOL = "ocpp1.6"
SCENARIOS = ("boot", "status", "session", "charger")
PLUG_ORDERS = ("before-scan", "after-scan", "never")
START_MODES = ("auto", "screen")
END_MODES = ("unplug", "local", "remote", "full", "power-loss")

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

# How often a scripted driver checks on its gun (seconds); only paces polling.
_POLL_SECONDS = 0.05
# Readings a full car stays SuspendedEV before the driver unplugs (--end full).
_SUSPENDED_SAMPLES_BEFORE_UNPLUG = 2
# Nominal DC voltage of the simulated car battery (V), for the current reading.
_NOMINAL_VOLTAGE_V = 650.0

CHARGER_HELP = """Commands (N = gun number):
  plug N            plug the gun into the car
  unplug N          unplug it (ends a running charge: EVDisconnected)
  start N           press start on the screen (needs a scan or a card first)
  stop N            press stop on the screen (Local)
  card N TAG        present a card with idTag TAG at gun N (Authorize + start)
  full N            the car reaches its full state of charge (SuspendedEV)
  fault N [CODE]    the gun reports a fault (default OtherError)
  clear N           the fault is cleared
  status            show every gun
  wait S            pause S seconds (for piped scripts)
  power-loss        drop the connection without any stop message
  quit              stop the simulator
"""


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
        id_tag: ``session``: a tag the driver presents at the charger (a
            card) instead of waiting for a QR scan's remote start.
        remote_start_timeout_seconds: How long a scripted session waits for
            the remote start.
        meter_start_wh: The meter reading of every gun at start-up (Wh).
        meter_stop_wh: The lowest closing reading StopTransaction reports.
        heartbeats: How many Heartbeats the ``boot`` scenario sends.
        heartbeat_interval_seconds: Pause between those Heartbeats.
        configuration: Key table served for ``GetConfiguration``.
        config_overrides: ``(key, value)`` pairs applied over that table.
        linger_seconds: How long to stay connected after a scripted scenario
            so CSMS-initiated calls can still arrive and be answered.
        timeout_seconds: Timeout for connecting and for each call.
        sessions: How many scripted sessions run side by side.
        samples: Readings before a scripted ``unplug``/``local``/
            ``power-loss`` end; with a zero sample interval, exactly this many
            readings are sent back to back.
        sample_interval_seconds: Seconds between readings; ``None`` uses the
            ``MeterValueSampleInterval`` configuration key.
        energy_step_wh: Wh the meter rises per reading; ``None`` derives it
            from ``power_kw`` and the interval.
        power_kw: Charging power reported while charging.
        battery_kwh: Usable capacity of the simulated car battery.
        start_soc_percent: The car's state of charge when plugged in.
        full_soc_percent: The state of charge at which the car is full.
        plug: When the scripted driver plugs in: ``PLUG_ORDERS``.
        plug_delay_seconds: ``after-scan``: seconds between the remote start
            and the plug.
        start: Who starts a scripted charge: ``START_MODES``.
        press_start_delay_seconds: ``screen``: seconds before the driver
            presses start.
        end: How a scripted session ends: ``END_MODES``.
        remote_start_requires_gun: Reject a remote start that names no gun.
        naive_timestamps: Send every timestamp without a timezone.
        uppercase_id_tag: Send the idTag upper-cased.
        long_texts: Send boot and status texts longer than the 1.6 limits.
        float_meter_start: Send ``meterStart`` as a float.
        resend_stop: Send StopTransaction twice.
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
    meter_stop_wh: int = 0
    heartbeats: int = 1
    heartbeat_interval_seconds: float = 1.0
    configuration: tuple[tuple[str, str, bool], ...] = DEFAULT_CONFIGURATION
    config_overrides: tuple[tuple[str, str], ...] = ()
    linger_seconds: float = 1.0
    timeout_seconds: float = 5.0
    sessions: int = 1
    samples: int = 2
    sample_interval_seconds: float | None = None
    energy_step_wh: int | None = None
    power_kw: float = 120.0
    battery_kwh: float = 60.0
    start_soc_percent: float = 40.0
    full_soc_percent: float = 100.0
    plug: str = "before-scan"
    plug_delay_seconds: float = 3.0
    start: str = "auto"
    press_start_delay_seconds: float = 2.0
    end: str = "unplug"
    remote_start_requires_gun: bool = False
    naive_timestamps: bool = False
    uppercase_id_tag: bool = False
    long_texts: bool = False
    float_meter_start: bool = False
    resend_stop: bool = False

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


@dataclass(slots=True)
class PendingAuthorization:
    """A charge the charger may start: from a remote start or a card.

    Attributes:
        id_tag: The idTag the charge will start with.
        gun: The gun it is for, or ``None`` for whichever gun is used.
        is_remote: ``True`` for a remote start (a QR scan), ``False`` for a
            card presented at the charger.
    """

    id_tag: str
    gun: int | None
    is_remote: bool


@dataclass(slots=True)
class Gun:
    """The live state of one gun and the car plugged into it.

    Attributes:
        number: The OCPP connector ID (1..connectors).
        status: The last StatusNotification sent for it.
        is_plugged: Whether a car is plugged in.
        meter_wh: The gun's energy register (Wh), never reset.
        soc_percent: The plugged car's state of charge.
        transaction_id: The running transaction, if any.
        id_tag: The idTag of the running transaction.
        samples_sent: Readings sent during the running transaction.
        is_suspended: Whether the car is full (SuspendedEV, no power).
        suspended_at_sample: ``samples_sent`` when the car became full.
        meter_task: The task sending the running transaction's readings.
        ended: Set when the running transaction ends.
    """

    number: int
    meter_wh: int
    soc_percent: float
    status: str = "Available"
    is_plugged: bool = False
    transaction_id: int | None = None
    id_tag: str | None = None
    samples_sent: int = 0
    is_suspended: bool = False
    suspended_at_sample: int = 0
    meter_task: "asyncio.Task[None] | None" = None
    ended: asyncio.Event = field(default_factory=asyncio.Event)


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
    """A 1.6J charge point with per-gun state, answering CSMS-initiated calls.

    Handlers for CSMS calls never await a call of their own (``python-ocpp``'s
    receive loop is sequential, so that would deadlock); follow-up work such
    as an ``Authorize`` after a remote start runs in a task.

    Attributes:
        id: The charger's identity (its "Charger ID").
        config: Immutable configuration of this run.
        report: Collects CALLERRORs so the process can exit non-zero.
        configuration: The live key table: ``ChangeConfiguration`` edits it,
            ``GetConfiguration`` reads it.
        guns: Every gun by number.
        authorizations: Charges authorized but not started yet.
        authorization_arrived: Set whenever a remote start authorizes a charge.
        power_lost: Set when the connection must drop without any message.
        background_tasks: Tasks started by handlers, kept until they finish.
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
        self.configuration: dict[str, tuple[str, bool]] = {
            name: (value, readonly) for name, value, readonly in config.configuration
        }
        for key, value in config.config_overrides:
            readonly = self.configuration.get(key, (value, False))[1]
            self.configuration[key] = (value, readonly)
        self.guns: dict[int, Gun] = {
            number: Gun(
                number=number,
                meter_wh=config.meter_start_wh,
                soc_percent=config.start_soc_percent,
            )
            for number in range(1, config.connectors + 1)
        }
        self.authorizations: list[PendingAuthorization] = []
        self.authorization_arrived = asyncio.Event()
        self.power_lost = asyncio.Event()
        self.background_tasks: set[asyncio.Task[None]] = set()
        # Guns a scripted driver stands at; a scan naming one of them is that
        # driver's, so two scripted drivers never take the same scan.
        self.driver_guns: set[int] = set()

    # --- helpers ------------------------------------------------------------

    def timestamp(self) -> str:
        """Build a timestamp in this run's style (naive with ``--naive-timestamps``).

        Returns:
            The current time as an OCPP timestamp.
        """
        return utc_timestamp(naive=self.config.naive_timestamps)

    def setting(self, key: str) -> str:
        """Read a configuration key's current value.

        Args:
            key: The configuration key.

        Returns:
            Its value, or an empty string when the key is unknown.
        """
        return self.configuration.get(key, ("", False))[0]

    def sample_interval(self) -> float:
        """Seconds between readings: ``--sample-interval`` or the config key.

        Returns:
            The interval; ``0`` sends the scripted readings back to back.
        """
        if self.config.sample_interval_seconds is not None:
            return self.config.sample_interval_seconds
        return float(self.setting("MeterValueSampleInterval") or 60)

    def spawn(self, coroutine: object) -> None:
        """Run follow-up work in a task that a handler does not await.

        Args:
            coroutine: The coroutine to run.
        """
        task: asyncio.Task[None] = asyncio.create_task(coroutine)  # type: ignore[arg-type]
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    def present_tag(self, id_tag: str) -> str:
        """The idTag as this charger sends it (upper-cased with the flag).

        Args:
            id_tag: The tag as issued.

        Returns:
            The tag to put in Authorize, StartTransaction and StopTransaction.
        """
        return id_tag.upper() if self.config.uppercase_id_tag else id_tag

    # --- CSMS-initiated calls -----------------------------------------------

    @on(Action.remote_start_transaction)  # type: ignore[untyped-decorator]
    async def on_remote_start_transaction(
        self, id_tag: str, connector_id: int | None = None, **_: object
    ) -> call_result.RemoteStartTransaction:
        """Authorize a charge after a QR scan; the gun must still be plugged in.

        Args:
            id_tag: The single-use token the scan issued.
            connector_id: The gun the scan named, if any.
            **_: Other optional OCPP fields (``chargingProfile``).

        Returns:
            ``Accepted`` when the charge can be authorized; ``Rejected`` for an
            unknown or busy gun, or for no gun with
            ``--remote-start-requires-gun``.

        Side Effects:
            Schedules ``authorize_remote`` (Authorize when the charger is set
            to, the ``ConnectionTimeOut`` expiry, an automatic start).
        """
        gun = self.guns.get(connector_id) if connector_id is not None else None
        if connector_id is None and self.config.remote_start_requires_gun:
            verdict = "Rejected (no gun named)"
        elif connector_id is not None and gun is None:
            verdict = "Rejected (no such gun)"
        elif gun is not None and (
            gun.transaction_id is not None or gun.status in ("Faulted", "Finishing")
        ):
            verdict = f"Rejected (gun {gun.number} is {gun.status})"
        else:
            verdict = "Accepted"
        print(
            f"CSMS asked RemoteStartTransaction connector={connector_id} -> {verdict}"
        )
        if verdict != "Accepted":
            return call_result.RemoteStartTransaction(
                status=RemoteStartStopStatus.rejected
            )
        self.spawn(self.authorize_remote(id_tag, connector_id))
        return call_result.RemoteStartTransaction(status=RemoteStartStopStatus.accepted)

    @on(Action.remote_stop_transaction)  # type: ignore[untyped-decorator]
    async def on_remote_stop_transaction(
        self, transaction_id: int, **_: object
    ) -> call_result.RemoteStopTransaction:
        """Stop a running charge when the app asks.

        Args:
            transaction_id: The transaction the gateway wants stopped.
            **_: No other fields are defined.

        Returns:
            ``Accepted`` for a running transaction, otherwise ``Rejected``.

        Side Effects:
            Schedules the StopTransaction (reason ``Remote``).
        """
        gun = next(
            (g for g in self.guns.values() if g.transaction_id == transaction_id), None
        )
        print(
            f"CSMS asked RemoteStopTransaction transaction_id={transaction_id} "
            f"-> {'Accepted' if gun else 'Rejected'}"
        )
        if gun is None:
            return call_result.RemoteStopTransaction(
                status=RemoteStartStopStatus.rejected
            )
        self.spawn(self.stop_charge(gun, Reason.remote))
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

    # --- outbound calls -----------------------------------------------------

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
        """Send one StatusNotification and remember a gun's new status.

        Args:
            connector_id: ``0`` for the whole charger, otherwise the gun number.
            status: An OCPP 1.6 ``ChargePointStatus`` label.
            error_code: An OCPP 1.6 ``ChargePointErrorCode`` label.
            vendor_error_code: Vendor-specific error code, if any.
            info: Free-text information, if any.
            with_timestamp: Set to ``False`` to omit the (optional) timestamp.
        """
        gun = self.guns.get(connector_id)
        if gun is not None:
            gun.status = status
        where = f"gun {connector_id}" if connector_id else "charger"
        print(
            f"STATUS {where} -> {status}"
            + (f" ({error_code})" if error_code != "NoError" else "")
        )
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

    async def boot(self) -> None:
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

    async def report_all_available(self) -> None:
        """Report the whole charger and every gun Available after boot."""
        await self.send_status(
            0,
            "Available",
            info=("Charger ready after power-on self test " * 3)
            if self.config.long_texts
            else None,
        )
        for number in self.guns:
            await self.send_status(number, "Available")

    # --- the charger's behaviour --------------------------------------------

    async def authorize_remote(self, id_tag: str, gun_number: int | None) -> None:
        """Hold a remote start's authorization until it is used or expires.

        Args:
            id_tag: The token the scan issued.
            gun_number: The gun the scan named, or ``None``.

        Side Effects:
            Sends ``Authorize`` first when ``AuthorizeRemoteTxRequests`` is
            ``true`` (and drops the charge unless it is ``Accepted``); starts
            at once on a plugged gun with ``--start auto``; forgets the
            authorization after ``ConnectionTimeOut`` seconds.
        """
        if self.setting("AuthorizeRemoteTxRequests").lower() == "true":
            answer = await self.send_call(
                call.Authorize(id_tag=self.present_tag(id_tag))
            )
            status = getattr(answer, "id_tag_info", {}).get("status")
            if status != AuthorizationStatus.accepted:
                print(f"AUTHORIZE refused ({status}); the charge will not start")
                return
        authorization = PendingAuthorization(id_tag, gun_number, is_remote=True)
        self.authorizations.append(authorization)
        self.authorization_arrived.set()
        if self.config.start == "auto":
            gun = self.find_plugged_gun(gun_number)
            if gun is not None:
                await self.start_charge(gun)
        timeout = float(self.setting("ConnectionTimeOut") or 60)
        await asyncio.sleep(timeout)
        if authorization in self.authorizations:
            self.authorizations.remove(authorization)
            print(
                f"AUTHORIZATION EXPIRED: no charge started within ConnectionTimeOut "
                f"({timeout:.0f} s); the scan is now unused"
            )

    def find_plugged_gun(self, gun_number: int | None) -> Gun | None:
        """Find the plugged, idle gun an authorization can start on.

        Args:
            gun_number: The gun named, or ``None`` for the first plugged one.

        Returns:
            The gun, or ``None`` when no such gun is plugged and idle.
        """
        for gun in self.guns.values():
            if gun_number is not None and gun.number != gun_number:
                continue
            if (
                gun.is_plugged
                and gun.transaction_id is None
                and gun.status == "Preparing"
            ):
                return gun
        return None

    def claim_authorization(self, gun: Gun) -> PendingAuthorization | None:
        """Take the authorization for a gun (named for it, or for any gun).

        Args:
            gun: The gun about to start.

        Returns:
            The authorization, removed from the pending list, or ``None``.
        """
        for authorization in self.authorizations:
            if authorization.gun in (None, gun.number):
                self.authorizations.remove(authorization)
                return authorization
        return None

    async def plug(self, gun: Gun) -> None:
        """The driver plugs the gun into the car.

        Args:
            gun: The gun.

        Side Effects:
            Reports ``Preparing``; with ``--start auto`` a pending
            authorization for the gun starts the charge at once.
        """
        if gun.is_plugged or gun.status != "Available":
            print(f"gun {gun.number} cannot be plugged now ({gun.status})")
            return
        gun.is_plugged = True
        gun.soc_percent = self.config.start_soc_percent
        gun.is_suspended = False
        print(f"PLUGGED gun {gun.number}")
        await self.send_status(gun.number, "Preparing")
        if self.config.start == "auto" and any(
            a.gun in (None, gun.number) for a in self.authorizations
        ):
            await self.start_charge(gun)

    async def press_start(self, gun: Gun) -> None:
        """The driver presses start on the screen for a gun.

        Args:
            gun: The gun.

        Side Effects:
            Starts the charge when the gun is plugged and a scan or card
            authorized it; otherwise prints what is missing (the screen would
            ask for a card).
        """
        if not gun.is_plugged:
            print(f"gun {gun.number}: plug the car in first")
            return
        if gun.transaction_id is not None:
            print(f"gun {gun.number} is already charging")
            return
        if not any(a.gun in (None, gun.number) for a in self.authorizations):
            print(f"gun {gun.number}: not authorized - scan the QR or present a card")
            return
        await self.start_charge(gun)

    async def present_card(self, gun: Gun, id_tag: str) -> None:
        """The driver taps a card (a local idTag) at a plugged gun.

        Args:
            gun: The gun.
            id_tag: The card's idTag.

        Side Effects:
            Sends ``Authorize``; when ``Accepted`` the charge starts, at once
            with ``--start auto`` or after ``press_start`` otherwise.
        """
        answer = await self.send_call(call.Authorize(id_tag=self.present_tag(id_tag)))
        status = getattr(answer, "id_tag_info", {}).get("status")
        if status != AuthorizationStatus.accepted:
            print(f"CARD refused ({status})")
            return
        self.authorizations.append(PendingAuthorization(id_tag, gun.number, False))
        if gun.is_plugged and self.config.start == "auto":
            await self.start_charge(gun)

    async def start_charge(self, gun: Gun) -> None:
        """Send StartTransaction for a plugged, authorized gun and charge.

        Args:
            gun: The gun.

        Side Effects:
            On ``Accepted``: reports ``Charging`` and starts the readings. On
            any other answer the charger ends the transaction at once
            (``StopTransactionOnInvalidId``): ``StopTransaction`` for the
            given transactionId (``0``) with reason ``DeAuthorized``.
        """
        authorization = self.claim_authorization(gun)
        if authorization is None:
            return
        id_tag = self.present_tag(authorization.id_tag)
        gun.ended = asyncio.Event()
        gun.samples_sent = 0
        started = await self.send_call(
            call.StartTransaction(
                connector_id=gun.number,
                id_tag=id_tag,
                meter_start=(
                    float(gun.meter_wh)
                    if self.config.float_meter_start
                    else gun.meter_wh
                ),
                timestamp=self.timestamp(),
            )
        )
        if not isinstance(started, call_result.StartTransaction):
            gun.ended.set()
            return
        status = started.id_tag_info.get("status")
        if status != AuthorizationStatus.accepted:
            print(
                f"START refused ({status}): ending transaction "
                f"{started.transaction_id} with DeAuthorized"
            )
            await self.send_call(
                call.StopTransaction(
                    meter_stop=gun.meter_wh,
                    timestamp=self.timestamp(),
                    transaction_id=started.transaction_id,
                    reason=Reason.de_authorized,
                    id_tag=id_tag,
                )
            )
            await self.send_status(gun.number, "Finishing")
            gun.ended.set()
            return
        gun.transaction_id = started.transaction_id
        gun.id_tag = id_tag
        print(
            f"TRANSACTION gun={gun.number} assigned transaction_id={gun.transaction_id}"
        )
        await self.send_status(gun.number, "Charging")
        gun.meter_task = asyncio.create_task(self.send_readings(gun))

    def reading(self, gun: Gun, power_w: float) -> list[dict[str, object]]:
        """Build one ``meterValue`` group for a gun.

        Args:
            gun: The gun.
            power_w: The power being delivered now (W).

        Returns:
            A one-group list with the energy register (kWh), SoC, power,
            voltage, current, temperature, offered power and one
            vendor-specific measurand.
        """
        return [
            {
                "timestamp": self.timestamp(),
                "sampled_value": [
                    {
                        "value": f"{gun.meter_wh / 1000:.3f}",
                        "measurand": "Energy.Active.Import.Register",
                        "unit": "kWh",
                        "context": "Sample.Periodic",
                    },
                    {
                        "value": f"{gun.soc_percent:.0f}",
                        "measurand": "SoC",
                        "unit": "Percent",
                    },
                    {
                        "value": f"{power_w:.0f}",
                        "measurand": "Power.Active.Import",
                        "unit": "W",
                    },
                    {
                        "value": f"{_NOMINAL_VOLTAGE_V:.1f}",
                        "measurand": "Voltage",
                        "unit": "V",
                    },
                    {
                        "value": f"{power_w / _NOMINAL_VOLTAGE_V:.0f}",
                        "measurand": "Current.Import",
                        "unit": "A",
                    },
                    {"value": "31", "measurand": "Temperature", "unit": "Celsius"},
                    {
                        "value": f"{self.config.power_kw * 1000:.0f}",
                        "measurand": "Power.Offered",
                        "unit": "W",
                    },
                    # Not an OCPP 1.6 measurand: vendors send these anyway.
                    {"value": "600.0", "measurand": "Voltage.Demand", "unit": "V"},
                ],
            }
        ]

    async def send_readings(self, gun: Gun) -> None:
        """Send MeterValues for a running charge until it ends.

        With a zero interval exactly ``--samples`` readings are sent back to
        back (fast scripted runs); otherwise one every interval until the
        transaction ends. The meter rises with the power, the car's state of
        charge with the energy; at ``--full-soc`` the gun reports
        ``SuspendedEV`` and the power drops to zero.

        Args:
            gun: The charging gun.
        """
        while gun.transaction_id is not None:
            interval = self.sample_interval()
            if interval <= 0 and gun.samples_sent >= self.config.samples:
                return
            if interval > 0:
                await asyncio.sleep(interval)
                if gun.transaction_id is None:
                    return
            if gun.soc_percent >= self.config.full_soc_percent and not gun.is_suspended:
                gun.is_suspended = True
                gun.suspended_at_sample = gun.samples_sent
                await self.send_status(gun.number, "SuspendedEV")
            power_w = 0.0 if gun.is_suspended else self.config.power_kw * 1000
            if not gun.is_suspended:
                step_wh = self.config.energy_step_wh
                if step_wh is None:
                    step_wh = round(power_w * interval / 3600)
                gun.meter_wh += step_wh
                gun.soc_percent = min(
                    gun.soc_percent + step_wh / (self.config.battery_kwh * 10),
                    self.config.full_soc_percent,
                )
            gun.samples_sent += 1
            await self.send_call(
                call.MeterValues(
                    connector_id=gun.number,
                    transaction_id=gun.transaction_id,
                    meter_value=self.reading(gun, power_w),
                ),
                skip_schema_validation=True,
            )

    async def stop_charge(self, gun: Gun, reason: Reason) -> None:
        """End the running charge with a StopTransaction.

        Args:
            gun: The gun.
            reason: Why: ``Remote`` (the app), ``Local`` (the screen),
                ``EVDisconnected`` (unplugged), ``Other`` (a fault).

        Side Effects:
            Stops the readings, sends StopTransaction with the closing reading
            and a ``Transaction.End`` sample (twice with ``--resend-stop``),
            then reports ``Finishing`` (``Available`` when already unplugged).
        """
        if gun.transaction_id is None:
            return
        transaction_id = gun.transaction_id
        gun.transaction_id = None
        if gun.meter_task is not None:
            gun.meter_task.cancel()
            await asyncio.gather(gun.meter_task, return_exceptions=True)
            gun.meter_task = None
        meter_stop_wh = max(self.config.meter_stop_wh, gun.meter_wh)
        stop = call.StopTransaction(
            meter_stop=meter_stop_wh,
            timestamp=self.timestamp(),
            transaction_id=transaction_id,
            reason=reason,
            id_tag=gun.id_tag,
            transaction_data=[
                {
                    "timestamp": self.timestamp(),
                    "sampled_value": [
                        {"value": str(meter_stop_wh), "context": "Transaction.End"},
                        {
                            "value": f"{gun.soc_percent:.0f}",
                            "measurand": "SoC",
                            "unit": "Percent",
                            "context": "Transaction.End",
                        },
                    ],
                }
            ],
        )
        print(
            f"STOPPING gun={gun.number} transaction_id={transaction_id} reason={reason.value}"
        )
        await self.send_call(stop)
        if self.config.resend_stop:
            # The gateway's answer was "lost" on 4G: a real charger sends the
            # same stop again and must get the same confirmation (CO-16).
            print("RESENDING StopTransaction (simulating a lost answer)")
            await self.send_call(stop)
        await self.send_status(gun.number, "Finishing")
        if not gun.is_plugged:
            await self.send_status(gun.number, "Available")
        gun.ended.set()

    async def unplug(self, gun: Gun) -> None:
        """The driver unplugs the gun.

        Args:
            gun: The gun.

        Side Effects:
            Ends a running charge with ``EVDisconnected``; reports
            ``Available`` once the gun is free.
        """
        if not gun.is_plugged:
            print(f"gun {gun.number} is not plugged")
            return
        gun.is_plugged = False
        print(f"UNPLUGGED gun {gun.number}")
        if gun.transaction_id is not None:
            await self.stop_charge(gun, Reason.ev_disconnected)
            return
        if gun.status != "Faulted":
            await self.send_status(gun.number, "Available")

    async def press_stop(self, gun: Gun) -> None:
        """The driver presses stop on the screen.

        Args:
            gun: The gun.
        """
        if gun.transaction_id is None:
            print(f"gun {gun.number} is not charging")
            return
        await self.stop_charge(gun, Reason.local)

    async def fault(self, gun: Gun, error_code: str) -> None:
        """The gun reports a fault; a running charge ends with reason ``Other``.

        Args:
            gun: The gun.
            error_code: An OCPP 1.6 ``ChargePointErrorCode`` label.
        """
        if gun.transaction_id is not None:
            await self.stop_charge(gun, Reason.other)
        await self.send_status(gun.number, "Faulted", error_code=error_code)

    async def clear_fault(self, gun: Gun) -> None:
        """The fault is cleared: the gun is Preparing if plugged, else Available.

        Args:
            gun: The gun.
        """
        await self.send_status(
            gun.number, "Preparing" if gun.is_plugged else "Available"
        )

    def print_guns(self) -> None:
        """Print every gun's status, plug, transaction, meter and SoC."""
        for gun in self.guns.values():
            print(
                f"gun {gun.number}: {gun.status:<12} plugged={'yes' if gun.is_plugged else 'no':<3} "
                f"tx={gun.transaction_id or '-'} meter={gun.meter_wh} Wh "
                f"SoC={gun.soc_percent:.0f}%"
            )
        pending = ", ".join(
            f"{'scan' if a.is_remote else 'card'}->gun {a.gun or 'any'}"
            for a in self.authorizations
        )
        print(f"authorized, not started: {pending or 'none'}")

    # --- scenarios ----------------------------------------------------------

    async def run_status_scenario(self) -> None:
        """Boot, then report the charger and every gun through a charging cycle.

        Side Effects:
            Sends the boot messages and then one StatusNotification per step;
            each is answered by the gateway or reported as a CALLERROR.
        """
        await self.boot()
        await self.send_status(0, "Available")
        for number in self.guns:
            await self.send_status(number, "Available")
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
        """Boot, then run ``--sessions`` scripted drivers side by side.

        Side Effects:
            Sends the boot messages, then runs one ``run_scripted_driver`` per
            session at the same time (session ``n`` on gun ``n``).
        """
        await self.boot()
        await self.report_all_available()
        await asyncio.gather(
            *(
                self.run_scripted_driver(index + 1)
                for index in range(self.config.sessions)
            )
        )

    async def wait_for(self, condition: object, timeout: float) -> bool:
        """Poll a condition until it holds or the timeout passes.

        Args:
            condition: A callable returning ``bool``.
            timeout: Seconds to wait at most.

        Returns:
            Whether the condition held in time.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not condition():  # type: ignore[operator]
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(_POLL_SECONDS)
        return True

    async def run_scripted_driver(self, gun_number: int) -> None:
        """One driver's charge on one gun, following ``--plug/--start/--end``.

        Args:
            gun_number: The gun this driver uses (a remote start naming another
                gun moves the driver there).

        Side Effects:
            Plugs in, waits for the scan (or presents ``--id-tag``), starts,
            charges and ends the charge as configured; a missing remote start
            or a start that never happens is recorded in ``report``.
        """
        gun = self.guns[gun_number]
        self.driver_guns.add(gun.number)
        if self.config.plug == "before-scan":
            await self.plug(gun)
        if self.config.id_tag is not None:
            if not gun.is_plugged and self.config.plug != "never":
                await self.plug(gun)
            await self.present_card(gun, self.config.id_tag)
        else:
            print(
                f"WAITING for RemoteStartTransaction for gun {gun.number}: scan "
                f"{self.config.identity} in the app "
                f"(up to {self.config.remote_start_timeout_seconds:.0f} s)"
            )
            has_scan = await self.wait_for(
                lambda: (
                    gun.transaction_id is not None
                    or self.scan_for_driver(gun.number) is not None
                ),
                self.config.remote_start_timeout_seconds,
            )
            if not has_scan:
                self.report.errors.append("no RemoteStartTransaction arrived")
                print("NO RemoteStartTransaction arrived; session not started")
                return
            scan = self.scan_for_driver(gun.number)
            if scan is not None and scan.gun not in (None, gun.number):
                # The driver scanned another gun's QR: they walk over to it.
                print(f"driver moves from gun {gun.number} to gun {scan.gun}")
                if gun.is_plugged:
                    await self.unplug(gun)
                self.driver_guns.discard(gun.number)
                gun = self.guns[scan.gun]
                self.driver_guns.add(gun.number)
                if self.config.plug == "before-scan":
                    await self.plug(gun)
            if self.config.plug == "after-scan":
                print(f"driver plugs in after {self.config.plug_delay_seconds:.0f} s")
                await asyncio.sleep(self.config.plug_delay_seconds)
                await self.plug(gun)
        if self.config.plug == "never":
            print("driver never plugs in; the authorization will expire")
            return
        if self.config.start == "screen" and gun.transaction_id is None:
            await asyncio.sleep(self.config.press_start_delay_seconds)
            print(f"driver presses start on gun {gun.number}")
            await self.press_start(gun)
        if gun.transaction_id is None:
            await self.wait_for(lambda: gun.transaction_id is not None, 2.0)
        if gun.transaction_id is None:
            print(f"gun {gun.number}: no charge started")
            if gun.is_plugged:
                await asyncio.sleep(1.0)
                await self.unplug(gun)
            return
        await self.end_scripted_charge(gun)

    def scan_for_driver(self, gun_number: int) -> PendingAuthorization | None:
        """Find a pending scan a scripted driver at a gun should take.

        Args:
            gun_number: The driver's gun.

        Returns:
            A remote start for that gun, for any gun, or for a gun no other
            scripted driver stands at; ``None`` when there is none.
        """
        for authorization in self.authorizations:
            if not authorization.is_remote:
                continue
            if authorization.gun in (None, gun_number):
                return authorization
            if authorization.gun not in self.driver_guns:
                return authorization
        return None

    async def end_scripted_charge(self, gun: Gun) -> None:
        """End a scripted charge the way ``--end`` says.

        Args:
            gun: The charging gun.
        """
        end = self.config.end
        if end in ("unplug", "local", "power-loss"):
            await self.wait_for(
                lambda: (
                    gun.samples_sent >= self.config.samples
                    or gun.transaction_id is None
                ),
                float("inf"),
            )
            if end == "unplug":
                await self.unplug(gun)
            elif end == "local":
                await self.press_stop(gun)
            else:
                print("POWER LOSS: the connection drops without StopTransaction")
                if gun.meter_task is not None:
                    gun.meter_task.cancel()
                self.power_lost.set()
                return
        elif end == "remote":
            print("WAITING for RemoteStopTransaction (stop the charge in the app)")
        elif end == "full":
            await self.wait_for(
                lambda: self.suspended_long_enough(gun) or gun.transaction_id is None,
                float("inf"),
            )
            await self.unplug(gun)
        await gun.ended.wait()
        if gun.is_plugged:
            await asyncio.sleep(1.0)
            await self.unplug(gun)

    def suspended_long_enough(self, gun: Gun) -> bool:
        """Whether a full car has stayed SuspendedEV for a couple of readings.

        Args:
            gun: The gun.

        Returns:
            ``True`` once ``_SUSPENDED_SAMPLES_BEFORE_UNPLUG`` readings were sent
            since the car became full.
        """
        return (
            gun.is_suspended
            and gun.samples_sent - gun.suspended_at_sample
            >= _SUSPENDED_SAMPLES_BEFORE_UNPLUG
        )

    async def run_charger_scenario(self) -> None:
        """Boot, then operate the charger from typed commands until ``quit``.

        Side Effects:
            Reads one command per line from standard input (end of input
            quits); remote starts, stops and configuration changes from the
            backend are answered meanwhile.
        """
        await self.boot()
        await self.report_all_available()
        print(CHARGER_HELP)
        print(f"READY: charger {self.config.identity} (type help)")
        while not self.power_lost.is_set():
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                print("end of input: quitting")
                return
            words = line.split()
            if not words:
                continue
            if words[0] == "quit":
                return
            await self.run_command(words)

    async def run_command(self, words: list[str]) -> None:
        """Run one typed charger command.

        Args:
            words: The command and its arguments.
        """
        command, arguments = words[0].lower(), words[1:]
        if command == "help":
            print(CHARGER_HELP)
            return
        if command == "status":
            self.print_guns()
            return
        if command == "wait" and arguments:
            await asyncio.sleep(float(arguments[0]))
            return
        if command == "power-loss":
            print("POWER LOSS: the connection drops without any stop message")
            self.power_lost.set()
            return
        gun_commands = (
            "plug",
            "unplug",
            "start",
            "stop",
            "card",
            "full",
            "fault",
            "clear",
        )
        if command not in gun_commands or not arguments or not arguments[0].isdigit():
            print(f"unknown command: {' '.join(words)} (type help)")
            return
        gun = self.guns.get(int(arguments[0]))
        if gun is None:
            print(f"no gun {arguments[0]}")
            return
        if command == "plug":
            await self.plug(gun)
        elif command == "unplug":
            await self.unplug(gun)
        elif command == "start":
            await self.press_start(gun)
        elif command == "stop":
            await self.press_stop(gun)
        elif command == "card":
            if len(arguments) < 2:
                print("usage: card N TAG")
                return
            await self.present_card(gun, arguments[1])
            if self.config.start == "screen" and gun.is_plugged:
                print("press start to begin (start N)")
        elif command == "full":
            gun.soc_percent = self.config.full_soc_percent
            print(f"gun {gun.number}: the car is full; SuspendedEV at the next reading")
        elif command == "fault":
            await self.fault(gun, arguments[1] if len(arguments) > 1 else "OtherError")
        elif command == "clear":
            await self.clear_fault(gun)


async def run(config: SimulatorConfig) -> RunReport:
    """Connect as a 1.6J charger, run the scenario, and disconnect.

    Args:
        config: Configuration for this run.

    Returns:
        The report with every CALLERROR received.

    Side Effects:
        Opens one WebSocket to the gateway with subprotocol ``ocpp1.6`` and
        closes it at the end. A background task serves CSMS-initiated calls
        for the whole time the connection is open. A power loss closes the
        connection at once, without lingering.
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
                await charge_point.boot()
            elif config.scenario == "status":
                await charge_point.run_status_scenario()
            elif config.scenario == "session":
                await charge_point.run_session_scenario()
            elif config.scenario == "charger":
                await charge_point.run_charger_scenario()
            if not charge_point.power_lost.is_set():
                await asyncio.sleep(config.linger_seconds)
        finally:
            for task in list(charge_point.background_tasks):
                task.cancel()
            for gun in charge_point.guns.values():
                if gun.meter_task is not None:
                    gun.meter_task.cancel()
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        print(f"FINISHED identity={config.identity} scenario={config.scenario}")
        return charge_point.report


def _config_override(text: str) -> tuple[str, str]:
    """Parse one ``--config KEY=VALUE`` argument.

    Args:
        text: The argument.

    Returns:
        The key and the value.

    Raises:
        argparse.ArgumentTypeError: If it has no ``=``.
    """
    key, separator, value = text.partition("=")
    if not separator or not key:
        raise argparse.ArgumentTypeError("expected KEY=VALUE")
    return key, value


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
        help="session: present this tag at the charger (a card) instead of "
        "waiting for a QR scan's remote start",
    )
    parser.add_argument("--remote-start-timeout", type=float, default=120.0)
    parser.add_argument("--meter-start", type=int, default=1000)
    parser.add_argument(
        "--meter-stop",
        type=int,
        default=0,
        help="lowest closing reading reported in StopTransaction",
    )
    parser.add_argument("--heartbeats", type=int, default=1)
    parser.add_argument("--heartbeat-interval", type=float, default=1.0)
    parser.add_argument("--linger", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--config",
        action="append",
        type=_config_override,
        default=[],
        metavar="KEY=VALUE",
        help="override a configuration key, e.g. AuthorizeRemoteTxRequests=true",
    )
    parser.add_argument("--sessions", type=int, default=1)
    parser.add_argument(
        "--plug",
        choices=PLUG_ORDERS,
        default="before-scan",
        help="session: when the driver plugs in",
    )
    parser.add_argument("--plug-delay", type=float, default=3.0)
    parser.add_argument(
        "--start",
        choices=START_MODES,
        default="auto",
        help="session/charger: start as soon as plugged and authorized, or "
        "when start is pressed on the screen",
    )
    parser.add_argument("--press-start-delay", type=float, default=2.0)
    parser.add_argument(
        "--end", choices=END_MODES, default="unplug", help="session: how it ends"
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=2,
        help="session: readings before an unplug/local/power-loss end",
    )
    parser.add_argument(
        "--sample-interval",
        type=float,
        default=None,
        help="seconds between readings (default: MeterValueSampleInterval)",
    )
    parser.add_argument(
        "--energy-step-wh",
        type=int,
        default=None,
        help="Wh per reading (default: from --power-kw and the interval)",
    )
    parser.add_argument("--power-kw", type=float, default=120.0)
    parser.add_argument("--battery-kwh", type=float, default=60.0)
    parser.add_argument("--start-soc", type=float, default=40.0)
    parser.add_argument("--full-soc", type=float, default=100.0)
    parser.add_argument("--remote-start-requires-gun", action="store_true")
    parser.add_argument(
        "--stop-on-remote",
        action="store_true",
        help="alias of --end remote",
    )
    parser.add_argument(
        "--no-stop", action="store_true", help="alias of --end power-loss"
    )
    parser.add_argument("--naive-timestamps", action="store_true")
    parser.add_argument("--uppercase-id-tag", action="store_true")
    parser.add_argument("--long-texts", action="store_true")
    parser.add_argument("--float-meter-start", action="store_true")
    parser.add_argument("--resend-stop", action="store_true")
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
    end = args.end
    if args.stop_on_remote:
        end = "remote"
    if args.no_stop:
        end = "power-loss"
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
            config_overrides=tuple(args.config),
            linger_seconds=args.linger,
            timeout_seconds=args.timeout,
            sessions=args.sessions,
            samples=args.samples,
            sample_interval_seconds=args.sample_interval,
            energy_step_wh=args.energy_step_wh,
            power_kw=args.power_kw,
            battery_kwh=args.battery_kwh,
            start_soc_percent=args.start_soc,
            full_soc_percent=args.full_soc,
            plug=args.plug,
            plug_delay_seconds=args.plug_delay,
            start=args.start,
            press_start_delay_seconds=args.press_start_delay,
            end=end,
            remote_start_requires_gun=args.remote_start_requires_gun,
            naive_timestamps=args.naive_timestamps,
            uppercase_id_tag=args.uppercase_id_tag,
            long_texts=args.long_texts,
            float_meter_start=args.float_meter_start,
            resend_stop=args.resend_stop,
        )
    )
    return 1 if report.errors else 0


if __name__ == "__main__":
    # Line-buffered output so a log file or a pipe shows each event at once.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("Stopped OCPP 1.6J simulator")
