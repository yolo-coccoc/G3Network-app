"""Smoke tests for OCPP 1.6J MeterValues, measurement extraction and StopTransaction data."""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from ocpp.v16.enums import Action

import app.domains.charging_sessions.service as charging_service
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
from app.domains.charging_sessions.exceptions import (
    ChargingSessionNotFoundError,
    ChargingSessionStateError,
)
from app.domains.charging_stations.ocpp.ocpp16_charge_point import OCPP16ChargePoint
from app.domains.charging_stations.ocpp.ocpp16_measurements import (
    extract_v16_measurements,
    to_decimal,
)

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
STATION_ID, SESSION_ID = uuid4(), uuid4()
TS = "2026-09-24T10:00:00Z"


def _group(*sampled_values: dict[str, Any], timestamp: str = TS) -> dict[str, Any]:
    """Build one ``meterValue`` group as python-ocpp delivers it (snake_cased)."""
    return {"timestamp": timestamp, "sampled_value": list(sampled_values)}


# --- extraction: the energy register ------------------------------------------


def test_energy_register_without_unit_defaults_to_wh() -> None:
    """1.6 says the default unit is Wh, and no measurand means the energy register."""
    result = extract_v16_measurements([_group({"value": "1500"})])

    assert [(s.value_wh, s.sampled_at) for s in result.energy] == [(Decimal(1500), NOW)]
    assert result.measurements == ()


def test_kwh_energy_register_is_converted_to_wh_not_stored_as_wh() -> None:
    """Pins the 1.6 unit hazard: 12.5 kWh must become 12500 Wh, never 12.5."""
    result = extract_v16_measurements(
        [
            _group(
                {
                    "value": "12.5",
                    "unit": "kWh",
                    "measurand": "Energy.Active.Import.Register",
                }
            )
        ]
    )

    assert result.energy[0].value_wh == Decimal(12500)


@pytest.mark.parametrize("unit", ["KWH", "kwh", "kWh"])
def test_energy_unit_match_is_case_insensitive(unit: str) -> None:
    """Chargers are sloppy about case; the unit is matched case-insensitively."""
    result = extract_v16_measurements([_group({"value": "2", "unit": unit})])

    assert result.energy[0].value_wh == Decimal(2000)


def test_energy_register_keeps_its_reading_context() -> None:
    """The context (Transaction.Begin/End, Sample.Periodic…) must not be dropped."""
    result = extract_v16_measurements(
        [_group({"value": "10", "context": "Transaction.End"})]
    )

    assert result.energy[0].context == "Transaction.End"


@pytest.mark.parametrize(
    "sampled_value",
    [
        {"value": "1", "unit": "varh"},  # not an active-energy unit
        {"value": "1", "unit": "MWh"},
        {"value": "abc"},  # unreadable
        {"value": None},
        {"value": "1", "format": "SignedData"},  # cannot be interpreted
    ],
)
def test_energy_register_that_cannot_be_read_fails_loudly(
    sampled_value: dict[str, Any],
) -> None:
    """A register reading that cannot be interpreted is never silently dropped."""
    with pytest.raises(ValueError):
        extract_v16_measurements([_group(sampled_value)])


# --- extraction: everything else -------------------------------------------------


def test_soc_power_voltage_current_temperature_and_offered_are_all_stored() -> None:
    """The mandatory and should-have measurands go to measurements, with default units."""
    values = [
        {"measurand": "SoC", "value": "80"},
        {"measurand": "Power.Active.Import", "value": "120000"},
        {"measurand": "Voltage", "value": "650.5"},
        {"measurand": "Current.Import", "value": "184"},
        {"measurand": "Temperature", "value": "31"},
        {"measurand": "Power.Offered", "value": "120000"},
    ]

    result = extract_v16_measurements([_group(*values)])

    assert result.energy == ()
    assert [(m.measurand, m.value, m.unit) for m in result.measurements] == [
        ("SoC", Decimal(80), "Percent"),
        ("Power.Active.Import", Decimal(120000), "W"),
        ("Voltage", Decimal("650.5"), "V"),
        ("Current.Import", Decimal(184), "A"),
        ("Temperature", Decimal(31), "Celsius"),
        ("Power.Offered", Decimal(120000), "W"),
    ]


def test_explicit_context_phase_and_location_are_kept_and_the_unit_is_converted() -> (
    None
):
    """Context, phase and location are stored as sent; a known unit becomes the fixed one (CE-14)."""
    result = extract_v16_measurements(
        [
            _group(
                {
                    "measurand": "Temperature",
                    "value": "-4.5",
                    "unit": "Fahrenheit",
                    "context": "Sample.Periodic",
                    "phase": "L1-N",
                    "location": "Cable",
                }
            )
        ]
    )

    measurement = result.measurements[0]
    # -4.5 F is about -20.28 Celsius: negative values are legitimate.
    assert measurement.value == Decimal("-20.277778")
    assert (measurement.unit, measurement.context) == ("Celsius", "Sample.Periodic")
    assert (measurement.phase, measurement.measurement_location) == ("L1-N", "Cable")


def test_vendor_specific_measurand_is_stored_as_sent() -> None:
    """A vendor's own name (seen on the vendor platform) must not be rejected."""
    result = extract_v16_measurements(
        [
            _group(
                {"measurand": "Voltage.Demand", "value": "600.0"},
                {"measurand": "Current.Demand", "value": "200.0", "unit": "A"},
            )
        ]
    )

    assert [(m.measurand, m.value) for m in result.measurements] == [
        ("Voltage.Demand", Decimal("600.0")),
        ("Current.Demand", Decimal("200.0")),
    ]
    assert result.measurements[0].unit is None  # unknown measurand, no unit sent


def test_soc_zero_is_stored_not_treated_as_missing() -> None:
    """SoC 0 before the BMS handshake is stored as sent (consumers treat it as unknown)."""
    result = extract_v16_measurements([_group({"measurand": "SoC", "value": "0"})])

    assert result.measurements[0].value == Decimal(0)


@pytest.mark.parametrize(
    ("sampled_value", "reason"),
    [
        (
            {"measurand": "SoC", "value": "AABBCC", "format": "SignedData"},
            "signed_data",
        ),
        ({"measurand": "Voltage", "value": "n/a"}, "non_numeric"),
        ({"measurand": "Voltage"}, "non_numeric"),  # value missing
        ({"measurand": "Voltage", "value": "NaN"}, "non_finite"),
        ({"measurand": "X" * 61, "value": "1"}, "field_too_long"),
        ({"measurand": "Voltage", "value": "1", "unit": "U" * 21}, "field_too_long"),
        ({"measurand": "Voltage", "value": "1", "context": "c" * 31}, "field_too_long"),
    ],
)
def test_unstorable_non_energy_samples_are_skipped_and_counted_not_fatal(
    sampled_value: dict[str, Any], reason: str
) -> None:
    """One bad extra sample must not reject the whole message; it is counted instead."""
    result = extract_v16_measurements(
        [_group({"value": "1500"}, sampled_value, {"measurand": "SoC", "value": "50"})]
    )

    assert result.skipped == {reason: 1}
    assert result.skipped_count == 1
    assert len(result.energy) == 1  # the good samples around it survive
    assert [m.measurand for m in result.measurements] == ["SoC"]


def test_extraction_keeps_payload_order_across_groups() -> None:
    """Samples come out in the order the charger sent them."""
    result = extract_v16_measurements(
        [
            _group({"value": "1"}, timestamp="2026-09-24T10:00:00Z"),
            _group({"value": "2"}, timestamp="2026-09-24T10:00:30Z"),
        ]
    )

    assert [s.value_wh for s in result.energy] == [Decimal(1), Decimal(2)]


def test_timestamp_without_timezone_and_missing_keys_fail_loudly() -> None:
    """The strict timestamp rule (D11) holds, and a malformed group is an error."""
    with pytest.raises(ValueError, match="timezone"):
        extract_v16_measurements(
            [_group({"value": "1"}, timestamp="2026-09-24T10:00:00")]
        )
    with pytest.raises(KeyError):
        extract_v16_measurements([{"timestamp": TS}])
    with pytest.raises(KeyError):
        extract_v16_measurements([{"sampled_value": []}])


@pytest.mark.parametrize("value", ["abc", None, "NaN", "Infinity"])
def test_to_decimal_rejects_non_numbers_with_a_clear_error(value: object) -> None:
    """Required numeric fields fail as ValueError, not as a decimal library error."""
    with pytest.raises(ValueError, match="my_field"):
        to_decimal(value, "my_field")


@pytest.mark.parametrize("value", [1500, "1500", 1500.0, Decimal(1500)])
def test_to_decimal_accepts_ints_strings_floats_and_decimals(value: object) -> None:
    """A schema-less handler may receive any numeric shape; all are accepted."""
    assert to_decimal(value, "x") == Decimal(1500)


# --- handlers -----------------------------------------------------------------


class _CountingFactory:
    def __init__(self) -> None:
        self.entered = 0

    def begin(self) -> "_CountingFactory":
        return self

    async def __aenter__(self) -> object:
        self.entered += 1
        return object()

    async def __aexit__(self, *_: object) -> bool:
        return False


