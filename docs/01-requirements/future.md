# Future Components — Thành phần hoãn lại

> File này lưu những thành phần/lớp/tech/component tạm thời bị bỏ qua ở thời điểm hiện tại để sớm hoàn thành MVP.
> Mỗi khi bỏ qua một thành phần với lý do "hiện tại chưa cần, nhưng sau này chắc chắn phải thêm", hãy ghi nhận vào đây.

---

## Mục đích

- Tránh đặt placeholder rải rác trong source code (gây nhiễu, khó bảo trì)
- Giữ một nguồn chân lý tập trung về những gì đã hoãn lại
- Dễ dàng rà soát khi bắt đầu phase tiếp theo

---

## Danh sách thành phần đã hoãn

### 1. [Tên thành phần]

- **Mô tả ngắn**: 
- **Tác dụng/Vai trò trong hệ thống**: 
- **Lý do hoãn lại**: 
- **Liên quan đến planner/feature**: (VD: `backend-telemetry-ingestion.md`, `AD-03`)
- **Ngày ghi nhận**: YYYY-MM-DD
- **Ghi chú thêm**: (nếu có)

---

## Ví dụ mẫu

### 1. API Gateway / Reverse Proxy (Traefik/Nginx)

- **Mô tả ngắn**: Layer trung gian giữa frontend và backend, xử lý routing, rate limiting, authentication tại edge.
- **Tác dụng/Vai trò trong hệ thống**: 
  - Bảo vệ backend khỏi các request độc hại
  - Giảm tải cho backend bằng caching tại edge
  - Centralized logging và monitoring cho tất cả API calls
  - SSL termination
- **Lý do hoãn lại**: Môi trường dev không cần — frontend gọi thẳng vào backend qua localhost. Sẽ cân nhắc khi làm `docker-compose.prod.yml`.
- **Liên quan đến planner/feature**: Không liên quan trực tiếp đến feature cụ thể, là thành phần hạ tầng chung.
- **Ngày ghi nhận**: 2026-07-24
- **Ghi chú thêm**: Đã có đề xuất trong AGENTS.md Mục 1 (Reverse proxy / API Gateway).

---

### 2. MQTT QoS 1+ với Retry và Duplicate Detection

- **Mô tả ngắn**: Nâng cấp MQTT từ QoS 0 lên QoS 1 hoặc QoS 2, kèm theo retry logic và duplicate detection mechanism.
- **Tác dụng/Vai trò trong hệ thống**: 
  - Đảm bảo message được deliver ít nhất một lần (QoS 1) hoặc đúng một lần (QoS 2)
  - Retry tự động khi network failure hoặc broker unavailable
  - Duplicate detection để tránh insert trùng message vào database
  - Zero data loss khi network hoặc process gặp lỗi
- **Lý do hoãn lại**: MVP tập trung chứng minh luồng hoạt động cơ bản với giả định network lý tưởng. QoS 0 đủ để test end-to-end flow. Retry và duplicate detection sẽ thêm khi triển khai production.
- **Liên quan đến planner/feature**: `backend-telemetry-ingestion.md` (AD-02, FM-01, FM-02)
- **Ngày ghi nhận**: 2026-07-25
- **Ghi chú thêm**: Cần cân nhắc trade-off giữa reliability và performance. QoS 1+ sẽ tăng latency và giảm throughput.

---

### 3. Dead-Letter Queue (DLQ)

- **Mô tả ngắn**: Hàng đợi lưu các message không thể xử lý sau nhiều lần retry, để điều tra và xử lý thủ công sau.
- **Tác dụng/Vai trò trong hệ thống**: 
  - Không mất message khi có lỗi nghiêm trọng (parse error, validation error, DB schema mismatch)
  - Cho phép replay message sau khi fix bug hoặc update schema
  - Audit trail để debug production issue
  - Tách biệt message lỗi khỏi luồng chính để không ảnh hưởng performance
- **Lý do hoãn lại**: MVP không cần DLQ vì volume thấp và có thể debug qua log. Sẽ thêm khi scale lên production với volume cao.
- **Liên quan đến planner/feature**: `backend-telemetry-ingestion.md` (AD-02)
- **Ngày ghi nhận**: 2026-07-25
- **Ghi chú thêm**: Có thể implement DLQ bằng file JSON (đơn giản) hoặc DB table (query được). Cần có cleanup policy (xóa sau 7-30 ngày).

---

### 4. Persistent Queue

- **Mô tả ngắn**: Queue được lưu trên disk thay vì in-memory, đảm bảo không mất message khi process crash hoặc restart.
- **Tác dụng/Vai trò trong hệ thống**: 
  - Survive process restart hoặc crash
  - Không mất message đang trong queue khi deploy mới
  - Cho phép queue lớn hơn RAM capacity
  - Replay message từ queue khi cần
- **Lý do hoãn lại**: MVP dùng asyncio.Queue in-memory đủ cho demo. Process crash sẽ mất message trong queue, nhưng chấp nhận được với QoS 0. Sẽ thêm persistent queue (SQLite, Redis, hoặc Kafka) khi cần reliability cao hơn.
- **Liên quan đến planner/feature**: `backend-telemetry-ingestion.md` (AD-02)
- **Ngày ghi nhận**: 2026-07-25
- **Ghi chú thêm**: Cân nhắc giữa SQLite (đơn giản, local), Redis (nhanh, cần thêm infra), hoặc Kafka (scale tốt, phức tạp).

---

### 5. Cache Layer cho Telematic Mapping

- **Mô tả ngắn**: In-memory cache (Redis hoặc dict) lưu mapping telematic_serial → (telematic_id, vehicle_id) để tránh query DB mỗi batch.
- **Tác dụng/Vai trò trong hệ thống**: 
  - Giảm số lượng DB query từ N queries/batch xuống 1 query/batch (hoặc 0 nếu cache hit)
  - Tăng throughput ingest
  - Giảm DB load
  - Latency thấp hơn cho mỗi message
- **Lý do hoãn lại**: MVP dùng batch lookup (1 query cho cả batch) đã đủ hiệu quả. Cache sẽ thêm khi volume tăng và DB trở thành bottleneck.
- **Liên quan đến planner/feature**: `backend-telemetry-ingestion.md` (AD-02)
- **Ngày ghi nhận**: 2026-07-25
- **Ghi chú thêm**: Nếu dùng cache, cần TTL (5-10 phút) và invalidation khi telematic được gán/xóa khỏi vehicle. Cân nhắc Redis nếu cần share cache giữa nhiều worker instance.

---

### 6. Unique Constraint cho message_uuid

- **Mô tả ngắn**: Thêm unique constraint vào cột message_uuid trong bảng vehicle_telemetry để đảm bảo không có duplicate message.
- **Tác dụng/Vai trò trong hệ thống**: 
  - Ngăn chặn insert duplicate message (cùng message_uuid)
  - Database-level guarantee (không phụ thuộc application logic)
  - Hỗ trợ idempotency khi retry
- **Lý do hoãn lại**: TimescaleDB yêu cầu unique constraint phải chứa partition key (recorded_at). Constraint (message_uuid, recorded_at) không ngăn duplicate nếu message bị retry với recorded_at khác. Cần logic duplicate detection phức tạp hơn (VD: separate table tracking processed message_uuid). Sẽ thêm khi implement QoS 1+.
- **Liên quan đến planner/feature**: `backend-telemetry-ingestion.md` (AD-02)
- **Ngày ghi nhận**: 2026-07-25
- **Ghi chú thêm**: Có thể dùng bảng riêng `processed_messages` để track message_uuid đã xử lý, với TTL (VD: 24h) để không bị tràn.

---

### 7. Duplicate Detection Nâng Cao

- **Mô tả ngắn**: Logic phát hiện và xử lý duplicate message phức tạp hơn, không chỉ dựa vào unique constraint database.
- **Tác dụng/Vai trò trong hệ thống**: 
  - Detect duplicate ngay trước insert (không đợi DB constraint violation)
  - Hỗ trợ idempotency cho retry logic
  - Xử lý edge case: message retry với recorded_at khác
  - Có thể ignore duplicate hoặc update existing record
- **Lý do hoãn lại**: MVP dùng QoS 0 nên không có retry, không cần duplicate detection. Sẽ thêm khi nâng lên QoS 1+.
- **Liên quan đến planner/feature**: `backend-telemetry-ingestion.md` (AD-02)
- **Ngày ghi nhận**: 2026-07-25
- **Ghi chú thêm**: Có thể implement bằng:
  - Bloom filter trong memory (nhanh, có false positive)
  - Bảng processed_messages với TTL
  - Redis SET với EXPIRE

---

### 8. Exponential Backoff cho Retry

