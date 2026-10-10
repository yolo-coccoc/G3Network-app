"""Shared ORM-record and message builders for the smoke tests.

Builders return in-memory objects only; nothing here touches a database.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.charging_sessions.models import ChargingSessionModel
from app.domains.charging_sessions.types import (
    SessionStatus,
)
from app.domains.charging_stations.models import (
    ChargingLocationModel,
    ChargingStationModel,
    ChargingStationStateModel,
)
from app.domains.charging_stations.types import ChargingResourceStatus
from app.domains.drivers.models import DriverModel, DrivingSessionModel
from app.domains.drivers.types import CheckInMethod, DriverStatus
from app.domains.fleet.models import FleetModel, FleetVehicleMembershipModel
from app.domains.identity.models import OrganizationModel
from app.domains.identity.types import (
    MembershipPersonReference,
    MembershipStatus,
    OrganizationLegalForm,
    OrganizationStatus,
    UserStatus,
)
from app.domains.support.models import SupportCaseModel
from app.domains.support.types import (
    SupportCaseCategory,
    SupportCaseChannel,
    SupportCaseStatus,
    SupportCaseType,
)
from app.domains.telematics.models import TelematicModel
from app.domains.telematics.types import TelematicStatus
from app.domains.telemetry.models import TelemetryModel
from app.domains.telemetry.schemas import TelemetryEnvelope, TelemetryMessage
from app.domains.vehicles.models import VehicleModel, VehicleModelModel
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.geo import coordinates_to_location


def fake_db_session() -> AsyncSession:
    """Create a placeholder session for a unit test that doesn't touch the database."""
    return cast(AsyncSession, object())


def build_vehicle_record() -> VehicleModel:
    """Create a minimal ORM vehicle for the service to convert into a response."""
    now = datetime.now(timezone.utc)
    return VehicleModel(
        vehicle_id=uuid4(),
        organization_id=uuid4(),
        acquired_at=now,
        license_plate="TEST-001",
        vin="1HGBH41JXMN109186",
        vehicle_model_id=uuid4(),
        year=2026,
        status=VehicleStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )


def build_vehicle_model_record() -> VehicleModelModel:
    """Create a minimal ORM vehicle model (catalog row)."""
    now = datetime.now(timezone.utc)
    return VehicleModelModel(
        vehicle_model_id=uuid4(),
        make="Tri-Ring",
        model_name="EVT-400",
        created_at=now,
        updated_at=now,
    )


