# Planner: Backend Charging Stations (AD-03)

> Mã chức năng: AD-03; đối chiếu `feature-list.md` mục 3.1, 3.4, 4.2 và 4.3
>
> Trạng thái: 📋 Dự kiến
>
> Ngày tạo: 2026-07-31
>
> Rà soát gần nhất: 2026-07-31

## 1. Mục tiêu và phạm vi

Xây dựng domain `charging_stations` quản lý hồ sơ trụ, topology
`ChargingStation → EVSE → Connector`, trạng thái thiết bị và OCPP 2.0.1.
Domain này sở hữu WebSocket gateway, connection registry và transport của remote
command. Nghiệp vụ phiên, điều kiện được phép remote start/stop, tính tiền và
thanh toán thuộc `charging_sessions`.

Phạm vi đầy đủ:

- CRUD/soft delete Charging Station, EVSE và Connector;
- trạng thái online/offline, connector status và lịch sử sự kiện kỹ thuật;
- OCPP 2.0.1: boot, heartbeat, status, notify event, transaction và meter values;
- hàng đợi command bền vững;
- `RequestStartTransaction` và `RequestStopTransaction`;
- theo dõi command từ lúc tiếp nhận đến khi được trụ chấp nhận/từ chối, timeout
  và đối chiếu với `TransactionEvent`;
- API status/history/remote command;
- simulator và integration test OCPP.

Không thuộc planner: Web Portal, OCPP 1.6/2.1, reservation, smart charging,
firmware management và general-purpose alert delivery.

## 2. Quyết định kiến trúc bắt buộc

- Chỉ hỗ trợ WebSocket subprotocol `ocpp2.0.1`.
- Payload/dataclass `ocpp.v201` chỉ xuất hiện trong `charging_stations/ocpp/`.
- `charging_stations` gọi public service của `charging_sessions`; chiều ngược lại
  bị cấm để tránh dependency cycle.
- Remote API ghi command vào database và trả `202 Accepted`; OCPP worker gửi
  command sau khi transaction HTTP đã commit.
- Command delivery có trạng thái riêng; response `Accepted` của trụ chưa đồng
  nghĩa phiên đã bắt đầu/dừng. Kết quả cuối được đối chiếu bằng
  `TransactionEvent`.
- Một Charging Station có N EVSE; một EVSE có N Connector. Không hard-code hai
  súng và không suy diễn khả năng sạc đồng thời chỉ từ số connector.
- Session/history không bị cascade delete khi station được soft delete.
- Mọi timestamp UTC timezone-aware; location dùng PostGIS
  `geography(Point, 4326)`.

## 3. Thứ tự thực hiện

Hai planner có dependency lẫn nhau ở mức public service, nên thứ tự tổng thể là:

1. `charging_stations` Bước 0-3: chốt contract và tạo persistence topology.
2. `charging_sessions` Bước 0-13: hoàn thiện session, tiền, payment, debt và
   public remote-control guards.
3. `charging_stations` Bước 4-11: CRUD/OCPP/event/remote dispatcher/simulator.
4. `charging_sessions` Bước 14: API báo cáo sau khi integration đã nối.
5. `charging_stations` Bước 12, rồi `charging_sessions` Bước 15 để nghiệm thu.

Mỗi lần chỉ prompt một bước theo thứ tự trên. Sau mỗi bước, agent phải cập nhật
mục “Kết quả thực tế” của chính bước đó trước khi đánh dấu hoàn thành; không
đánh dấu dựa trên giả định.

---

## Bước 0: Chốt contract thiết bị và remote control

**Mục tiêu:** Thu thập quyết định còn thiếu trước khi tạo schema/source.

**Prompt:**

```text
Đọc toàn bộ AGENTS.md, docs/01-requirements/feature-list.md,
docs/02-planners/backend-charging-stations.md và
docs/02-planners/backend-charging-sessions.md.

Chỉ thực hiện Bước 0 của planner charging stations, chưa viết source code.

1. Rà code/migration/config hiện tại để xác nhận chưa có domain charging.
2. Lập bảng các thông tin cần tôi xác nhận:
   - OCPP identity, URL và security profile/TLS của trụ;
   - topology thật của trụ hai súng: số EVSE, connector trên từng EVSE, hai súng
     có sạc đồng thời không và cách chia công suất;
   - danh sách connector type;
   - heartbeat interval, meter sample interval và hành vi offline buffer;
   - idToken dùng để remote start và cách liên kết idToken với vehicle/driver;
   - role được remote start/stop;
   - timeout gửi command, timeout chờ TransactionEvent và policy retry.
3. Phân biệt thông tin bắt buộc trước khi code với thông tin chỉ cần trước khi
   test trụ thật.
4. Ghi các quyết định đã xác nhận vào planner. Không tự đặt credential, timeout
   hoặc topology.
5. Kết thúc bằng danh sách câu hỏi ngắn để tôi trả lời.
```

