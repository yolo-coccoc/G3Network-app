"""Turn OCPP 1.6J ``MeterValues`` payloads into storable measurements.

This is the 1.6J counterpart of the 2.0.1 energy normalizer in
``ocpp201_charge_point.py`` and must **not** share code with it: the two protocols shape
a ``SampledValue`` differently (1.6J has a flat ``unit`` string, 2.0.1 a nested
``unitOfMeasure`` with a multiplier), and reusing the 2.0.1 function would
silently store a ``kWh`` reading as Wh (1000x too small).

Rules, in one place:

* The **energy register** (``Energy.Active.Import.Register``, also the default
  when a sample names no measurand) is the measurand the energy series and
  the live energy read. Its unit must be ``Wh`` or ``kWh`` (default ``Wh``)
  and is converted to Wh; anything else, an unreadable value, or a signed-data
  value **raises** ``ValueError`` - a register reading that cannot be
  interpreted must fail loudly instead of leaving the energy stale.
* **Every other known measurand** is converted to its fixed unit (CE-14, see
  ``measurement_units.py``); a vendor-specific name is stored as sent, so a
  charger's extra data is never rejected. A missing context or location is
  filled with the OCPP default (``Sample.Periodic``, ``Outlet``). A sample that
  cannot be stored
  (signed data, a non-numeric or non-finite value, or a field longer than its
  column) is **skipped and counted**, never silently dropped and never failing
  the whole message; the caller logs the count.
* Timestamps must carry a timezone (decision D11).

Scope: pure functions over the plain-dict payload ``python-ocpp`` delivers; no
I/O and no database access.
"""

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Final

from app.domains.charging_sessions.types import (
    DEFAULT_MEASUREMENT_CONTEXT,
    DEFAULT_MEASUREMENT_LOCATION,
    ENERGY_ACTIVE_IMPORT_REGISTER,
    MeasurementInput,
    MeterSampleInput,
)
from app.domains.charging_stations.ocpp.measurement_units import (
    KNOWN_MEASURAND_UNITS,
    convert_measurement,
)
from app.domains.charging_stations.ocpp.parsing import (
    OcppPayload,
    parse_ocpp_timestamp,
)

# Keys are lowercased for a case-insensitive match.
_ENERGY_UNIT_FACTORS_WH: Final[dict[str, Decimal]] = {
    "wh": Decimal(1),
    "kwh": Decimal(1000),
}
# Column limits of charging_session_measurements.
_MAX_MEASURAND, _MAX_UNIT, _MAX_CONTEXT, _MAX_PHASE, _MAX_LOCATION = 60, 20, 30, 10, 20


@dataclass(frozen=True, slots=True)
class V16Extraction:
    """The storable content of one or more ``meterValue`` groups.

    Attributes:
        energy: Energy-register samples (canonical Wh), in payload order.
        measurements: Every other measurement, in payload order.
        skipped: How many samples could not be stored, by reason
            (``signed_data``, ``non_numeric``, ``non_finite``,
            ``field_too_long``).
    """

    energy: tuple[MeterSampleInput, ...] = ()
    measurements: tuple[MeasurementInput, ...] = ()
    skipped: dict[str, int] = field(default_factory=dict)

    @property
    def skipped_count(self) -> int:
        """Total number of samples that could not be stored."""
        return sum(self.skipped.values())


def to_decimal(value: object, field_name: str) -> Decimal:
    """Convert a payload number to ``Decimal`` or fail with a clear error.

    Args:
        value: An ``int``, ``float`` or numeric string from the payload.
        field_name: The field name used in the error message.

    Returns:
        The value as a finite ``Decimal``.

    Raises:
        ValueError: If the value is missing, not numeric, or not finite.
    """
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{field_name} is not a number: {value!r}") from error
    if not parsed.is_finite():
        raise ValueError(f"{field_name} is not finite: {value!r}")
    return parsed


def _energy_value_wh(sampled_value: OcppPayload) -> Decimal:
    """Read an energy-register sample as canonical Wh.

    Args:
        sampled_value: One ``sampledValue`` object (plain dict, snake_cased).

    Returns:
        The reading in Wh.

    Raises:
        ValueError: If the sample is signed data, its unit is neither Wh nor
            kWh, or its value is unreadable.
    """
    if (sampled_value.get("format") or "Raw") != "Raw":
        raise ValueError("Signed-data energy register readings are not supported")
    unit = str(sampled_value.get("unit") or "Wh")
    factor = _ENERGY_UNIT_FACTORS_WH.get(unit.lower())
    if factor is None:
        raise ValueError(f"Unrecognized energy unit '{unit}' for the energy register")
    return to_decimal(sampled_value.get("value"), "energy value") * factor


def extract_v16_measurements(meter_values: list[OcppPayload]) -> V16Extraction:
    """Split ``meterValue`` groups into energy samples and other measurements.

    Args:
        meter_values: The ``meterValue`` list of a ``MeterValues`` message, or
            the ``transactionData`` list of a ``StopTransaction``; plain dicts
            with snake_cased keys (``timestamp``, ``sampled_value``).

    Returns:
        The energy samples, the other measurements, and a count of skipped
        samples by reason.

    Raises:
        KeyError: If a group lacks ``timestamp`` or ``sampled_value``.
        ValueError: If a timestamp lacks a timezone, or an energy-register
            sample has an uninterpretable unit or value.
    """
    energy: list[MeterSampleInput] = []
    measurements: list[MeasurementInput] = []
    skipped: Counter[str] = Counter()
    for group in meter_values:
        sampled_at = parse_ocpp_timestamp(group["timestamp"])
        for sampled_value in group["sampled_value"]:
            measurand = sampled_value.get("measurand") or ENERGY_ACTIVE_IMPORT_REGISTER
            context = sampled_value.get("context") or DEFAULT_MEASUREMENT_CONTEXT
            location = sampled_value.get("location") or DEFAULT_MEASUREMENT_LOCATION
            if measurand == ENERGY_ACTIVE_IMPORT_REGISTER:
                energy.append(
                    MeterSampleInput(
                        sampled_at=sampled_at,
                        value_wh=_energy_value_wh(sampled_value),
                        context=context,
                        measurement_location=location,
                    )
                )
                continue
            if (sampled_value.get("format") or "Raw") != "Raw":
                skipped["signed_data"] += 1
                continue
            try:
                value = Decimal(str(sampled_value.get("value")))
            except InvalidOperation:
                skipped["non_numeric"] += 1
                continue
            if not value.is_finite():
                skipped["non_finite"] += 1
                continue
            value, unit = convert_measurement(
                measurand,
                value,
                sampled_value.get("unit") or (KNOWN_MEASURAND_UNITS.get(measurand)),
            )
            phase = sampled_value.get("phase")
            if (
                len(measurand) > _MAX_MEASURAND
                or (unit is not None and len(unit) > _MAX_UNIT)
                or len(context) > _MAX_CONTEXT
                or (phase is not None and len(phase) > _MAX_PHASE)
                or len(location) > _MAX_LOCATION
            ):
                skipped["field_too_long"] += 1
                continue
            measurements.append(
                MeasurementInput(
                    sampled_at=sampled_at,
                    measurand=measurand,
                    value=value,
                    unit=unit,
                    context=context,
                    phase=phase,
                    measurement_location=location,
                )
            )
    return V16Extraction(
        energy=tuple(energy),
        measurements=tuple(measurements),
        skipped=dict(skipped),
    )
