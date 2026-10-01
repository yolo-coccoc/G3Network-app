"""Smoke tests for OCPP 2.0.1 payload parsing and meter-sample normalization."""

from decimal import Decimal

import pytest

from app.domains.charging_stations.ocpp.ocpp_server import (
    extract_meter_samples,
    parse_ocpp_evse_reference,
    parse_ocpp_transaction_id,
)


def test_extract_meter_samples_reads_raw_dict_payload() -> None:
    """extract_meter_samples parses the plain-dict shape python-ocpp actually
    delivers (Step 0's regression test) - not the ocpp.v201 dataclasses.
    """
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [{"value": 1.25}],
    }

    samples = extract_meter_samples([meter_value])

    assert samples[0].value_wh == Decimal("1.25")


def test_ocpp_kwh_sample_is_normalized_to_wh() -> None:
    """A kWh-unit energy-register sample is converted to Wh (F-B2).

    Renamed from ``test_ocpp_meter_value_is_kept_without_unit_conversion``,
    which used to assert the bug this fix removes (a kWh reading kept
    as-is, off by a factor of 1000). Renaming rather than editing in
    place makes the behavior change impossible to miss in review.
    """
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [
            {
                "value": 1.25,
                "measurand": "Energy.Active.Import.Register",
                "unit_of_measure": {"unit": "kWh"},
            }
        ],
    }

    samples = extract_meter_samples([meter_value])

    assert samples[0].value_wh == Decimal("1250")


def test_ocpp_sample_without_unit_defaults_to_wh() -> None:
    """A sample with no unit_of_measure at all is treated as Wh (F-B2)."""
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [{"value": 500}],
    }

    samples = extract_meter_samples([meter_value])

    assert samples[0].value_wh == Decimal("500")


def test_ocpp_sample_without_multiplier_defaults_to_zero() -> None:
    """A sample with a unit but no multiplier key defaults multiplier to 0 (F-B2).

    This is the exact shape the simulator sends: ``{"unit": "Wh"}`` with
    no ``multiplier`` key.
    """
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [{"value": 500, "unit_of_measure": {"unit": "Wh"}}],
    }

    samples = extract_meter_samples([meter_value])

    assert samples[0].value_wh == Decimal("500")


def test_ocpp_sample_multiplier_scales_value() -> None:
    """A non-zero multiplier scales the value by 10**multiplier (F-B2)."""
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [
            {"value": 1.5, "unit_of_measure": {"unit": "Wh", "multiplier": 3}}
        ],
    }

    samples = extract_meter_samples([meter_value])

    assert samples[0].value_wh == Decimal("1500")


def test_ocpp_sample_without_measurand_defaults_to_energy_register() -> None:
    """An absent measurand is treated as the energy import register (F-B2)."""
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [{"value": 1000}],
    }

    samples = extract_meter_samples([meter_value])

    assert len(samples) == 1
    assert samples[0].value_wh == Decimal("1000")


def test_ocpp_non_energy_measurand_is_skipped() -> None:
    """A non-energy-register measurand (e.g. power) is skipped, not an error (F-B2)."""
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [{"value": 7.5, "measurand": "Power.Active.Import"}],
    }

    samples = extract_meter_samples([meter_value])

    assert samples == []


def test_ocpp_unknown_unit_on_energy_register_is_rejected() -> None:
    """An unrecognized unit on an energy-register sample raises, not silently skips (F-B2)."""
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [
            {
                "value": 1.0,
                "measurand": "Energy.Active.Import.Register",
                "unit_of_measure": {"unit": "J"},
            }
        ],
    }

    with pytest.raises(ValueError):
        extract_meter_samples([meter_value])


def test_ocpp_mixed_measurands_keep_only_energy_register() -> None:
    """A message with energy + power + SoC keeps only the energy register (F-B2)."""
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [
            {"value": 1000, "measurand": "Energy.Active.Import.Register"},
            {"value": 7.5, "measurand": "Power.Active.Import"},
            {"value": 80, "measurand": "SoC"},
        ],
    }

    samples = extract_meter_samples([meter_value])

    assert len(samples) == 1
    assert samples[0].value_wh == Decimal("1000")


def test_parse_ocpp_transaction_id_reads_dict_payload() -> None:
    """parse_ocpp_transaction_id reads the snake_cased dict python-ocpp delivers."""
    assert parse_ocpp_transaction_id({"transaction_id": "TX-001"}) == "TX-001"
    with pytest.raises(ValueError):
        parse_ocpp_transaction_id({})


def test_parse_ocpp_evse_reference_reads_dict_payload() -> None:
    """parse_ocpp_evse_reference reads the snake_cased dict python-ocpp delivers."""
    assert parse_ocpp_evse_reference({"id": 1, "connector_id": 2}) == (1, 2)
    with pytest.raises(ValueError):
        parse_ocpp_evse_reference(None)
    with pytest.raises(ValueError):
        parse_ocpp_evse_reference({"id": 1})