def build_driver_record(
    *, driver_id: UUID | None = None, membership_id: UUID | None = None
) -> DriverModel:
    """Create a minimal ORM driver for the service to convert into a response."""
    now = datetime.now(timezone.utc)
    return DriverModel(
        driver_id=driver_id or uuid4(),
        membership_id=membership_id or uuid4(),
        license_number="LICENSE-001",
        license_class="CE",
        license_expires_on=(now + timedelta(days=365)).date(),
        status=DriverStatus.ACTIVE,
        status_reason=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def build_person_reference(
    *,
    membership_id: UUID | None = None,
    membership_status: MembershipStatus = MembershipStatus.ACTIVE,
    user_status: UserStatus = UserStatus.ACTIVE,
    left_at: datetime | None = None,
) -> MembershipPersonReference:
    """Create the identity DTO a driver's membership resolves to."""
    return MembershipPersonReference(
        membership_id=membership_id or uuid4(),
        organization_id=uuid4(),
        user_id=uuid4(),
        full_name="Test Driver",
        phone_number="+84900000001",
        membership_status=membership_status.value,
        user_status=user_status.value,
        left_at=left_at,
    )


def build_driving_session_record(
    *,
    driver_id: UUID,
    vehicle_id: UUID,
    ended_at: datetime | None = None,
) -> DrivingSessionModel:
    """Create a minimal ORM driving session, open unless `ended_at` is given."""
    now = datetime.now(timezone.utc)
    return DrivingSessionModel(
        driving_session_id=uuid4(),
        organization_id=uuid4(),
        driver_id=driver_id,
        vehicle_id=vehicle_id,
        check_in_method=CheckInMethod.APP.value,
        check_in_location=None,
        started_at=now,
        ended_at=ended_at,
        end_cause=None,
        created_at=now,
        updated_at=now,
    )


def build_support_case_record(
    *,
    case_type: SupportCaseType = SupportCaseType.TICKET,
    status: SupportCaseStatus = SupportCaseStatus.OPEN,
    sla_response_minutes: int = 60,
    response_due_at: datetime | None = None,
    first_responded_at: datetime | None = None,
    resolved_at: datetime | None = None,
    closed_at: datetime | None = None,
    driver_id: UUID | None = None,
    organization_id: UUID | None = None,
) -> SupportCaseModel:
    """Create a minimal ORM support case for the service to convert into a response."""
    now = datetime.now(timezone.utc)
    return SupportCaseModel(
        case_id=uuid4(),
        organization_id=organization_id,
        case_type=case_type,
        category=SupportCaseCategory.TECHNICAL,
        channel=SupportCaseChannel.IN_APP,
        status=status,
        vehicle_id=None,
        driver_id=driver_id,
        vin=None,
        error_code=None,
        location=None,
        subject="Test subject",
        description=None,
        sla_response_minutes=sla_response_minutes,
        response_due_at=response_due_at
        or (now + timedelta(minutes=sla_response_minutes)),
        first_responded_at=first_responded_at,
        resolved_at=resolved_at,
        closed_at=closed_at,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def build_organization_record(
    *,
    organization_id: UUID | None = None,
    tax_code: str | None = None,
) -> OrganizationModel:
    """Create a minimal active, non-internal ORM organization.

    Insert it first in an integration test that needs an owner for fleets
    (``fleets.organization_id`` is required).
    """
    now = datetime.now(timezone.utc)
    return OrganizationModel(
        organization_id=organization_id or uuid4(),
        is_internal=False,
        legal_form=OrganizationLegalForm.COMPANY.value,
        display_name="Test Organization",
        legal_name="Test Organization Co., Ltd.",
        tax_code=tax_code,
        status=OrganizationStatus.ACTIVE.value,
        created_at=now,
        updated_at=now,
    )


def build_fleet_record(
    *, fleet_id: UUID | None = None, organization_id: UUID | None = None
) -> FleetModel:
    """Create a minimal ORM fleet for the service to convert into a response."""
    now = datetime.now(timezone.utc)
    return FleetModel(
        fleet_id=fleet_id or uuid4(),
        organization_id=organization_id or uuid4(),
        fleet_code="FLEET-001",
        name="Test Fleet",
        parent_fleet_id=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def build_membership_record(
    *,
    fleet_id: UUID,
    vehicle_id: UUID,
    removed_at: datetime | None = None,
) -> FleetVehicleMembershipModel:
    """Create a minimal ORM membership, open unless `removed_at` is given."""
    now = datetime.now(timezone.utc)
    return FleetVehicleMembershipModel(
        fleet_vehicle_membership_id=uuid4(),
        fleet_id=fleet_id,
        vehicle_id=vehicle_id,
        added_at=now,
        removed_at=removed_at,
        created_at=now,
        updated_at=now,
    )


def build_telematic_record(vehicle_id: UUID) -> TelematicModel:
    """Create a minimal ORM telematic already assigned to a vehicle."""
    now = datetime.now(timezone.utc)
    return TelematicModel(
        telematic_id=uuid4(),
        telematic_serial="TBOX-TEST-001",
        organization_id=uuid4(),
        acquired_at=now,
        vehicle_id=vehicle_id,
        status=TelematicStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )


def build_telemetry_envelope(
    soc: float = 80.0,
    *,
    battery_temperature: float | None = None,
    battery_voltage: float | None = None,
    soh_percent: float | None = None,
    cycle_count: int | None = None,
    errors: list[str] | None = None,
) -> TelemetryEnvelope:
    """Create a valid telemetry envelope for process_message."""
    message = TelemetryMessage.model_validate(
        {
            "message_uuid": str(uuid4()),
            "telematic_serial": "TBOX-TEST-001",
            "recorded_at": "2026-08-26T10:00:00Z",
            "location": {"latitude": 10.8, "longitude": 106.7},
            "battery": {
                "soc": soc,
                "temperature": battery_temperature,
                "voltage": battery_voltage,
                "soh_percent": soh_percent,
                "cycle_count": cycle_count,
            },
            "errors": errors,
        }
    )
    return TelemetryEnvelope(message=message, raw_payload={"test": True})


def build_telemetry_record(
    *,
    vehicle_id: UUID,
    recorded_at: datetime,
    message_id: int = 1,
    soh_percent: float | None = None,
    cycle_count: int | None = None,
) -> TelemetryModel:
    """Create a minimal ORM telemetry record for a history/latest test."""
    return TelemetryModel(
        message_id=message_id,
        organization_id=uuid4(),
        device_message_id=uuid4(),
        telematic_id=uuid4(),
        vehicle_id=vehicle_id,
        recorded_at=recorded_at,
        received_at=recorded_at,
        location=coordinates_to_location(10.762622, 106.660172),
        speed_kmh=None,
        heading_degrees=None,
        soc_percent=80.0,
        battery_voltage_v=None,
        battery_current_a=None,
        battery_temperature_celsius=None,
        soh_percent=soh_percent,
        cycle_count=cycle_count,
        motor_temperature_celsius=None,
        odometer_km=None,
        signal_dbm=None,
        error_codes=None,
        raw_payload={},
        schema_version=1,
    )


def build_charging_session(
    *,
    status: SessionStatus = SessionStatus.ACTIVE,
    meter_start_wh: Decimal | None = None,
    meter_stop_wh: Decimal | None = None,
) -> ChargingSessionModel:
    """Create a minimal started session for the charging service test."""
    now = datetime.now(timezone.utc)
    is_started = status in (SessionStatus.ACTIVE, SessionStatus.COMPLETED)
    return ChargingSessionModel(
        session_id=uuid4(),
        station_id=uuid4(),
        evse_id=uuid4() if is_started else None,
        connector_id=uuid4() if is_started else None,
        organization_id=uuid4(),
        started_by=uuid4(),
        vehicle_id=None,
        ocpp_transaction_id="TX-TEST-001" if is_started else None,
        status=status,
        started_at=now if is_started else None,
        ended_at=now if status is SessionStatus.COMPLETED else None,
        meter_start_wh=meter_start_wh,
        id_token="TOKEN-TEST-01",
        stop_reason=None,
        meter_stop_wh=meter_stop_wh,
        created_at=now,
        updated_at=now,
    )


def build_charging_location_record(
    *, location_id: UUID | None = None, organization_id: UUID | None = None
) -> ChargingLocationModel:
    """Create a minimal ORM location (public, ACTIVE) for mapper tests."""
    now = datetime.now(timezone.utc)
    return ChargingLocationModel(
        location_id=location_id or uuid4(),
        organization_id=organization_id or uuid4(),
        display_name="Test Location",
        address="Km 1872+500 QL1A, Dong Nai",
        coordinates=coordinates_to_location(10.762622, 106.660172),
        is_public=True,
        status=ChargingResourceStatus.ACTIVE.value,
        status_reason=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def build_charging_station_record(
    *, station_id: UUID | None = None, location_id: UUID | None = None
) -> ChargingStationModel:
    """Create a minimal ORM station (ACTIVE) for a mapper test."""
    now = datetime.now(timezone.utc)
    return ChargingStationModel(
        station_id=station_id or uuid4(),
        location_id=location_id or uuid4(),
        ocpp_identity="OCPP-TEST-001",
        registered_serial_number="SN-TEST-001",
        physical_reference="Tru 1",
        max_power_kw=Decimal("120.00"),
        status=ChargingResourceStatus.ACTIVE.value,
        status_reason=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def build_charging_station_state_record(
    *, station_id: UUID | None = None, last_seen_at: datetime | None = None
) -> ChargingStationStateModel:
    """Create a minimal ORM station state row for a mapper test."""
    return ChargingStationStateModel(
        station_id=station_id or uuid4(), last_seen_at=last_seen_at
    )
