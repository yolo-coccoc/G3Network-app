"""The charger's hardware and charging logic: two guns, the QR happy path.

Models a dual-gun DC charger (240 kW shared by its guns) through the flow our
backend supports: the driver scans the QR code with the phone app, the
backend sends ``RemoteStartTransaction``, and the charge runs until it is
stopped from the app, on the screen, or by unplugging. Out of scope on
purpose (owner decision TT-26): RFID cards, Autocharge, offline buffering,
faults and the E-stop.

Per gun (connector 1 and 2; connector 0 is the charger and stays
``Available``)::

    Available --plug in--> Preparing
    Available --remote start--> Preparing (authorized, waiting for the plug
              for ConnectionTimeOut seconds, then Available again)
    Preparing (plugged + authorized) --StartTransaction--> Charging
    Charging --car full--> SuspendedEV
    Charging / SuspendedEV --remote stop | screen Stop--> Finishing
    Charging / SuspendedEV --unplug--> Available (stop reason EVDisconnected)
    Preparing / Finishing --unplug--> Available

A remote start that names no gun goes to the gun already plugged in, or to
the first gun plugged in before ``ConnectionTimeOut``.

**Threads and order.** Everything here runs in the asyncio loop: the OCPP
handlers, the page's buttons (submitted with ``run_coroutine_threadsafe``)
and the one-second tick. Messages to the backend are put on one outgoing
queue and sent by ``serve`` one after the other, so they keep their order
and a button never waits for the network. The web thread only reads
``snapshot``; every change of state happens under ``_lock``.

**Time.** The charge itself (energy, SoC) can run faster than real time
(``speed``: x1, x10, x60), simulator only; the OCPP intervals
(``MeterValueSampleInterval``, ``ConnectionTimeOut``) stay in real seconds.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from car import Car
from configuration import ChargerConfiguration
from ocpp.exceptions import OCPPError
from ocpp.v16 import call
from ocpp.v16.enums import ChargePointErrorCode, ChargePointStatus, Reason
from state import iso, iso_now, log, utc_now
from websockets.exceptions import ConnectionClosed

# The charger's total DC power, shared by the guns that are charging.
CHARGER_MAX_POWER_KW = 240.0
GUN_NUMBERS = (1, 2)

# How often the charge is advanced and the timers checked, in real seconds.
TICK_SECONDS = 1.0

# How much faster than real time the charge may run (simulator only).
SPEEDS = (1, 10, 60)

# The measurands this charger can report, with their unit and location.
MEASURAND_UNITS: dict[str, tuple[str, str]] = {
    "Energy.Active.Import.Register": ("Wh", "Outlet"),
    "Power.Active.Import": ("W", "Outlet"),
    "Power.Offered": ("W", "Outlet"),
    "Current.Import": ("A", "Outlet"),
    "Voltage": ("V", "Outlet"),
    "SoC": ("Percent", "EV"),
    "Temperature": ("Celsius", "EV"),
}


class OcppLink(Protocol):
    """The part of the OCPP connection the station needs: sending a call."""

    async def call(self, payload: Any, suppress: bool = ...) -> Any:
        """Send one request and return the answer's payload."""


# A message to send: given the open link, it sends and handles the answer.
Job = Callable[[OcppLink], Awaitable[None]]


class StationError(Exception):
    """A button press that is not possible in the gun's current state."""


def mask(id_tag: str | None) -> str:
    """Show only the start of a token in the console (IS-07).

    Args:
        id_tag: The token.

    Returns:
        Its first two characters and an ellipsis.
    """
    return f"{id_tag[:2]}…" if id_tag else "-"