**Kiểm tra:**

- [x] Không có source/migration/dependency mới.
- [x] Topology MVP và security development đã được xác nhận; security production
      vẫn là policy triển khai riêng.
- [x] Actor/role remote start/stop đã được xác nhận; các blocking decision của
      MVP đã được người dùng xác nhận và ghi vào planner.

**Kết quả thực tế:** ✅ Rà source và chốt contract MVP ngày 2026-07-31.

Kết quả rà soát:

- Chưa có package `charging_stations`/`charging_sessions`, model, migration,
  router, OCPP entrypoint hoặc simulator; có thể thiết kế mới mà không phải
  migrate dữ liệu charging cũ.
- `python-ocpp` chưa nằm trong `pyproject.toml`/`uv.lock`; settings hiện chỉ có
  `APP_`, `DATABASE_URL`, `MQTT_`, `TELEMETRY_` và pagination, chưa có `OCPP_`.
- FastAPI hiện chỉ mount vehicles, telematics và telemetry. Chưa có domain
  `identity`, `drivers`, `billing` hoặc payment để tái sử dụng role/idToken.
- `vehicles.service` đã có public lookup theo UUID/VIN; đây là contract có thể
  dùng sau khi chốt cách liên kết idToken với xe.
- Shared `Base`, `get_db` và `async_session_factory` đã tồn tại. OCPP process và
  worker phải tái sử dụng các thành phần này.
- Database hiện đã có TimescaleDB/PostGIS qua hạ tầng chung; chưa có schema
  charging nên migration mới phải bắt đầu sau Alembic head hiện tại.

Phân loại thông tin:

| Mức độ | Thông tin |
|---|---|
| Đã chốt cho MVP | Pre-provision; development không TLS/không authentication trong môi trường cô lập; topology `2 EVSE × 1 connector`; RFID làm `idToken`; timeout 30/60 giây; không auto-retry khi không rõ kết quả |
| Đã chốt remote control | Admin vận hành và tài xế đang được gán xe được điều khiển; Admin được force stop nhưng không force start khi có debt |
| Chặn production | Production security profile, certificate/credential lifecycle và network boundary |
| Chỉ chặn test trụ thật | Identity/credential/certificate thật; EVSE/connector ID thật; connector type/công suất; heartbeat/sample interval; offline buffer và semantics event bù |

Các quyết định đã xác nhận:

1. Pre-provision station/topology qua Admin API; `BootNotification` không tự tạo
   station hoặc EVSE/Connector lạ.
2. WebSocket path `/ocpp/{ocpp_identity}`.
3. Development chạy được ở mức thấp nhất: không TLS/không authentication trong
   môi trường cô lập; không hard-code credential và không đưa certificate thật
   vào repo. Đây không phải policy production.
4. Simulator và topology MVP dùng hai EVSE độc lập, mỗi EVSE một connector;
   topology thật được thay bằng dữ liệu nhà sản xuất khi có.
5. RFID card là phương thức local authorization; `idToken` từ thẻ map tới
   driver, driver map tới vehicle qua active shift/assignment, rồi tạo session.
   Backend lưu hash/reference của card ID, không lưu dữ liệu thẻ nhạy cảm.
6. Actor remote control: Admin vận hành và tài xế đang được gán xe được điều
   khiển; Admin được force stop nhưng không force start khi có debt.
7. Remote command response timeout 30 giây, confirmation timeout 60 giây và
   không tự retry khi chưa biết trụ đã nhận request hay chưa để tránh lặp lệnh.
8. Dự kiến trụ có offline buffer; semantics buffer, sequence và replay phải xác
   nhận lại khi test thiết bị thật.

Các điểm không còn chặn Bước 1-3 nhưng phải chốt trước production/integration:

1. Production chọn TLS + Basic Auth (Security Profile 2) hay mutual TLS
   (Security Profile 3).
2. Nhà sản xuất xác nhận offline buffer có giữ `seqNo`/transaction event cũ và
   gửi replay hay không.
3. Nhà sản xuất xác nhận connector type, công suất, heartbeat/sample interval và
   `evseId`/`connectorId` thật.