- **Mô tả ngắn**: Logic retry với delay tăng dần theo cấp số nhân (1s, 2s, 4s, 8s...) khi gặp lỗi transient.
- **Tác dụng/Vai trò trong hệ thống**: 
  - Tránh spam retry khi DB hoặc broker down
  - Giảm load lên hệ thống đang gặp sự cố
  - Tăng cơ hội thành công khi hệ thống recover
  - Circuit breaker pattern để fail fast
- **Lý do hoãn lại**: MVP không có retry logic. Batch worker sẽ dừng khi gặp lỗi, cần restart thủ công. Sẽ thêm retry với exponential backoff khi cần reliability cao hơn.
- **Liên quan đến planner/feature**: `backend-telemetry-ingestion.md` (AD-02)
- **Ngày ghi nhận**: 2026-07-25
- **Ghi chú thêm**: Cần config max retry (VD: 3-5 lần) và max backoff (VD: 60s). Sau khi hết retry, đưa vào DLQ.

---

### 9. PostGIS Geography cho vị trí telemetry

- **Mô tả ngắn**: Thay hai cột `latitude`/`longitude` bằng cột `geography(Point, 4326)` hoặc bổ sung cột geography được đồng bộ.
- **Tác dụng/Vai trò trong hệ thống**: Hỗ trợ spatial index, truy vấn bán kính, geofence và lịch sử hành trình hiệu quả.
- **Lý do hoãn lại**: Planner telemetry MVP đã chốt lưu tọa độ bằng hai cột `DOUBLE PRECISION` để chứng minh luồng ingest trước.
- **Liên quan đến planner/feature**: `backend-telemetry-ingestion.md` (AD-02, FM-01, FM-02)
- **Ngày ghi nhận**: 2026-07-26
- **Ghi chú thêm**: Cần migration dữ liệu hiện có và chốt geometry hay geography trước khi triển khai.

---

### 10. Foreign key từ vehicles đến fleet

- **Mô tả ngắn**: Chuyển `vehicles.fleet_id` sang UUID internal ID và tạo foreign key đến bảng thuộc domain fleet.
- **Tác dụng/Vai trò trong hệ thống**: Bảo đảm toàn vẹn phân công xe theo đội và tuân thủ quy tắc foreign key luôn tham chiếu internal ID.
- **Lý do hoãn lại**: Domain và bảng fleet chưa được triển khai; không đặt relationship placeholder trong source trước khi có model đích.
- **Liên quan đến planner/feature**: FM-01…FM-07
- **Ngày ghi nhận**: 2026-07-26
- **Ghi chú thêm**: Khi triển khai fleet phải có migration chuyển dữ liệu `String(36)` hiện tại sang UUID và thêm constraint.

---

### 11. Import-linter trong CI

- **Mô tả ngắn**: Cấu hình `import-linter` để kiểm tra tự động ranh giới import giữa các bounded context.
- **Tác dụng/Vai trò trong hệ thống**: Ngăn domain import trực tiếp `models.py`/`repository.py` của domain khác và biến convention kiến trúc thành ràng buộc CI.
- **Lý do hoãn lại**: Nền tảng CI/CD chưa được lựa chọn và package/config import-linter chưa tồn tại trong backend.
- **Liên quan đến planner/feature**: Quy tắc kiến trúc chung trong `AGENTS.md`.
- **Ngày ghi nhận**: 2026-07-26
- **Ghi chú thêm**: Khi triển khai cần thêm dependency bằng `uv`, contract cấu hình và job CI tương ứng.

---

### 12. Alembic filter cho object do PostGIS/TimescaleDB quản lý

- **Mô tả ngắn**: Thêm `include_object` vào Alembic để bỏ qua `spatial_ref_sys` và index nội bộ do TimescaleDB tạo.
- **Tác dụng/Vai trò trong hệ thống**: Giúp `alembic check` và autogenerate chỉ phản ánh schema do application quản lý, tránh sinh migration xóa object của extension.
- **Lý do hoãn lại**: MVP chưa chốt CI/CD và migration hiện được review/chạy thủ công; Alembic head hiện vẫn đúng.
- **Liên quan đến planner/feature**: `backend-telemetry-ingestion.md` (AD-02), cấu hình database chung.
- **Ngày ghi nhận**: 2026-07-26
- **Ghi chú thêm**: Trước khi bật `alembic check` trong CI hoặc dùng autogenerate cho migration mới, hạng mục này phải được hoàn thành.

