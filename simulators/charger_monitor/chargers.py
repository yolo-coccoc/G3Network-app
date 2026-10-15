"""Registers, edits and deletes chargers through the backend API.

A charger a driver can scan needs four things in the backend: a **location**
(public), the **charger** (its OCPP ID, the ID typed on its screen), one
**gun** per outlet (an EVSE with one connector: the OCPP 1.6 gun ``n`` is
EVSE ``n`` / connector 1) and a **tariff** in force at that location. This
module turns the page's form into the API calls, one step at a time, and
reports each step:

* **Register:** location (an existing one, or a new one created under the
  monitor's own organization) -> charger -> each gun and its plug -> the
  price. Every step first looks for what already exists (the location by
  name, the charger by OCPP ID, a gun by number, the location's tariff), so
  pressing Register again after a failure completes the job instead of
  duplicating it.
* **Edit:** only what changed: the charger's fields, each gun's plug,
  guns added or removed, and the price (a new tariff version, which applies
  to every charger at that location).
* **Delete:** the backend's soft delete of the charger and its guns
  (refused while a charge is running).

The API calls are separate transactions: when a step fails, the earlier ones
stay done, and the report says where it stopped.
"""

from __future__ import annotations

import re
from typing import Any

from api_client import ApiClient, ApiError

STATIONS = "/api/v1/charging-stations"
LOCATIONS = "/api/v1/charging-locations"
TARIFFS = "/api/v1/tariffs"
PAGE_SIZE = 100

# The reason stored with a price change (tariff versions need one).
PRICE_CHANGE_REASON = "Set in the charger monitor"

# The eMI3 prefix of a gun's public ID (country * operator * E...), CS-16.
EMI3_PREFIX = "VN*G3N*E"

# Plug standards the backend knows (its ConnectorStandard).
PLUG_STANDARDS = (
    "IEC_62196_T2_COMBO",
    "IEC_62196_T1_COMBO",
    "CHADEMO",
    "GBT_DC",
    "CHAOJI",
)


class FormError(Exception):
    """The page sent a charger form that cannot be used."""


class Steps:
    """The step-by-step report of one register or edit.

    Attributes:
        items: One entry per step: ``step``, ``ok`` and ``detail``.
    """

    def __init__(self) -> None:
        """Start with no step."""
        self.items: list[dict[str, Any]] = []

    def done(self, step: str, detail: str) -> None:
        """Record a step that worked.

        Args:
            step: What the step did.
            detail: Its result.
        """
        self.items.append({"step": step, "ok": True, "detail": detail})

    def failed(self, step: str, error: ApiError) -> dict[str, Any]:
        """Record the step that failed and return the report.

        Args:
            step: What the step tried.
            error: The backend's refusal.

        Returns:
            The report (``ok`` false, every step so far).
        """
        self.items.append({"step": step, "ok": False, "detail": error.detail})
        return {"ok": False, "steps": self.items}

    def report(self) -> dict[str, Any]:
        """Return the report of a run where every step worked.

        Returns:
            ``ok`` true and the steps.
        """
        return {"ok": True, "steps": self.items}


def _text(
    form: dict[str, Any], name: str, *, required: bool, max_length: int
) -> str | None:
    """Read a text field of the form.

    Args:
        form: The decoded form.
        name: The field.
        required: Whether it must be filled.
        max_length: Longest accepted value.

    Returns:
        The text, stripped; ``None`` when empty and not required.

    Raises:
        FormError: Missing when required, not text, or too long.
    """
    value = form.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise FormError(f"{name} is required")
        return None
    if not isinstance(value, str):
        raise FormError(f"{name} must be text")
    if len(value.strip()) > max_length:
        raise FormError(f"{name} is longer than {max_length} characters")
    return value.strip()


def _number(form: dict[str, Any], name: str, *, required: bool) -> float | None:
    """Read a number field of the form.

    Args:
        form: The decoded form.
        name: The field.
        required: Whether it must be filled.

    Returns:
        The number; ``None`` when empty and not required.

    Raises:
        FormError: Missing when required, or not a number.
    """
    value = form.get(name)
    if value is None or value == "":
        if required:
            raise FormError(f"{name} is required")
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FormError(f"{name} must be a number")
    return float(value)


