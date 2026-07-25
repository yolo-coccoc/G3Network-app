# MQTT Specification - Telematic Device Protocol

> Phiên bản: 1.0.0  
> Ngày tạo: 2026-07-25  
> Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

---

## 1. Tổng quan

Tài liệu này định nghĩa giao thức giao tiếp giữa thiết bị Telematics (gắn trên xe tải điện) và Backend qua MQTT broker (EMQX).

**Nguyên tắc thiết kế:**
- Telematic là nguồn chân lý về thời gian (`recorded_at`)
- Backend bổ sung metadata (`message_id`, `telematic_id`, `vehicle_id`, `received_at`)
- Payload không chứa ID nội bộ hệ thống
- QoS 0 (fire-and-forget) cho MVP

---

## 2. MQTT Topics

### 2.1. Telemetry Data (Publish từ Telematic)

**Topic pattern:**
```
g3network/telematics/{telematic_serial}/telemetry
```

**Ví dụ:**
```
g3network/telematics/TBOX-VN-000123/telemetry
```

**Giải thích:**
- `g3network`: Root topic prefix cho toàn hệ thống
- `telematics`: Loại thiết bị
- `{telematic_serial}`: Mã serial vật lý của thiết bị (VD: `TBOX-VN-000123`)
- `telemetry`: Loại dữ liệu (dữ liệu thời gian thực)

**QoS Level:** 0 (fire-and-forget)  
**Retain:** false

---

### 2.2. Telematic Status (Publish từ Telematic)

**Topic pattern:**
```
g3network/telematics/{telematic_serial}/status
```

**Payload:**
```json
{
  "status": "online",
  "firmware_version": "1.2.3",
  "timestamp": "2026-07-25T10:30:00Z"
}
```

**Giải thích:**
- Dùng để theo dõi trạng thái kết nối của thiết bị
- Có thể dùng MQTT Last Will để tự động publish `offline` khi mất kết nối

---

### 2.3. Backend Command (Subscribe từ Telematic - dành cho phase sau)

**Topic pattern:**
```
g3network/telematics/{telematic_serial}/command
```

**Ví dụ payload:**
```json
{
  "command": "restart",
  "timestamp": "2026-07-25T10:30:00Z"
}
```

**Lưu ý:** Chưa triển khai trong MVP, chỉ định nghĩa trước để thiết kế ACL.

---

## 3. Payload Schema - Telemetry Message

### 3.1. Cấu trúc JSON

```json
{
  "message_uuid": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
  "telematic_serial": "TBOX-VN-000123",
  "recorded_at": "2026-07-25T10:30:00Z",
  "location": {
    "latitude": 21.0285,
    "longitude": 105.8542
  },
  "vehicle_state": {
    "speed": 45.2,
    "heading": 90.0,
    "odometer": 12345.6
  },
  "battery": {
    "soc": 78.5,
    "voltage": 400.2,
    "current": -15.3,
    "temperature": 35.2
  },
  "motor": {
    "temperature": 42.1
  },
  "signal": {
    "strength": -75
  },
  "errors": ["E001", "E005"]
}
```

### 3.2. Chi tiết từng trường

#### Trường gốc (Root level)

| Trường | Kiểu | Bắt buộc | Mô tả |
|--------|------|-----------|-------|
| `message_uuid` | UUID (string) | ✓ | ID duy nhất cho message, do telematic tạo. Dùng để trace và tránh duplicate |
| `telematic_serial` | string | ✓ | Mã serial vật lý của thiết bị (VD: `TBOX-VN-000123`) |
| `recorded_at` | ISO 8601 datetime | ✓ | Thời điểm telematic ghi nhận dữ liệu (UTC). Format: `YYYY-MM-DDTHH:MM:SSZ` |

#### Location (Vị trí GPS)

| Trường | Kiểu | Bắt buộc | Range | Đơn vị | Mô tả |
|--------|------|-----------|-------|--------|-------|
| `latitude` | float | ✓ | -90 to 90 | độ (°) | Vĩ độ |
| `longitude` | float | ✓ | -180 to 180 | độ (°) | Kinh độ |

#### Vehicle State (Trạng thái xe)

| Trường | Kiểu | Bắt buộc | Range | Đơn vị | Mô tả |
|--------|------|-----------|-------|--------|-------|
| `speed` | float | ✗ | 0-200 | km/h | Tốc độ hiện tại |
| `heading` | float | ✗ | 0-360 | độ (°) | Hướng di chuyển. 0°=Bắc, 90°=Đông, 180°=Nam, 270°=Tây. Nullable vì không phải telematic nào cũng có la bàn |
| `odometer` | float | ✗ | ≥0 | km | Tổng quãng đường đã đi |

#### Battery (Pin)

| Trường | Kiểu | Bắt buộc | Range | Đơn vị | Mô tả |
|--------|------|-----------|-------|--------|-------|
| `soc` | float | ✓ | 0-100 | % | State of Charge - mức pin còn lại |
| `voltage` | float | ✗ | ≥0 | V (Volt) | Điện áp pin |
| `current` | float | ✗ | bất kỳ | A (Ampere) | Dòng điện. Âm = đang xả, Dương = đang sạc |
| `temperature` | float | ✗ | bất kỳ | °C | Nhiệt độ pin |

