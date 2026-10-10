"""Pydantic schemas for the drivers domain HTTP API."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.domains.drivers.types import (
    CheckInMethod,
    CheckInWarning,
    DeclaredLoadStatus,
    DriverStatus,
    DriverWarning,
    DrivingSessionEndCause,
    LicenseClass,
    TripStatus,
)
from app.libs.common.config import settings


class DriverCreateRequest(BaseModel):
    """HTTP request data for creating a driver profile for a membership."""

    membership_id: UUID = Field(
        ..., description="Membership (person in an organization) the profile is for"
    )
    license_number: str = Field(
        ..., min_length=1, max_length=50, description="Driving licence number"
    )
    license_class: LicenseClass = Field(..., description="Licence class")
    license_expires_on: date = Field(..., description="Licence expiry date")
    status: DriverStatus = Field(
        default=DriverStatus.ACTIVE, description="Driver status"
    )


class DriverUpdateRequest(BaseModel):
    """HTTP request data for partially updating a driver profile."""

    license_number: str | None = Field(
        default=None, min_length=1, max_length=50, description="Driving licence number"
    )
    license_class: LicenseClass | None = Field(
        default=None, description="Licence class"
    )
    license_expires_on: date | None = Field(
        default=None, description="Licence expiry date"
    )
    status: DriverStatus | None = Field(default=None, description="Driver status")
    status_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Why the status is what it is",
    )


class DriverResponse(BaseModel):
    """Driver profile data returned via the HTTP API.

    Attributes:
        driver_id: Internal ID of the driver profile.
        membership_id: The membership the profile belongs to.
        organization_id: The membership's organization.
        full_name: The person's name (from the user).
        phone_number: The person's phone number (from the user).
        license_number: Driving licence number.
        license_class: Licence class.
        license_expires_on: Licence expiry date.
        is_license_expired: Computed from the expiry date, never stored.
        days_until_license_expiry: Days left until the expiry date (negative
            once expired), for the expiry reminder (DRV-01).
        status: Profile status.
        status_reason: Why the profile has its status.
        current_vehicle_id: Truck the driver is at the wheel of now (open
            driving session), or `None`.
        current_vehicle_vin: VIN of that truck, enriched by the service.
        created_at: Creation time.
        updated_at: Last update time.
        warnings: Notices about the profile (set when it is created or its
            licence number changes), e.g. the licence number is already on
            another person's profile (DR-09); empty otherwise.
    """

    driver_id: UUID
    membership_id: UUID
    organization_id: UUID | None
    full_name: str | None
    phone_number: str | None
    license_number: str
    license_class: str
    license_expires_on: date
    is_license_expired: bool
    days_until_license_expiry: int
    status: DriverStatus
    status_reason: str | None
    current_vehicle_id: UUID | None = None
    current_vehicle_vin: str | None = None
    created_at: datetime
    updated_at: datetime
    warnings: list[DriverWarning] = Field(default_factory=list)


class DriverListResponse(BaseModel):
    """Paginated driver list data returned via the HTTP API."""

    items: list[DriverResponse] = Field(..., description="List of drivers")
    total: int = Field(..., ge=0, description="Total number of drivers")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )


class DrivingSessionCheckInRequest(BaseModel):
    """HTTP request data for checking a driver in to a truck."""

    driver_id: UUID | None = Field(
        default=None,
        description=(
            "Driver profile checking in; omitted means the caller's own "
            "profile, a manager may name another driver of the organization"
        ),
    )
    vehicle_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description=(
            "Code printed on the truck (the QR content is still open, "
            "deferred.md 94): its VIN or its licence plate"
        ),
    )
    vehicle_vin: str | None = Field(
        default=None,
        min_length=17,
        max_length=17,
        description="VIN of the truck; same as `vehicle_code` when it is a VIN",
    )
    check_in_method: CheckInMethod = Field(..., description="How the driver checked in")
    latitude: float | None = Field(
        default=None, ge=-90, le=90, description="Phone latitude at check-in"
    )
    longitude: float | None = Field(
        default=None, ge=-180, le=180, description="Phone longitude at check-in"
    )

    @model_validator(mode="after")
    def _check_code_and_position(self) -> "DrivingSessionCheckInRequest":
        """Require one truck code and a complete phone position.

        Returns:
            The validated request.

        Raises:
            ValueError: Neither or both of `vehicle_code` / `vehicle_vin`
                are given, or only one of latitude and longitude.
        """
        if (self.vehicle_code is None) == (self.vehicle_vin is None):
            raise ValueError("Give exactly one of vehicle_code and vehicle_vin")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be given together")
        return self

    @property
    def truck_code(self) -> str:
        """The truck code to look up, whichever field carried it."""
        return self.vehicle_code or self.vehicle_vin or ""


class DrivingSessionCheckOutRequest(BaseModel):
    """HTTP request data for ending a driver's open driving session."""

    driver_id: UUID | None = Field(
        default=None,
        description=(
            "Driver profile checking out; omitted means the caller's own "
            "profile, a manager may name another driver of the organization"
        ),
    )