Về câu hỏi “các hệ thống như Xanh SM làm thế nào”: không có tài liệu kỹ thuật
công khai đủ để kết luận kiến trúc nội bộ của Xanh SM. Mô hình planner chọn là
mô hình fleet phổ biến: RFID card nhận diện driver/account; backend kiểm tra
driver đang được gán vehicle; OCPP station tạo/nhận `TransactionEvent`; session
lưu cả driver, vehicle, station, EVSE và connector. OCPP 2.0.1 Core có authorization
và remote control; các implementation được OCA chứng nhận có thể dùng RFID
ISO 14443/15693, nhưng đó không phải bằng chứng riêng về hệ thống Xanh SM.

---

## Bước 1: Chốt schema, API và state machine command

**Mục tiêu:** Hoàn thiện design contract trước khi triển khai.

**Prompt:**

```text
Thực hiện Bước 1 của docs/02-planners/backend-charging-stations.md.
Đọc lại kết quả Bước 0 và source hiện tại. Chỉ sửa tài liệu planner/contract,
chưa viết source code.

Hãy thiết kế chi tiết:
1. Bảng charging_stations, charging_evses, charging_connectors.
2. Hypertable charging_station_status_events.
3. Bảng charging_remote_commands với UUID internal ID, command type
   request_start/request_stop, idempotency key, station/EVSE/session reference,
   request payload tối thiểu, trạng thái, attempt count, timestamps và error.
4. State machine command:
   pending -> dispatching -> accepted|rejected|timed_out|failed
   accepted -> confirmed|confirmation_timed_out.
5. Constraint/index/FK, soft-delete behavior, timezone và upgrade/downgrade.
6. REST API CRUD/status/history và remote start/stop/status command.
7. Domain exception -> HTTP status mapping.
8. Transaction/partial-failure boundary cho HTTP writer và OCPP dispatcher.

Giữ remote command là transport concern của charging_stations. Mọi validation
nghiệp vụ về session, công nợ, vehicle và payment phải gọi public service của
charging_sessions. Không tạo dependency ngược.

Cập nhật planner với contract cuối và các quyết định còn thiếu. Không tạo
placeholder source.
```

**Kiểm tra:**

- [ ] State machine phân biệt accepted với confirmed.
- [ ] Có idempotency contract và unique constraint tương ứng.
- [ ] Không có dependency cycle giữa hai domain.
- [ ] Delete/update topology không làm mồ côi lịch sử.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 2: Thêm dependency và cấu hình OCPP

**Mục tiêu:** Chuẩn bị runtime OCPP 2.0.1 bằng shared settings.

**Prompt:**

```text
Thực hiện Bước 2 của planner charging stations.

1. Đọc AGENTS.md và pattern config hiện có.
2. Thêm python-ocpp bằng uv, pin version tương thích Python 3.12 và cập nhật
   pyproject.toml + uv.lock.
3. Chỉ thêm các setting đã chốt ở Bước 0/1, namespace OCPP_, gồm host, port,
   heartbeat/offline timeout, command polling/response/confirmation timeout và
   security option cần thiết.
4. Cập nhật backend/.env.example bằng giá trị mẫu không chứa secret.
5. Không tạo engine, session factory, logger hoặc HTTP runtime riêng.
6. Viết docstring/comment tiếng Việt đầy đủ cho code thay đổi.
7. Chạy uv sync/check import và ghi kết quả thực tế vào planner.
```

**Kiểm tra:**

- [ ] `uv.lock` đồng bộ.
- [ ] Import `ocpp.v201` chạy trên Python 3.12.
- [ ] Không hard-code timeout/host/port/credential.
- [ ] Không thêm config ngoài scope.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 3: Tạo types, models và migration trụ sạc

**Mục tiêu:** Tạo persistence cho station, topology, status event và command.

**Prompt:**

```text
Thực hiện Bước 3 của planner charging stations theo contract đã chốt ở Bước 1.

1. Tạo package charging_stations và chỉ các file thực sự cần:
   types.py, models.py, exceptions.py và __init__.py chỉ chứa docstring.
2. Tạo models charging_stations, charging_evses, charging_connectors,
   charging_station_status_events và charging_remote_commands.
3. Dùng shared Base; UUID internal ID cho bảng chính; mọi FK tham chiếu internal
   ID; timestamp timezone-aware; location PostGIS geography(Point, 4326).
4. Status events là hypertable; review primary/unique key phải chứa partition
   key theo TimescaleDB.
5. Remote command là bảng quan hệ thường và có index phục vụ worker claim.
6. Tạo một Alembic migration mới, không sửa migration đã merge. Viết đầy đủ
   upgrade/downgrade và import model vào Alembic metadata theo pattern hiện có.
7. Review cascade, soft delete, index, constraint và concurrent command.
8. Chạy format/lint/type/compile phù hợp và migration upgrade/downgrade smoke
   test nếu database sẵn sàng.
9. Cập nhật kết quả thực tế vào planner.
```

