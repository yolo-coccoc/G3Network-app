"""Response mappers and update helpers of the charging_stations domain.

Turns ORM rows into the HTTP responses of locations, chargers, EVSEs, guns,
OCPP messages, nearby results and commands; the ``build_`` functions also
read the charger's state or connectors (documented per function). Also holds
the helpers that turn an update request into column values and a history
reason. Internal module: other domains use ``service.py``.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_stations.repository as charging_stations_repository
from app.domains.charging_stations.exceptions import (
    ChargingLocationNotFoundError,
)
from app.domains.charging_stations.models import (
    ChargingConnectorModel,
    ChargingConnectorStateModel,
    ChargingEvseModel,
    ChargingLocationModel,
    ChargingStationModel,
    ChargingStationStateModel,
)
from app.domains.charging_stations.schemas import (
    ChargingConnectorResponse,
    ChargingEvseResponse,
    ChargingLocationResponse,
    ChargingStationResponse,
)
from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ChargingResourceStatus,
    ConnectorStandard,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.geo import location_to_coordinates

# Short names a driver app or portal may send for a plug standard (CS-17); the
# stored value is always the OCPI name.
_CONNECTOR_STANDARD_ALIASES = {
    "CCS2": ConnectorStandard.IEC_62196_T2_COMBO,
    "CCS1": ConnectorStandard.IEC_62196_T1_COMBO,
    "GBT": ConnectorStandard.GBT_DC,
    "GB/T": ConnectorStandard.GBT_DC,
}


def to_optional_float(value: Decimal | None) -> float | None:
    """Convert a nullable ``Numeric`` column value into the float the API uses.

    Args:
        value: The stored decimal (e.g. ``max_power_kw``), or ``None``.

    Returns:
        ``float(value)``, or ``None`` when no value is stored.
    """
    return float(value) if value is not None else None


def is_station_online(
    state: ChargingStationStateModel | None, checked_at: datetime
) -> bool:
    """Derive whether a charger is online at a given time.

    Args:
        state: The charger's state row carrying ``last_seen_at``, or ``None``.
        checked_at: Reference time of the check.

    Returns:
        ``True`` if the latest frame of any kind arrived within
        ``CHARGING_OFFLINE_TIMEOUT_SECONDS`` of ``checked_at``; ``False`` for
        a station that has never connected.
    """
    return (
        state is not None
        and state.last_seen_at is not None
        and (checked_at - state.last_seen_at)
        <= timedelta(seconds=settings.CHARGING_OFFLINE_TIMEOUT_SECONDS)
    )


def normalize_connector_standard(value: str) -> str:
    """Turn a plug standard sent by a client into the stored OCPI name.

    Args:
        value: An OCPI name (``IEC_62196_T2_COMBO``) or a short name
            (``CCS2``, ``GBT``), in any case.

    Returns:
        The OCPI name when ``value`` is a known short name or OCPI name;
        otherwise ``value`` unchanged (it then matches no connector).
    """
    key = value.strip().upper()
    alias = _CONNECTOR_STANDARD_ALIASES.get(key)
    return alias.value if alias is not None else key


def to_charging_location_response(
    location: ChargingLocationModel,
) -> ChargingLocationResponse:
    """Build a location response from the ORM model.

    Args:
        location: Location ORM object queried or created by the repository.

    Returns:
        The response schema, with the map pin split into latitude and longitude.
    """
    latitude, longitude = location_to_coordinates(location.coordinates)
    return ChargingLocationResponse(
        location_id=location.location_id,
        organization_id=location.organization_id,
        display_name=location.display_name,
        address=location.address,
        latitude=latitude,
        longitude=longitude,
        is_public=location.is_public,
        status=ChargingResourceStatus(location.status),
        status_reason=location.status_reason,
        created_at=location.created_at,
        updated_at=location.updated_at,
        deleted_at=location.deleted_at,
    )


def to_charging_station_response(
    station: ChargingStationModel,
    location: ChargingLocationModel,
    state: ChargingStationStateModel | None,
    *,
    connector_count: int,
    available_connector_count: int,
    now: datetime | None = None,
) -> ChargingStationResponse:
    """Build a station response from the profile, its location and its state.

    Pure mapping only - the connector counts are computed by the caller (via
    repository queries) rather than here, since a pure mapper must never do
    I/O. The only outside input is the clock, needed to derive ``is_online``;
    it can be injected through ``now`` (tests do).

    Args:
        station: Station ORM object queried or created by the repository.
        location: The location the station stands at.
        state: The station's state row, or ``None`` (then nothing is reported).
        connector_count: Number of active connectors across the station's
            active EVSEs, already computed by the caller.
        available_connector_count: How many of them last reported
            ``Available``, already computed by the caller.
        now: Reference time for the online check; defaults to the current
            UTC time.

    Returns:
        Response schema including the profile, the owner read through the
        location, connector counts, the charger's reported info, and
        ``is_online`` (``last_seen_at`` within
        ``CHARGING_OFFLINE_TIMEOUT_SECONDS``; ``False`` if never seen).
    """
    checked_at = now if now is not None else utc_now()
    latitude, longitude = location_to_coordinates(location.coordinates)
    charger_status = (
        ChargingConnectorStatus(state.charger_status)
        if state is not None and state.charger_status is not None
        else None
    )
    return ChargingStationResponse(
        station_id=station.station_id,
        location_id=station.location_id,
        organization_id=location.organization_id,
        location_display_name=location.display_name,
        latitude=latitude,
        longitude=longitude,
        ocpp_identity=station.ocpp_identity,
        registered_serial_number=station.registered_serial_number,
        physical_reference=station.physical_reference,
        max_power_kw=to_optional_float(station.max_power_kw),
        status=ChargingResourceStatus(station.status),
        status_reason=station.status_reason,
        connector_count=connector_count,
        available_connector_count=available_connector_count,
        ocpp_protocol_version=state.ocpp_protocol_version if state else None,
        vendor=state.vendor if state else None,
        model=state.model if state else None,
        serial_number=state.serial_number if state else None,
        firmware_version=state.firmware_version if state else None,
        last_boot_at=state.last_boot_at if state else None,
        last_seen_at=state.last_seen_at if state else None,
        charger_status=charger_status,
        charger_status_updated_at=(state.charger_status_updated_at if state else None),
        charger_error_code=state.charger_error_code if state else None,
        charger_vendor_error_code=state.charger_vendor_error_code if state else None,
        is_online=is_station_online(state, checked_at),
        created_at=station.created_at,
        updated_at=station.updated_at,
        deleted_at=station.deleted_at,
    )


def to_charging_evse_response(evse: ChargingEvseModel) -> ChargingEvseResponse:
    """Build an EVSE response from the ORM model.

    Args:
        evse: EVSE ORM object queried or created by the repository.

    Returns:
        Response schema corresponding to the EVSE.
    """
    return ChargingEvseResponse.model_validate(evse)


def to_charging_connector_response(
    connector: ChargingConnectorModel,
    state: ChargingConnectorStateModel | None,
) -> ChargingConnectorResponse:
    """Build a connector response from the profile and the reported state.

    Args:
        connector: Connector ORM object queried or created by the
            repository.
        state: The connector's state row, or ``None`` (then no report).

    Returns:
        Response schema corresponding to the connector.
    """
    reported_status = (
        ChargingConnectorStatus(state.status)
        if state is not None and state.status is not None
        else None
    )
    return ChargingConnectorResponse(
        connector_id=connector.connector_id,
        evse_id=connector.evse_id,
        ocpp_connector_id=connector.ocpp_connector_id,
        standard=ConnectorStandard(connector.standard),
        max_power_kw=float(connector.max_power_kw),
        max_voltage_v=connector.max_voltage_v,
        max_current_a=connector.max_current_a,
        status=reported_status,
        status_updated_at=state.status_updated_at if state else None,
        error_code=state.error_code if state else None,
        vendor_error_code=state.vendor_error_code if state else None,
        status_info=state.status_info if state else None,
        created_at=connector.created_at,
        updated_at=connector.updated_at,
        deleted_at=connector.deleted_at,
    )


async def build_charging_station_response(
    db: AsyncSession, station: ChargingStationModel
) -> ChargingStationResponse:
    """Load a station's location and state, count its connectors, build its response.

    Args:
        db: Async session owned by the caller's entry boundary.
        station: Station ORM object already loaded by the caller.

    Returns:
        The station response with ``connector_count`` and
        ``available_connector_count`` filled in.

    Raises:
        ChargingLocationNotFoundError: If the station's location is missing
            (cannot happen under the foreign key).

    Side Effects:
        Runs the location and state reads plus two count queries; does not
        commit or roll back.
    """
    location = await charging_stations_repository.get_location_by_id(
        db, station.location_id, include_deleted=True
    )
    if location is None:
        raise ChargingLocationNotFoundError(
            f"Location '{station.location_id}' not found"
        )
    state = await charging_stations_repository.get_station_state(db, station.station_id)
    connector_count = await charging_stations_repository.count_connectors_by_station_id(
        db, station.station_id
    )
    available_connector_count = (
        await charging_stations_repository.count_available_connectors_by_station_id(
            db, station.station_id
        )
    )
    return to_charging_station_response(
        station,
        location,
        state,
        connector_count=connector_count,
        available_connector_count=available_connector_count,
    )


async def build_charging_connector_response(
    db: AsyncSession, connector: ChargingConnectorModel
) -> ChargingConnectorResponse:
    """Load a connector's reported state and build its response.

    Args:
        db: Async session owned by the caller's entry boundary.
        connector: Connector ORM object already loaded by the caller.

    Returns:
        The connector response.
    """
    state = await charging_stations_repository.get_connector_state(
        db, connector.connector_id
    )
    return to_charging_connector_response(connector, state)


def clean_update_values(requested_fields: Mapping[str, object]) -> dict[str, object]:
    """Drop ``None`` fields per the backend's PATCH convention.

    Args:
        requested_fields: Mapping from ``model_dump(exclude_unset=True)``.

    Returns:
        Mapping containing only the fields with values to update.

    Note:
        This domain has no dedicated contract yet for clearing a nullable
        value with ``null``; therefore ``None`` is treated as "do not
        update".
    """
    return {
        field_name: value
        for field_name, value in requested_fields.items()
        if value is not None
    }


def resolve_status_change_reason(
    update_data: dict[str, object], default_reason: str
) -> str:
    """Normalize a status change in ``update_data`` and pick the change reason.

    A status of ``ACTIVE`` clears the reason (it is only kept while out of
    service); a person-typed ``status_reason`` becomes the history reason.

    Args:
        update_data: The cleaned PATCH values; changed in place (the status
            becomes its string value, and ``status_reason`` is cleared on
            ``ACTIVE``).
        default_reason: History reason when the person typed none.

    Returns:
        The reason to record in the change history.
    """
    status = update_data.get("status")
    if isinstance(status, ChargingResourceStatus):
        update_data["status"] = status.value
        if status is ChargingResourceStatus.ACTIVE:
            update_data["status_reason"] = None
    reason = update_data.get("status_reason")
    return reason if isinstance(reason, str) else default_reason