def _charge_point(factory: _CountingFactory | None = None) -> OCPP16ChargePoint:
    return OCPP16ChargePoint(
        "LSC",
        object(),  # type: ignore[arg-type]
        factory or _CountingFactory(),  # type: ignore[arg-type]
    )


def _patch(
    monkeypatch: pytest.MonkeyPatch, *, completed: bool = False, missing: bool = False
) -> list[tuple[str, dict[str, Any]]]:
    """Record every service call the handlers make, in order."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_station(db: object, ocpp_identity: str) -> Any:
        return STATION_ID

    async def fake_reference(db: object, **kwargs: Any) -> Any:
        calls.append(("resolve_session", kwargs))
        if missing:
            raise ChargingSessionNotFoundError("no such transaction")
        return SimpleNamespace(
            session_id=SESSION_ID,
            station_id=STATION_ID,
            evse_id=uuid4(),
            connector_id=uuid4(),
        )

    async def fake_meter(db: object, **kwargs: Any) -> None:
        if completed:
            raise ChargingSessionStateError("already completed")
        calls.append(("energy", kwargs))

    async def fake_measurements(db: object, **kwargs: Any) -> int:
        calls.append(("measurements", kwargs))
        return len(kwargs["samples"])

    async def fake_ingest(db: object, **kwargs: Any) -> None:
        calls.append(("event", kwargs))

    monkeypatch.setattr(
        ocpp_state_service, "resolve_station_id_by_identity", fake_station
    )
    monkeypatch.setattr(
        charging_service, "resolve_session_by_transaction", fake_reference
    )
    monkeypatch.setattr(charging_service, "ingest_meter_values", fake_meter)
    monkeypatch.setattr(charging_service, "ingest_measurements", fake_measurements)
    monkeypatch.setattr(charging_service, "complete_session", fake_ingest)
    return calls


def test_meter_values_and_stop_transaction_skip_schema_validation_but_others_do_not() -> (
    None
):
    """Only the two handlers that must tolerate vendor values opt out of schema validation."""
    route_map = _charge_point().route_map

    assert route_map[Action.meter_values]["_skip_schema_validation"] is True
    assert route_map[Action.stop_transaction]["_skip_schema_validation"] is True
    for strict in (
        Action.status_notification,
        Action.start_transaction,
        Action.boot_notification,
        Action.authorize,
    ):
        assert route_map[strict]["_skip_schema_validation"] is False


@pytest.mark.asyncio
async def test_meter_values_stores_energy_and_other_measurements_for_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Energy updates the session total; everything else becomes a measurement."""
    calls = _patch(monkeypatch)

    await _charge_point().on_meter_values(
        connector_id=1,
        transaction_id=7,
        meter_value=[
            _group(
                {"value": "12.5", "unit": "kWh"},
                {"measurand": "SoC", "value": "80"},
                {"measurand": "Voltage.Demand", "value": "600"},
            )
        ],
    )

    assert calls[0] == (
        "resolve_session",
        {"station_id": STATION_ID, "transaction_id": "7"},
    )
    kinds = [kind for kind, _ in calls]
    assert kinds == ["resolve_session", "energy", "measurements"]
    assert calls[1][1]["session_id"] == SESSION_ID
    assert calls[1][1]["sample"].value_wh == Decimal(12500)
    assert [m.measurand for m in calls[2][1]["samples"]] == ["SoC", "Voltage.Demand"]


@pytest.mark.asyncio
async def test_meter_values_on_a_new_connection_finds_its_session_in_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a reconnect the mapping is not lost: the session is looked up by transactionId."""
    calls = _patch(monkeypatch)
    first_connection = _charge_point()
    after_reconnect = _charge_point()  # a brand-new adapter, as after a reconnect

    await after_reconnect.on_meter_values(
        connector_id=1, transaction_id=7, meter_value=[_group({"value": "100"})]
    )

    assert [kind for kind, _ in calls] == ["resolve_session", "energy"]
    for adapter in (first_connection, after_reconnect):
        assert not hasattr(adapter, "_session_by_evse")  # no per-connection memory


@pytest.mark.asyncio
async def test_meter_values_without_a_transaction_id_stores_nothing_and_opens_no_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clock-aligned/outside-transaction readings are left to the raw log only."""
    calls = _patch(monkeypatch)
    factory = _CountingFactory()

    await _charge_point(factory).on_meter_values(
        connector_id=1, meter_value=[_group({"value": "100"})]
    )

    assert calls == []
    assert factory.entered == 0


