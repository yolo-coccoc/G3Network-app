"""Service nghiệp vụ và public contract của domain vehicles.

Module này giữ business rule của hồ sơ xe. Domain khác chỉ được gọi các hàm
`resolve_*` công khai để nhận DTO nội bộ, không nhận ORM model hoặc HTTP
response schema của domain vehicles.
"""

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.vehicles import repository as vehicle_repository
from app.domains.vehicles.exceptions import (
    VehicleConflictError,
    VehicleNotFoundError,
)
from app.domains.vehicles.models import VehicleModel
from app.domains.vehicles.schemas import (
    VehicleCreateRequest,
    VehicleListResponse,
    VehicleResponse,
    VehicleUpdateRequest,
)
from app.domains.vehicles.types import VehicleReference, VehicleStatus
from app.libs.common.config import settings


def to_vehicle_response(vehicle_record: VehicleModel) -> VehicleResponse:
    """Chuyển bản ghi ORM xe thành response của HTTP API.

    Args:
        vehicle_record: Bản ghi xe đã được repository truy vấn hoặc tạo.

    Returns:
        Dữ liệu response tương ứng với bản ghi xe.
    """
    return VehicleResponse.model_validate(vehicle_record)


def to_vehicle_reference(vehicle_record: VehicleModel) -> VehicleReference:
    """Chuyển bản ghi ORM thành DTO tối thiểu cho domain khác.

    Args:
        vehicle_record: Bản ghi xe đang hoạt động.

    Returns:
        DTO chứa ID nội bộ và VIN của xe.
    """
    return VehicleReference(
        vehicle_id=vehicle_record.vehicle_id,
        vin=vehicle_record.vin,
    )


async def resolve_vehicle_reference_by_vin(
    db_session: AsyncSession,
    vin: str,
) -> VehicleReference | None:
    """Tìm xe đang hoạt động theo VIN và trả về DTO nội bộ.

    Args:
        db_session: Phiên database do entry boundary sở hữu.
        vin: Số khung cần tra cứu.

    Returns:
        `VehicleReference` nếu tìm thấy xe; ngược lại trả về `None`.

    Side Effects:
        Chỉ thực hiện truy vấn read-only; không commit hoặc rollback.
    """
    vehicle_record = await vehicle_repository.find_by_vin(db_session, vin)
    return to_vehicle_reference(vehicle_record) if vehicle_record else None


async def resolve_vehicle_reference_by_id(
    db_session: AsyncSession,
    vehicle_id: UUID,
) -> VehicleReference | None:
    """Tìm xe đang hoạt động theo ID và trả về DTO nội bộ.

    Args:
        db_session: Phiên database do entry boundary sở hữu.
        vehicle_id: ID nội bộ của xe.

    Returns:
        `VehicleReference` nếu tìm thấy xe; ngược lại trả về `None`.

    Side Effects:
        Chỉ thực hiện truy vấn read-only; không commit hoặc rollback.
    """
    vehicle_record = await vehicle_repository.get_by_id(db_session, vehicle_id)
    return to_vehicle_reference(vehicle_record) if vehicle_record else None


async def list_active_vehicle_references(
    db_session: AsyncSession,
) -> list[VehicleReference]:
    """Trả về DTO tối thiểu của toàn bộ xe active cho domain telemetry.

    Args:
        db_session: Phiên database do entry boundary sở hữu.

    Returns:
        Danh sách ``VehicleReference`` không chứa ORM model.
    """
    vehicle_records = await vehicle_repository.list_all_active(db_session)
    return [to_vehicle_reference(record) for record in vehicle_records]


async def create_vehicle(
    db_session: AsyncSession,
    vehicle_create_request: VehicleCreateRequest,
) -> VehicleResponse:
    """Tạo xe mới sau khi kiểm tra VIN và biển số là duy nhất.

    Args:
        db_session: Phiên database do entry boundary sở hữu.
        vehicle_create_request: Dữ liệu request đã qua Pydantic validation.

    Returns:
        Response của xe vừa tạo.

    Raises:
        VehicleConflictError: Khi VIN hoặc biển số đã tồn tại.
    """
    existing_vehicle_by_plate = await vehicle_repository.find_by_license_plate(
        db_session,
        vehicle_create_request.license_plate,
    )
    if existing_vehicle_by_plate:
        raise VehicleConflictError(
            f"Vehicle with license plate "
            f"'{vehicle_create_request.license_plate}' already exists"
        )

    existing_vehicle_by_vin = await vehicle_repository.find_by_vin(
        db_session,
        vehicle_create_request.vin,
    )
    if existing_vehicle_by_vin:
        raise VehicleConflictError(
            f"Vehicle with VIN '{vehicle_create_request.vin}' already exists"
        )

    try:
        vehicle_record = await vehicle_repository.insert(
            db_session,
            vehicle_create_request.model_dump(),
        )
    except IntegrityError as error:
        raise VehicleConflictError(
            "Vehicle license plate or VIN already exists"
        ) from error

    return to_vehicle_response(vehicle_record)


