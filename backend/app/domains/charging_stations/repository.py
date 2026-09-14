"""Repository bất đồng bộ cho topology charging station.

Repository chỉ truy vấn và flush dữ liệu; không commit hoặc rollback transaction.
Các kiểm tra thuộc ranh giới nghiệp vụ như parent tồn tại, conflict identity và
cascade soft-delete được service điều phối bằng các hàm công khai ở đây.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.charging_stations.models import (
    ChargingConnectorModel,
    ChargingEvseModel,
    ChargingStationModel,
)


def utc_now() -> datetime:
    """Lấy thời điểm UTC dùng cho cập nhật và soft-delete.

    Returns:
        Thời điểm hiện tại có timezone UTC.
    """
    return datetime.now(timezone.utc)


async def create_charging_station(
    db: AsyncSession,
    *,
    ocpp_identity: str,
    display_name: str,
    latitude: float | None = None,
    longitude: float | None = None,
) -> ChargingStationModel:
    """Tạo station và flush để phát hiện constraint ngay trong transaction.

    Args:
        db: Async session do entry boundary sở hữu.
        ocpp_identity: OCPP identity duy nhất của station.
        display_name: Tên hiển thị.
        latitude: Vĩ độ station, nullable.
        longitude: Kinh độ station, nullable.

    Returns:
        Station vừa được persistence.
    """
    station = ChargingStationModel(
        ocpp_identity=ocpp_identity,
        display_name=display_name,
        latitude=latitude,
        longitude=longitude,
    )
    db.add(station)
    await db.flush()
    await db.refresh(station)
    return station


async def get_station_by_id(
    db: AsyncSession, station_id: UUID, *, include_deleted: bool = False
) -> ChargingStationModel | None:
    """Tìm station theo internal ID.

    Args:
        db: Async session hiện tại.
        station_id: UUID nội bộ.
        include_deleted: Có cho phép resolve record soft-delete hay không.

    Returns:
        Station phù hợp hoặc None.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingStationModel.station_id == station_id
    ]
    if not include_deleted:
        conditions.append(ChargingStationModel.deleted_at.is_(None))
    result = await db.execute(select(ChargingStationModel).where(and_(*conditions)))
    return result.scalar_one_or_none()


async def get_station_by_identity(
    db: AsyncSession, ocpp_identity: str, *, include_deleted: bool = True
) -> ChargingStationModel | None:
    """Tìm station theo OCPP identity.

    Args:
        db: Async session hiện tại.
        ocpp_identity: Business identity cần tra cứu.
        include_deleted: Có bao gồm record soft-delete hay không. Mặc định là
            ``True`` để service phát hiện identity không được tái sử dụng.

    Returns:
        Station phù hợp hoặc ``None``.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingStationModel.ocpp_identity == ocpp_identity
    ]
    if not include_deleted:
        conditions.append(ChargingStationModel.deleted_at.is_(None))
    result = await db.execute(select(ChargingStationModel).where(and_(*conditions)))
    return result.scalar_one_or_none()


async def list_charging_stations(
    db: AsyncSession,
    *,
    offset: int,
    limit: int,
) -> list[ChargingStationModel]:
    """Lấy station chưa soft-delete theo thứ tự ổn định.

    Args:
        db: Async session hiện tại.
        offset: Số record bỏ qua.
        limit: Số record tối đa trả về.

    Returns:
        Danh sách station active theo thứ tự tạo giảm dần.
    """
    conditions: list[ColumnElement[bool]] = [ChargingStationModel.deleted_at.is_(None)]
    result = await db.execute(
        select(ChargingStationModel)
        .where(and_(*conditions))
        .order_by(
            ChargingStationModel.created_at.desc(),
            ChargingStationModel.station_id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_stations(
    db: AsyncSession,
) -> int:
    """Đếm station active.

    Args:
        db: Async session hiện tại.

    Returns:
        Số station chưa soft-delete.
    """
    conditions: list[ColumnElement[bool]] = [ChargingStationModel.deleted_at.is_(None)]
    result = await db.execute(
        select(func.count(ChargingStationModel.station_id)).where(and_(*conditions))
    )
    return int(result.scalar() or 0)


async def list_station_connector_summaries(
    db: AsyncSession,
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[tuple[ChargingStationModel, int]]:
    """Lấy station cùng tổng connector active.

    Args:
        db: Async session hiện tại.
        offset: Số station bỏ qua.
        limit: Số station tối đa; ``None`` để lấy toàn bộ cho bản đồ.

    Returns:
        Tuple gồm ``(station, total_connectors)``.
    """
    total_connectors = (
        select(
            ChargingEvseModel.station_id.label("station_id"),
            func.count(ChargingConnectorModel.connector_id).label("total"),
        )
        .join(
            ChargingConnectorModel,
            ChargingConnectorModel.evse_id == ChargingEvseModel.evse_id,
        )
        .where(
            ChargingEvseModel.deleted_at.is_(None),
            ChargingConnectorModel.deleted_at.is_(None),
        )
        .group_by(ChargingEvseModel.station_id)
        .subquery()
    )
    statement = (
        select(
            ChargingStationModel,
            func.coalesce(total_connectors.c.total, 0),
        )
        .outerjoin(
            total_connectors,
            total_connectors.c.station_id == ChargingStationModel.station_id,
        )
        .where(ChargingStationModel.deleted_at.is_(None))
        .order_by(
            ChargingStationModel.created_at.desc(),
            ChargingStationModel.station_id.desc(),
        )
        .offset(offset)
    )
    if limit is not None:
        statement = statement.limit(limit)

    result = await db.execute(statement)
    return [(row[0], int(row[1])) for row in result.all()]


async def update_charging_station(
    db: AsyncSession, station_id: UUID, update_data: Mapping[str, object]
) -> ChargingStationModel | None:
    """Cập nhật station active bằng các field đã được service lọc.

    Args:
        db: Async session hiện tại.
        station_id: UUID station cần cập nhật.
        update_data: Mapping chỉ chứa field được phép update.

    Returns:
        Station sau cập nhật hoặc None nếu không còn active.

    Side Effects:
        Gán field, cập nhật ``updated_at`` và flush; không commit.
    """
    station = await get_station_by_id(db, station_id)
    if station is None:
        return None
    for field_name, value in update_data.items():
        setattr(station, field_name, value)
    station.updated_at = utc_now()
    await db.flush()
    await db.refresh(station)
    return station


async def soft_delete_station(db: AsyncSession, station_id: UUID) -> bool:
    """Soft-delete station và toàn bộ EVSE/connector active thuộc station.

    Cascade chỉ cập nhật ``deleted_at``; không physical-delete history hoặc
    topology record để giữ business identity và foreign key cho audit.

    Args:
        db: Async session hiện tại.
        station_id: UUID station cần xoá mềm.

    Returns:
        True nếu station active tồn tại và đã được đánh dấu; False nếu không có.
    """
    station = await get_station_by_id(db, station_id)
    if station is None:
        return False

    now = utc_now()
    evse_result = await db.execute(
        select(ChargingEvseModel.evse_id).where(
            ChargingEvseModel.station_id == station_id,
            ChargingEvseModel.deleted_at.is_(None),
        )
    )
    evse_ids = list(evse_result.scalars().all())
    if evse_ids:
        # Cập nhật connector trước EVSE để giữ topology con nhất quán trong
        # cùng transaction, kể cả khi caller rollback ở entry boundary.
        await db.execute(
            update(ChargingConnectorModel)
            .where(
                ChargingConnectorModel.evse_id.in_(evse_ids),
                ChargingConnectorModel.deleted_at.is_(None),
            )
            .values(deleted_at=now, updated_at=now)
        )
        await db.execute(
            update(ChargingEvseModel)
            .where(ChargingEvseModel.evse_id.in_(evse_ids))
            .values(deleted_at=now, updated_at=now)
        )
    station.deleted_at = now
    station.updated_at = now
    await db.flush()
    return True


async def create_charging_evse(
    db: AsyncSession,
    *,
    station_id: UUID,
    ocpp_evse_id: int,
) -> ChargingEvseModel:
    """Tạo EVSE và flush constraint/FK trong transaction hiện tại.

    Args:
        db: Async session hiện tại.
        station_id: UUID station parent.
        ocpp_evse_id: Identity EVSE dương trong station.

    Returns:
        EVSE ORM vừa persist.

    Side Effects:
        Thêm record, flush và refresh generated values; không commit.
    """
    evse = ChargingEvseModel(
        station_id=station_id,
        ocpp_evse_id=ocpp_evse_id,
    )
    db.add(evse)
    await db.flush()
    await db.refresh(evse)
    return evse


async def get_evse_by_id(
    db: AsyncSession, evse_id: UUID, *, include_deleted: bool = False
) -> ChargingEvseModel | None:
    """Tìm EVSE theo internal ID.

    Args:
        db: Async session hiện tại.
        evse_id: UUID EVSE cần truy vấn.
        include_deleted: Có bao gồm record soft-delete hay không.

    Returns:
        EVSE phù hợp hoặc ``None``.
    """
    conditions: list[ColumnElement[bool]] = [ChargingEvseModel.evse_id == evse_id]
    if not include_deleted:
        conditions.append(ChargingEvseModel.deleted_at.is_(None))
    result = await db.execute(select(ChargingEvseModel).where(and_(*conditions)))
    return result.scalar_one_or_none()


async def get_evse_by_identity(
    db: AsyncSession,
    station_id: UUID,
    ocpp_evse_id: int,
    *,
    include_deleted: bool = True,
) -> ChargingEvseModel | None:
    """Tìm EVSE theo identity composite station/OCPP ID.

    Args:
        db: Async session hiện tại.
        station_id: UUID station parent.
        ocpp_evse_id: Identity EVSE trong station.
        include_deleted: Có bao gồm identity đã soft-delete hay không.

    Returns:
        EVSE phù hợp hoặc ``None``.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingEvseModel.station_id == station_id,
        ChargingEvseModel.ocpp_evse_id == ocpp_evse_id,
    ]
    if not include_deleted:
        conditions.append(ChargingEvseModel.deleted_at.is_(None))
    result = await db.execute(select(ChargingEvseModel).where(and_(*conditions)))
    return result.scalar_one_or_none()