#### Motor (Động cơ)

| Trường | Kiểu | Bắt buộc | Range | Đơn vị | Mô tả |
|--------|------|-----------|-------|--------|-------|
| `temperature` | float | ✗ | bất kỳ | °C | Nhiệt độ động cơ |

#### Signal (Tín hiệu mạng)

| Trường | Kiểu | Bắt buộc | Range | Đơn vị | Mô tả |
|--------|------|-----------|-------|--------|-------|
| `strength` | int | ✗ | bất kỳ | dBm | Cường độ tín hiệu. Giá trị âm (VD: -75), càng gần 0 càng mạnh |

#### Errors (Mã lỗi)

| Trường | Kiểu | Bắt buộc | Mô tả |
|--------|------|-----------|-------|
| `errors` | array[string] | ✗ | Danh sách mã lỗi đang active. VD: `["E001", "E005"]`. Null hoặc empty nếu không có lỗi |

---

## 4. Ví dụ minh họa

### 4.1. Message đầy đủ

```json
{
  "message_uuid": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
  "telematic_serial": "TBOX-VN-000123",
  "recorded_at": "2026-07-25T10:30:00Z",
  "location": {
    "latitude": 21.0285,
    "longitude": 105.8542
  },
  "vehicle_state": {
    "speed": 45.2,
    "heading": 90.0,
    "odometer": 12345.6
  },
  "battery": {
    "soc": 78.5,
    "voltage": 400.2,
    "current": -15.3,
    "temperature": 35.2
  },
  "motor": {
    "temperature": 42.1
  },
  "signal": {
    "strength": -75
  },
  "errors": ["E001"]
}
```

### 4.2. Message tối thiểu (chỉ trường bắt buộc)

```json
{
  "message_uuid": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
  "telematic_serial": "TBOX-VN-000123",
  "recorded_at": "2026-07-25T10:30:00Z",
  "location": {
    "latitude": 21.0285,
    "longitude": 105.8542
  },
  "battery": {
    "soc": 78.5
  }
}
```

---

## 5. QoS và Retain

| Thiết lập | Giá trị | Lý do |
|-----------|---------|-------|
| QoS Level | 0 | Fire-and-forget. Telematic gửi liên tục (5-10s/message), mất vài message không ảnh hưởng nghiệp vụ. MVP không cần reliability cao |
| Retain | false | Không cần retain message cũ. Backend chỉ quan tâm message mới nhất |
| Clean Session | true | Telematic không cần nhận message cũ khi reconnect |

---

## 6. Trường KHÔNG nằm trong payload

Các trường sau được Backend bổ sung, **không** do Telematic gửi:

| Trường | Nguồn | Mô tả |
|--------|-------|-------|
| `message_id` | Backend | Auto-increment BIGINT, PK trong DB |
| `telematic_id` | Backend | UUID, lookup từ `telematic_serial` |
| `vehicle_id` | Backend | UUID, lookup từ `telematic_id` |
| `received_at` | Backend | Thời điểm backend nhận message |

**Lý do:**
- Telematic không biết `telematic_id` hay `vehicle_id` (UUID nội bộ)
- `message_id` là technical ID, không có ý nghĩa nghiệp vụ với telematic
- `received_at` giúp đo latency và debug

---

## 7. ACL Rules (EMQX)

### 7.1. Telematic Device

```
pattern = ${clientid}
allow publish g3network/telematics/${clientid}/telemetry
allow publish g3network/telematics/${clientid}/status
allow subscribe g3network/telematics/${clientid}/command
```

**Giải thích:**
- `${clientid}`: Client ID (telematic serial)
- Telematic chỉ được publish vào topic của chính nó
- Telematic có thể subscribe để nhận command từ backend

### 7.2. Backend Service

```
user = g3network-backend
allow subscribe g3network/telematics/+/telemetry
allow subscribe g3network/telematics/+/status
allow publish g3network/telematics/+/command
```

**Giải thích:**
- Backend có thể subscribe tất cả telematic
- Backend có thể gửi command đến bất kỳ telematic nào

---

## 8. Testing

### 8.1. Subscribe (Backend)

```bash
mosquitto_sub -h localhost -p 1883 -u g3network-backend \
  -i g3network-backend -t "g3network/telematics/+/telemetry" -v
```

### 8.2. Publish (Telematic Simulator)

```bash
mosquitto_pub -h localhost -p 1883 -q 0 -i TBOX-VN-000123 \
  -t "g3network/telematics/TBOX-VN-000123/telemetry" \
  -m '{"message_uuid":"497f6eca-6276-4993-bfeb-53cbbbba6f08","telematic_serial":"TBOX-VN-000123","recorded_at":"2026-07-25T10:00:00Z","location":{"latitude":10.76,"longitude":106.66},"battery":{"soc":50.0}}'
```

---

## 9. Version History

| Phiên bản | Ngày | Thay đổi |
|-----------|------|----------|
| 1.0.0 | 2026-07-25 | Phiên bản đầu tiên |
