"""Pydantic schemas for the drivers domain HTTP API."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.domains.drivers.types import (
    CheckInMethod,
    DriverStatus,
    DrivingSessionEndCause,
    LicenseClass,
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
        None, min_length=1, max_length=50, description="Driving licence number"
    )
    license_class: LicenseClass | None = Field(None, description="Licence class")
    license_expires_on: date | None = Field(None, description="Licence expiry date")
    status: DriverStatus | None = Field(None, description="Driver status")
    status_reason: str | None = Field(
        None, min_length=1, max_length=200, description="Why the status is what it is"
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
        status: Profile status.
        status_reason: Why the profile has its status.
        current_vehicle_id: Truck the driver is at the wheel of now (open
            driving session), or `None`.
        current_vehicle_vin: VIN of that truck, enriched by the service.
        created_at: Creation time.
        updated_at: Last update time.
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
    status: DriverStatus
    status_reason: str | None
    current_vehicle_id: UUID | None = None
    current_vehicle_vin: str | None = None
    created_at: datetime
    updated_at: datetime


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
    vehicle_vin: str = Field(
        ..., min_length=17, max_length=17, description="VIN of the truck"
    )
    check_in_method: CheckInMethod = Field(..., description="How the driver checked in")
    latitude: float | None = Field(
        default=None, ge=-90, le=90, description="Phone latitude at check-in"
    )
    longitude: float | None = Field(
        default=None, ge=-180, le=180, description="Phone longitude at check-in"
    )


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


class DrivingSessionListResponse(BaseModel):
    """Paginated driving session list, newest first."""

    items: list[DrivingSessionResponse] = Field(..., description="Sessions")
    total: int = Field(..., ge=0, description="Total number of sessions")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )
