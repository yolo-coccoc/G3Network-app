"""Pure price rules of the billing domain: time-of-use periods and bill rounding.

No database and no clock: every function takes its inputs and returns a value,
so the rules can be tested alone (BL-09, BL-10).

Time-of-use periods (``tariff_versions.time_periods``) are a JSON list of
``{days, from, to, price_per_kwh}`` read in **Vietnam time**. ``days`` are
three-letter weekday codes (``MON`` .. ``SUN``), ``from`` / ``to`` are
``HH:MM`` (``to`` may be ``24:00``). A period with ``from`` later than ``to``
runs across midnight (``22:00`` to ``04:00``) and its ``days`` name the day it
**starts** on, so ``SAT 22:00-04:00`` ends on Sunday morning. Two periods may
not cover the same minute of the week.

Rounding (BL-10): the amount before VAT is ``energy_wh / 1000 x price_per_kwh``
rounded half up to whole dong; the VAT is that amount times the rate, rounded
half up to whole dong; the total is the sum of the two stored figures, so the
receipt, the wallet debit and a later e-invoice always agree.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Final
from zoneinfo import ZoneInfo

from app.domains.billing.exceptions import TariffInputError

# Tariff periods are written in the time of the country the chargers stand in.
VIETNAM_TIMEZONE: Final[ZoneInfo] = ZoneInfo("Asia/Ho_Chi_Minh")

WEEKDAY_CODES: Final[tuple[str, ...]] = (
    "MON",
    "TUE",
    "WED",
    "THU",
    "FRI",
    "SAT",
    "SUN",
)
_MINUTES_PER_DAY: Final[int] = 24 * 60
_MINUTES_PER_WEEK: Final[int] = 7 * _MINUTES_PER_DAY
_CLOCK_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(\d{2}):(\d{2})$")
_WH_PER_KWH: Final[Decimal] = Decimal(1000)
_WHOLE_DONG: Final[Decimal] = Decimal(1)
_ONE_HUNDRED: Final[Decimal] = Decimal(100)


@dataclass(frozen=True, slots=True)
class TariffPeriod:
    """One validated time-of-use period.

    Attributes:
        weekdays: Weekday numbers the period starts on (Monday is 0).
        from_minute: Start, in minutes after midnight (0 to 1439).
        to_minute: End, in minutes after midnight (1 to 1440); an end at or
            before the start means the period crosses midnight.
        price_per_kwh: Price in the period, before VAT, in whole dong.
    """

    weekdays: frozenset[int]
    from_minute: int
    to_minute: int
    price_per_kwh: Decimal

    @property
    def crosses_midnight(self) -> bool:
        """Tell whether the period runs into the next day."""
        return self.from_minute > self.to_minute


def _parse_clock(value: object, field_name: str, *, allow_24: bool) -> int:
    """Read an ``HH:MM`` text as minutes after midnight.

    Args:
        value: The JSON value.
        field_name: ``from`` or ``to``, for the message.
        allow_24: Whether ``24:00`` (end of day) is accepted.

    Returns:
        Minutes after midnight.

    Raises:
        TariffInputError: The value is not a valid clock time.
    """
    match = _CLOCK_PATTERN.match(value) if isinstance(value, str) else None
    if match is None:
        raise TariffInputError(f"time period '{field_name}' must be HH:MM")
    hours, minutes = int(match.group(1)), int(match.group(2))
    total = hours * 60 + minutes
    is_end_of_day = allow_24 and total == _MINUTES_PER_DAY
    if minutes > 59 or (total >= _MINUTES_PER_DAY and not is_end_of_day):
        raise TariffInputError(f"time period '{field_name}' is not a valid time")
    return total


def _parse_whole_dong(value: object, field_name: str) -> Decimal:
    """Read a JSON number as a non-negative whole-dong price.

    Args:
        value: The JSON value.
        field_name: Name for the message.

    Returns:
        The price.

    Raises:
        TariffInputError: Not a whole, non-negative number.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str | Decimal):
        raise TariffInputError(f"{field_name} must be a number")
    try:
        price = Decimal(str(value))
    except ArithmeticError as error:
        raise TariffInputError(f"{field_name} must be a number") from error
    if not price.is_finite() or price < 0 or price != price.to_integral_value():
        raise TariffInputError(f"{field_name} must be a whole number of dong")
    return price


def parse_time_periods(raw_periods: object) -> list[TariffPeriod]:
    """Validate the JSON periods of a tariff version and read them.

    Args:
        raw_periods: The ``time_periods`` value: ``None`` or a list of
            ``{days, from, to, price_per_kwh}``.

    Returns:
        The periods, empty for ``None``.

    Raises:
        TariffInputError: A period is malformed, empty in days, starts and
            ends at the same time, or two periods cover the same minute.
    """
    if raw_periods is None:
        return []
    if not isinstance(raw_periods, list):
        raise TariffInputError("time_periods must be a list")
    periods: list[TariffPeriod] = []
    for raw_period in raw_periods:
        if not isinstance(raw_period, dict):
            raise TariffInputError("every time period must be an object")
        raw_days = raw_period.get("days")
        if not isinstance(raw_days, list) or not raw_days:
            raise TariffInputError("a time period needs at least one day")
        weekdays: set[int] = set()
        for day in raw_days:
            if day not in WEEKDAY_CODES:
                raise TariffInputError(f"'{day}' is not a weekday code (MON..SUN)")
            weekdays.add(WEEKDAY_CODES.index(day))
        from_minute = _parse_clock(raw_period.get("from"), "from", allow_24=False)
        to_minute = _parse_clock(raw_period.get("to"), "to", allow_24=True)
        if from_minute == to_minute:
            raise TariffInputError(
                "a time period cannot start and end at the same time"
            )
        periods.append(
            TariffPeriod(
                weekdays=frozenset(weekdays),
                from_minute=from_minute,
                to_minute=to_minute,
                price_per_kwh=_parse_whole_dong(
                    raw_period.get("price_per_kwh"), "time period price_per_kwh"
                ),
            )
        )
    _reject_overlaps(periods)
    return periods


def _period_week_minutes(period: TariffPeriod) -> list[int]:
    """List every minute of the week a period covers.

    Args:
        period: A validated period.

    Returns:
        Minute numbers from Monday 00:00 (0) up to Sunday 23:59 (10079); a
        period crossing midnight wraps from Sunday into Monday.
    """
    minutes: list[int] = []
    for weekday in period.weekdays:
        day_start = weekday * _MINUTES_PER_DAY
        if period.crosses_midnight:
            ranges = [
                (period.from_minute, _MINUTES_PER_DAY),
                (_MINUTES_PER_DAY, _MINUTES_PER_DAY + period.to_minute),
            ]
        else:
            ranges = [(period.from_minute, period.to_minute)]
        for start, end in ranges:
            minutes.extend(
                (day_start + minute) % _MINUTES_PER_WEEK for minute in range(start, end)
            )
    return minutes


def _reject_overlaps(periods: list[TariffPeriod]) -> None:
    """Refuse periods that cover the same minute of the week.

    Args:
        periods: The validated periods.

    Raises:
        TariffInputError: Two periods overlap.
    """
    occupied: set[int] = set()
    for period in periods:
        minutes = _period_week_minutes(period)
        if occupied.intersection(minutes):
            raise TariffInputError("time periods overlap")
        occupied.update(minutes)


def normalize_time_periods(raw_periods: object) -> list[dict[str, object]] | None:
    """Validate periods and return them in the form stored in the database.

    Args:
        raw_periods: The ``time_periods`` value of a request.

    Returns:
        ``None`` when there are no periods, otherwise the list with whole-dong
        integer prices, uppercase day codes and ``HH:MM`` times.

    Raises:
        TariffInputError: See `parse_time_periods`.
    """
    periods = parse_time_periods(raw_periods)
    if not periods:
        return None
    normalized: list[dict[str, object]] = []
    for period in periods:
        normalized.append(
            {
                "days": [
                    code
                    for index, code in enumerate(WEEKDAY_CODES)
                    if index in period.weekdays
                ],
                "from": f"{period.from_minute // 60:02d}:{period.from_minute % 60:02d}",
                "to": f"{period.to_minute // 60:02d}:{period.to_minute % 60:02d}",
                "price_per_kwh": int(period.price_per_kwh),
            }
        )
    return normalized


def price_at(
    normal_price_per_kwh: Decimal, raw_periods: object, at: datetime
) -> Decimal:
    """Find the price per kWh (before VAT) at a moment.

    Args:
        normal_price_per_kwh: The version's normal price.
        raw_periods: The version's stored ``time_periods``.
        at: The moment; must carry a timezone.

    Returns:
        The price of the period covering the moment, else the normal price.

    Raises:
        ValueError: ``at`` has no timezone.
    """
    if at.tzinfo is None:
        raise ValueError("'at' must carry a timezone")
    local = at.astimezone(VIETNAM_TIMEZONE)
    minute_of_day = local.hour * 60 + local.minute
    weekday = local.weekday()
    for period in parse_time_periods(raw_periods):
        if period.crosses_midnight:
            starts_today = minute_of_day >= period.from_minute
            ends_today = minute_of_day < period.to_minute
            if (starts_today and weekday in period.weekdays) or (
                ends_today and (weekday - 1) % 7 in period.weekdays
            ):
                return period.price_per_kwh
        elif weekday in period.weekdays and (
            period.from_minute <= minute_of_day < period.to_minute
        ):
            return period.price_per_kwh
    return normal_price_per_kwh


