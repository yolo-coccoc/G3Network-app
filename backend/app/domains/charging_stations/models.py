"""SQLAlchemy models tối thiểu cho topology trạm sạc.

Module chỉ mô tả ba bảng topology active của MVP lý tưởng. Station, EVSE và
connector đã được pre-provision; technical status, capability và device
metadata không thuộc persistence contract của bước này.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.libs.db.base import Base


def utc_now() -> datetime:
    """Lấy thời điểm UTC dùng cho default và soft-delete timestamp.

    Returns:
        Thời điểm hiện tại dưới dạng ``datetime`` có timezone UTC.
    """
    return datetime.now(timezone.utc)


class ChargingStation(Base):
    """Hồ sơ station đã được pre-provision trong MVP.

    Attributes:
        station_id: UUID nội bộ.
        ocpp_identity: Identity xuất hiện trong OCPP WebSocket path.
        display_name: Tên hiển thị.
        created_at: Thời điểm tạo record.
        updated_at: Thời điểm cập nhật record.
        deleted_at: Thời điểm soft-delete, nullable.

    Invariants:
        ``ocpp_identity`` là business identity duy nhất và không được tái sử
        dụng sau soft-delete.
    """

    __tablename__ = "charging_stations"

    station_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    ocpp_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
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
        Index("ix_charging_stations_deleted_at", "deleted_at"),
    )


class ChargingEvse(Base):
    """EVSE thuộc một station đã được pre-provision.

    Attributes:
        evse_id: UUID nội bộ.
        station_id: UUID station sở hữu EVSE.
        ocpp_evse_id: ID EVSE do OCPP sử dụng, dương.
        created_at: Thời điểm tạo record.
        updated_at: Thời điểm cập nhật record.
        deleted_at: Thời điểm soft-delete, nullable.

    Invariants:
        ``ocpp_evse_id`` chỉ duy nhất trong station parent và phải là số dương.
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
    )


class ChargingConnector(Base):
    """Connector vật lý thuộc một EVSE đã được pre-provision.

    Attributes:
        connector_id: UUID nội bộ.
        evse_id: UUID EVSE sở hữu connector.
        ocpp_connector_id: ID connector do OCPP sử dụng, dương.
        created_at: Thời điểm tạo record.
        updated_at: Thời điểm cập nhật record.
        deleted_at: Thời điểm soft-delete, nullable.

    Invariants:
        ``ocpp_connector_id`` chỉ duy nhất trong EVSE parent và phải là số
        dương.
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
        UniqueConstraint(
            "evse_id",
            "ocpp_connector_id",
            name="uq_charging_connectors_evse_ocpp_id",
        ),
        Index("ix_charging_connectors_evse_deleted", "evse_id", "deleted_at"),
    )