@dataclass
class Gun:
    """One gun (OCPP connector) and what is happening on it.

    Attributes:
        number: The connector number (1 or 2).
        status: The OCPP status last reported.
        car: The car plugged in; ``None`` when the gun is in its holster.
        register_wh: The gun's energy meter; it only goes up.
        authorized_tag: The token of a remote start waiting to be used.
        authorized_until: Loop time at which that authorization expires.
        starting: Whether a StartTransaction is on its way.
        transaction_id: The backend's transaction ID while charging.
        transaction_tag: The token the transaction was started with.
        meter_start_wh: The meter at the start of the transaction.
        started_at: When the transaction started.
        power_kw: Power delivered right now.
        offered_kw: Power the charger allots to this gun right now.
        last_sample_at: Loop time of the last MeterValues.
    """

    number: int
    status: str = "Available"
    car: Car | None = None
    register_wh: float = 0.0
    authorized_tag: str | None = None
    authorized_until: float = 0.0
    starting: bool = False
    transaction_id: int | None = None
    transaction_tag: str | None = None
    meter_start_wh: int | None = None
    started_at: datetime | None = None
    power_kw: float = 0.0
    offered_kw: float = 0.0
    last_sample_at: float = 0.0

    @property
    def is_busy(self) -> bool:
        """Whether a remote start must be refused on this gun."""
        return (
            self.starting
            or self.transaction_id is not None
            or self.authorized_tag is not None
            or self.status in ("Finishing", "Faulted", "Unavailable")
        )


