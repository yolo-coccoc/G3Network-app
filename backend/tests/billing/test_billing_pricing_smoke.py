"""Smoke tests for the pure billing rules: periods, rounding, VietQR (WP9)."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

import app.domains.billing.pricing as pricing
import app.domains.billing.topup_service as topup_service
import app.domains.billing.vietqr as vietqr
from app.domains.billing.exceptions import (
    PaymentInputError,
    TariffInputError,
    WebhookAuthenticationError,
)
from app.libs.common.config import settings

NORMAL_PRICE = Decimal(4500)
# Night price Monday to Saturday 22:00-04:00 Vietnam time (the DBML example).
NIGHT_PERIODS = [
    {
        "days": ["MON", "TUE", "WED", "THU", "FRI", "SAT"],
        "from": "22:00",
        "to": "04:00",
        "price_per_kwh": 3200,
    }
]


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Build a UTC moment."""
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# --- Time-of-use periods (BL-09) --------------------------------------------


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        # 2026-10-12 is a Monday. 23:00 Vietnam time = 16:00 UTC: night price.
        (_utc(2026, 10, 12, 16), 3200),
        # 10:00 Vietnam time on Monday: normal price.
        (_utc(2026, 10, 12, 3), 4500),
        # Tuesday 02:00 Vietnam time belongs to the window that started Monday.
        (_utc(2026, 10, 12, 19), 3200),
        # Sunday 02:00 belongs to the window that started on Saturday.
        (_utc(2026, 10, 17, 19), 3200),
        # Sunday 23:00 is not covered: the days name the day a window starts on.
        (_utc(2026, 10, 18, 16), 4500),
        # 04:00 sharp is outside the window (the end is exclusive).
        (_utc(2026, 10, 12, 21), 4500),
    ],
)
def test_price_at_follows_the_time_of_use_period_in_vietnam_time(
    moment: datetime, expected: int
) -> None:
    """The period covering the Vietnam local time replaces the normal price."""
    assert pricing.price_at(NORMAL_PRICE, NIGHT_PERIODS, moment) == Decimal(expected)


def test_price_at_without_periods_is_the_normal_price() -> None:
    """No periods means one price all day; a naive moment is refused."""
    assert pricing.price_at(NORMAL_PRICE, None, _utc(2026, 10, 12, 16)) == NORMAL_PRICE
    with pytest.raises(ValueError):
        pricing.price_at(NORMAL_PRICE, None, datetime(2026, 10, 12, 16))


@pytest.mark.parametrize(
    "periods",
    [
        # Two windows cover Monday 23:00.
        [
            {"days": ["MON"], "from": "20:00", "to": "23:30", "price_per_kwh": 1},
            {"days": ["MON"], "from": "23:00", "to": "24:00", "price_per_kwh": 2},
        ],
        # A window crossing midnight overlaps the next day's morning window.
        [
            {"days": ["MON"], "from": "22:00", "to": "04:00", "price_per_kwh": 1},
            {"days": ["TUE"], "from": "03:00", "to": "05:00", "price_per_kwh": 2},
        ],
        [{"days": ["MON"], "from": "10:00", "to": "10:00", "price_per_kwh": 1}],
        [{"days": [], "from": "10:00", "to": "11:00", "price_per_kwh": 1}],
        [{"days": ["XXX"], "from": "10:00", "to": "11:00", "price_per_kwh": 1}],
        [{"days": ["MON"], "from": "25:00", "to": "26:00", "price_per_kwh": 1}],
        [{"days": ["MON"], "from": "10:00", "to": "11:00", "price_per_kwh": 12.5}],
        [{"days": ["MON"], "from": "10:00", "to": "11:00", "price_per_kwh": -1}],
    ],
)
def test_malformed_or_overlapping_periods_are_refused(
    periods: list[dict[str, object]],
) -> None:
    """Bad times, days, prices and overlaps fail before a version is stored."""
    with pytest.raises(TariffInputError):
        pricing.normalize_time_periods(periods)


