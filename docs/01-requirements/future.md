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

## Quy tắc cập nhật

1. **Khi nào ghi nhận**: Khi developer hoặc AI agent quyết định bỏ qua/xóa một thành phần với lý do "hiện tại chưa cần, nhưng sau này chắc chắn phải thêm".
2. **Không ghi nhận khi**: Thành phần đó thực sự không cần cho hệ thống (không có kế hoạch thêm trong tương lai).
3. **Format**: Thêm entry mới theo mẫu trên, đánh số thứ tự tiếp theo.
4. **Rà soát**: Định kỳ (VD: mỗi sprint) review lại file này để lên kế hoạch triển khai.
