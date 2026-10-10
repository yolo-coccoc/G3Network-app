"""Smoke tests for the fixed-unit conversion of OCPP measurements (CE-14)."""

from decimal import Decimal

import pytest

from app.domains.charging_stations.ocpp.measurement_units import convert_measurement


@pytest.mark.parametrize(
    ("measurand", "value", "unit", "expected"),
    [
        ("Power.Active.Import", "120", "kW", (Decimal(120000), "W")),
        ("Power.Active.Import", "90000", None, (Decimal(90000), "W")),
        ("Energy.Active.Import.Register", "1.5", "kWh", (Decimal(1500), "Wh")),
        ("Energy.Reactive.Import.Register", "2", "kvarh", (Decimal(2000), "varh")),
        ("Temperature", "300", "K", (Decimal("26.85"), "Celsius")),
        ("Temperature", "212", "Fahrenheit", (Decimal(100), "Celsius")),
        ("SoC", "80", None, (Decimal(80), "Percent")),
        ("Voltage", "650.5", "V", (Decimal("650.5"), "V")),
    ],
)
def test_known_measurands_are_stored_in_their_fixed_unit(
    measurand: str, value: str, unit: str | None, expected: tuple[Decimal, str]
) -> None:
    """Kilo-units are multiplied by 1000, Fahrenheit and Kelvin become Celsius."""
    assert convert_measurement(measurand, Decimal(value), unit) == expected


def test_vendor_measurands_and_foreign_units_are_stored_as_sent() -> None:
    """A vendor name, or a known one with a unit outside its family, is untouched."""
    assert convert_measurement("Vendor.Thing", Decimal(7), "kW") == (Decimal(7), "kW")
    assert convert_measurement("Voltage", Decimal(7), "kW") == (Decimal(7), "kW")
    assert convert_measurement("Vendor.Thing", Decimal(7), None) == (Decimal(7), None)
