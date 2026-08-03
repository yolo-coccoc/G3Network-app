"""Business service cho CRUD và soft-delete topology charging.

Service là nơi giữ các invariant pre-provision: station phải tồn tại trước EVSE,
EVSE phải thuộc station trước connector, identity topology không được tái sử
dụng kể cả khi record cũ đã soft-delete, và OCPP không được tự tạo topology.
Transaction do FastAPI ``get_db`` sở hữu; module này không commit/rollback.
"""

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.charging_stations import repository
from app.domains.charging_stations.exceptions import (
    ChargingConnectorNotFoundError,
    ChargingEvseNotFoundError,
    ChargingStationNotFoundError,
    ChargingTopologyConflictError,
)
from app.domains.charging_stations.models import (
    ChargingConnector,
    ChargingEvse,
    ChargingStation,
)
from app.domains.charging_stations.schemas import (
    ConnectorCreate,
    ConnectorListResponse,
    ConnectorResponse,
    ConnectorUpdate,
    DeleteResponse,
    EvseCreate,
    EvseListResponse,
    EvseResponse,
    EvseUpdate,
    StationCreate,
    StationListResponse,
    StationResponse,
    StationUpdate,
)
from app.libs.common.config import settings


def _station_response(station: ChargingStation) -> StationResponse:
    """Dựng response station từ topology model tối thiểu.

    Args:
        station: ORM station đã được repository truy vấn hoặc tạo.

    Returns:
        Schema response không chứa technical metadata.
    """
    return StationResponse(
        station_id=station.station_id,
        ocpp_identity=station.ocpp_identity,
        display_name=station.display_name,
        created_at=station.created_at,
        updated_at=station.updated_at,
        deleted_at=station.deleted_at,
    )


def _evse_response(evse: ChargingEvse) -> EvseResponse:
    """Dựng response EVSE từ ORM model.

    Args:
        evse: ORM EVSE đã được repository truy vấn hoặc tạo.

    Returns:
        Schema response tương ứng với EVSE.
    """
    return EvseResponse.model_validate(evse)


def _connector_response(connector: ChargingConnector) -> ConnectorResponse:
    """Dựng response connector từ ORM model.

    Args:
        connector: ORM connector đã được repository truy vấn hoặc tạo.

    Returns:
        Schema response tương ứng với connector.
    """
    return ConnectorResponse.model_validate(connector)


def _clean_update_data(data: Mapping[str, object]) -> dict[str, object]:
    """Loại field ``None`` theo convention PATCH của backend.

    Args:
        data: Mapping từ ``model_dump(exclude_unset=True)``.

    Returns:
        Mapping chỉ còn field có giá trị cần cập nhật.

    Note:
        Domain này chưa có contract riêng cho việc xóa giá trị nullable bằng
        ``null``; vì vậy ``None`` được hiểu là không cập nhật.
    """
    return {
        field_name: value for field_name, value in data.items() if value is not None
    }


async def create_station(
    db: AsyncSession, station_data: StationCreate
) -> StationResponse:
    """Tạo station mới sau khi kiểm tra OCPP identity toàn bảng.

    Args:
        db: Async session do HTTP boundary sở hữu.
        station_data: Dữ liệu station đã qua Pydantic validation.

    Returns:
        Station response vừa tạo.

    Raises:
        ChargingTopologyConflictError: Nếu identity đã tồn tại, kể cả soft-delete.
    """
    if await repository.get_station_by_identity(db, station_data.ocpp_identity):
        raise ChargingTopologyConflictError(
            f"OCPP identity '{station_data.ocpp_identity}' đã tồn tại"
        )
    try:
        station = await repository.create_station(
            db,
            ocpp_identity=station_data.ocpp_identity,
            display_name=station_data.display_name,
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "OCPP identity của station đã tồn tại"
        ) from error
    return _station_response(station)