def round_to_whole_dong(value: Decimal) -> Decimal:
    """Round an amount half up to whole dong (BL-10).

    Args:
        value: An unrounded amount.

    Returns:
        The amount in whole dong.
    """
    return value.quantize(_WHOLE_DONG, rounding=ROUND_HALF_UP)


def calculate_bill_amounts(
    energy_wh: Decimal, price_per_kwh: Decimal, vat_rate_percent: Decimal
) -> tuple[Decimal, Decimal]:
    """Compute the two stored amounts of a bill (BL-10).

    Args:
        energy_wh: Energy billed, in Wh.
        price_per_kwh: Frozen price per kWh before VAT.
        vat_rate_percent: Frozen VAT rate.

    Returns:
        ``(amount_before_vat, vat_amount)`` in whole dong; the total is their
        sum.
    """
    amount_before_vat = round_to_whole_dong(energy_wh / _WH_PER_KWH * price_per_kwh)
    vat_amount = round_to_whole_dong(
        amount_before_vat * vat_rate_percent / _ONE_HUNDRED
    )
    return amount_before_vat, vat_amount


# A gap between the stop reading and the newest measurement below this size is
# noise (a rounding of the charger's sample) and never holds a bill.
MISMATCH_FLOOR_WH: Final[Decimal] = Decimal(1000)


@dataclass(frozen=True, slots=True)
class BillableEnergy:
    """The energy to bill for a session, or the reason it cannot be billed yet.

    Attributes:
        energy_wh: Energy in Wh from the best reading; ``None`` when no figure
            can be computed.
        energy_source: ``METER_STOP`` or ``LAST_MEASUREMENT``; ``None`` with
            ``energy_wh``.
        hold_reason: Why the bill must wait for review (CE-12); ``None`` when
            it can be billed at once.
    """

    energy_wh: Decimal | None
    energy_source: str | None
    hold_reason: str | None


def decide_billable_energy(
    *,
    meter_start_wh: Decimal | None,
    meter_stop_wh: Decimal | None,
    last_measured_wh: Decimal | None,
    tolerance_percent: Decimal,
) -> BillableEnergy:
    """Decide the billed energy of a finished session (CE-12).

    The charger's closing reading minus its start reading is the billing
    figure. Without a closing reading the newest outlet measurement stands in.
    A closing reading that disagrees with the newest measurement by more than
    the tolerance (and by at least `MISMATCH_FLOOR_WH`) is computed but put on
    hold for review. A missing start reading, no closing figure at all, or a
    closing figure below the start reading cannot be billed.

    Args:
        meter_start_wh: The charger's start reading.
        meter_stop_wh: The charger's closing reading.
        last_measured_wh: The newest outlet energy-register measurement.
        tolerance_percent: Allowed gap in percent of the energy.

    Returns:
        The energy, its source and, if it must wait, why.
    """
    if meter_start_wh is None:
        return BillableEnergy(None, None, "The charger sent no start reading")
    if meter_stop_wh is not None:
        closing_wh, source = meter_stop_wh, "METER_STOP"
    elif last_measured_wh is not None:
        closing_wh, source = last_measured_wh, "LAST_MEASUREMENT"
    else:
        return BillableEnergy(None, None, "The charger sent no closing reading")
    energy_wh = closing_wh - meter_start_wh
    if energy_wh < 0:
        return BillableEnergy(
            None, None, "The closing reading is below the start reading"
        )
    if source == "METER_STOP" and last_measured_wh is not None:
        measured_wh = last_measured_wh - meter_start_wh
        gap_wh = abs(energy_wh - measured_wh)
        allowed_wh = max(energy_wh, measured_wh) * tolerance_percent / _ONE_HUNDRED
        if gap_wh >= MISMATCH_FLOOR_WH and gap_wh > allowed_wh:
            return BillableEnergy(
                energy_wh,
                source,
                "Stop reading and last measurement differ by "
                f"{gap_wh.quantize(Decimal(1))} Wh",
            )
    return BillableEnergy(energy_wh, source, None)
