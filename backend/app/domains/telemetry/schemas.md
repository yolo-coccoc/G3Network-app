# schemas.py — Pydantic Schemas cho Telemetry Domain

## Vai trò

File này định nghĩa các Pydantic models dùng để validate message telemetry từ thiết bị Telematics qua MQTT trước khi đưa vào queue xử lý.

## Mã chức năng

- **AD-02**: Nhận dữ liệu thời gian thực

## Nội dung chính

### 1. LocationData

Schema cho dữ liệu vị trí GPS:
- `latitude`: Vĩ độ (-90 to 90 độ)
- `longitude`: Kinh độ (-180 to 180 độ)

### 2. VehicleState

Schema cho trạng thái xe:
- `speed`: Tốc độ (0-200 km/h, nullable)
- `heading`: Hướng di chuyển (0-360 độ, nullable)
- `odometer`: Tổng quãng đường đã đi (km, nullable)

### 3. BatteryData

Schema cho dữ liệu pin:
- `soc`: State of Charge - mức pin còn lại (0-100%)
- `voltage`: Điện áp pin (V, nullable)
- `current`: Dòng điện (A, nullable). Âm = đang xả, Dương = đang sạc
- `temperature`: Nhiệt độ pin (°C, nullable)

### 4. MotorData

Schema cho dữ liệu động cơ:
- `temperature`: Nhiệt độ động cơ (°C, nullable)

### 5. SignalData

Schema cho dữ liệu tín hiệu mạng:
- `strength`: Cường độ tín hiệu (dBm, nullable). Giá trị âm, càng gần 0 càng mạnh

### 6. TelemetryMessage

Schema chính cho message telemetry từ thiết bị Telematics:

**Thuộc tính:**
- `message_uuid`: ID duy nhất cho message, do telematic tạo
- `telematic_serial`: Mã serial vật lý của thiết bị (VD: TBOX-VN-000123)
- `recorded_at`: Thời điểm telematic ghi nhận dữ liệu (UTC)
- `location`: Dữ liệu vị trí GPS
- `vehicle_state`: Trạng thái xe (nullable)
- `battery`: Dữ liệu pin
- `motor`: Dữ liệu động cơ (nullable)
- `signal`: Dữ liệu tín hiệu mạng (nullable)
- `errors`: Danh sách mã lỗi đang active (nullable)

**Method chính:**
- `to_db_dict(telematic_id, vehicle_id, received_at)`: Convert message thành dict phù hợp với VehicleTelemetry model để insert vào DB

## Lưu ý quan trọng

1. **Message từ telematic**: Không chứa ID nội bộ hệ thống (telematic_id, vehicle_id). Backend sẽ lookup từ `telematic_serial` và bổ sung các ID này.

2. **Validation**: Pydantic tự động validate:
   - Range constraints (VD: `soc` phải từ 0-100)
   - Required fields (VD: `location` và `battery` là bắt buộc)
   - Custom validator cho `telematic_serial` (không rỗng, không khoảng trắng thừa)

3. **JSON Schema examples**: Mỗi schema đều có `model_config` với examples để hỗ trợ API documentation.

## Sử dụng

```python
from app.domains.telemetry.schemas import TelemetryMessage

# Parse message từ MQTT
message = TelemetryMessage.model_validate_json(mqtt_payload)

# Convert để insert vào DB
db_dict = message.to_db_dict(
    telematic_id=telematic_id,
    vehicle_id=vehicle_id,
    received_at=datetime.utcnow()
)
```

## Liên quan

- `models.py`: VehicleTelemetry model (đích đến của `to_db_dict()`)
- `mqtt_consumer.py`: Sử dụng schema này để validate message
- `docs/02-planners/mqtt-spec.md`: Đặc tả MQTT topic và payload format