class DrivingSessionResponse(BaseModel):
    """One driving session, open or closed.

    Attributes:
        driving_session_id: Internal ID of the session.
        organization_id: Owner of the truck when the session was recorded.
        driver_id: Driver profile at the wheel.
        vehicle_id: Truck driven.
        vehicle_vin: VIN of the truck, `None` if it no longer resolves.
        check_in_method: How the driver checked in.
        started_at: Check-in time.
        ended_at: End time, `None` while open.
        end_cause: Why it ended, `None` while open.
        ended_sessions: Sessions this check-in ended (the truck's previous
            driver, the driver's previous truck); empty otherwise.
        warnings: Notices of the check-in (driver from another organization,
            no recent truck position); empty for any other read.
    """

    driving_session_id: UUID
    organization_id: UUID
    driver_id: UUID
    vehicle_id: UUID
    vehicle_vin: str | None
    check_in_method: CheckInMethod
    started_at: datetime
    ended_at: datetime | None
    end_cause: DrivingSessionEndCause | None
    ended_sessions: list["DrivingSessionResponse"] = Field(default_factory=list)
    warnings: list[CheckInWarning] = Field(default_factory=list)


class DrivingSessionListResponse(BaseModel):
    """Paginated driving session list, newest first."""

    items: list[DrivingSessionResponse] = Field(..., description="Sessions")
    total: int = Field(..., ge=0, description="Total number of sessions")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )


class DrivingSummarySessionItem(BaseModel):
    """One session in the driver's own summary: no route, GPS or places (DR-11).

    Attributes:
        driving_session_id: Internal ID of the session.
        day: Calendar day of the check-in in the report time zone.
        license_plate: Plate of the truck driven.
        started_at: Check-in time.
        ended_at: Check-out time, `None` while the driver is still at the wheel.
        duration_minutes: Minutes at the wheel (up to now while open).
        distance_km: Distance from the odometer, `None` when the T-Box sent
            fewer than two odometer readings in the session.
        end_cause: Why it ended, `None` while open.
    """

    driving_session_id: UUID
    day: date
    license_plate: str | None
    started_at: datetime
    ended_at: datetime | None
    duration_minutes: int
    distance_km: float | None
    end_cause: DrivingSessionEndCause | None


class DrivingSummaryTotal(BaseModel):
    """Driving time and distance of one day or one week (DR-11).

    Attributes:
        period_start: The day, or the Monday of the week (report time zone).
        session_count: Sessions that started in the period.
        driving_minutes: Minutes at the wheel, a session counted in the
            period it started in.
        distance_km: Sum of the known session distances.
    """

    period_start: date
    session_count: int
    driving_minutes: int
    distance_km: float


class DrivingSummaryResponse(BaseModel):
    """The driver's own driving summary over a range (DR-11).

    Attributes:
        from_time: Start of the range (UTC).
        to_time: End of the range (UTC).
        sessions: The sessions, newest first.
        totals_per_day: Totals per day, newest first.
        totals_per_week: Totals per week, newest first.
    """

    from_time: datetime
    to_time: datetime
    sessions: list[DrivingSummarySessionItem]
    totals_per_day: list[DrivingSummaryTotal]
    totals_per_week: list[DrivingSummaryTotal]


class TripPlanRequest(BaseModel):
    """HTTP request data for a manager planning a trip (DR-12)."""

    origin_name: str = Field(..., min_length=1, max_length=200, description="From")
    destination_name: str = Field(..., min_length=1, max_length=200, description="To")
    planned_start_at: datetime = Field(..., description="Planned departure (with zone)")
    planned_end_at: datetime | None = Field(
        default=None, description="Planned arrival (with zone)"
    )
    planned_driver_id: UUID | None = Field(
        default=None, description="Driver profile assigned; may be set later"
    )
    planned_vehicle_id: UUID | None = Field(default=None, description="Truck assigned")

    @model_validator(mode="after")
    def _check_times(self) -> "TripPlanRequest":
        """Require zoned times and an arrival after the departure.

        Returns:
            The validated request.

        Raises:
            ValueError: A time has no zone, or the arrival is not after the
                departure.
        """
        _check_trip_times(self.planned_start_at, self.planned_end_at)
        return self


class TripUpdateRequest(BaseModel):
    """HTTP request data for rerouting or reassigning a planned trip (DR-12)."""

    origin_name: str | None = Field(default=None, min_length=1, max_length=200)
    destination_name: str | None = Field(default=None, min_length=1, max_length=200)
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None
    planned_driver_id: UUID | None = None
    planned_vehicle_id: UUID | None = None
    reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Why it changes (history)",
    )

    @model_validator(mode="after")
    def _check_times(self) -> "TripUpdateRequest":
        """Require zoned times and, when both are sent, an arrival after the departure.

        Returns:
            The validated request.

        Raises:
            ValueError: A time has no zone, or the arrival is not after the
                departure.
        """
        _check_trip_times(self.planned_start_at, self.planned_end_at)
        return self