**Kiểm tra:**

- [ ] Model/migration khớp contract.
- [ ] Hypertable và PostGIS type đúng.
- [ ] Có unique idempotency key cho remote command.
- [ ] Downgrade không để object TimescaleDB dư.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 4: CRUD Charging Station và topology

**Mục tiêu:** Hoàn thiện REST CRUD trước khi kết nối OCPP.

**Prompt:**

```text
Thực hiện Bước 4 của planner charging stations.

1. Tạo schemas.py, repository.py, service.py và router.py đúng layer boundary.
2. Implement CRUD/soft delete Charging Station và quản lý EVSE/Connector theo
   API contract Bước 1.
3. Partial update dùng PATCH + model_dump(exclude_unset=True), lọc None theo
   convention; operation topology phải atomic.
4. Không cho đổi ocpp_identity thành giá trị trùng hoặc tái sử dụng identity đã
   soft delete nếu contract chưa cho phép.
5. Trước delete/deactivate station hoặc EVSE, gọi public service của
   charging_sessions để chặn khi có session active/ending; đồng thời kiểm tra
   repository nội bộ để chặn remote command chưa terminal.
6. Chuyển IntegrityError sang domain conflict; router mới chuyển sang HTTP.
7. Đăng ký router trong app.api.main.
8. Chạy static checks và Swagger smoke test create/list/detail/update/delete,
   topology hai EVSE và conflict.
9. Cập nhật kết quả thực tế vào planner.
```

**Kiểm tra:**

- [ ] Router không chứa business logic.
- [ ] Service/repository không commit/rollback.
- [ ] Không import model/repository của domain khác.
- [ ] Topology hỗ trợ N EVSE/N Connector.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 5: API trạng thái và lịch sử sự kiện

**Mục tiêu:** Cung cấp dữ liệu giám sát trụ trước khi nối thiết bị thật.

**Prompt:**

```text
Thực hiện Bước 5 của planner charging stations.

1. Bổ sung repository/service/schema/router cho:
   GET /api/v1/charging-stations/{id}/status
   GET /api/v1/charging-stations/{id}/status-history
2. Filter history theo khoảng UTC, EVSE, connector, source action, status và
   severity; pagination/cursor phải có thứ tự ổn định.
3. Response snapshot gồm connection state, last_seen_at và topology status.
4. Không trả raw_payload mặc định.
5. Query phải dùng index/hypertable phù hợp, không N+1 topology.
6. Smoke test không dữ liệu, nhiều event cùng timestamp, filter và pagination.
7. Chạy static checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] Timestamp input bắt buộc timezone và normalize UTC.
- [ ] Pagination không mất/trùng event.
- [ ] Query plan tận dụng index phù hợp.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 6: OCPP WebSocket server và lifecycle

**Mục tiêu:** Nhận kết nối OCPP 2.0.1 bằng process riêng.

**Prompt:**

```text
Thực hiện Bước 6 của planner charging stations.

1. Tạo charging_stations/ocpp/ocpp_server.py và entrypoint.py; __init__.py chỉ
   chứa docstring.
2. Chỉ negotiate ocpp2.0.1 và validate ocpp_identity trong WebSocket path.
3. Dùng python-ocpp v201; registry in-memory quản lý đúng một active connection
   cho mỗi station và có policy reconnect đã chốt.
4. Một component duy nhất sở hữu lifecycle server/connection/dispatcher.
5. OCPP entry boundary dùng shared async_session_factory cho từng unit-of-work.
6. Implement signal handling và graceful shutdown; không swallow task exception.
7. Connection open/close cập nhật online/offline theo transaction rõ ràng.
8. Dùng structured logging qua extra, không f-string trong logger.
9. Tạo OCPP simulator tối thiểu phục vụ smoke test connect/reconnect/reject
   protocol/identity; không Docker hóa simulator nếu planner không yêu cầu.
10. Chạy static checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] Client sai subprotocol/identity bị từ chối.
- [ ] Reconnect không để hai connection cùng identity.
- [ ] Shutdown đóng server/task/connection sạch.
- [ ] Không tạo engine/logger riêng.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 7: Boot, heartbeat, status và sự kiện kỹ thuật

