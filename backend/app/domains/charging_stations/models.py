"""SQLAlchemy models cho topology và technical history của trạm sạc.

Module này chỉ mô tả persistence contract của bounded context
``charging_stations``. Business rule, OCPP adapter và transaction boundary được
đặt ở các module service/entrypoint tương ứng khi triển khai các bước sau.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from geoalchemy2 import Geography
from sqlalchemy import (
    CheckConstraint,
    DateTime,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.charging_stations.types import (
    EvseAdministrativeStatus,
    StationAdministrativeStatus,
    StationConnectionStatus,
    TechnicalStatus,
)
from app.libs.db.base import Base


def utc_now() -> datetime:
    """Trả về thời điểm hiện tại với timezone UTC."""
    return datetime.now(timezone.utc)


def enum_values(enum_type: type[object]) -> list[str]:
    """Lấy value của enum để PostgreSQL lưu contract value thay vì member name.

    Args:
        enum_type: Enum class được SQLAlchemy truyền vào khi khởi tạo type.

    Returns:
        Danh sách string value theo thứ tự khai báo của enum.
    """
    return [member.value for member in enum_type]  # type: ignore[attr-defined]


class ChargingStation(Base):
    """Hồ sơ vật lý và snapshot kết nối của một charging station.

    Attributes:
        station_id: UUID nội bộ dùng trong các boundary của backend.
        ocpp_identity: Identity xuất hiện trong OCPP WebSocket path.
        display_name: Tên hiển thị do quản trị viên đặt.
        manufacturer: Nhà sản xuất, nullable.
        model: Model thiết bị, nullable.
        serial_number: Serial vật lý, nullable.
        firmware_version: Firmware gần nhất, nullable.
        location: Vị trí PostGIS geography Point WGS84, nullable.
        administrative_status: Trạng thái quản trị.
        connection_status: Snapshot trạng thái kết nối.
        last_seen_at: Lần cuối nhận message từ station.
        last_boot_at: Lần cuối nhận BootNotification.
        created_at: Thời điểm tạo record.
        updated_at: Thời điểm cập nhật record.
        deleted_at: Thời điểm soft-delete, nullable.
    """

    __tablename__ = "charging_stations"

    station_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    ocpp_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True
    )
    administrative_status: Mapped[StationAdministrativeStatus] = mapped_column(
        SQLEnum(
            StationAdministrativeStatus,
            name="chargingstationadministrativestatus",
            values_callable=enum_values,
        ),
        nullable=False,
        default=StationAdministrativeStatus.ACTIVE,
    )
    connection_status: Mapped[StationConnectionStatus] = mapped_column(
        SQLEnum(
            StationConnectionStatus,
            name="chargingstationconnectionstatus",
            values_callable=enum_values,
        ),
        nullable=False,
        default=StationConnectionStatus.UNKNOWN,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_boot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("ocpp_identity", name="uq_charging_stations_ocpp_identity"),
        Index(
            "ix_charging_stations_admin_deleted",
            "administrative_status",
            "deleted_at",
        ),
        Index(
            "ix_charging_stations_connection_last_seen",
            "connection_status",
            desc("last_seen_at"),
        ),
        Index("ix_charging_stations_deleted_at", "deleted_at"),
        Index(
            "ix_charging_stations_location_gist",
            "location",
            postgresql_using="gist",
        ),
    )


class ChargingEvse(Base):
    """EVSE thuộc một station đã được pre-provision.

    Attributes:
        evse_id: UUID nội bộ của EVSE.
        station_id: UUID station sở hữu EVSE.
        ocpp_evse_id: ID EVSE do OCPP sử dụng, dương.
        display_name: Tên hiển thị, nullable.
        administrative_status: Trạng thái quản trị.
        technical_status: Trạng thái kỹ thuật chuẩn hóa.
        capabilities: Capability JSON đã chuẩn hóa, không chứa credential.
        last_status_at: Thời điểm status kỹ thuật gần nhất.
        created_at: Thời điểm tạo record.
        updated_at: Thời điểm cập nhật record.
        deleted_at: Thời điểm soft-delete, nullable.
    """

    __tablename__ = "charging_evses"

    evse_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    station_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_stations.station_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ocpp_evse_id: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    administrative_status: Mapped[EvseAdministrativeStatus] = mapped_column(
        SQLEnum(
            EvseAdministrativeStatus,
            name="chargingevseadministrativestatus",
            values_callable=enum_values,
        ),
        nullable=False,
        default=EvseAdministrativeStatus.ACTIVE,
    )
    technical_status: Mapped[TechnicalStatus] = mapped_column(
        SQLEnum(
            TechnicalStatus, name="chargingtechnicalstatus", values_callable=enum_values
        ),
        nullable=False,
        default=TechnicalStatus.UNKNOWN,
    )
    capabilities: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    last_status_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("ocpp_evse_id > 0", name="ck_charging_evses_ocpp_id_positive"),
        UniqueConstraint(
            "station_id", "ocpp_evse_id", name="uq_charging_evses_station_ocpp_id"
        ),
        Index("ix_charging_evses_station_deleted", "station_id", "deleted_at"),
        Index(
            "ix_charging_evses_technical_station",
            "technical_status",
            "station_id",
        ),
    )


class ChargingConnector(Base):
    """Connector vật lý thuộc một EVSE.

    Attributes:
        connector_id: UUID nội bộ của connector.
        evse_id: UUID EVSE sở hữu connector.
        ocpp_connector_id: ID connector do OCPP sử dụng, dương.
        connector_type: Loại connector, nullable khi chưa có dữ liệu thực tế.
        max_power_kw: Công suất tối đa theo kW, nullable và dương nếu có.
        administrative_status: Trạng thái quản trị.
        technical_status: Trạng thái kỹ thuật chuẩn hóa.
        capabilities: Capability JSON đã chuẩn hóa, không chứa credential.
        last_status_at: Thời điểm status kỹ thuật gần nhất.
        created_at: Thời điểm tạo record.
        updated_at: Thời điểm cập nhật record.
        deleted_at: Thời điểm soft-delete, nullable.
    """

    __tablename__ = "charging_connectors"

    connector_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    evse_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_evses.evse_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ocpp_connector_id: Mapped[int] = mapped_column(Integer, nullable=False)
    connector_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    max_power_kw: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    administrative_status: Mapped[EvseAdministrativeStatus] = mapped_column(
        SQLEnum(
            EvseAdministrativeStatus,
            name="chargingconnectoradministrativestatus",
            values_callable=enum_values,
        ),
        nullable=False,
        default=EvseAdministrativeStatus.ACTIVE,
    )
    technical_status: Mapped[TechnicalStatus] = mapped_column(
        SQLEnum(
            TechnicalStatus, name="chargingtechnicalstatus", values_callable=enum_values
        ),
        nullable=False,
        default=TechnicalStatus.UNKNOWN,
    )
    capabilities: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    last_status_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "ocpp_connector_id > 0", name="ck_charging_connectors_ocpp_id_positive"
        ),
        CheckConstraint(
            "max_power_kw IS NULL OR max_power_kw > 0",
            name="ck_charging_connectors_max_power_positive",
        ),
        UniqueConstraint(
            "evse_id",
            "ocpp_connector_id",
            name="uq_charging_connectors_evse_ocpp_id",
        ),
        Index("ix_charging_connectors_evse_deleted", "evse_id", "deleted_at"),
        Index("ix_charging_connectors_technical_evse", "technical_status", "evse_id"),
    )


# Legacy technical-status history intentionally disabled for the ideal MVP.
# The old source is kept as a comment because station/EVSE/connector are assumed
# always online and active. Re-enable the class only with future.md item 27.
# class ChargingStationStatusEvent(Base):
#     __tablename__ = "charging_station_status_events"
#     # recorded_at, topology IDs, source_action, status/event_code,
#     # idempotency_key, received_at and sanitized_raw_payload belonged to the
#     # old heartbeat/status/reconnect path.

# ---------------------------------------------------------------------------
# LEGACY COMPONENT (COMMENTED OUT FOR THE IDEAL MVP)
# ---------------------------------------------------------------------------
# class ChargingStationStatusEvent(Base):
#     """Technical history của station, EVSE hoặc connector.
#
#     Đây là bảng time-series. ``recorded_at`` nằm trong primary key và mọi unique
#     constraint để TimescaleDB chấp nhận partition key trong index.
#
#     Attributes:
#         status_event_id: UUID nội bộ của history record.
#         recorded_at: Thời điểm event phát sinh từ thiết bị và partition key.
#         station_id: Station phát sinh event.
#         evse_id: EVSE liên quan, nullable với event cấp station.
#         connector_id: Connector liên quan, nullable.
#         source_action: Action OCPP đã chuẩn hóa.
#         technical_status: Status kỹ thuật, nullable nếu message không có status.
#         event_code: Mã event đã chuẩn hóa, nullable.
#         ocpp_message_id: Message ID OCPP, nullable.
#         idempotency_key: Identity ổn định do adapter tạo.
#         received_at: Thời điểm backend nhận event.
#         sanitized_raw_payload: Payload đã redact, nullable.
#     """
#
#     __tablename__ = "charging_station_status_events"
#
#     status_event_id: Mapped[UUID] = mapped_column(
#         PG_UUID(as_uuid=True), primary_key=True, default=uuid4
#     )
#     recorded_at: Mapped[datetime] = mapped_column(
#         DateTime(timezone=True), primary_key=True, nullable=False
#     )
#     station_id: Mapped[UUID] = mapped_column(
#         PG_UUID(as_uuid=True),
#         ForeignKey("charging_stations.station_id", ondelete="RESTRICT"),
#         nullable=False,
#     )
#     evse_id: Mapped[UUID | None] = mapped_column(
#         PG_UUID(as_uuid=True),
#         ForeignKey("charging_evses.evse_id", ondelete="RESTRICT"),
#         nullable=True,
#     )
#     connector_id: Mapped[UUID | None] = mapped_column(
#         PG_UUID(as_uuid=True),
#         ForeignKey("charging_connectors.connector_id", ondelete="RESTRICT"),
#         nullable=True,
#     )
#     source_action: Mapped[StationSourceAction] = mapped_column(
#         SQLEnum(
#             StationSourceAction,
#             name="chargingstationsourceaction",
#             values_callable=enum_values,
#         ),
#         nullable=False,
#     )
#     technical_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
#     event_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
#     ocpp_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
#     idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
#     received_at: Mapped[datetime] = mapped_column(
#         DateTime(timezone=True), nullable=False
#     )
#     sanitized_raw_payload: Mapped[dict[str, object] | None] = mapped_column(
#         JSONB, nullable=True
#     )
#
#     __table_args__ = (
#         CheckConstraint(
#             "connector_id IS NULL OR evse_id IS NOT NULL",
#             name="ck_charging_status_connector_requires_evse",
#         ),
#         UniqueConstraint(
#             "station_id",
#             "idempotency_key",
#             "recorded_at",
#             name="uq_charging_status_station_idempotency_recorded",
#         ),
#         Index(
#             "ix_charging_status_station_recorded",
#             "station_id",
#             desc("recorded_at"),
#         ),
#         Index("ix_charging_status_evse_recorded", "evse_id", desc("recorded_at")),
#         Index(
#             "ix_charging_status_connector_recorded",
#             "connector_id",
#             desc("recorded_at"),
#         ),
#         Index(
#             "ix_charging_status_action_recorded",
#             "source_action",
#             desc("recorded_at"),
#         ),
#     )
#