def emi3_evse_id(ocpp_identity: str, gun_number: int) -> str:
    """Make a gun's public eMI3 ID from the charger's OCPP ID and the gun number.

    The backend requires one per gun (CS-16); it is derived, not typed:
    ``VN*G3N*E`` + the OCPP ID's letters and digits + ``*`` + the gun.

    Args:
        ocpp_identity: The charger's OCPP ID.
        gun_number: The gun (1, 2, ...).

    Returns:
        The ID, at most 48 characters (the backend's limit).
    """
    core = re.sub(r"[^A-Z0-9]", "", ocpp_identity.upper())
    suffix = f"*{gun_number}"
    return EMI3_PREFIX + core[: 48 - len(EMI3_PREFIX) - len(suffix)] + suffix


def parse_guns(form: dict[str, Any]) -> list[dict[str, Any]]:
    """Read the guns of the form.

    Args:
        form: The decoded form; ``guns`` is a list, in gun-number order.

    Returns:
        Per gun: ``number`` (1, 2, ...), ``evse_id`` (an existing gun, when
        editing), ``standard``, ``max_power_kw``, ``max_voltage_v``,
        ``max_current_a``.

    Raises:
        FormError: No gun, more than 8, or a plug field is invalid.
    """
    guns = form.get("guns")
    if not isinstance(guns, list) or not 1 <= len(guns) <= 8:
        raise FormError("a charger needs 1 to 8 guns")
    parsed = []
    for number, gun in enumerate(guns, start=1):
        if not isinstance(gun, dict):
            raise FormError(f"gun {number} is not valid")
        standard = gun.get("standard")
        if standard not in PLUG_STANDARDS:
            raise FormError(f"gun {number}: unknown plug standard {standard!r}")
        power = _number(gun, "max_power_kw", required=True)
        voltage = _number(gun, "max_voltage_v", required=True)
        current = _number(gun, "max_current_a", required=True)
        assert power is not None and voltage is not None and current is not None
        if power <= 0 or voltage <= 0 or current <= 0:
            raise FormError(f"gun {number}: power, voltage and current must be above 0")
        evse_id = gun.get("evse_id")
        parsed.append(
            {
                "number": number,
                "evse_id": evse_id if isinstance(evse_id, str) and evse_id else None,
                "standard": standard,
                "max_power_kw": power,
                "max_voltage_v": int(voltage),
                "max_current_a": int(current),
            }
        )
    return parsed


def parse_price(form: dict[str, Any]) -> tuple[int, float] | None:
    """Read the price of the form.

    Args:
        form: The decoded form; ``price_per_kwh`` and ``vat_rate_percent``.

    Returns:
        ``(price per kWh in dong, VAT %)``, or ``None`` when the price is left
        empty (keep the location's tariff as it is).

    Raises:
        FormError: Only one of the two is filled, or a value is out of range.
    """
    price = _number(form, "price_per_kwh", required=False)
    vat = _number(form, "vat_rate_percent", required=False)
    if price is None and vat is None:
        return None
    if price is None or vat is None:
        raise FormError("fill in both the price per kWh and the VAT, or neither")
    if price < 0 or not 0 <= vat <= 100:
        raise FormError("the price must be 0 or more and the VAT between 0 and 100 %")
    return int(price), vat