def test_normalized_periods_keep_whole_dong_prices_and_sorted_days() -> None:
    """The stored JSON is canonical."""
    normalized = pricing.normalize_time_periods(
        [
            {
                "days": ["SAT", "MON"],
                "from": "22:00",
                "to": "04:00",
                "price_per_kwh": 3200,
            }
        ]
    )

    assert normalized == [
        {"days": ["MON", "SAT"], "from": "22:00", "to": "04:00", "price_per_kwh": 3200}
    ]


# --- Rounding (BL-10) -------------------------------------------------------


def test_bill_amounts_match_the_design_example() -> None:
    """192,520 Wh at 4,500 gives 866,340 before 10 % VAT of 86,634."""
    amount, vat = pricing.calculate_bill_amounts(
        Decimal("192520"), Decimal(4500), Decimal(10)
    )

    assert (amount, vat) == (Decimal(866340), Decimal(86634))


@pytest.mark.parametrize(
    ("energy_wh", "price", "vat_rate", "expected"),
    [
        # 1.0005 kWh x 3,000 = 3001.5 -> 3002 (half up); VAT 8 % of 3002 = 240.16 -> 240
        ("1000.5", 3000, "8", (3002, 240)),
        # 0.0001 kWh rounds the amount to zero
        ("0.1", 4500, "10", (0, 0)),
        # VAT half up: 1,250 x 10 % = 125 exactly; 1,255 x 10 % = 125.5 -> 126
        ("279", 4500, "10", (1256, 126)),
    ],
)
def test_bill_amounts_round_half_up_to_whole_dong(
    energy_wh: str, price: int, vat_rate: str, expected: tuple[int, int]
) -> None:
    """Both stored figures are whole dong, rounded half up."""
    amount, vat = pricing.calculate_bill_amounts(
        Decimal(energy_wh), Decimal(price), Decimal(vat_rate)
    )

    assert (amount, vat) == (Decimal(expected[0]), Decimal(expected[1]))
    assert amount == amount.to_integral_value()
    assert vat == vat.to_integral_value()


# --- Billable energy (CE-12) ------------------------------------------------


def test_the_stop_reading_is_the_billing_figure() -> None:
    """Stop minus start, source METER_STOP, no hold when the measurement agrees."""
    decision = pricing.decide_billable_energy(
        meter_start_wh=Decimal(1000),
        meter_stop_wh=Decimal(51_000),
        last_measured_wh=Decimal(50_500),
        tolerance_percent=Decimal(5),
    )

    assert decision == pricing.BillableEnergy(Decimal(50_000), "METER_STOP", None)


def test_the_last_measurement_stands_in_for_a_missing_stop_reading() -> None:
    """Without a closing reading the newest measurement is used (LAST_MEASUREMENT)."""
    decision = pricing.decide_billable_energy(
        meter_start_wh=Decimal(1000),
        meter_stop_wh=None,
        last_measured_wh=Decimal(11_000),
        tolerance_percent=Decimal(5),
    )

    assert decision == pricing.BillableEnergy(Decimal(10_000), "LAST_MEASUREMENT", None)


@pytest.mark.parametrize(
    ("start", "stop", "measured"),
    [
        (None, 5000, 4000),  # no start reading
        (1000, None, None),  # nothing to close with
        (5000, 4000, None),  # closing below start
    ],
)
def test_unbillable_readings_are_held_without_an_energy(
    start: int | None, stop: int | None, measured: int | None
) -> None:
    """A missing or impossible reading puts the bill on hold with a reason."""
    decision = pricing.decide_billable_energy(
        meter_start_wh=None if start is None else Decimal(start),
        meter_stop_wh=None if stop is None else Decimal(stop),
        last_measured_wh=None if measured is None else Decimal(measured),
        tolerance_percent=Decimal(5),
    )

    assert decision.energy_wh is None
    assert decision.hold_reason


def test_a_stop_reading_far_from_the_last_measurement_is_held_with_its_figure() -> None:
    """A gap beyond the tolerance (and 1 kWh) keeps the computed energy for review."""
    decision = pricing.decide_billable_energy(
        meter_start_wh=Decimal(0),
        meter_stop_wh=Decimal(60_000),
        last_measured_wh=Decimal(40_000),
        tolerance_percent=Decimal(5),
    )

    assert decision.energy_wh == Decimal(60_000)
    assert decision.energy_source == "METER_STOP"
    assert decision.hold_reason is not None