async def list_charging_evses(
    db: AsyncSession, *, station_id: UUID, offset: int, limit: int
) -> list[ChargingEvseModel]:
    """Lấy EVSE active của một station theo thứ tự ổn định.

    Args:
        db: Async session hiện tại.
        station_id: UUID station parent.
        offset: Số record bỏ qua.
        limit: Số record tối đa trả về.

    Returns:
        Danh sách EVSE active.
    """
    result = await db.execute(
        select(ChargingEvseModel)
        .where(
            ChargingEvseModel.station_id == station_id,
            ChargingEvseModel.deleted_at.is_(None),
        )
        .order_by(ChargingEvseModel.created_at.asc(), ChargingEvseModel.evse_id.asc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_evses(db: AsyncSession, *, station_id: UUID) -> int:
    """Đếm EVSE active thuộc một station.

    Args:
        db: Async session hiện tại.
        station_id: UUID station parent.

    Returns:
        Số EVSE active.
    """
    result = await db.execute(
        select(func.count(ChargingEvseModel.evse_id)).where(
            ChargingEvseModel.station_id == station_id,
            ChargingEvseModel.deleted_at.is_(None),
        )
    )
    return int(result.scalar() or 0)


async def update_charging_evse(
    db: AsyncSession, evse_id: UUID, update_data: Mapping[str, object]
) -> ChargingEvseModel | None:
    """Cập nhật EVSE active bằng field đã được service kiểm tra.

    Args:
        db: Async session hiện tại.
        evse_id: UUID EVSE cần cập nhật.
        update_data: Mapping field đã qua kiểm tra nghiệp vụ.

    Returns:
        EVSE sau cập nhật hoặc ``None`` nếu không còn active.

    Side Effects:
        Gán field, cập nhật timestamp, flush và refresh; không commit.
    """
    evse = await get_evse_by_id(db, evse_id)
    if evse is None:
        return None
    for field_name, value in update_data.items():
        setattr(evse, field_name, value)
    evse.updated_at = utc_now()
    await db.flush()
    await db.refresh(evse)
    return evse


async def soft_delete_evse(db: AsyncSession, evse_id: UUID) -> bool:
    """Soft-delete EVSE và connector active thuộc EVSE đó.

    Args:
        db: Async session hiện tại.
        evse_id: UUID EVSE cần xoá mềm.

    Returns:
        ``True`` nếu EVSE active tồn tại; ``False`` nếu không tìm thấy.

    Side Effects:
        Cập nhật timestamp của EVSE và connector con rồi flush; không commit.
    """
    evse = await get_evse_by_id(db, evse_id)
    if evse is None:
        return False
    now = utc_now()
    await db.execute(
        update(ChargingConnectorModel)
        .where(
            ChargingConnectorModel.evse_id == evse_id,
            ChargingConnectorModel.deleted_at.is_(None),
        )
        .values(deleted_at=now, updated_at=now)
    )
    evse.deleted_at = now
    evse.updated_at = now
    await db.flush()
    return True


async def create_charging_connector(
    db: AsyncSession,
    *,
    evse_id: UUID,
    ocpp_connector_id: int,
) -> ChargingConnectorModel:
    """Tạo connector và flush constraint/FK trong transaction hiện tại.

    Args:
        db: Async session hiện tại.
        evse_id: UUID EVSE parent.
        ocpp_connector_id: Identity connector dương trong EVSE.

    Returns:
        Connector ORM vừa persist.

    Side Effects:
        Thêm record, flush và refresh generated values; không commit.
    """
    connector = ChargingConnectorModel(
        evse_id=evse_id,
        ocpp_connector_id=ocpp_connector_id,
    )
    db.add(connector)
    await db.flush()
    await db.refresh(connector)
    return connector


async def get_connector_by_id(
    db: AsyncSession, connector_id: UUID, *, include_deleted: bool = False
) -> ChargingConnectorModel | None:
    """Tìm connector theo internal ID.

    Args:
        db: Async session hiện tại.
        connector_id: UUID connector cần truy vấn.
        include_deleted: Có bao gồm record soft-delete hay không.

    Returns:
        Connector phù hợp hoặc ``None``.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingConnectorModel.connector_id == connector_id
    ]
    if not include_deleted:
        conditions.append(ChargingConnectorModel.deleted_at.is_(None))
    result = await db.execute(select(ChargingConnectorModel).where(and_(*conditions)))
    return result.scalar_one_or_none()


async def get_connector_by_identity(
    db: AsyncSession,
    evse_id: UUID,
    ocpp_connector_id: int,
    *,
    include_deleted: bool = True,
) -> ChargingConnectorModel | None:
    """Tìm connector theo identity composite EVSE/OCPP ID.

    Args:
        db: Async session hiện tại.
        evse_id: UUID EVSE parent.
        ocpp_connector_id: Identity connector trong EVSE.
        include_deleted: Có bao gồm identity đã soft-delete hay không.

    Returns:
        Connector phù hợp hoặc ``None``.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingConnectorModel.evse_id == evse_id,
        ChargingConnectorModel.ocpp_connector_id == ocpp_connector_id,
    ]
    if not include_deleted:
        conditions.append(ChargingConnectorModel.deleted_at.is_(None))
    result = await db.execute(select(ChargingConnectorModel).where(and_(*conditions)))
    return result.scalar_one_or_none()


async def list_charging_connectors(
    db: AsyncSession, *, evse_id: UUID, offset: int, limit: int
) -> list[ChargingConnectorModel]:
    """Lấy connector active của một EVSE theo thứ tự ổn định.

    Args:
        db: Async session hiện tại.
        evse_id: UUID EVSE parent.
        offset: Số record bỏ qua.
        limit: Số record tối đa trả về.

    Returns:
        Danh sách connector active.
    """
    result = await db.execute(
        select(ChargingConnectorModel)
        .where(
            ChargingConnectorModel.evse_id == evse_id,
            ChargingConnectorModel.deleted_at.is_(None),
        )
        .order_by(
            ChargingConnectorModel.created_at.asc(),
            ChargingConnectorModel.connector_id.asc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_connectors(db: AsyncSession, *, evse_id: UUID) -> int:
    """Đếm connector active thuộc một EVSE.

    Args:
        db: Async session hiện tại.
        evse_id: UUID EVSE parent.

    Returns:
        Số connector active.
    """
    result = await db.execute(
        select(func.count(ChargingConnectorModel.connector_id)).where(
            ChargingConnectorModel.evse_id == evse_id,
            ChargingConnectorModel.deleted_at.is_(None),
        )
    )
    return int(result.scalar() or 0)


async def update_charging_connector(
    db: AsyncSession, connector_id: UUID, update_data: Mapping[str, object]
) -> ChargingConnectorModel | None:
    """Cập nhật connector active bằng field đã được service kiểm tra.

    Args:
        db: Async session hiện tại.
        connector_id: UUID connector cần cập nhật.
        update_data: Mapping field đã qua kiểm tra nghiệp vụ.

    Returns:
        Connector sau cập nhật hoặc ``None`` nếu không còn active.

    Side Effects:
        Gán field, cập nhật timestamp, flush và refresh; không commit.
    """
    connector = await get_connector_by_id(db, connector_id)
    if connector is None:
        return None
    for field_name, value in update_data.items():
        setattr(connector, field_name, value)
    connector.updated_at = utc_now()
    await db.flush()
    await db.refresh(connector)
    return connector


async def soft_delete_connector(db: AsyncSession, connector_id: UUID) -> bool:
    """Đánh dấu connector đã xoá mềm mà không physical-delete record.

    Args:
        db: Async session hiện tại.
        connector_id: UUID connector cần xoá mềm.

    Returns:
        ``True`` nếu connector active tồn tại; ``False`` nếu không tìm thấy.

    Side Effects:
        Cập nhật ``deleted_at`` và ``updated_at`` rồi flush; không commit.
    """
    connector = await get_connector_by_id(db, connector_id)
    if connector is None:
        return False
    connector.deleted_at = utc_now()
    connector.updated_at = utc_now()
    await db.flush()
    return True