class TripCancelRequest(BaseModel):
    """HTTP request data for cancelling a planned trip."""

    reason: str = Field(..., min_length=1, max_length=200, description="Why")


class TripStartRequest(BaseModel):
    """HTTP request data for the driver pressing Start on a planned trip."""

    declared_load_status: DeclaredLoadStatus | None = Field(
        default=None, description="LOADED or EMPTY (MON-13)"
    )


class PersonalTripStartRequest(TripStartRequest):
    """HTTP request data for a driver with no plan starting a personal trip."""

    origin_name: str | None = Field(default=None, min_length=1, max_length=200)
    destination_name: str | None = Field(default=None, min_length=1, max_length=200)


class TripResponse(BaseModel):
    """One trip: the plan, the actual run and the figures computed from it.

    Attributes:
        trip_id: Internal ID of the trip.
        organization_id: Organization the trip belongs to.
        status: PLANNED / IN_PROGRESS / COMPLETED / CANCELLED.
        status_reason: Why the trip has its status, if said.
        planned_by: Manager who planned it, `None` for a personal trip.
        planned_driver_id: Driver assigned in the plan.
        planned_driver_name: That driver's name.
        planned_vehicle_id: Truck assigned in the plan.
        planned_vehicle_vin: That truck's VIN.
        origin_name: Where the trip starts.
        destination_name: Where it ends.
        planned_start_at: Planned departure.
        planned_end_at: Planned arrival.
        driving_session_id: Session the trip was started in.
        actual_driver_id: Driver at the wheel (from the session).
        actual_vehicle_id: Truck driven (from the session).
        actual_vehicle_vin: That truck's VIN.
        started_at: When Start was pressed.
        ended_at: When Finish was pressed or the session closed the trip.
        start_latitude: T-Box latitude at Start.
        start_longitude: T-Box longitude at Start.
        end_latitude: T-Box latitude at the end.
        end_longitude: T-Box longitude at the end.
        start_odometer_km: Odometer at Start.
        end_odometer_km: Odometer at the end.
        start_soc_percent: Battery % at Start.
        end_soc_percent: Battery % at the end.
        declared_load_status: LOADED / EMPTY declared at Start.
        duration_minutes: Minutes from Start to the end (to now while running).
        distance_km: End minus start odometer, computed when read.
        energy_kwh: Battery drop times the pack capacity, computed when read.
        kwh_per_km: Energy per kilometre, when both are known.
        cost_vnd: Energy times the flat tariff setting.
        driver_differs_from_plan: The driver at the wheel is not the planned one.
        vehicle_differs_from_plan: The truck driven is not the planned one.
        created_at: Creation time.
        updated_at: Last update time.
    """

    trip_id: UUID
    organization_id: UUID
    status: TripStatus
    status_reason: str | None
    planned_by: UUID | None
    planned_driver_id: UUID | None
    planned_driver_name: str | None
    planned_vehicle_id: UUID | None
    planned_vehicle_vin: str | None
    origin_name: str | None
    destination_name: str | None
    planned_start_at: datetime | None
    planned_end_at: datetime | None
    driving_session_id: UUID | None
    actual_driver_id: UUID | None
    actual_vehicle_id: UUID | None
    actual_vehicle_vin: str | None
    started_at: datetime | None
    ended_at: datetime | None
    start_latitude: float | None
    start_longitude: float | None
    end_latitude: float | None
    end_longitude: float | None
    start_odometer_km: float | None
    end_odometer_km: float | None
    start_soc_percent: float | None
    end_soc_percent: float | None
    declared_load_status: DeclaredLoadStatus | None
    duration_minutes: int | None
    distance_km: float | None
    energy_kwh: float | None
    kwh_per_km: float | None
    cost_vnd: float | None
    driver_differs_from_plan: bool
    vehicle_differs_from_plan: bool
    created_at: datetime
    updated_at: datetime


class TripListResponse(BaseModel):
    """Paginated trip list data returned via the HTTP API."""

    items: list[TripResponse] = Field(..., description="Trips")
    total: int = Field(..., ge=0, description="Total number of trips")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )


def _check_trip_times(start_at: datetime | None, end_at: datetime | None) -> None:
    """Validate the planned times of a trip.

    Args:
        start_at: Planned departure, if given.
        end_at: Planned arrival, if given.

    Raises:
        ValueError: A time has no zone, or the arrival is not after the
            departure.
    """
    for value in (start_at, end_at):
        if value is not None and value.tzinfo is None:
            raise ValueError("Planned times must carry a time zone")
    if start_at is not None and end_at is not None and end_at <= start_at:
        raise ValueError("The planned arrival must be after the planned departure")
