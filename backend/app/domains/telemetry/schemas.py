"""
Pydantic schemas for MQTT telemetry message validation.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Schema này validate message từ MQTT trước khi đưa vào queue.
Message được gửi từ thiết bị Telematics, không chứa ID nội bộ hệ thống.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class VehicleTelemetryLatestResponse(BaseModel):
    """Dữ liệu telemetry mới nhất trả về cho một xe.

    Attributes:
        vehicle_id: ID nội bộ của xe.
        telematic_serial: Serial thiết bị gửi bản tin.
        recorded_at: Thời điểm thiết bị ghi nhận dữ liệu theo UTC.
        latitude: Vĩ độ GPS.
        longitude: Kinh độ GPS.
        speed: Tốc độ hiện tại, km/h.
        heading: Hướng di chuyển, độ.
        soc: Phần trăm pin còn lại.
        battery_voltage: Điện áp pin, V.
        battery_current: Dòng điện pin, A.
        battery_temperature: Nhiệt độ pin, °C.
        motor_temperature: Nhiệt độ động cơ, °C.
        odometer: Tổng quãng đường, km.
        signal_strength: Cường độ tín hiệu, dBm.
        error_codes: Mã lỗi từ thiết bị.
    """

    model_config = {"from_attributes": True}

    vehicle_id: UUID
    telematic_serial: str
    recorded_at: datetime
    latitude: float
    longitude: float
    speed: float | None
    heading: float | None
    soc: float
    battery_voltage: float | None
    battery_current: float | None
    battery_temperature: float | None
    motor_temperature: float | None
    odometer: float | None
    signal_strength: int | None
    error_codes: dict[str, list[str]] | None


class TelemetryLocationPayload(BaseModel):
    """
    Dữ liệu vị trí GPS từ telematic.

    Attributes:
        latitude: Vĩ độ (-90 to 90 độ)
        longitude: Kinh độ (-180 to 180 độ)
    """

    latitude: Annotated[float, Field(ge=-90, le=90, description="Vĩ độ (độ)")]
    longitude: Annotated[float, Field(ge=-180, le=180, description="Kinh độ (độ)")]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"latitude": 21.0285, "longitude": 105.8542},
                {"latitude": 10.762622, "longitude": 106.660172},
            ]
        }
    }


class TelemetryVehicleStatePayload(BaseModel):
    """
    Trạng thái xe từ telematic.

    Attributes:
        speed: Tốc độ (0-200 km/h)
        heading: Hướng di chuyển (0-360 độ, nullable). 0°=Bắc, 90°=Đông, 180°=Nam, 270°=Tây
        odometer: Tổng quãng đường đã đi (km)
    """

    speed: Annotated[
        float | None, Field(default=None, ge=0, le=200, description="Tốc độ (km/h)")
    ]
    heading: Annotated[
        float | None,
        Field(
            default=None,
            ge=0,
            le=360,
            description="Hướng di chuyển (độ). 0°=Bắc, 90°=Đông, 180°=Nam, 270°=Tây",
        ),
    ]
    odometer: Annotated[
        float | None, Field(default=None, ge=0, description="Tổng quãng đường (km)")
    ]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"speed": 45.2, "heading": 90.0, "odometer": 12345.6},
                {"speed": 0, "heading": None, "odometer": 5000.0},
            ]
        }
    }


class TelemetryBatteryPayload(BaseModel):
    """
    Dữ liệu pin từ telematic.

    Attributes:
        soc: State of Charge - mức pin còn lại (0-100%)
        voltage: Điện áp pin (V)
        current: Dòng điện (A). Âm = đang xả, Dương = đang sạc
        temperature: Nhiệt độ pin (°C)
    """

    soc: Annotated[
        float, Field(ge=0, le=100, description="State of Charge - mức pin còn lại (%)")
    ]
    voltage: Annotated[
        float | None, Field(default=None, ge=0, description="Điện áp pin (V)")
    ]
    current: Annotated[
        float | None,
        Field(default=None, description="Dòng điện (A). Âm = xả, Dương = sạc"),
    ]
    temperature: Annotated[
        float | None, Field(default=None, description="Nhiệt độ pin (°C)")
    ]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"soc": 78.5, "voltage": 400.2, "current": -15.3, "temperature": 35.2},
                {"soc": 50.0, "voltage": None, "current": None, "temperature": None},
            ]
        }
    }


class TelemetryMotorPayload(BaseModel):
    """
    Dữ liệu động cơ từ telematic.

    Attributes:
        temperature: Nhiệt độ động cơ (°C)
    """

    temperature: Annotated[
        float | None, Field(default=None, description="Nhiệt độ động cơ (°C)")
    ]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"temperature": 42.1},
                {"temperature": None},
            ]
        }
    }


class TelemetrySignalPayload(BaseModel):
    """
    Dữ liệu tín hiệu mạng từ telematic.

    Attributes:
        strength: Cường độ tín hiệu (dBm). Giá trị âm, càng gần 0 càng mạnh
    """

    strength: Annotated[
        int | None,
        Field(
            default=None,
            description="Cường độ tín hiệu (dBm). Giá trị âm, càng gần 0 càng mạnh",
        ),
    ]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"strength": -75},
                {"strength": -95},
            ]
        }
    }


class TelemetryMessage(BaseModel):
    """
    Message telemetry từ thiết bị Telematics qua MQTT.

    Message này được gửi từ telematic, không chứa ID nội bộ hệ thống.
    Backend sẽ bổ sung: message_id, telematic_id, vehicle_id, received_at.

    Attributes:
        message_uuid: ID duy nhất cho message, do telematic tạo
        telematic_serial: Mã serial vật lý của thiết bị (VD: TBOX-VN-000123)
        recorded_at: Thời điểm telematic ghi nhận dữ liệu (UTC)
        location: Dữ liệu vị trí GPS
        vehicle_state: Trạng thái xe (nullable)
        battery: Dữ liệu pin
        motor: Dữ liệu động cơ (nullable)
        signal: Dữ liệu tín hiệu mạng (nullable)
        errors: Danh sách mã lỗi đang active (nullable)
    """

    message_uuid: Annotated[
        UUID, Field(description="ID duy nhất cho message, do telematic tạo")
    ]
    telematic_serial: Annotated[
        str,
        Field(min_length=1, max_length=50, description="Mã serial vật lý của thiết bị"),
    ]
    recorded_at: Annotated[
        datetime, Field(description="Thời điểm telematic ghi nhận dữ liệu (UTC)")
    ]
    location: Annotated[
        TelemetryLocationPayload,
        Field(description="Dữ liệu vị trí GPS"),
    ]
    vehicle_state: Annotated[
        TelemetryVehicleStatePayload | None,
        Field(default=None, description="Trạng thái xe"),
    ]
    battery: Annotated[
        TelemetryBatteryPayload,
        Field(description="Dữ liệu pin"),
    ]
    motor: Annotated[
        TelemetryMotorPayload | None,
        Field(default=None, description="Dữ liệu động cơ"),
    ]
    signal: Annotated[
        TelemetrySignalPayload | None,
        Field(default=None, description="Dữ liệu tín hiệu mạng"),
    ]
    errors: Annotated[
        list[str] | None,
        Field(default=None, description="Danh sách mã lỗi đang active"),
    ]

    @field_validator("telematic_serial")
    @classmethod
    def validate_telematic_serial(cls, v: str) -> str:
        """Validate telematic_serial không được rỗng và không có khoảng trắng thừa."""
        v = v.strip()
        if not v:
            raise ValueError("telematic_serial không được rỗng")
        return v

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, value: datetime) -> datetime:
        """Require a timezone and normalize the recorded timestamp to UTC."""
        if value.utcoffset() is None:
            raise ValueError("recorded_at phải có timezone")
        return value.astimezone(timezone.utc)

    def to_vehicle_telemetry_values(
        self,
        telematic_id: UUID,
        vehicle_id: UUID,
        received_at: datetime,
        raw_payload: dict[str, object],
    ) -> dict[str, object]:
        """
        Chuyển message thành dict phù hợp với VehicleTelemetryModel.

        Args:
            telematic_id: UUID của telematic (lookup từ telematic_serial)
            vehicle_id: UUID của xe (lookup từ telematic_id)
            received_at: Thời điểm backend nhận message
            raw_payload: JSON object nguyên bản trước khi Pydantic normalize

        Returns:
            Dict với đầy đủ trường để insert vào DB
        """
        # Build vehicle_state dict
        vehicle_state_dict = None
        if self.vehicle_state:
            vehicle_state_dict = {
                "speed": self.vehicle_state.speed,
                "heading": self.vehicle_state.heading,
                "odometer": self.vehicle_state.odometer,
            }

        # Build battery dict
        battery_dict = {
            "soc": self.battery.soc,
            "voltage": self.battery.voltage,
            "current": self.battery.current,
            "temperature": self.battery.temperature,
        }

        # Build motor dict
        motor_dict = None
        if self.motor:
            motor_dict = {
                "temperature": self.motor.temperature,
            }

        # Build signal dict
        signal_dict = None
        if self.signal:
            signal_dict = {
                "strength": self.signal.strength,
            }

        # Build error_codes as JSONB
        error_codes_dict = None
        if self.errors:
            error_codes_dict = {"codes": self.errors}

        return {
            "message_uuid": self.message_uuid,
            "telematic_id": telematic_id,
            "telematic_serial": self.telematic_serial,
            "vehicle_id": vehicle_id,
            "recorded_at": self.recorded_at,
            "received_at": received_at,
            "latitude": self.location.latitude,
            "longitude": self.location.longitude,
            "speed": vehicle_state_dict.get("speed") if vehicle_state_dict else None,
            "heading": (
                vehicle_state_dict.get("heading") if vehicle_state_dict else None
            ),
            "soc": battery_dict["soc"],
            "battery_voltage": battery_dict.get("voltage"),
            "battery_current": battery_dict.get("current"),
            "battery_temperature": battery_dict.get("temperature"),
            "motor_temperature": motor_dict.get("temperature") if motor_dict else None,
            "odometer": (
                vehicle_state_dict.get("odometer") if vehicle_state_dict else None
            ),
            "signal_strength": signal_dict.get("strength") if signal_dict else None,
            "error_codes": error_codes_dict,
            "raw_payload": raw_payload,
        }

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "message_uuid": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
                    "telematic_serial": "TBOX-VN-000123",
                    "recorded_at": "2026-07-25T10:30:00Z",
                    "location": {"latitude": 21.0285, "longitude": 105.8542},
                    "vehicle_state": {
                        "speed": 45.2,
                        "heading": 90.0,
                        "odometer": 12345.6,
                    },
                    "battery": {
                        "soc": 78.5,
                        "voltage": 400.2,
                        "current": -15.3,
                        "temperature": 35.2,
                    },
                    "motor": {"temperature": 42.1},
                    "signal": {"strength": -75},
                    "errors": ["E001"],
                },
                {
                    "message_uuid": "497f6eca-6276-4993-bfeb-53cbbbba6f09",
                    "telematic_serial": "TBOX-VN-000124",
                    "recorded_at": "2026-07-25T10:30:00Z",
                    "location": {"latitude": 10.762622, "longitude": 106.660172},
                    "battery": {"soc": 50.0},
                },
            ]
        }
    }


@dataclass(slots=True)
class TelemetryEnvelope:
    """
    Validated telemetry message paired with its original JSON object.

    Attributes:
        message: Telemetry payload after Pydantic validation and normalization.
        raw_payload: Parsed JSON object before Pydantic modifies or drops fields.
    """

    message: TelemetryMessage
    raw_payload: dict[str, object]