@pytest.mark.asyncio
async def test_meter_values_for_a_completed_session_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COMPLETED stays terminal: a late MeterValues is an error, not a silent rewrite."""
    _patch(monkeypatch, completed=True)

    with pytest.raises(ChargingSessionStateError):
        await _charge_point().on_meter_values(
            connector_id=1, transaction_id=7, meter_value=[_group({"value": "100"})]
        )


@pytest.mark.asyncio
async def test_meter_values_for_an_unknown_transaction_propagates_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown transaction is loud (a CALLERROR), not silently ACKed."""
    _patch(monkeypatch, missing=True)

    with pytest.raises(ChargingSessionNotFoundError):
        await _charge_point().on_meter_values(
            connector_id=1, transaction_id=7, meter_value=[_group({"value": "100"})]
        )


@pytest.mark.asyncio
async def test_meter_values_logs_a_warning_with_the_count_of_skipped_samples(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Skipped samples are never silent: the warning carries how many and why."""
    calls = _patch(monkeypatch)

    with caplog.at_level(logging.WARNING):
        await _charge_point().on_meter_values(
            connector_id=1,
            transaction_id=7,
            meter_value=[
                _group(
                    {"value": "100"},
                    {"measurand": "Voltage", "value": "n/a"},
                    {"measurand": "SoC", "value": "AB", "format": "SignedData"},
                )
            ],
        )

    warning = next(
        r for r in caplog.records if "Skipped meter samples" in r.getMessage()
    )
    assert warning.skipped_samples == 2  # type: ignore[attr-defined]
    assert warning.skipped_by_reason == {  # type: ignore[attr-defined]
        "non_numeric": 1,
        "signed_data": 1,
    }
    assert [kind for kind, _ in calls] == [
        "resolve_session",
        "energy",
    ]  # the good one stored


@pytest.mark.asyncio
async def test_meter_values_with_an_unreadable_energy_register_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad energy reading fails the whole message before any database work."""
    calls = _patch(monkeypatch)
    factory = _CountingFactory()

    with pytest.raises(ValueError):
        await _charge_point(factory).on_meter_values(
            connector_id=1,
            transaction_id=7,
            meter_value=[_group({"value": "1", "unit": "MWh"})],
        )

    assert calls == []
    assert factory.entered == 0


# --- StopTransaction transactionData and vendor values -------------------------------------


@pytest.mark.asyncio
async def test_stop_transaction_stores_transaction_data_before_ending_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Samples must land while the session is still active, then Ended closes it."""
    calls = _patch(monkeypatch)

    await _charge_point().on_stop_transaction(
        meter_stop=1500,
        timestamp="2026-09-24T10:30:00Z",
        transaction_id=7,
        reason="EVDisconnected",
        transaction_data=[
            _group(
                {"value": "1450", "context": "Transaction.End"},
                {"measurand": "SoC", "value": "100"},
                timestamp="2026-09-24T10:29:59Z",
            )
        ],
    )

    assert [kind for kind, _ in calls] == [
        "resolve_session",
        "energy",
        "measurements",
        "event",
    ]
    assert calls[1][1]["sample"].context == "Transaction.End"
    assert calls[3][1]["meter_stop_wh"] == Decimal(1500)


@pytest.mark.asyncio
async def test_stop_transaction_accepts_a_vendor_specific_stop_reason_and_measurand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-standard reason or measurand must not leave the session open forever."""
    calls = _patch(monkeypatch)

    await _charge_point().on_stop_transaction(
        meter_stop=1500,
        timestamp="2026-09-24T10:30:00Z",
        transaction_id=7,
        reason="BMS charger abort (CST frame received)",  # 39 characters, not a 1.6 reason
        transaction_data=[_group({"measurand": "Vendor.Thing", "value": "1"})],
    )

    event = calls[-1][1]
    assert event["stop_reason"] == "BMS charger abort (CST frame r"  # truncated to 30
    assert len(event["stop_reason"]) == 30
    assert calls[-2][1]["samples"][0].measurand == "Vendor.Thing"


@pytest.mark.asyncio
async def test_stop_transaction_with_an_unreadable_meter_stop_opens_no_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The closing reading is required and validated here, since the schema is off."""
    calls = _patch(monkeypatch)
    factory = _CountingFactory()

    with pytest.raises(ValueError, match="meter_stop"):
        await _charge_point(factory).on_stop_transaction(
            meter_stop="lots",
            timestamp="2026-09-24T10:30:00Z",
            transaction_id=7,
        )

    assert calls == []
    assert factory.entered == 0


@pytest.mark.asyncio
async def test_stop_transaction_accepts_the_transaction_id_as_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the schema off, a charger sending "7" instead of 7 still finds the session."""
    calls = _patch(monkeypatch)

    await _charge_point().on_stop_transaction(
        meter_stop=1,
        timestamp="2026-09-24T10:30:00Z",
        transaction_id="7",
    )

    assert calls[0][1]["transaction_id"] == "7"