async def list_stations(
    db: AsyncSession,
    *,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> StationListResponse:
    """Liệt kê station active với pagination giới hạn theo settings.

    Args:
        db: Async session do HTTP boundary sở hữu.
        page: Trang bắt đầu từ một; giá trị thấp hơn được clamp về mặc định.
        page_size: Kích thước trang được clamp theo settings.

    Returns:
        Danh sách station và metadata phân trang.

    Side Effects:
        Thực hiện hai truy vấn đọc; không commit hoặc rollback.
    """
    page = max(page, settings.API_DEFAULT_PAGE)
    page_size = min(
        max(page_size, settings.API_DEFAULT_PAGE_SIZE), settings.API_MAX_PAGE_SIZE
    )
    offset = (page - 1) * page_size
    stations = await repository.list_stations(
        db,
        offset=offset,
        limit=page_size,
    )
    total = await repository.count_stations(db)
    return StationListResponse(
        items=[_station_response(station) for station in stations],
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_station(db: AsyncSession, station_id: UUID) -> StationResponse:
    """Lấy station active theo internal UUID.

    Args:
        db: Async session do HTTP boundary sở hữu.
        station_id: UUID station cần truy vấn.

    Returns:
        Response station active.

    Raises:
        ChargingStationNotFoundError: Nếu station không tồn tại hoặc đã xoá mềm.
    """
    station = await repository.get_station_by_id(db, station_id)
    if station is None:
        raise ChargingStationNotFoundError(f"Không tìm thấy station '{station_id}'")
    return _station_response(station)


async def update_station(
    db: AsyncSession, station_id: UUID, station_data: StationUpdate
) -> StationResponse:
    """PATCH station và kiểm tra identity conflict trước khi flush.

    Args:
        db: Async session do HTTP boundary sở hữu.
        station_id: UUID station cần cập nhật.
        station_data: Các field PATCH đã qua Pydantic validation.

    Returns:
        Response station sau cập nhật.

    Raises:
        ChargingStationNotFoundError: Nếu station không tồn tại hoặc đã xoá.
        ChargingTopologyConflictError: Nếu identity mới đã được sử dụng.
    """
    station = await repository.get_station_by_id(db, station_id)
    if station is None:
        raise ChargingStationNotFoundError(f"Không tìm thấy station '{station_id}'")
    if (
        station_data.ocpp_identity is not None
        and station_data.ocpp_identity != station.ocpp_identity
        and await repository.get_station_by_identity(db, station_data.ocpp_identity)
    ):
        raise ChargingTopologyConflictError(
            f"OCPP identity '{station_data.ocpp_identity}' đã tồn tại"
        )

    update_data = _clean_update_data(station_data.model_dump(exclude_unset=True))
    if not update_data:
        return _station_response(station)
    try:
        updated = await repository.update_station(db, station_id, update_data)
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "OCPP identity của station đã tồn tại"
        ) from error
    if updated is None:
        raise ChargingStationNotFoundError(f"Không tìm thấy station '{station_id}'")
    return _station_response(updated)


async def delete_station(db: AsyncSession, station_id: UUID) -> DeleteResponse:
    """Soft-delete station và topology con trong cùng transaction.

    Args:
        db: Async session do HTTP boundary sở hữu.
        station_id: UUID station cần xoá mềm.

    Returns:
        Thông báo soft-delete thành công.

    Raises:
        ChargingStationNotFoundError: Nếu station không tồn tại hoặc đã xoá.

    Side Effects:
        Đánh dấu station, EVSE và connector con bằng ``deleted_at``; không
        physical-delete record và không tự commit.
    """
    if not await repository.soft_delete_station(db, station_id):
        raise ChargingStationNotFoundError(f"Không tìm thấy station '{station_id}'")
    return DeleteResponse(message="Đã xoá mềm charging station")


async def create_evse(
    db: AsyncSession, station_id: UUID, evse_data: EvseCreate
) -> EvseResponse:
    """Tạo EVSE chỉ khi station parent active và identity chưa dùng.

    Args:
        db: Async session do HTTP boundary sở hữu.
        station_id: UUID station parent.
        evse_data: Identity EVSE đã qua validation.

    Returns:
        Response EVSE vừa tạo.

    Raises:
        ChargingStationNotFoundError: Nếu station parent không active.
        ChargingTopologyConflictError: Nếu identity EVSE đã tồn tại.
    """
    if await repository.get_station_by_id(db, station_id) is None:
        raise ChargingStationNotFoundError(f"Không tìm thấy station '{station_id}'")
    if await repository.get_evse_by_identity(db, station_id, evse_data.ocpp_evse_id):
        raise ChargingTopologyConflictError(
            f"EVSE ID '{evse_data.ocpp_evse_id}' đã tồn tại trong station"
        )
    try:
        evse = await repository.create_evse(
            db,
            station_id=station_id,
            ocpp_evse_id=evse_data.ocpp_evse_id,
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "EVSE identity đã tồn tại trong station"
        ) from error
    return _evse_response(evse)


async def list_evses(
    db: AsyncSession,
    station_id: UUID,
    *,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> EvseListResponse:
    """Liệt kê EVSE active thuộc station parent.

    Args:
        db: Async session do HTTP boundary sở hữu.
        station_id: UUID station parent.
        page: Trang bắt đầu từ một.
        page_size: Kích thước trang bị giới hạn bởi settings.

    Returns:
        Danh sách EVSE và metadata phân trang.

    Raises:
        ChargingStationNotFoundError: Nếu station parent không active.
    """
    if await repository.get_station_by_id(db, station_id) is None:
        raise ChargingStationNotFoundError(f"Không tìm thấy station '{station_id}'")
    page = max(page, settings.API_DEFAULT_PAGE)
    page_size = min(
        max(page_size, settings.API_DEFAULT_PAGE_SIZE), settings.API_MAX_PAGE_SIZE
    )
    evses = await repository.list_evses(
        db, station_id=station_id, offset=(page - 1) * page_size, limit=page_size
    )
    total = await repository.count_evses(db, station_id=station_id)
    return EvseListResponse(
        items=[_evse_response(evse) for evse in evses],
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_evse(db: AsyncSession, evse_id: UUID) -> EvseResponse:
    """Lấy EVSE active theo internal UUID.

    Args:
        db: Async session do HTTP boundary sở hữu.
        evse_id: UUID EVSE cần truy vấn.

    Returns:
        Response EVSE active.

    Raises:
        ChargingEvseNotFoundError: Nếu EVSE không tồn tại hoặc đã xoá.
    """
    evse = await repository.get_evse_by_id(db, evse_id)
    if evse is None:
        raise ChargingEvseNotFoundError(f"Không tìm thấy EVSE '{evse_id}'")
    return _evse_response(evse)


async def update_evse(
    db: AsyncSession, evse_id: UUID, evse_data: EvseUpdate
) -> EvseResponse:
    """PATCH EVSE và giữ unique identity trong parent station.

    Args:
        db: Async session do HTTP boundary sở hữu.
        evse_id: UUID EVSE cần cập nhật.
        evse_data: Các field PATCH đã qua validation.

    Returns:
        Response EVSE sau cập nhật.

    Raises:
        ChargingEvseNotFoundError: Nếu EVSE không active.
        ChargingTopologyConflictError: Nếu identity mới trùng trong station.
    """
    evse = await repository.get_evse_by_id(db, evse_id)
    if evse is None:
        raise ChargingEvseNotFoundError(f"Không tìm thấy EVSE '{evse_id}'")
    if (
        evse_data.ocpp_evse_id is not None
        and evse_data.ocpp_evse_id != evse.ocpp_evse_id
        and await repository.get_evse_by_identity(
            db, evse.station_id, evse_data.ocpp_evse_id
        )
    ):
        raise ChargingTopologyConflictError(
            f"EVSE ID '{evse_data.ocpp_evse_id}' đã tồn tại trong station"
        )
    update_data = _clean_update_data(evse_data.model_dump(exclude_unset=True))
    if not update_data:
        return _evse_response(evse)
    try:
        updated = await repository.update_evse(db, evse_id, update_data)
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "EVSE identity đã tồn tại trong station"
        ) from error
    if updated is None:
        raise ChargingEvseNotFoundError(f"Không tìm thấy EVSE '{evse_id}'")
    return _evse_response(updated)


async def delete_evse(db: AsyncSession, evse_id: UUID) -> DeleteResponse:
    """Soft-delete EVSE và connector con.

    Args:
        db: Async session do HTTP boundary sở hữu.
        evse_id: UUID EVSE cần xoá mềm.

    Returns:
        Thông báo soft-delete thành công.

    Raises:
        ChargingEvseNotFoundError: Nếu EVSE không active.

    Side Effects:
        Đánh dấu EVSE và connector con, không physical-delete và không commit.
    """
    if not await repository.soft_delete_evse(db, evse_id):
        raise ChargingEvseNotFoundError(f"Không tìm thấy EVSE '{evse_id}'")
    return DeleteResponse(message="Đã xoá mềm EVSE")


async def create_connector(
    db: AsyncSession, evse_id: UUID, connector_data: ConnectorCreate
) -> ConnectorResponse:
    """Tạo connector chỉ khi EVSE parent active và identity chưa dùng.

    Args:
        db: Async session do HTTP boundary sở hữu.
        evse_id: UUID EVSE parent.
        connector_data: Identity connector đã qua validation.

    Returns:
        Response connector vừa tạo.

    Raises:
        ChargingEvseNotFoundError: Nếu EVSE parent không active.
        ChargingTopologyConflictError: Nếu identity connector đã tồn tại.
    """
    if await repository.get_evse_by_id(db, evse_id) is None:
        raise ChargingEvseNotFoundError(f"Không tìm thấy EVSE '{evse_id}'")
    if await repository.get_connector_by_identity(
        db, evse_id, connector_data.ocpp_connector_id
    ):
        raise ChargingTopologyConflictError(
            f"Connector ID '{connector_data.ocpp_connector_id}' đã tồn tại trong EVSE"
        )
    try:
        connector = await repository.create_connector(
            db,
            evse_id=evse_id,
            ocpp_connector_id=connector_data.ocpp_connector_id,
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "Connector identity đã tồn tại trong EVSE"
        ) from error
    return _connector_response(connector)


async def list_connectors(
    db: AsyncSession,
    evse_id: UUID,
    *,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> ConnectorListResponse:
    """Liệt kê connector active thuộc EVSE parent.

    Args:
        db: Async session do HTTP boundary sở hữu.
        evse_id: UUID EVSE parent.
        page: Trang bắt đầu từ một.
        page_size: Kích thước trang bị giới hạn bởi settings.

    Returns:
        Danh sách connector và metadata phân trang.

    Raises:
        ChargingEvseNotFoundError: Nếu EVSE parent không active.
    """
    if await repository.get_evse_by_id(db, evse_id) is None:
        raise ChargingEvseNotFoundError(f"Không tìm thấy EVSE '{evse_id}'")
    page = max(page, settings.API_DEFAULT_PAGE)
    page_size = min(
        max(page_size, settings.API_DEFAULT_PAGE_SIZE), settings.API_MAX_PAGE_SIZE
    )
    connectors = await repository.list_connectors(
        db, evse_id=evse_id, offset=(page - 1) * page_size, limit=page_size
    )
    total = await repository.count_connectors(db, evse_id=evse_id)
    return ConnectorListResponse(
        items=[_connector_response(connector) for connector in connectors],
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_connector(db: AsyncSession, connector_id: UUID) -> ConnectorResponse:
    """Lấy connector active theo internal UUID.

    Args:
        db: Async session do HTTP boundary sở hữu.
        connector_id: UUID connector cần truy vấn.

    Returns:
        Response connector active.

    Raises:
        ChargingConnectorNotFoundError: Nếu connector không tồn tại hoặc đã xoá.
    """
    connector = await repository.get_connector_by_id(db, connector_id)
    if connector is None:
        raise ChargingConnectorNotFoundError(
            f"Không tìm thấy connector '{connector_id}'"
        )
    return _connector_response(connector)


async def update_connector(
    db: AsyncSession, connector_id: UUID, connector_data: ConnectorUpdate
) -> ConnectorResponse:
    """PATCH connector và giữ unique identity trong parent EVSE.

    Args:
        db: Async session do HTTP boundary sở hữu.
        connector_id: UUID connector cần cập nhật.
        connector_data: Các field PATCH đã qua validation.

    Returns:
        Response connector sau cập nhật.

    Raises:
        ChargingConnectorNotFoundError: Nếu connector không active.
        ChargingTopologyConflictError: Nếu identity mới trùng trong EVSE.
    """
    connector = await repository.get_connector_by_id(db, connector_id)
    if connector is None:
        raise ChargingConnectorNotFoundError(
            f"Không tìm thấy connector '{connector_id}'"
        )
    if (
        connector_data.ocpp_connector_id is not None
        and connector_data.ocpp_connector_id != connector.ocpp_connector_id
        and await repository.get_connector_by_identity(
            db, connector.evse_id, connector_data.ocpp_connector_id
        )
    ):
        raise ChargingTopologyConflictError(
            f"Connector ID '{connector_data.ocpp_connector_id}' đã tồn tại trong EVSE"
        )
    update_data = _clean_update_data(connector_data.model_dump(exclude_unset=True))
    if not update_data:
        return _connector_response(connector)
    try:
        updated = await repository.update_connector(db, connector_id, update_data)
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "Connector identity đã tồn tại trong EVSE"
        ) from error
    if updated is None:
        raise ChargingConnectorNotFoundError(
            f"Không tìm thấy connector '{connector_id}'"
        )
    return _connector_response(updated)


async def delete_connector(db: AsyncSession, connector_id: UUID) -> DeleteResponse:
    """Soft-delete connector.

    Args:
        db: Async session do HTTP boundary sở hữu.
        connector_id: UUID connector cần xoá mềm.

    Returns:
        Thông báo soft-delete thành công.

    Raises:
        ChargingConnectorNotFoundError: Nếu connector không active.

    Side Effects:
        Đánh dấu ``deleted_at`` trong transaction hiện tại; không tự commit.
    """
    if not await repository.soft_delete_connector(db, connector_id):
        raise ChargingConnectorNotFoundError(
            f"Không tìm thấy connector '{connector_id}'"
        )
    return DeleteResponse(message="Đã xoá mềm connector")
