# Quy chuẩn coding backend

> Phạm vi: `backend/` — Python 3.12, FastAPI, SQLAlchemy async và các
> background worker/entrypoint liên quan.
>
> Tài liệu này tập trung vào cách đặt tên, phân biệt loại object và chuyển đổi
> dữ liệu giữa các layer. Các quy tắc về transaction, database, logging,
> migration và domain boundary trong `AGENTS.md` vẫn có hiệu lực song song.

## 1. Nguyên tắc chung

Tên phải giúp người đọc trả lời được ba câu hỏi:

1. Đây là object nghiệp vụ nào?
2. Nó thuộc loại kỹ thuật nào?
3. Nó được dùng ở layer hoặc boundary nào?

Mẫu tên ưu tiên:

```text
<Tên đối tượng nghiệp vụ><Vai trò kỹ thuật>
```

Ví dụ:

```text
VehicleModel
VehicleCreateRequest
VehicleResponse
VehicleReference
VehicleNotFoundError
```

Code mới phải tuân theo quy chuẩn này. Code hiện tại được chuyển đổi dần khi
được sửa trong một task liên quan; không đổi tên hàng loạt nếu không cần thiết.

## 2. Quy tắc tên cơ bản

| Thành phần | Quy tắc | Ví dụ |
|---|---|---|
| Module/file | `snake_case` | `charging_sessions.py` |
| Function/method | `snake_case` | `find_vehicle_by_vin()` |
| Biến/tham số | `snake_case` | `vehicle_record` |
| Class | `PascalCase` | `VehicleModel` |
| Enum member | `UPPER_SNAKE_CASE` | `VehicleStatus.ACTIVE` |
| Hằng số | `UPPER_SNAKE_CASE` | `DEFAULT_PAGE_SIZE` |
| Private helper | bắt đầu bằng `_` | `_clean_update_values()` |

Không dùng tên quá chung khi object có thể xuất hiện ở nhiều ngữ cảnh:

```text
data, item, result, model, schema, service, manager, handler, utils
```

Có thể dùng tên ngắn trong một scope rất nhỏ nếu ngữ cảnh hoàn toàn rõ ràng,
nhưng các object đi qua nhiều layer phải có tên mô tả vai trò.

Không đặt tên biến trùng hoặc dễ nhầm với built-in Python như `id`, `type`,
`input`, `list`, `filter`, `format`.

## 3. SQLAlchemy model

Class ORM dùng hậu tố `Model`:

```python
class VehicleModel(Base):
    ...


class TelematicModel(Base):
    ...


class VehicleTelemetryModel(Base):
    ...


class ChargingSessionModel(Base):
    ...


class ChargingSessionEventModel(Base):
    ...
```

Tên class phải sử dụng danh từ nghiệp vụ ở dạng số ít. Tên bảng vẫn dùng số
nhiều theo convention database:

```python
class VehicleModel(Base):
    __tablename__ = "vehicles"
```

Mọi bảng phải có internal ID. Không dùng business key như `vin`,
`license_plate` hoặc `telematic_serial` làm primary key. Foreign key cũng phải
tham chiếu internal ID.

## 4. Pydantic schema

### 4.1 HTTP request

Dữ liệu nhận từ API dùng hậu tố `Request`:

```python
class VehicleCreateRequest(BaseModel):
    ...


class VehicleUpdateRequest(BaseModel):
    ...
```

Không dùng tên quá chung như `Create`, `Update` hoặc `Base` cho public schema.

Class base chỉ phục vụ kế thừa nội bộ nên đặt private hoặc thể hiện rõ vai trò:

```python
class _VehicleInputFields(BaseModel):
    ...
```

### 4.2 HTTP response

Dữ liệu trả về API dùng hậu tố `Response`:

```python
class VehicleResponse(BaseModel):
    ...


class VehicleListResponse(BaseModel):
    ...
```

Nếu cần nhấn mạnh pagination, có thể dùng `VehiclePageResponse`. Chỉ chọn một
kiểu nhất quán trong từng nhóm API.

### 4.3 Schema cho message bên ngoài

Tên phải thể hiện nguồn hoặc vai trò của message:

```python
class TelemetryMessage(BaseModel):
    ...


class TelemetryEnvelope(BaseModel):
    ...


class TelemetryPayload(BaseModel):
    ...
```

Không dùng một schema HTTP cho MQTT message hoặc ngược lại nếu hai contract
này có mục đích khác nhau.

## 5. DTO nội bộ và value object

### 5.1 DTO nội bộ

DTO mang dữ liệu giữa các layer hoặc domain. Tên phải thể hiện mục đích:

```text
Reference  tham chiếu tối thiểu đến object
Summary    dữ liệu tóm tắt
Mapping    kết quả ánh xạ giữa các object
Lookup     kết quả tra cứu
Target     đối tượng đích của một luồng xử lý
```

Ví dụ:

```python
@dataclass(frozen=True)
class VehicleReference:
    vehicle_id: UUID
    vin: str
```

`VehicleResponse` là hợp đồng HTTP; `VehicleReference` là hợp đồng nội bộ.
Không truyền `VehicleModel` hoặc HTTP response schema sang domain khác.

DTO nội bộ không bắt buộc là Pydantic. Dùng `dataclass(frozen=True)` khi dữ
liệu đã ở trong backend và chỉ cần một object typed, immutable, không cần JSON
serialization hoặc OpenAPI.

DTO nhỏ, thuần domain có thể đặt trong `types.py`. Nếu một domain có nhiều
contract công khai nội bộ, chỉ tạo `contracts.py` khi thực sự cần; không tạo
file rỗng hoặc file chỉ để gom tên cho đủ cấu trúc.

### 5.2 Value object

Value object đại diện cho một khái niệm nghiệp vụ, được so sánh bằng giá trị,
thường immutable và có thể tự bảo vệ invariant:

```python
@dataclass(frozen=True)
class Vin:
    value: str
```

Ví dụ phù hợp:

```text
Vin, LicensePlate, GeoPoint, Money, DateRange, BatteryPercentage
```

DTO là “dữ liệu cần truyền đi”; value object là “khái niệm nghiệp vụ có rule”.
Một value object có thể được triển khai bằng dataclass, nhưng không phải mọi
dataclass đều là value object.

## 6. Enum và exception

Enum dùng tên đối tượng cộng với khái niệm:

```python
class VehicleStatus(str, Enum):
    ...


class TelematicStatus(str, Enum):
    ...


class ChargingSessionStatus(str, Enum):
    ...
```

Exception dùng mẫu `<Object><Condition>Error`:

```python
VehicleNotFoundError
VehicleConflictError
TelematicNotFoundError
ChargingStationNotFoundError
ChargingTopologyConflictError
```

Service chỉ ném domain exception. Router mới chuyển domain exception thành
`HTTPException` hoặc status code tương ứng.

## 7. Function và method

### 7.1 Service

Service dùng động từ + đối tượng nghiệp vụ:

```python
create_vehicle()
get_vehicle()
list_vehicles()
update_vehicle()
soft_delete_vehicle()
```

Các tiền tố có ý nghĩa:

```text
get_       lấy một object, thường kỳ vọng tồn tại
find_      tìm kiếm, có thể trả về None
list_      trả về collection
count_     đếm
resolve_   tra cứu hoặc ánh xạ qua repository/service
create_    tạo mới
update_    cập nhật
soft_delete_ xoá mềm
```

Không đặt tên `get_` cho function thực hiện ghi dữ liệu. Nếu thao tác là xoá
mềm, phải ghi rõ `soft_delete_` thay vì chỉ dùng `delete_`.

### 7.2 Repository

Repository được gọi qua module alias theo domain:

```python
from app.domains.vehicles import repository as vehicle_repository

vehicle_record = await vehicle_repository.find_by_vin(db_session, vin)
```

Function trong repository có thể ngắn vì module đã thể hiện domain:

```text
get_by_id()
find_by_vin()
find_by_license_plate()
list_all()
count()
insert()
update_fields()
soft_delete()
```

Hạn chế tên vừa chứa module vừa chứa domain như:

```python
repo_get_vehicle_by_id()
repo_create_vehicle()
```

### 7.3 Router

Router handler không dùng tên chung như `create`, `get`, `update`, `delete`,
`list_items`. Dùng tên có domain và hậu tố `endpoint`:

```python
create_vehicle_endpoint()
get_vehicle_endpoint()
list_vehicles_endpoint()
update_vehicle_endpoint()
soft_delete_vehicle_endpoint()
```

Tên service và router nên phân biệt được khi tìm kiếm code hoặc đọc stack trace.

## 8. Biến, tham số, collection và boolean

### 8.1 Tên biến theo vai trò

```python
vehicle_create_request
vehicle_update_request
vehicle_record
vehicle_response
query_result
db_session
telemetry_message
charging_session_record
```

Tránh:

```python
data
item
result
model
schema
session
```

`db_session` nên được ưu tiên hơn `db` khi function có nhiều loại session hoặc
có nguy cơ nhầm với database engine.

### 8.2 Số ít và số nhiều

Object đơn dùng số ít:

```python
vehicle_record
telematic_model
```

Collection dùng số nhiều:

```python
vehicles
telematics
charging_sessions
```

### 8.3 Boolean

Boolean nên có tiền tố:

```python
is_active
is_deleted
has_vehicle
can_retry
should_close
```

Tránh tên boolean mơ hồ như `active`, `deleted`, `retry` nếu chúng không nằm
trong một ngữ cảnh đủ rõ.

### 8.4 ID, timestamp và đơn vị

ID phải ghi rõ loại object:

```python
vehicle_id
telematic_id
station_id
evse_id
connector_id
session_id
```

Timestamp dùng hậu tố `_at`:

```python
created_at
updated_at
recorded_at
received_at
deleted_at
```

Khi đơn vị có thể gây nhầm, ghi đơn vị vào tên:

```python
meter_start_wh
distance_km
energy_kwh
duration_seconds
battery_temperature_celsius
```

Từ viết tắt chuẩn của domain được giữ nguyên: `UUID`, `VIN`, `EVSE`, `OCPP`,
`MQTT`, `GPS`, `HTTP`.

## 9. Quy tắc import và layer boundary

Import module theo domain alias:

```python
from app.domains.vehicles import repository as vehicle_repository
from app.domains.vehicles import service as vehicle_service
```

Không dùng wildcard import. `__init__.py` chỉ chứa module docstring, không
export hoặc import object.

Giữa hai domain, chỉ gọi public service của domain sở hữu dữ liệu. Không import
chéo `models.py` hoặc `repository.py` của domain khác.

Trách nhiệm layer:

```text
router      HTTP request/response, status code, HTTPException
service     business rule và orchestration
repository  database query, không business rule
schemas     request/response hoặc external message validation
models      SQLAlchemy mapping
types       enum, value object, DTO nội bộ nhỏ
```

## 10. Quy tắc chuyển đổi object

Tên function phải thể hiện nguồn hoặc đích chuyển đổi:

```text
to_<target>          mapping thuần tuý, không I/O
build_<target>       dựng object tổng hợp hoặc làm giàu dữ liệu
resolve_<object>     tra cứu/ánh xạ qua repository hoặc service
parse_<object>       chuyển dữ liệu thô thành object
normalize_<field>    chuẩn hóa một giá trị
serialize_<object>   chuyển object thành JSON/bytes
calculate_<thing>    tính toán nghiệp vụ
```

Ví dụ mapping thuần tuý:

```python
def to_vehicle_reference(
    vehicle_record: VehicleModel,
) -> VehicleReference:
    """Chuyển ORM model thành DTO tham chiếu xe."""
    return VehicleReference(
        vehicle_id=vehicle_record.vehicle_id,
        vin=vehicle_record.vin,
    )
```

Ví dụ response có enrichment và I/O:

```python
async def build_telematic_response(
    db_session: AsyncSession,
    telematic_record: TelematicModel,
) -> TelematicResponse:
    """Dựng response telematic và bổ sung VIN từ domain vehicles."""
    ...
```

Không dùng các tên mơ hồ như:

```text
convert(), transform(), map_data(), _response()
```

Mapper thuần tuý không được tự gọi database, service khác hoặc commit. Nếu
function vừa truy vấn vừa dựng object, dùng `resolve_` hoặc `build_` và ghi rõ
side effect trong docstring.

Không đặt logic chuyển đổi HTTP vào ORM model, và không để schema import
SQLAlchemy model. Việc chuyển đổi nên nằm trong service hoặc một mapper module
riêng nếu số lượng mapper đủ lớn.

Luồng dữ liệu chuẩn:

```text
Repository: database → ORM Model
Service:    Model → DTO/Response
Router:     Response → HTTP
```

## 11. Docstring, comment và test

Các quy tắc docstring/comment backend trong `AGENTS.md` vẫn áp dụng:

- Docstring và comment viết bằng tiếng Việt.
- Module phải mô tả trách nhiệm, phạm vi và giới hạn.
- Class phải mô tả vai trò và state quan trọng.
- Function phải mô tả hành vi, `Args`, `Returns`, `Raises` và side effect khi có.
- Comment giải thích lý do, invariant, transaction, concurrency, timeout hoặc
  workaround; không diễn giải lại từng dòng code.

Tên test phải mô tả hành vi và kết quả:

```python
async def test_create_vehicle_rejects_duplicate_vin():
    ...


async def test_find_active_vehicle_ignores_soft_deleted_record():
    ...
```

## 12. Checklist khi thêm hoặc sửa backend code

- [ ] Tên class có thể hiện object và vai trò kỹ thuật.
- [ ] Request/response không bị dùng làm DTO nội bộ.
- [ ] ORM model không bị truyền sang domain khác.
- [ ] Function phân biệt rõ query, command, resolve và mapping.
- [ ] Tên biến không dùng chung chung khi object đi qua nhiều layer.
- [ ] ID, timestamp, boolean và đơn vị có tên rõ ràng.
- [ ] Import module khác dùng alias theo domain.
- [ ] Router không chứa business rule.
- [ ] Service không import FastAPI hoặc ném `HTTPException`.
- [ ] Repository không commit/rollback và không chứa business policy.
- [ ] Mapper thuần tuý không thực hiện I/O.
- [ ] Docstring/comment được cập nhật khi logic thay đổi.
- [ ] Chạy Black, isort, Ruff, mypy và smoke test phù hợp.