---

### 13. Đối chiếu telematic serial giữa MQTT topic và payload

- **Mô tả ngắn**: Parse `{telematic_serial}` từ MQTT topic và reject message nếu không khớp `telematic_serial` trong JSON payload.
- **Tác dụng/Vai trò trong hệ thống**: Ngăn message bị gán nhầm thiết bị khi topic và payload không đồng nhất, đồng thời hỗ trợ kiểm soát danh tính device.
- **Lý do hoãn lại**: MVP giả định telematic publish đúng topic và payload theo đặc tả.
- **Liên quan đến planner/feature**: `backend-telemetry-ingestion.md` (AD-02).
- **Ngày ghi nhận**: 2026-07-26
- **Ghi chú thêm**: Nên triển khai cùng authentication/authorization MQTT trước môi trường production.

---

### 14. Chính xác hóa cập nhật `telematics.last_seen_at`

- **Mô tả ngắn**: Chỉ cập nhật `last_seen_at`, `updated_at` và row count khi timestamp mới thực sự lớn hơn giá trị hiện tại.
- **Tác dụng/Vai trò trong hệ thống**: Giữ `updated_at` đúng ngữ nghĩa và làm metric/log `telematics_updated` phản ánh số thiết bị thực sự thay đổi.
- **Lý do hoãn lại**: Sai lệch hiện tại chỉ ảnh hưởng metadata/log, không làm `last_seen_at` lùi thời gian và không chặn luồng ingest MVP.
- **Liên quan đến planner/feature**: `backend-telemetry-ingestion.md` (AD-02).
- **Ngày ghi nhận**: 2026-07-26
- **Ghi chú thêm**: Cần cân nhắc batch SQL phù hợp để vẫn giữ một lần update cho cả batch.

---

### 15. Phân biệt telematic không tồn tại và chưa gán xe

- **Mô tả ngắn**: Batch lookup trả đủ thiết bị kể cả `vehicle_id` null để service log/metric riêng hai trạng thái provisioning.
- **Tác dụng/Vai trò trong hệ thống**: Giúp vận hành phân biệt serial không hợp lệ với thiết bị hợp lệ nhưng chưa được gán xe.
- **Lý do hoãn lại**: Cả hai trường hợp đều được skip an toàn trong MVP và chưa có dashboard vận hành provisioning.
- **Liên quan đến planner/feature**: `backend-telemetry-ingestion.md` (AD-02), AD-05.
- **Ngày ghi nhận**: 2026-07-26
- **Ghi chú thêm**: Khi triển khai cần đổi return type mapping thành `tuple[UUID, UUID | None]` và bổ sung metric riêng.

---

### 16. Automated backend test suite

- **Mô tả ngắn**: Bổ sung pytest, pytest-asyncio, test fixtures và các test unit/integration cho backend.
- **Tác dụng/Vai trò trong hệ thống**: Bảo vệ transaction boundary, API validation, repository query, MQTT ingestion, batch window và graceful shutdown khỏi regression.
- **Lý do hoãn lại**: MVP hiện ưu tiên hoàn thiện luồng chức năng; tạm dùng Ruff, mypy, compile, smoke test và kiểm tra end-to-end thủ công.
- **Liên quan đến planner/feature**: Toàn bộ backend; ưu tiên `backend-telemetry-ingestion.md` (AD-02) và vehicles AD-05.
- **Ngày ghi nhận**: 2026-07-26
- **Ghi chú thêm**: Trước khi thiết lập CI/CD phải thêm test dependencies bằng `uv`, sửa `make backend-test` để dùng môi trường đã cài test và xác định ngưỡng coverage.

### 24. Phân chia rõ ràng cấu hình giữa `config.py` và `.env`

- **Mô tả ngắn**: Chuẩn hóa ranh giới giữa schema/cấu hình mặc định trong `backend/app/libs/common/config.py` và giá trị runtime theo môi trường trong `backend/.env`.
- **Tác dụng/Vai trò trong hệ thống**:
  - `config.py` là nguồn định nghĩa tên biến, kiểu dữ liệu, validation, giá trị mặc định và cách truy cập cấu hình bằng `Settings`.
  - `.env` chỉ chứa giá trị thay đổi theo môi trường như database URL, broker connection, credentials và tuning runtime; không chứa business rule hoặc logic ứng dụng.
  - Mọi source code backend và Alembic truy cập cấu hình qua `app.libs.common.config.settings`, không tự gọi `os.getenv()` hoặc `load_dotenv()`.