async def get_vehicle(
    db_session: AsyncSession,
    vehicle_id: UUID,
) -> VehicleResponse:
    """Lấy một xe đang hoạt động theo ID.

    Args:
        db_session: Phiên database hiện tại.
        vehicle_id: ID nội bộ của xe.

    Returns:
        Response của xe.

    Raises:
        VehicleNotFoundError: Khi xe không tồn tại hoặc đã soft delete.
    """
    vehicle_record = await vehicle_repository.get_by_id(db_session, vehicle_id)
    if not vehicle_record:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    return to_vehicle_response(vehicle_record)


async def list_vehicles(
    db_session: AsyncSession,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: VehicleStatus | None = None,
) -> VehicleListResponse:
    """Lấy danh sách xe đang hoạt động có phân trang.

    Args:
        db_session: Phiên database hiện tại.
        page: Số trang, bắt đầu từ 1.
        page_size: Số xe tối đa trong một trang.
        status_filter: Bộ lọc trạng thái nếu có.

    Returns:
        Response danh sách xe có phân trang.
    """
    page = max(page, settings.API_DEFAULT_PAGE)
    if page_size < 1:
        page_size = settings.API_DEFAULT_PAGE_SIZE
    elif page_size > settings.API_MAX_PAGE_SIZE:
        page_size = settings.API_MAX_PAGE_SIZE
    skip = (page - 1) * page_size

    vehicle_records = await vehicle_repository.list_all(
        db_session,
        skip,
        page_size,
        status_filter,
    )
    total = await vehicle_repository.count(db_session, status_filter)

    return VehicleListResponse(
        items=[
            to_vehicle_response(vehicle_record) for vehicle_record in vehicle_records
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


async def update_vehicle(
    db_session: AsyncSession,
    vehicle_id: UUID,
    vehicle_update_request: VehicleUpdateRequest,
) -> VehicleResponse:
    """Cập nhật từng phần một xe sau khi kiểm tra các field unique.

    Args:
        db_session: Phiên database hiện tại.
        vehicle_id: ID nội bộ của xe.
        vehicle_update_request: Dữ liệu field cần cập nhật.

    Returns:
        Response của xe sau cập nhật.

    Raises:
        VehicleNotFoundError: Khi xe không tồn tại hoặc đã soft delete.
        VehicleConflictError: Khi VIN hoặc biển số mới đã được sử dụng.
    """
    vehicle_record = await vehicle_repository.get_by_id(db_session, vehicle_id)
    if not vehicle_record:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    if (
        vehicle_update_request.license_plate
        and vehicle_update_request.license_plate != vehicle_record.license_plate
    ):
        existing_vehicle = await vehicle_repository.find_by_license_plate(
            db_session,
            vehicle_update_request.license_plate,
        )
        if existing_vehicle:
            raise VehicleConflictError(
                f"Vehicle with license plate "
                f"'{vehicle_update_request.license_plate}' already exists"
            )

    if vehicle_update_request.vin and vehicle_update_request.vin != vehicle_record.vin:
        existing_vehicle = await vehicle_repository.find_by_vin(
            db_session,
            vehicle_update_request.vin,
        )
        if existing_vehicle:
            raise VehicleConflictError(
                f"Vehicle with VIN '{vehicle_update_request.vin}' already exists"
            )

    update_values = {
        field_name: value
        for field_name, value in vehicle_update_request.model_dump(
            exclude_unset=True
        ).items()
        if value is not None
    }
    if not update_values:
        return to_vehicle_response(vehicle_record)

    try:
        updated_vehicle_record = await vehicle_repository.update_fields(
            db_session,
            vehicle_id,
            update_values,
        )
    except IntegrityError as error:
        raise VehicleConflictError(
            "Vehicle license plate or VIN already exists"
        ) from error

    if updated_vehicle_record is None:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    return to_vehicle_response(updated_vehicle_record)


async def soft_delete_vehicle(
    db_session: AsyncSession,
    vehicle_id: UUID,
) -> dict[str, str]:
    """Soft delete một xe.

    Args:
        db_session: Phiên database hiện tại.
        vehicle_id: ID nội bộ của xe.

    Returns:
        Thông báo xoá thành công.

    Raises:
        VehicleNotFoundError: Khi xe không tồn tại hoặc đã soft delete.
    """
    vehicle_record = await vehicle_repository.soft_delete(db_session, vehicle_id)
    if not vehicle_record:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    return {"message": "Vehicle deleted successfully"}