class ChargingStation:
    """The charger: its guns, the sample cars, and the messages they cause.

    Attributes:
        configuration: The OCPP configuration keys (intervals, measurands).
        guns: The guns by connector number.
        speed: How much faster than real time the charge runs.
    """

    def __init__(self, configuration: ChargerConfiguration) -> None:
        """Start with both guns in their holsters.

        Args:
            configuration: The charger's OCPP configuration keys.
        """
        self.configuration = configuration
        self.guns = {number: Gun(number) for number in GUN_NUMBERS}
        self.speed = 1
        self._lock = threading.Lock()
        self._outbox: asyncio.Queue[Job] = asyncio.Queue()
        # A remote start that named no gun: (token, loop time it expires).
        self._unassigned: tuple[str, float] | None = None
        # The asyncio loop, saved by ``run`` so the web thread can read the
        # loop clock for the page's countdowns.
        self.loop: asyncio.AbstractEventLoop | None = None

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _now() -> float:
        """Return the loop's clock (monotonic seconds)."""
        return asyncio.get_running_loop().time()

    def _gun(self, number: int) -> Gun:
        """Return a gun by number.

        Args:
            number: The connector number.

        Returns:
            The gun.

        Raises:
            StationError: There is no such gun.
        """
        gun = self.guns.get(number)
        if gun is None:
            raise StationError(f"there is no gun {number}")
        return gun

    def _set_status(self, gun: Gun, status: str) -> None:
        """Change a gun's status and queue its StatusNotification.

        Args:
            gun: The gun.
            status: The new OCPP status.
        """
        if gun.status == status:
            return
        gun.status = status
        self._outbox.put_nowait(self._status_job(gun.number, status))

    def _status_job(self, connector_id: int, status: str) -> Job:
        """Make the job that sends one StatusNotification.

        Args:
            connector_id: 0 (the charger) or a gun.
            status: The OCPP status.

        Returns:
            The job for the outgoing queue.
        """

        async def job(link: OcppLink) -> None:
            await self._send_status(link, connector_id, status)

        return job

    def announce_statuses(self) -> None:
        """Queue a StatusNotification for the charger and each gun.

        Sent after every boot or reconnect, so the backend has the current
        state of every connector.
        """
        self._outbox.put_nowait(self._status_job(0, "Available"))
        for gun in self.guns.values():
            self._outbox.put_nowait(self._status_job(gun.number, gun.status))

    # --- the page's buttons (physical actions and the screen) ---------------

    def plug(self, number: int, start_soc: float) -> str:
        """Plug the sample car into a gun.

        Args:
            number: The gun.
            start_soc: The car's state of charge, in percent.

        Returns:
            What happened, for the page.

        Raises:
            StationError: The gun is already plugged in or not usable.
        """
        with self._lock:
            gun = self._gun(number)
            if gun.car is not None:
                raise StationError(f"gun {number} is already plugged in")
            if gun.status in ("Faulted", "Unavailable"):
                raise StationError(f"gun {number} is {gun.status}")
            gun.car = Car.sample(start_soc)
            log(f"   gun {number}: car plugged in at {gun.car.soc:.0f} % SoC")
            if gun.authorized_tag is None and self._unassigned is not None:
                gun.authorized_tag, gun.authorized_until = self._unassigned
                self._unassigned = None
            if gun.authorized_tag is not None:
                self._queue_start(gun)
                return f"gun {number} plugged in; starting the authorized charge"
            self._set_status(gun, "Preparing")
            return f"gun {number} plugged in; waiting for a QR scan"

    def unplug(self, number: int) -> str:
        """Take the gun out of the car and put it back in its holster.

        A running transaction ends with reason ``EVDisconnected``
        (``StopTransactionOnEVSideDisconnect`` is true on this charger).

        Args:
            number: The gun.

        Returns:
            What happened, for the page.

        Raises:
            StationError: The gun is not plugged in.
        """
        with self._lock:
            gun = self._gun(number)
            if gun.car is None:
                raise StationError(f"gun {number} is not plugged in")
            gun.car = None
            gun.power_kw = gun.offered_kw = 0.0
            log(f"   gun {number}: unplugged")
            if gun.transaction_id is not None:
                self._queue_stop(gun, "EVDisconnected")
                return f"gun {number} unplugged; the charge stops (EVDisconnected)"
            if not gun.starting:
                gun.authorized_tag = None
                self._set_status(gun, "Available")
            return f"gun {number} unplugged"

    def stop(self, number: int) -> str:
        """Press Stop on the charger's screen for a gun.

        Args:
            number: The gun.

        Returns:
            What happened, for the page.

        Raises:
            StationError: No charge is running on the gun.
        """
        with self._lock:
            gun = self._gun(number)
            if gun.transaction_id is None:
                raise StationError(f"no charge is running on gun {number}")
            self._queue_stop(gun, "Local")
            return f"gun {number}: stopping (reason Local)"

    def set_speed(self, speed: int) -> str:
        """Change how fast the charge runs (simulator only).

        Args:
            speed: One of ``SPEEDS``.

        Returns:
            What happened, for the page.

        Raises:
            StationError: Not an allowed speed.
        """
        if speed not in SPEEDS:
            raise StationError(f"speed must be one of {', '.join(map(str, SPEEDS))}")
        with self._lock:
            self.speed = speed
        return f"charging speed x{speed}"

    # --- requests from the backend ----------------------------------------

    def remote_start(self, id_tag: str, connector_id: int | None) -> str:
        """Handle ``RemoteStartTransaction`` (the driver scanned the QR code).

        The authorization waits ``ConnectionTimeOut`` seconds for the car to
        be plugged in; the charge starts as soon as both are there.

        Args:
            id_tag: The token the backend issued for the scan.
            connector_id: The gun the driver chose; ``None`` = any gun.

        Returns:
            ``Accepted``, or ``Rejected`` when the gun is unknown or busy (or,
            without a gun, when every gun is busy).
        """
        with self._lock:
            until = self._now() + self.configuration.int_value("ConnectionTimeOut")
            if connector_id:
                gun = self.guns.get(connector_id)
                if gun is None or gun.is_busy:
                    return "Rejected"
            else:
                waiting = [g for g in self.guns.values() if g.car and not g.is_busy]
                free = [g for g in self.guns.values() if not g.is_busy]
                if not free or self._unassigned is not None:
                    return "Rejected"
                if not waiting:
                    # No car plugged in yet: the first gun plugged takes it.
                    self._unassigned = (id_tag, until)
                    self._queue_authorize(None, id_tag)
                    return "Accepted"
                gun = waiting[0]
            gun.authorized_tag, gun.authorized_until = id_tag, until
            self._queue_authorize(gun.number, id_tag)
            if gun.car is not None:
                self._queue_start(gun)
            else:
                self._set_status(gun, "Preparing")
            return "Accepted"

    def remote_stop(self, transaction_id: int) -> str:
        """Handle ``RemoteStopTransaction`` (the driver stopped in the app).

        Args:
            transaction_id: The transaction to stop.

        Returns:
            ``Accepted``, or ``Rejected`` when no gun runs that transaction.
        """
        with self._lock:
            for gun in self.guns.values():
                if gun.transaction_id == transaction_id:
                    self._queue_stop(gun, "Remote")
                    return "Accepted"
            return "Rejected"

    # --- messages to the backend --------------------------------------------

    def _queue_authorize(self, number: int | None, id_tag: str) -> None:
        """Queue an ``Authorize`` for a remote start, if the charger asks one.

        With ``AuthorizeRemoteTxRequests`` false the token is trusted as is.

        Args:
            number: The gun holding the authorization; ``None`` = not yet
                assigned to a gun.
            id_tag: The token.
        """
        if self.configuration.value("AuthorizeRemoteTxRequests") != "true":
            return

        async def job(link: OcppLink) -> None:
            response = await link.call(call.Authorize(id_tag=id_tag), suppress=False)
            status = response.id_tag_info.get("status")
            log(f"<- Authorize {mask(id_tag)}: {status}")
            if status == "Accepted":
                return
            with self._lock:
                if number is None:
                    self._unassigned = None
                    return
                gun = self.guns[number]
                if gun.authorized_tag == id_tag and not gun.starting:
                    gun.authorized_tag = None
                    self._set_status(gun, "Preparing" if gun.car else "Available")

        log(f"-> Authorize {mask(id_tag)}")
        self._outbox.put_nowait(job)

    def _queue_start(self, gun: Gun) -> None:
        """Queue the ``StartTransaction`` of a plugged-in, authorized gun.

        Args:
            gun: The gun; holds the token and the car.
        """
        gun.starting = True
        number = gun.number

        async def job(link: OcppLink) -> None:
            with self._lock:
                gun = self.guns[number]
                id_tag = gun.authorized_tag
                if id_tag is None or gun.car is None:
                    # The Authorize was refused, or the car left meanwhile.
                    gun.starting = False
                    self._set_status(gun, "Preparing" if gun.car else "Available")
                    return
                meter_start = int(gun.register_wh)
            log(f"-> StartTransaction gun {number}, meterStart {meter_start} Wh")
            response = await link.call(
                call.StartTransaction(
                    connector_id=number,
                    id_tag=id_tag,
                    meter_start=meter_start,
                    timestamp=iso_now(),
                ),
                suppress=False,
            )
            status = response.id_tag_info.get("status")
            log(
                f"<- StartTransaction: {status}, transactionId {response.transaction_id}"
            )
            with self._lock:
                gun.starting = False
                gun.authorized_tag = None
                gun.transaction_id = int(response.transaction_id)
                gun.transaction_tag = id_tag
                gun.meter_start_wh = meter_start
                gun.started_at = utc_now()
                gun.last_sample_at = self._now()
                if status != "Accepted":
                    # StopTransactionOnInvalidId: end it at once.
                    self._queue_stop(gun, "DeAuthorized")
                elif gun.car is None:
                    # Unplugged while the start was on its way.
                    self._queue_stop(gun, "EVDisconnected")
                else:
                    self._set_status(gun, "Charging")
                    # A first reading at the start, as real chargers send:
                    # the app shows the energy and SoC at once.
                    self._queue_meter_values(gun, "Transaction.Begin")

        self._outbox.put_nowait(job)

    def _queue_stop(self, gun: Gun, reason: str) -> None:
        """Queue the ``StopTransaction`` of a gun's running transaction.

        Args:
            gun: The gun.
            reason: The OCPP stop reason (``Remote``, ``Local``,
                ``EVDisconnected``, ``DeAuthorized``).
        """
        number = gun.number
        transaction_id = gun.transaction_id
        gun.power_kw = 0.0

        async def job(link: OcppLink) -> None:
            with self._lock:
                gun = self.guns[number]
                if gun.transaction_id != transaction_id or transaction_id is None:
                    return  # already stopped
                meter_stop = int(gun.register_wh)
                final = self._sample(gun, "StopTxnSampledData", "Transaction.End")
                id_tag = gun.transaction_tag
            log(f"-> StopTransaction gun {number}, {reason}, meterStop {meter_stop} Wh")
            await link.call(
                call.StopTransaction(
                    meter_stop=meter_stop,
                    timestamp=iso_now(),
                    transaction_id=transaction_id,
                    reason=Reason(reason),
                    id_tag=id_tag,
                    transaction_data=[final] if final["sampled_value"] else None,
                ),
                suppress=False,
            )
            with self._lock:
                gun.transaction_id = gun.transaction_tag = None
                gun.meter_start_wh = None
                gun.started_at = None
                gun.power_kw = gun.offered_kw = 0.0
                self._set_status(gun, "Finishing" if gun.car else "Available")

        self._outbox.put_nowait(job)

    def _sample(self, gun: Gun, key: str, context: str) -> dict[str, Any]:
        """Build one meter reading with the measurands a configuration key lists.

        Args:
            gun: The gun.
            key: ``MeterValuesSampledData`` or ``StopTxnSampledData``.
            context: The OCPP reading context.

        Returns:
            A ``MeterValue`` entry (``timestamp`` and ``sampled_value``);
            measurands this charger cannot measure are left out.
        """
        car = gun.car
        voltage = car.voltage_v if car else 0.0
        values: dict[str, float] = {
            "Energy.Active.Import.Register": gun.register_wh,
            "Power.Active.Import": gun.power_kw * 1000,
            "Power.Offered": gun.offered_kw * 1000,
            "Voltage": voltage,
            "Current.Import": gun.power_kw * 1000 / voltage if voltage else 0.0,
        }
        if car is not None:
            values["SoC"] = car.soc
            values["Temperature"] = car.temperature_celsius(gun.power_kw)
        sampled = []
        for measurand in (self.configuration.value(key) or "").split(","):
            measurand = measurand.strip()
            if measurand in values:
                unit, location = MEASURAND_UNITS[measurand]
                number = values[measurand]
                text = str(int(number)) if unit == "Wh" else f"{number:.1f}"
                sampled.append(
                    {
                        "value": text,
                        "context": context,
                        "measurand": measurand,
                        "location": location,
                        "unit": unit,
                    }
                )
        return {"timestamp": iso_now(), "sampled_value": sampled}

    async def _send_status(
        self, link: OcppLink, connector_id: int, status: str
    ) -> None:
        """Send one StatusNotification.

        Args:
            link: The open connection.
            connector_id: 0 (the charger) or a gun.
            status: The OCPP status.
        """
        log(f"-> StatusNotification connector {connector_id}: {status}")
        await link.call(
            call.StatusNotification(
                connector_id=connector_id,
                error_code=ChargePointErrorCode.no_error,
                status=ChargePointStatus(status),
                timestamp=iso_now(),
            ),
            suppress=False,
        )

    async def serve(self, link: OcppLink) -> None:
        """Send the queued messages, one at a time, while the link is open.

        A message the backend refuses (CALLERROR) or does not answer is
        logged and dropped; a closed connection ends the loop (what is still
        queued goes out after the reconnect).

        Args:
            link: The open connection.

        Raises:
            ConnectionClosed: The connection closed.
        """
        while True:
            job = await self._outbox.get()
            try:
                await job(link)
            except (OCPPError, TimeoutError) as error:
                log(
                    f"!! message not accepted by the server: {type(error).__name__}: {error}"
                )
            except ConnectionClosed:
                log("!! connection closed while sending; message dropped")
                raise

    # --- the clock ------------------------------------------------------------

    async def run(self) -> None:
        """Advance the charges and the timers every second, until cancelled.

        Per gun with a transaction: the power is the car's request, capped by
        the charger's share (240 kW divided by the guns drawing power); the
        energy goes into the gun's meter and the car's battery, ``speed``
        times faster than real time. A full car turns the gun SuspendedEV.
        MeterValues go out every ``MeterValueSampleInterval`` real seconds.
        An authorization not used within ``ConnectionTimeOut`` expires.
        """
        self.loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(TICK_SECONDS)
            with self._lock:
                self._tick(self._now())

    def _tick(self, now: float) -> None:
        """One step of the clock (called with the lock held).

        Args:
            now: The loop time.
        """
        active = [
            gun
            for gun in self.guns.values()
            if gun.transaction_id is not None and gun.car and not gun.car.is_full
        ]
        share = CHARGER_MAX_POWER_KW / max(1, len(active))
        for gun in self.guns.values():
            car = gun.car
            if gun.transaction_id is None or car is None:
                continue
            gun.offered_kw = share if gun in active else 0.0
            gun.power_kw = min(car.requested_power_kw(), gun.offered_kw)
            energy_kwh = gun.power_kw * TICK_SECONDS / 3600 * self.speed
            gun.register_wh += energy_kwh * 1000
            car.take_energy(energy_kwh)
            if car.is_full and gun.status == "Charging":
                gun.power_kw = 0.0
                log(f"   gun {gun.number}: car full, BMS stops requesting power")
                self._set_status(gun, "SuspendedEV")
            interval = self.configuration.int_value("MeterValueSampleInterval")
            if interval > 0 and now - gun.last_sample_at >= interval:
                gun.last_sample_at = now
                self._queue_meter_values(gun, "Sample.Periodic")

        for gun in self.guns.values():
            expired = gun.authorized_tag is not None and now >= gun.authorized_until
            if expired and not gun.starting:
                log(f"   gun {gun.number}: authorization expired (ConnectionTimeOut)")
                gun.authorized_tag = None
                self._set_status(gun, "Preparing" if gun.car else "Available")
        if self._unassigned is not None and now >= self._unassigned[1]:
            log("   remote start expired: no gun was plugged in (ConnectionTimeOut)")
            self._unassigned = None

    def _queue_meter_values(self, gun: Gun, context: str) -> None:
        """Queue a MeterValues for a gun's transaction.

        Args:
            gun: The gun (with a transaction).
            context: ``Transaction.Begin`` (right after the start) or
                ``Sample.Periodic``.
        """
        sample = self._sample(gun, "MeterValuesSampledData", context)
        number, transaction_id = gun.number, gun.transaction_id
        if not sample["sampled_value"]:
            return

        async def job(link: OcppLink) -> None:
            await link.call(
                call.MeterValues(
                    connector_id=number,
                    transaction_id=transaction_id,
                    meter_value=[sample],
                ),
                suppress=False,
            )

        self._outbox.put_nowait(job)

    # --- the page ---------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return what the charger's screen shows, as JSON-ready values.

        Returns:
            The status light, the speed, and per gun: status, car, live
            readings, the transaction and any waiting authorization.
        """
        with self._lock:
            loop_now = self._loop_time_or_none()
            guns = []
            for gun in self.guns.values():
                car = gun.car
                waiting_s = None
                if gun.authorized_tag is not None and loop_now is not None:
                    waiting_s = max(0.0, gun.authorized_until - loop_now)
                guns.append(
                    {
                        "number": gun.number,
                        "status": gun.status,
                        "plugged": car is not None,
                        "car": None
                        if car is None
                        else {
                            "name": car.name,
                            "battery_kwh": car.battery_kwh,
                            "max_power_kw": car.max_power_kw,
                            "soc": round(car.soc, 1),
                            "stage": car.stage,
                            "voltage_v": round(car.voltage_v, 1),
                        },
                        "power_kw": round(gun.power_kw, 1),
                        "offered_kw": round(gun.offered_kw, 1),
                        "current_a": round(gun.power_kw * 1000 / car.voltage_v, 1)
                        if car and car.voltage_v
                        else 0.0,
                        "register_kwh": round(gun.register_wh / 1000, 3),
                        "session_kwh": round(
                            (gun.register_wh - gun.meter_start_wh) / 1000, 3
                        )
                        if gun.meter_start_wh is not None
                        else None,
                        "transaction_id": gun.transaction_id,
                        "started_at": iso(gun.started_at),
                        "starting": gun.starting,
                        "authorization_waiting_s": waiting_s,
                    }
                )
            statuses = {gun["status"] for gun in guns}
            if "Charging" in statuses:
                light = "charging"
            elif statuses & {"Preparing", "SuspendedEV", "Finishing"}:
                light = "connected"
            else:
                light = "standby"
            unassigned = None
            if self._unassigned is not None and loop_now is not None:
                unassigned = max(0.0, self._unassigned[1] - loop_now)
            return {
                "light": light,
                "speed": self.speed,
                "speeds": list(SPEEDS),
                "max_power_kw": CHARGER_MAX_POWER_KW,
                "unassigned_authorization_s": unassigned,
                "guns": guns,
            }

    def _loop_time_or_none(self) -> float | None:
        """Return the loop's clock, or ``None`` outside the loop's thread.

        The page reads the snapshot from the web thread, where there is no
        running loop; the waiting times are then computed from the saved
        loop reference.

        Returns:
            The loop time.
        """
        loop = self.loop
        return loop.time() if loop is not None else None
