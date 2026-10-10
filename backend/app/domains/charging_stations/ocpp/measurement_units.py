"""Fixed-unit conversion of OCPP measurements, shared by both OCPP adapters.

The gateway writes every known measurand in one fixed unit (CE-14), so readers
of ``charging_session_measurements`` never convert: energy in Wh (reactive in
varh), power in W (reactive var, apparent VA), current in A, voltage in V,
temperature in Celsius, state of charge and other percentages in Percent.
Kilo-units are multiplied by 1000; Fahrenheit and Kelvin become Celsius. A
vendor-specific measurand, or a known one whose unit does not belong to its
family, is stored as sent.

The module only converts a number and a unit label; each adapter extracts them
in its own protocol's shape (1.6J has a flat ``unit``, 2.0.1 a nested
``unitOfMeasure`` with a multiplier) and then calls ``convert_measurement``.
Pure functions, no I/O.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Final

# Fixed unit of each known measurand (OCPP 1.6 and 2.0.1 names), which is also
# the OCPP default unit used when the sample names none.
KNOWN_MEASURAND_UNITS: Final[dict[str, str]] = {
    "Energy.Active.Export.Register": "Wh",
    "Energy.Active.Import.Register": "Wh",
    "Energy.Reactive.Export.Register": "varh",
    "Energy.Reactive.Import.Register": "varh",
    "Energy.Active.Export.Interval": "Wh",
    "Energy.Active.Import.Interval": "Wh",
    "Energy.Reactive.Export.Interval": "varh",
    "Energy.Reactive.Import.Interval": "varh",
    "Power.Active.Export": "W",
    "Power.Active.Import": "W",
    "Power.Offered": "W",
    "Power.Reactive.Export": "var",
    "Power.Reactive.Import": "var",
    "Current.Export": "A",
    "Current.Import": "A",
    "Current.Offered": "A",
    "Voltage": "V",
    "Temperature": "Celsius",
    "SoC": "Percent",
}

_THOUSAND: Final[Decimal] = Decimal(1000)


def _scaled(factor: Decimal) -> Callable[[Decimal], Decimal]:
    """Build a converter that multiplies by a fixed factor.

    Args:
        factor: The multiplier.

    Returns:
        A function applying it.
    """
    return lambda value: value * factor


def _fahrenheit_to_celsius(value: Decimal) -> Decimal:
    """Convert degrees Fahrenheit to Celsius.

    Args:
        value: The temperature in Fahrenheit.

    Returns:
        The temperature in Celsius.
    """
    return (value - Decimal(32)) * Decimal(5) / Decimal(9)


def _kelvin_to_celsius(value: Decimal) -> Decimal:
    """Convert Kelvin to degrees Celsius.

    Args:
        value: The temperature in Kelvin.

    Returns:
        The temperature in Celsius.
    """
    return value - Decimal("273.15")


# Lowercased unit label (OCPP spelling) -> (fixed unit it converts to,
# converter). A family match against the measurand's fixed unit is checked by
# ``convert_measurement``.
_CONVERSIONS: Final[dict[str, tuple[str, Callable[[Decimal], Decimal]]]] = {
    "wh": ("Wh", _scaled(Decimal(1))),
    "kwh": ("Wh", _scaled(_THOUSAND)),
    "varh": ("varh", _scaled(Decimal(1))),
    "kvarh": ("varh", _scaled(_THOUSAND)),
    "w": ("W", _scaled(Decimal(1))),
    "kw": ("W", _scaled(_THOUSAND)),
    "var": ("var", _scaled(Decimal(1))),
    "kvar": ("var", _scaled(_THOUSAND)),
    "a": ("A", _scaled(Decimal(1))),
    "v": ("V", _scaled(Decimal(1))),
    "celsius": ("Celsius", _scaled(Decimal(1))),
    "fahrenheit": ("Celsius", _fahrenheit_to_celsius),
    "k": ("Celsius", _kelvin_to_celsius),
    "percent": ("Percent", _scaled(Decimal(1))),
}
_SIX_PLACES: Final[Decimal] = Decimal("0.000001")


def convert_measurement(
    measurand: str, value: Decimal, unit: str | None
) -> tuple[Decimal, str | None]:
    """Convert a reading of a known measurand to its fixed unit (CE-14).

    Args:
        measurand: The OCPP measurand name, as sent.
        value: The reading in ``unit``, already scaled by any multiplier.
        unit: The unit the charger sent, or ``None`` to mean the OCPP default
            unit of the measurand.

    Returns:
        ``(value, unit)`` in the measurand's fixed unit (rounded to the six
        places the column stores), or the arguments unchanged when the
        measurand is not a known one or the unit does not belong to its
        family (stored as sent).
    """
    fixed_unit = KNOWN_MEASURAND_UNITS.get(measurand)
    if fixed_unit is None:
        return value, unit
    conversion = _CONVERSIONS.get((unit or fixed_unit).lower())
    if conversion is None or conversion[0] != fixed_unit:
        return value, unit
    return conversion[1](value).quantize(_SIX_PLACES), fixed_unit