# --- VietQR (BL-15) ---------------------------------------------------------


def test_crc16_ccitt_false_known_vector() -> None:
    """The standard check value of CRC-16/CCITT-FALSE."""
    assert vietqr.calculate_crc16("123456789") == 0x29B1


def test_vietqr_payload_carries_the_fields_and_a_valid_crc() -> None:
    """The encoder writes the EMVCo fields in order and a CRC that verifies."""
    payload = vietqr.build_vietqr_payload(
        bank_bin="970436",
        account_number="0011004123456",
        amount=500_000,
        transfer_content="G3NAP7K2Q9",
    )

    assert payload.startswith("000201010212")
    fields = vietqr.parse_tlv_fields(payload)
    assert list(fields) == ["00", "01", "38", "53", "54", "58", "62", "63"]
    assert fields["53"] == "704"
    assert fields["54"] == "500000"
    assert fields["58"] == "VN"
    merchant = vietqr.parse_tlv_fields(fields["38"])
    assert merchant["00"] == "A000000727"
    assert merchant["02"] == "QRIBFTTA"
    assert vietqr.parse_tlv_fields(merchant["01"]) == {
        "00": "970436",
        "01": "0011004123456",
    }
    assert vietqr.parse_tlv_fields(fields["62"]) == {"08": "G3NAP7K2Q9"}
    assert fields["63"] == payload[-4:]
    assert vietqr.is_crc_valid(payload)
    assert not vietqr.is_crc_valid(payload.replace("500000", "500001"))


@pytest.mark.parametrize(
    "arguments",
    [
        {"amount": 0},
        {"transfer_content": "bad;content"},
        {"transfer_content": "X" * 26},
        {"bank_bin": "97043"},
        {"account_number": "00 11"},
    ],
)
def test_vietqr_refuses_values_it_cannot_encode(arguments: dict[str, object]) -> None:
    """Bad amount, content, BIN or account number are input errors."""
    values: dict[str, object] = {
        "bank_bin": "970436",
        "account_number": "0011004123456",
        "amount": 10_000,
        "transfer_content": "G3NAP7K2Q9",
    }
    values.update(arguments)

    with pytest.raises(PaymentInputError):
        vietqr.build_vietqr_payload(**values)  # type: ignore[arg-type]


# --- Transfer code and webhook secret ---------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "G3NAP7K2Q9",
        "nap tien g3nap7k2q9",
        "MBVCB.123456.G3NAP 7K2Q9.CT tu NGUYEN VAN A",
        "G3NAP-7K2Q9 chuyen khoan",
    ],
)
def test_the_transfer_code_is_found_in_the_content_banks_pass_on(content: str) -> None:
    """Case, spaces, punctuation and extra text do not hide the code."""
    assert topup_service.extract_transfer_code(content) == "G3NAP7K2Q9"


def test_content_without_a_code_matches_nothing() -> None:
    """Text with the prefix but a too-short or look-alike code is not a code."""
    assert topup_service.extract_transfer_code("G3NAP7K2") is None
    assert topup_service.extract_transfer_code("G3NAPO0000") is None
    assert topup_service.extract_transfer_code("thanks") is None


def test_generated_transfer_codes_fit_the_content_field() -> None:
    """A fresh code has the prefix and is found again by the extractor."""
    code = topup_service.generate_transfer_code()

    assert len(code) == 10
    assert topup_service.extract_transfer_code(code) == code


def test_the_webhook_secret_is_checked_and_empty_means_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong or missing secrets are refused; an unset secret refuses everything."""
    monkeypatch.setattr(settings, "BILLING_WEBHOOK_SECRET", "s3cret")
    topup_service.verify_webhook_secret("s3cret")
    for provided in (None, "", "s3cre", "S3CRET"):
        with pytest.raises(WebhookAuthenticationError):
            topup_service.verify_webhook_secret(provided)

    monkeypatch.setattr(settings, "BILLING_WEBHOOK_SECRET", "")
    with pytest.raises(WebhookAuthenticationError):
        topup_service.verify_webhook_secret("")