- **Lý do hoãn lại**: MVP hiện đã có `Settings` dùng Pydantic nhưng vẫn còn biến `DEBUG` không khớp với `APP_DEBUG`, `CORS_ORIGINS` trong `.env.example` chưa được khai báo/sử dụng, Alembic có cơ chế đọc `.env` riêng và một số default queue/batch/MQTT còn bị lặp trong source.
- **Liên quan đến planner/feature**: Cấu hình backend dùng chung; `backend/app/libs/common/config.py`, `backend/.env.example`, `backend/app/libs/db/migrations/env.py`, AD-02.
- **Ngày ghi nhận**: 2026-07-30
- **Ghi chú thêm**: Khi hoàn thiện cần sửa `DEBUG` thành `APP_DEBUG`, rà soát `CORS_ORIGINS`, cho Alembic dùng cùng `Settings`, loại bỏ hoặc xác định rõ các default cấu hình trùng lặp, và không commit giá trị bí mật trong `.env`.

---

### 17. Observability tập trung cho telemetry ingestion

- **Mô tả ngắn**: Hoàn thiện kênh thu thập log và metrics của telemetry worker
  để có thể theo dõi bên ngoài process, lưu giữ lịch sử và thiết lập cảnh báo.
- **Tác dụng/Vai trò trong hệ thống**:
  - Thu thập JSON log từ `stderr` vào hệ thống tập trung như Grafana Loki, ELK
    hoặc dịch vụ cloud tương đương.
  - Áp dụng retention, tìm kiếm, dashboard và alert cho lỗi ingest, message bị
    skip/drop, độ trễ batch và tình trạng worker dừng.
  - Expose metrics qua HTTP endpoint hoặc Prometheus exporter để hệ thống
    monitoring scrape được.
  - Lưu metrics bền vững qua các lần restart và tổng hợp số liệu từ nhiều worker
    instance.
- **Lý do hoãn lại**: MVP hiện chỉ cung cấp JSON structured logging qua Python
  `StreamHandler`. Log hiện chỉ xuất ra `stderr` để xem tại terminal hoặc qua
  `docker logs`; không ghi file, không có log shipping/retention/dashboard/alert.
  Metrics/counter trong MQTT consumer và batch worker đã bị bỏ để giữ ingestion
  tối giản, nên chưa có endpoint/exporter hoặc số liệu process-local.
- **Liên quan đến planner/feature**: `backend-telemetry-ingestion.md` bước 13-14
  (AD-02, FM-01, FM-02).
- **Ngày ghi nhận**: 2026-07-27
- **Ghi chú thêm**: Bước 14 đã chuyển JSON logging lên telemetry entrypoint nên
  log startup/MQTT/worker/shutdown dùng cùng output contract. Phần còn lại của
  mục này là log shipping, retention, dashboard/alert và thiết kế lại metrics
  từ đầu nếu cần exporter. Khi triển khai cần tránh gắn handler cũ + JSON
  handler gây output trùng và tránh log cùng traceback ở nhiều boundary nếu
  không bổ sung context mới. Cần chốt backend observability stack trước khi thêm
  dependency hoặc infrastructure mới.

---

### 18. API gán/tháo thiết bị telematic cho vehicle

- **Mô tả ngắn**: Bổ sung use case provisioning để Admin gán, đổi hoặc tháo một
  telematic khỏi vehicle; hiện telemetry đã có bảng `telematics`, foreign key và
  unique constraint nhưng domain vehicles chưa có API nghiệp vụ tương ứng.
- **Tác dụng/Vai trò trong hệ thống**:
  - Hoàn thành phần “gán thiết bị” của chức năng Quản lý xe.
  - Bảo đảm một vehicle có tối đa một telematic và ngăn một thiết bị bị gán sai.
  - Cho phép vận hành ingestion mà không phải insert/update mapping bằng SQL thủ
    công.
  - Có contract rõ cho replace/unassign, conflict và thiết bị/xe không tồn tại.
- **Lý do hoãn lại**: Vehicles CRUD MVP ban đầu loại provisioning khỏi phạm vi;
  telemetry ingestion mới chỉ cần mapping tồn tại để lookup và chưa xây Admin
  workflow quản lý thiết bị.