**Mục tiêu:** Đồng bộ snapshot/lịch sử từ OCPP.

**Prompt:**

```text
Thực hiện Bước 7 của planner charging stations.

Implement handler OCPP 2.0.1:
1. BootNotification: validate station, cập nhật vendor/model/serial/firmware và
   trả interval theo settings.
2. Heartbeat: cập nhật last_seen_at nhưng không tạo event thừa.
3. StatusNotification: resolve EVSE/Connector, cập nhật snapshot và insert
   charging_station_status_event trong cùng transaction.
4. NotifyEvent: lưu component/variable/event/severity đã normalize và raw
   payload; chưa gửi notification ra ngoài.
5. Unknown EVSE/Connector phải theo policy provisioning Bước 0, không tự tạo
   topology nếu chưa được xác nhận.
6. Message duplicate/out-of-order không làm snapshot lùi timestamp.
7. Implement offline detection theo timeout cấu hình bằng worker dùng shared
   session factory.
8. Smoke test normal, duplicate, out-of-order, unknown topology và DB rollback.
9. Chạy static checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] Snapshot + event atomic.
- [ ] Offline timeout không hard-code.
- [ ] OCPP response đúng schema 2.0.1.
- [ ] Raw payload không lộ credential.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 8: Nhận TransactionEvent và MeterValues

**Mục tiêu:** Chuyển event OCPP sang public contract của domain phiên.

**Prompt:**

```text
Thực hiện Bước 8 của planner charging stations sau khi public ingestion contract
của charging_sessions đã tồn tại.

1. Implement TransactionEvent và MeterValues handler OCPP 2.0.1.
2. Adapter resolve station/EVSE/Connector internal ID, convert payload thành
   primitive/standard-library values rồi gọi function công khai được định nghĩa
   trực tiếp trong charging_sessions.service.
3. Preserve transactionId, eventType, seqNo, triggerReason, chargingState,
   stoppedReason, timestamp, idToken reference và meter values.
4. Không đưa dataclass ocpp.v201 hoặc SQLAlchemy model qua domain boundary.
5. Mỗi message có một transaction do OCPP entry boundary sở hữu.
6. Phản hồi OCPP chỉ sau khi DB operation thành công; DB error rollback và đóng
   connection theo policy.
7. Test Started/Updated/Ended, duplicate seqNo, out-of-order, multiple sampled
   values và unknown transaction.
8. Review import boundary bằng rg, chạy static checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] Dependency chỉ `charging_stations -> charging_sessions.service`.
- [ ] Không mất OCPP transaction identity/seqNo.
- [ ] Duplicate không tạo session/sample lặp.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 9: API tạo remote command bền vững

**Mục tiêu:** Tiếp nhận remote start/stop an toàn và idempotent.

**Prompt:**

```text
Thực hiện Bước 9 của planner charging stations.

1. Implement:
   POST /api/v1/charging-stations/{id}/remote-start
   POST /api/v1/charging-stations/{id}/remote-stop
   GET  /api/v1/charging-stations/{id}/commands/{command_id}
2. Bắt buộc Idempotency-Key cho POST; cùng key + cùng payload trả command cũ,
   cùng key + payload khác trả conflict.
3. Remote start gọi public charging_sessions service để kiểm tra vehicle,
   idToken, EVSE availability, active session, unpaid/overdue debt và role theo
   contract đã chốt.
4. Remote stop gọi public service để resolve active session, station transaction
   ID và quyền dừng.
5. Nếu hợp lệ, chỉ ghi command pending trong transaction HTTP và trả 202; không
   gửi WebSocket trực tiếp trong request.
6. Từ chối station offline/inactive, EVSE không hợp lệ hoặc command xung đột
   bằng domain exception rõ ràng.
7. Ghi actor/reference cần thiết cho audit, không lưu credential/idToken thô nếu
   contract chỉ cho phép hash/token reference.
8. Smoke test idempotency, conflict, debt, offline và concurrent requests.
9. Chạy static checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] HTTP commit xảy ra trước khi worker gửi command.
- [ ] Validation nghiệp vụ không nằm trong OCPP adapter.
- [ ] Không có dependency ngược từ sessions sang stations.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 10: Dispatcher RequestStart/StopTransaction

**Mục tiêu:** Gửi command qua active connection và theo dõi kết quả.

**Prompt:**

```text
Thực hiện Bước 10 của planner charging stations.