class ChargerAdmin:
    """The Chargers tab's calls to the backend.

    Attributes:
        client: The logged-in API client (an internal admin).
    """

    def __init__(self, client: ApiClient) -> None:
        """Keep the client.

        Args:
            client: The logged-in API client.
        """
        self.client = client

    # --- reading -----------------------------------------------------------------

    def overview(self) -> dict[str, Any]:
        """Return every charger with its guns and price, and the locations.

        One API call per charger and per gun (the list is small); the price
        is the tariff in force for the charger now, ``None`` when there is
        none (then no scan can start there).

        Returns:
            ``chargers``, ``locations`` and the known ``plug_standards``.
        """
        stations = self.client.get(
            STATIONS, {"scope": "MANAGED", "page": 1, "page_size": PAGE_SIZE}
        ).get("items", [])
        chargers = []
        for station in stations:
            station_id = station["station_id"]
            chargers.append(
                {
                    "station_id": station_id,
                    "ocpp_identity": station["ocpp_identity"],
                    "registered_serial_number": station["registered_serial_number"],
                    "physical_reference": station.get("physical_reference"),
                    "max_power_kw": station.get("max_power_kw"),
                    "status": station.get("status"),
                    "is_online": station.get("is_online"),
                    "last_seen_at": station.get("last_seen_at"),
                    "location_id": station["location_id"],
                    "location_name": station.get("location_display_name"),
                    "guns": self._guns(station_id),
                    "price": self._price_in_force(station_id),
                }
            )
        locations = self.client.get(
            LOCATIONS, {"scope": "MANAGED", "page": 1, "page_size": PAGE_SIZE}
        ).get("items", [])
        return {
            "chargers": chargers,
            "locations": [
                {
                    "location_id": item["location_id"],
                    "display_name": item["display_name"],
                    "address": item["address"],
                    "is_public": item["is_public"],
                    "status": item["status"],
                    "price": self._location_tariff_price(item["location_id"]),
                }
                for item in locations
            ],
            "plug_standards": list(PLUG_STANDARDS),
        }

    def _guns(self, station_id: str) -> list[dict[str, Any]]:
        """Return a charger's guns with their plug, in gun-number order.

        Args:
            station_id: The charger.

        Returns:
            Per gun: number, IDs, status and the plug's limits.
        """
        evses = self.client.get(
            f"{STATIONS}/{station_id}/evses", {"page": 1, "page_size": PAGE_SIZE}
        ).get("items", [])
        guns = []
        for evse in sorted(evses, key=lambda item: item["ocpp_evse_id"]):
            connectors = self.client.get(
                f"/api/v1/charging-evses/{evse['evse_id']}/connectors",
                {"page": 1, "page_size": PAGE_SIZE},
            ).get("items", [])
            plug = connectors[0] if connectors else {}
            guns.append(
                {
                    "number": evse["ocpp_evse_id"],
                    "evse_id": evse["evse_id"],
                    "connector_id": plug.get("connector_id"),
                    "status": evse["status"],
                    "standard": plug.get("standard"),
                    "max_power_kw": plug.get("max_power_kw"),
                    "max_voltage_v": plug.get("max_voltage_v"),
                    "max_current_a": plug.get("max_current_a"),
                    "connector_status": plug.get("status"),
                }
            )
        return guns

    def _price_in_force(self, station_id: str) -> dict[str, Any] | None:
        """Return the price a driver would pay at a charger now.

        Args:
            station_id: The charger.

        Returns:
            ``price_per_kwh``, ``vat_rate_percent`` and the tariff's name, or
            ``None`` when no tariff prices it (the backend answers 404 / 409).
        """
        try:
            quote = self.client.get(f"{TARIFFS}/in-force", {"station_id": station_id})
        except ApiError as error:
            if error.status in (404, 409):
                return None
            raise
        return {
            "price_per_kwh": quote.get("price_per_kwh"),
            "vat_rate_percent": quote.get("vat_rate_percent"),
            "tariff_name": quote.get("tariff_name"),
        }

    def _location_tariff(self, location_id: str) -> dict[str, Any] | None:
        """Return the active tariff of a location, if it has one of its own.

        Args:
            location_id: The location.

        Returns:
            The backend's ``TariffResponse``, or ``None``.
        """
        items = self.client.get(
            TARIFFS,
            {
                "location_id": location_id,
                "status": "ACTIVE",
                "page": 1,
                "page_size": 10,
            },
        ).get("items", [])
        tariff: dict[str, Any] | None = items[0] if items else None
        return tariff

    def _location_tariff_price(self, location_id: str) -> dict[str, Any] | None:
        """Return the current price of a location's own tariff.

        Args:
            location_id: The location.

        Returns:
            ``price_per_kwh`` and ``vat_rate_percent``, or ``None``.
        """
        tariff = self._location_tariff(location_id)
        version = (tariff or {}).get("current_version") or {}
        if not version:
            return None
        return {
            "price_per_kwh": version.get("price_per_kwh"),
            "vat_rate_percent": version.get("vat_rate_percent"),
        }

    # --- register --------------------------------------------------------------------

    def register(self, form: dict[str, Any]) -> dict[str, Any]:
        """Register a charger: location, charger, guns, price.

        Args:
            form: ``location_id`` (an existing location) or ``new_location``
                (``display_name``, ``address``, ``latitude``, ``longitude``,
                ``is_public``); ``ocpp_identity``, ``registered_serial_number``,
                ``physical_reference``, ``max_power_kw``; ``guns``; and
                optionally ``price_per_kwh`` + ``vat_rate_percent``.

        Returns:
            The step-by-step report.

        Raises:
            FormError: The form cannot be used (nothing was sent).
        """
        identity = _text(form, "ocpp_identity", required=True, max_length=255)
        serial = _text(form, "registered_serial_number", required=True, max_length=100)
        label = _text(form, "physical_reference", required=False, max_length=16)
        max_power = _number(form, "max_power_kw", required=False)
        guns = parse_guns(form)
        price = parse_price(form)
        new_location = form.get("new_location")
        location_id = form.get("location_id")
        if not location_id and not isinstance(new_location, dict):
            raise FormError("choose a location or describe a new one")

        steps = Steps()
        try:
            if not location_id:
                assert isinstance(new_location, dict)
                location_id = self._find_or_create_location(new_location, steps)
            station_id = self._find_or_create_station(
                str(location_id), identity, serial, label, max_power, steps
            )
        except ApiError as error:
            return steps.failed("location / charger", error)
        try:
            self._sync_guns(station_id, guns, steps)
        except ApiError as error:
            return steps.failed("guns", error)
        try:
            self._apply_price(str(location_id), price, steps, require_price=True)
        except ApiError as error:
            return steps.failed("price", error)
        return steps.report()

    def _find_or_create_location(self, fields: dict[str, Any], steps: Steps) -> str:
        """Find a location by name, or create it under the monitor's organization.

        Args:
            fields: ``display_name``, ``address``, ``latitude``, ``longitude``,
                ``is_public``.
            steps: The report.

        Returns:
            The location's ID.

        Raises:
            FormError: A field is missing or invalid.
            ApiError: The backend refused.
        """
        name = _text(fields, "display_name", required=True, max_length=200)
        address = _text(fields, "address", required=True, max_length=500)
        latitude = _number(fields, "latitude", required=True)
        longitude = _number(fields, "longitude", required=True)
        assert name is not None and address is not None
        existing = self.client.get(
            LOCATIONS,
            {"scope": "MANAGED", "q": name, "page": 1, "page_size": PAGE_SIZE},
        ).get("items", [])
        for item in existing:
            if item["display_name"] == name:
                steps.done("location", f"using the existing location {name!r}")
                return str(item["location_id"])
        created = self.client.post(
            LOCATIONS,
            {
                "display_name": name,
                "address": address,
                "latitude": latitude,
                "longitude": longitude,
                "is_public": bool(fields.get("is_public", True)),
            },
        )
        steps.done("location", f"created {name!r}")
        return str(created["location_id"])

    def _find_or_create_station(
        self,
        location_id: str,
        identity: str | None,
        serial: str | None,
        label: str | None,
        max_power: float | None,
        steps: Steps,
    ) -> str:
        """Find the charger by OCPP ID, or create it at the location.

        Args:
            location_id: Where it stands.
            identity: Its OCPP ID.
            serial: Its serial number.
            label: Its label (or ``None``).
            max_power: Its total power in kW (or ``None``).
            steps: The report.

        Returns:
            The charger's ID.

        Raises:
            ApiError: The backend refused.
        """
        existing = self.client.get(
            STATIONS,
            {"scope": "MANAGED", "q": identity, "page": 1, "page_size": PAGE_SIZE},
        ).get("items", [])
        for item in existing:
            if item["ocpp_identity"] == identity:
                steps.done("charger", f"{identity} is already registered; using it")
                return str(item["station_id"])
        body: dict[str, object] = {
            "location_id": location_id,
            "ocpp_identity": identity,
            "registered_serial_number": serial,
        }
        if label:
            body["physical_reference"] = label
        if max_power:
            body["max_power_kw"] = max_power
        created = self.client.post(STATIONS, body)
        steps.done("charger", f"registered {identity}")
        return str(created["station_id"])

    # --- guns and price (register and edit) --------------------------------------------

    def _sync_guns(
        self, station_id: str, guns: list[dict[str, Any]], steps: Steps
    ) -> None:
        """Make the charger's guns match the form.

        Gun ``n`` of the form is EVSE ``n`` with connector 1 (its public eMI3
        ID derived by ``emi3_evse_id``). A gun that
        exists keeps its EVSE and gets its plug updated when it changed; a
        missing gun is created with its plug; a gun beyond the form's count is
        deleted (the backend refuses while it is charging).

        Args:
            station_id: The charger.
            guns: The form's guns.
            steps: The report.

        Raises:
            ApiError: The backend refused.
        """
        current = {gun["number"]: gun for gun in self._guns(station_id)}
        identity = self.client.get(f"{STATIONS}/{station_id}")["ocpp_identity"]
        for gun in guns:
            number = gun["number"]
            plug = {
                "standard": gun["standard"],
                "max_power_kw": gun["max_power_kw"],
                "max_voltage_v": gun["max_voltage_v"],
                "max_current_a": gun["max_current_a"],
            }
            existing = current.get(number)
            if existing is None:
                evse = self.client.post(
                    f"{STATIONS}/{station_id}/evses",
                    {
                        "ocpp_evse_id": number,
                        "emi3_evse_id": emi3_evse_id(str(identity), number),
                    },
                )
                self.client.post(
                    f"/api/v1/charging-evses/{evse['evse_id']}/connectors",
                    {"ocpp_connector_id": 1, **plug},
                )
                steps.done(f"gun {number}", f"added ({_describe(plug)})")
            elif existing.get("connector_id") is None:
                self.client.post(
                    f"/api/v1/charging-evses/{existing['evse_id']}/connectors",
                    {"ocpp_connector_id": 1, **plug},
                )
                steps.done(f"gun {number}", f"plug added ({_describe(plug)})")
            else:
                changed = {k: v for k, v in plug.items() if existing.get(k) != v}
                if changed:
                    self.client.patch(
                        f"/api/v1/charging-connectors/{existing['connector_id']}",
                        changed,
                    )
                    steps.done(f"gun {number}", f"plug updated ({_describe(plug)})")
                else:
                    steps.done(f"gun {number}", f"unchanged ({_describe(plug)})")
        for number, existing in sorted(current.items()):
            if number > len(guns):
                self.client.delete(f"/api/v1/charging-evses/{existing['evse_id']}")
                steps.done(f"gun {number}", "removed")

    def _apply_price(
        self,
        location_id: str,
        price: tuple[int, float] | None,
        steps: Steps,
        *,
        require_price: bool,
    ) -> None:
        """Make the location's price match the form.

        A location with its own tariff gets a new version when the price or
        VAT differs; a location without one gets a tariff created. With no
        price in the form the tariff is left alone, but a registration warns
        when nothing prices the location (no scan could start).

        Args:
            location_id: The location.
            price: ``(price per kWh, VAT %)`` or ``None``.
            steps: The report.
            require_price: Whether to report a location left without a price.

        Raises:
            ApiError: The backend refused.
        """
        tariff = self._location_tariff(location_id)
        version = (tariff or {}).get("current_version") or {}
        if price is None:
            if tariff is not None:
                steps.done(
                    "price",
                    f"unchanged: {version.get('price_per_kwh')} VND/kWh + "
                    f"{version.get('vat_rate_percent')} % VAT",
                )
            elif require_price:
                raise ApiError(
                    None,
                    "this location has no price: fill in the price per kWh and VAT, "
                    "or no scan can start here",
                )
            return
        price_per_kwh, vat = price
        if tariff is None:
            tariff = self.client.post(
                TARIFFS, {"name": "Charger monitor price", "location_id": location_id}
            )
        elif (
            float(version.get("price_per_kwh") or -1) == price_per_kwh
            and float(version.get("vat_rate_percent") or -1) == vat
        ):
            steps.done("price", f"unchanged: {price_per_kwh} VND/kWh + {vat:g} % VAT")
            return
        self.client.post(
            f"{TARIFFS}/{tariff['tariff_id']}/versions",
            {
                "price_per_kwh": price_per_kwh,
                "vat_rate_percent": vat,
                "change_reason": PRICE_CHANGE_REASON,
            },
        )
        steps.done("price", f"{price_per_kwh} VND/kWh + {vat:g} % VAT, from now")

    # --- edit and delete -----------------------------------------------------------------

    def edit(self, station_id: str, form: dict[str, Any]) -> dict[str, Any]:
        """Change a charger, its guns and its location's price.

        Args:
            station_id: The charger.
            form: The same fields as ``register`` (``location_id`` only; a new
                location is not created here), plus ``status`` (``ACTIVE`` /
                ``INACTIVE``) and ``status_reason`` (required by the backend
                when it changes).

        Returns:
            The step-by-step report.

        Raises:
            FormError: The form cannot be used (nothing was sent).
        """
        identity = _text(form, "ocpp_identity", required=True, max_length=255)
        serial = _text(form, "registered_serial_number", required=True, max_length=100)
        label = _text(form, "physical_reference", required=False, max_length=16)
        max_power = _number(form, "max_power_kw", required=False)
        status = form.get("status")
        reason = _text(form, "status_reason", required=False, max_length=200)
        location_id = _text(form, "location_id", required=True, max_length=36)
        if status not in (None, "ACTIVE", "INACTIVE"):
            raise FormError("status must be ACTIVE or INACTIVE")
        guns = parse_guns(form)
        price = parse_price(form)

        steps = Steps()
        try:
            station = self.client.get(f"{STATIONS}/{station_id}")
            wanted: dict[str, object | None] = {
                "location_id": location_id,
                "ocpp_identity": identity,
                "registered_serial_number": serial,
                "physical_reference": label,
                "max_power_kw": max_power,
                "status": status,
            }
            changed = {
                key: value
                for key, value in wanted.items()
                if value is not None and station.get(key) != value
            }
            if "status" in changed:
                changed["status_reason"] = reason or "Changed in the charger monitor"
            if changed:
                self.client.patch(f"{STATIONS}/{station_id}", changed)
                steps.done("charger", "updated: " + ", ".join(sorted(changed)))
            else:
                steps.done("charger", "unchanged")
        except ApiError as error:
            return steps.failed("charger", error)
        try:
            self._sync_guns(station_id, guns, steps)
        except ApiError as error:
            return steps.failed("guns", error)
        try:
            assert location_id is not None
            self._apply_price(location_id, price, steps, require_price=False)
        except ApiError as error:
            return steps.failed("price", error)
        return steps.report()

    def delete(self, station_id: str) -> dict[str, Any]:
        """Delete a charger and its guns (the backend's soft delete).

        Args:
            station_id: The charger.

        Returns:
            The step-by-step report.
        """
        steps = Steps()
        try:
            self.client.delete(f"{STATIONS}/{station_id}")
        except ApiError as error:
            return steps.failed("delete", error)
        steps.done("delete", "the charger and its guns are deleted")
        return steps.report()


def _describe(plug: dict[str, Any]) -> str:
    """Describe a plug in a few words.

    Args:
        plug: ``standard``, ``max_power_kw``, ``max_voltage_v``, ``max_current_a``.

    Returns:
        E.g. ``IEC_62196_T2_COMBO 240 kW / 1000 V / 300 A``.
    """
    return (
        f"{plug['standard']} {plug['max_power_kw']:g} kW / "
        f"{plug['max_voltage_v']} V / {plug['max_current_a']} A"
    )