- **Liên quan đến planner/feature**: `backend-crud-vehicles.md`,
  `backend-telemetry-ingestion.md` (AD-05, AD-02).
- **Ngày ghi nhận**: 2026-07-27
- **Ghi chú thêm**: Vì model/repository telematic thuộc domain telemetry,
  vehicles không được import trực tiếp các module nội bộ này. Trước khi triển
  khai cần chốt router/use-case owner; nếu vehicles điều phối thì phải gọi public
  API trong `telemetry/service.py`. Operation phải atomic và chuyển unique/FK
  `IntegrityError` thành domain conflict rõ ràng.

---

### 19. Structured logging, request metrics và health thực chất cho API process

- **Mô tả ngắn**: Áp dụng output contract logging/observability đã hình thành ở
  telemetry cho FastAPI API process và các domain HTTP như vehicles.
- **Tác dụng/Vai trò trong hệ thống**:
  - Cấu hình JSON logging tại FastAPI lifespan/process boundary để startup,
    shutdown, router/service/repository error dùng cùng format.
  - Ghi structured context cho request/operation quan trọng mà không log dữ liệu
    nhạy cảm.
  - Có counters/latency/error metrics cho CRUD/API thay vì chỉ access log.
  - Làm health/readiness phản ánh database và lifecycle thay vì luôn trả
    `{"status": "healthy"}` khi Python process còn chạy.
- **Lý do hoãn lại**: Bước 13 mới triển khai formatter và process-local metrics
  cho telemetry worker. `app.api.main` hiện chưa gọi `configure_logging()`;
  vehicles chưa có structured operation logs/metrics và `/health` không xác minh
  dependency hoặc readiness.
- **Liên quan đến planner/feature**: `backend-crud-vehicles.md`, API backend nói
  chung và mục 17 của `future.md`.
- **Ngày ghi nhận**: 2026-07-27
- **Ghi chú thêm**: Cần chốt ranh giới giữa access log, business audit log và
  application error log để tránh log trùng. Có thể tách `/live` và `/ready` khi
  deploy bằng orchestrator; không query dependency nặng trên mỗi health request.

---

### 20. Chuẩn hóa tài liệu source và OpenAPI examples của domain vehicles

- **Mô tả ngắn**: Rà soát module/class/function docstring, comment và schema
  examples của vehicles theo convention mới trong `AGENTS.md`.
- **Tác dụng/Vai trò trong hệ thống**:
  - Docstring/comment tiếng Việt mô tả đúng business rule, transaction ownership,
    exception và partial update.
  - `VehicleCreate`, `VehicleUpdate`, response/list schema có ví dụ nhất quán để
    Swagger và planner dùng làm contract.
  - Loại mô tả cũ/sai như tên field, HTTP method hoặc behavior không còn khớp
    implementation.
- **Lý do hoãn lại**: Domain vehicles được triển khai trước khi convention
  docstring/comment chi tiết bằng tiếng Việt được chốt. Nhiều docstring hiện còn
  ngắn và bằng tiếng Anh; `json_schema_extra` examples đã bị lược bỏ trong lịch
  sử trong khi telemetry schemas đã có examples chi tiết.
- **Liên quan đến planner/feature**: `backend-crud-vehicles.md` (AD-05), quy tắc
  coding convention trong `AGENTS.md`.
- **Ngày ghi nhận**: 2026-07-27
- **Ghi chú thêm**: Đây là documentation debt, không thay đổi API behavior. Khi
  thực hiện phải đối chiếu source hiện tại làm nguồn chân lý và không khôi phục
  example cũ nếu field/enum đã đổi.

---

### 21. Đồng bộ planner và bằng chứng nghiệm thu domain vehicles

- **Mô tả ngắn**: Viết lại `backend-crud-vehicles.md` theo cấu trúc planner đã
  chuẩn hóa ở telemetry, phản ánh implementation/migration thực tế và ghi bằng
  chứng smoke/integration test.
- **Tác dụng/Vai trò trong hệ thống**:
  - Phân biệt rõ bước đã triển khai, bước chỉ từng test thủ công và phần chưa có.
  - Sửa các contract cũ về `plate_number`/`license_plate`, `team_id`/`fleet_id`,
    UUID, `PUT`/`PATCH`, HTTP conflict status và timezone.
  - Làm tài liệu tham chiếu đáng tin cậy cho planner domain CRUD tiếp theo.