1. Tạo remote command dispatcher trong process OCPP, dùng shared session factory.
2. Claim command pending an toàn khi có nhiều worker bằng PostgreSQL locking
   phù hợp; không gửi cùng command đồng thời.
3. Map command sang OCPP 2.0.1 RequestStartTransaction hoặc
   RequestStopTransaction đúng schema.
4. Áp dụng timeout/retry đã chốt. Chỉ retry trường hợp transport chưa biết trụ
   đã nhận hay chưa theo policy; giữ idempotency/requestStartId correlation.
5. Cập nhật dispatching/accepted/rejected/timed_out/failed trong transaction
   riêng; log structured với command/station/session IDs.
6. Khi TransactionEvent tương ứng tới, đối chiếu accepted command thành
   confirmed; quá confirmation timeout thành confirmation_timed_out nhưng không
   tự suy diễn rằng trụ đã không sạc.
7. Shutdown phải dừng claim mới và xử lý task đang chạy theo timeout đã chốt.
8. Test accepted + confirmed, rejected, response timeout, reconnect, duplicate
   worker và confirmation timeout.
9. Chạy static checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] Không gửi duplicate do concurrent worker.
- [ ] Accepted không bị coi là session active/stopped.
- [ ] Partial failure được lưu và có thể quan sát qua API.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 11: Hoàn thiện simulator và integration scenarios

**Mục tiêu:** Kiểm chứng end-to-end không cần trụ thật.

**Prompt:**

```text
Thực hiện Bước 11 của planner charging stations.

Mở rộng simulator OCPP 2.0.1 để mô phỏng:
1. Một station có hai EVSE, mỗi EVSE một connector và hai phiên đồng thời.
2. Boot/heartbeat/status/notify event.
3. TransactionEvent Started/Updated/Ended và MeterValues.
4. RequestStartTransaction accepted/rejected.
5. RequestStopTransaction accepted/rejected.
6. Delay response, mất kết nối, reconnect, duplicate và out-of-order event.

Tạo kịch bản smoke/integration chạy được lặp lại bằng command rõ ràng. Không thêm
Docker service mới nếu chưa được xác nhận. Ghi expected result, log correlation
và kết quả thực tế vào planner.
```

**Kiểm tra:**

- [ ] Kịch bản hai EVSE chạy đồng thời.
- [ ] Remote command được đối chiếu bằng TransactionEvent.
- [ ] Failure/reconnect không làm mất trạng thái command.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 12: Nghiệm thu domain charging_stations

**Mục tiêu:** Audit toàn bộ scope và ghi bằng chứng.

**Prompt:**

```text
Thực hiện Bước 12, nghiệm thu planner charging stations.

1. Chạy Black, isort, Ruff, mypy và compile cho backend.
2. Chạy migration upgrade/downgrade/upgrade trên database phù hợp.
3. Chạy toàn bộ smoke/integration scenarios ở Bước 4-11.
4. Dùng rg kiểm tra import chéo repository/models, __init__.py, HTTPException
   ngoài router, commit/rollback ngoài entry boundary, datetime.utcnow, logger
   f-string và placeholder/TODO.
5. Review OCPP schema, timeout/shutdown, transaction, idempotency, topology,
   PostGIS/TimescaleDB, raw payload và secret handling.
6. Đối chiếu docs/01-requirements/feature-list.md và planner sessions.
7. Cập nhật checklist/kết quả thực tế từng bước; không đánh dấu pass nếu chưa có
   bằng chứng. Hạng mục hoãn thật sự phải chuyển vào future.md.
8. Báo cáo file thay đổi, command đã chạy, kết quả và giới hạn test với trụ thật.
```

**Tiêu chí hoàn thành:**

- [ ] CRUD/topology/status/history hoạt động.
- [ ] OCPP 2.0.1 lifecycle ổn định.
- [ ] Transaction/meter event vào đúng domain phiên.
- [ ] Remote start/stop idempotent, durable và quan sát được.
- [ ] Không vi phạm domain/transaction boundary.
- [ ] Static checks, migration và E2E có bằng chứng.

**Kết quả thực tế:** Chưa thực hiện.

## 4. Tài liệu giao thức tham chiếu

- [OCPP 2.0.1 JSON schemas](https://ocpp-spec.org/schemas/v2.0.1/)
- [OCPP 2.x numbering và transaction identity](https://ocpp-spec.org/docs/ocpp_2_0/architecture/numbering/)
- [`python-ocpp`](https://github.com/mobilityhouse/ocpp)