- **Lý do hoãn lại**: Planner vehicles vẫn ở trạng thái “Dự kiến” và phần lớn
  checklist chưa được cập nhật dù source đã tồn tại; audit hiện tại ưu tiên hoàn
  thiện planner telemetry và chỉ ghi nhận khoảng thiếu của vehicles.
- **Liên quan đến planner/feature**: `backend-crud-vehicles.md` (AD-05).
- **Ngày ghi nhận**: 2026-07-27
- **Ghi chú thêm**: Không được đánh dấu test pass chỉ dựa trên source tồn tại.
  Automated tests đã được theo dõi chung tại mục 16; mục này tập trung vào độ
  chính xác và traceability của planner.

---

### 22. Health/readiness và graceful drain cho telemetry ingestion

- **Mô tả ngắn**: Bổ sung lại endpoint health/readiness và cơ chế ngừng nhận
  MQTT rồi drain queue có timeout cho telemetry ingestion khi hệ thống cần vận
  hành production.
- **Tác dụng/Vai trò trong hệ thống**:
  - Cho orchestrator biết process đã sẵn sàng nhận dữ liệu và phát hiện
    consumer/worker bị lỗi.
  - Giảm mất telemetry đã nhận vào queue khi deploy hoặc shutdown có kế hoạch.
  - Cung cấp shutdown timeout và trạng thái lifecycle rõ ràng.
- **Lý do hoãn lại**: MVP chủ ý giữ process, task và queue hoàn toàn trong RAM,
  chấp nhận mất dữ liệu còn trong queue khi dừng để lifecycle và batch worker
  đơn giản hơn.
- **Liên quan đến planner/feature**:
  `backend-telemetry-ingestion.md` (AD-02), bước 14.
- **Ngày ghi nhận**: 2026-07-27
- **Ghi chú thêm**: Khi triển khai lại cần dựa trên môi trường deploy thực tế để
  chọn liveness/readiness contract và drain timeout; không khôi phục nguyên xi
  orchestration cũ nếu chưa xác nhận.

---

### 23. Startup probe và lifecycle failure propagation cho telemetry ingestion

- **Mô tả ngắn**: Bổ sung lại kiểm tra dependency lúc startup và API lifecycle rõ
  ràng để entrypoint theo dõi consumer/worker mà không truy cập private state.
- **Tác dụng/Vai trò trong hệ thống**:
  - Kiểm tra database bằng shared `async_session_factory` trước khi nhận MQTT để
    fail fast nếu DB chưa sẵn sàng.
  - Retrieve exception từ background task để tránh `Task exception was never
    retrieved` và giúp process thoát khác 0 khi consumer/worker chết bất thường.
  - Cung cấp public lifecycle API như `BatchWorker.wait()` hoặc cơ chế task handle
    rõ ràng, thay vì entrypoint truy cập trực tiếp `worker._task`.
  - Phân biệt shutdown do signal với shutdown do lỗi runtime trong log và exit
    code.
- **Lý do hoãn lại**: MVP hiện ưu tiên entrypoint thật ngắn: tạo queue RAM, start
  consumer/worker, chờ task đầu tiên kết thúc rồi cleanup. Trong demo hiện tại,
  DB failure vẫn được worker log và process dừng; startup probe và failure
  propagation chi tiết chưa cần để chứng minh luồng MQTT → DB.
- **Liên quan đến planner/feature**:
  `backend-telemetry-ingestion.md` (AD-02), bước 14-15.
- **Ngày ghi nhận**: 2026-07-28
- **Ghi chú thêm**: Nên triển khai cùng mục 22 nếu chuẩn bị chạy bằng
  orchestrator hoặc cần alert/exit code đáng tin cậy. Khi thêm lại, giữ API nhỏ
  và tránh kéo lại toàn bộ runtime orchestration cũ nếu không cần.

---

## Quy tắc cập nhật

1. **Khi nào ghi nhận**: Khi developer hoặc AI agent quyết định bỏ qua/xóa một thành phần với lý do "hiện tại chưa cần, nhưng sau này chắc chắn phải thêm".
2. **Không ghi nhận khi**: Thành phần đó thực sự không cần cho hệ thống (không có kế hoạch thêm trong tương lai).
3. **Format**: Thêm entry mới theo mẫu trên, đánh số thứ tự tiếp theo.
4. **Rà soát**: Định kỳ (VD: mỗi sprint) review lại file này để lên kế hoạch triển khai.
