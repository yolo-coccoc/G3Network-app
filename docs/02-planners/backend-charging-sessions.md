# Planner: Backend Charging Sessions (S-02)

> Mã chức năng: S-02; đối chiếu `feature-list.md` mục 1.6, 2.3, 2.4, 3.1,
> 3.5, 3.6, 4.3, 4.4, 4.8 và 4.11
>
> Trạng thái: 📋 Dự kiến
>
> Ngày tạo: 2026-07-31
>
> Rà soát gần nhất: 2026-07-31

## 1. Mục tiêu và phạm vi

Xây dựng domain `charging_sessions` sở hữu toàn bộ nghiệp vụ phiên sạc:

- vòng đời phiên từ OCPP `TransactionEvent`;
- meter samples và đối chiếu dữ liệu cuối từ trụ/xe;
- điều kiện nghiệp vụ cho remote start/stop;
- biểu giá, tính tiền và snapshot công thức áp dụng;
- phương thức thanh toán dạng token, payment transaction và webhook;
- retry thanh toán, pending payment, quá hạn/công nợ và chặn phiên mới;
- API giám sát realtime, lịch sử, chi tiết, tiền và đối soát;
- audit event bất biến cho thay đổi tiền và trạng thái thanh toán.

Remote command transport vẫn thuộc `charging_stations`: domain đó ghi command và
gửi OCPP. `charging_sessions` không import model/repository/OCPP adapter của
`charging_stations` và không gọi ngược sang domain trụ.

Domain `billing` hiện chỉ dành cho gói dịch vụ/thuê bao AD-09, không sở hữu tiền
của một charging session.

Không thuộc planner: Web Portal, reservation, smart charging, refund/chargeback
nếu chưa được người dùng xác nhận tại Bước 0, và general-purpose accounting
ledger ngoài công nợ phiên sạc.

## 2. Quyết định kiến trúc bắt buộc

- Tách `operational_status`, `pricing_status` và `payment_status`; thanh toán
  thành công không đổi session từ `completed` sang `paid`.
- UUID là internal primary key; OCPP transaction ID unique trong phạm vi station.
- `charging_sessions` là bảng aggregate quan hệ thường; event/meter sample là
  hypertable TimescaleDB.
- Tiền và điện năng dùng `Decimal`/`NUMERIC`, không dùng float.
- Giá trị điện năng canonical là Wh; tiền có currency và minor-unit/rounding
  theo business rules được chốt.
- Tariff/version đã được session tham chiếu phải bất biến; session lưu pricing
  snapshot đủ để giải thích và tính lại độc lập.
- Payment provider là adapter nằm trong domain; không lưu thông tin thẻ thô.
- POST tạo remote intent/payment dùng idempotency key.
- Webhook bắt buộc verify signature trước khi thay đổi state và xử lý idempotent.
- Event tiền/payment/audit không được update/delete bằng API.
- Mỗi business operation mặc định atomic. Worker pricing/payment/overdue xử lý
  từng unit-of-work bằng shared `async_session_factory`.

## 3. State machine

### 3.1. Trạng thái vận hành

```text
pending → active → ending → completed
                  └──────→ interrupted
```

- `pending`: recovery khi nhận event không bắt đầu hoặc đang liên kết remote intent.
- `active`: đã nhận `TransactionEvent(Started)`.
- `ending`: đã nhận `Ended`, đang chờ dữ liệu cuối/reconciliation.
- `completed`: kết thúc bình thường và đã chốt dữ liệu.
- `interrupted`: kết thúc bất thường/timeout nhưng vẫn giữ và tính theo meter
  cuối hợp lệ, không hoàn tác phiên.

### 3.2. Trạng thái tính tiền

```text
not_ready → pending → calculated
                  └→ calculation_failed
```

### 3.3. Trạng thái thanh toán

```text
not_ready → unpaid → processing → paid
                 ↘ failed ───────↗
                 └→ overdue
```

Retry từ `failed` hoặc `overdue` tạo payment attempt/transaction mới; không sửa
lịch sử attempt cũ. `paid` là terminal trong phạm vi hiện tại.

## 4. Thứ tự thực hiện

Thứ tự tổng thể giữa hai planner:

1. `charging_stations` Bước 0-3.
2. `charging_sessions` Bước 0-13 theo đúng thứ tự trong file này.
3. `charging_stations` Bước 4-11.
4. `charging_sessions` Bước 14.
5. `charging_stations` Bước 12, rồi `charging_sessions` Bước 15.

Mỗi lần chỉ prompt một bước. Sau mỗi bước, agent phải cập nhật “Kết quả thực tế”
của bước đó; không đánh dấu hoàn thành khi chưa chạy kiểm tra tương ứng.

---

## Bước 0: Chốt business rules phiên, giá và thanh toán

**Mục tiêu:** Không viết code trước khi có contract tiền và provider.

**Prompt:**

```text
Đọc toàn bộ AGENTS.md, docs/01-requirements/feature-list.md,
docs/02-planners/backend-charging-sessions.md,
docs/02-planners/backend-charging-stations.md và source/migration hiện tại.

Chỉ thực hiện Bước 0, chưa viết source code.

Hãy tổng hợp và hỏi tôi các quyết định bắt buộc:
1. Mapping OCPP idToken/driver/vehicle và ai được bắt đầu/dừng phiên.
2. Reconciliation timeout và nguồn sự thật khi meter trụ khác telemetry xe.
3. Measurand/unit do trụ gửi; cách xử lý meter reset, giảm, thiếu hoặc outlier.
4. Công thức giá: tiền/kWh, phí thời gian/phí cố định, thuế, currency, rounding,
   timezone, thời điểm chọn tariff và tariff theo station/khung giờ.
5. Phiên interrupted/thiếu meter có tính tiền không và cách tính.
6. Payment provider, payment methods, tokenization, redirect/SDK nếu có,
   webhook signature/secret, timeout và idempotency contract.
7. Khi nào tự động charge, số lần retry, khoảng retry và mốc overdue (đặc tả
   hiện nói 24 giờ).
8. Quy tắc tạo/xóa công nợ, chặn phiên mới và role mở khóa.
9. Có cần refund/partial refund/chargeback trong phase này không.
10. Retention/raw payload/audit access và dữ liệu nhạy cảm phải redact.

Phân loại blocking decision và đề xuất mặc định nhưng không tự chốt. Sau khi tôi
trả lời, cập nhật planner và tạo/cập nhật docs/business-rules-billing.md thành
nguồn chân lý, đồng thời sửa reference tài liệu nếu cần. Không tạo placeholder
source hoặc giả lập payment provider.
```

**Kiểm tra:**

- [ ] Provider và webhook contract đã xác nhận.
- [ ] Công thức giá có ví dụ số cụ thể và quy tắc rounding.
- [ ] Meter/reconciliation/interrupted rule rõ ràng.
- [ ] Debt/retry/blocking rule rõ ràng.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 1: Chốt schema, API và invariants

**Mục tiêu:** Thiết kế đầy đủ trước migration.

**Prompt:**

```text
Thực hiện Bước 1 của planner charging sessions. Chỉ sửa planner/tài liệu contract,
chưa viết source.

Dựa trên business rules Bước 0, thiết kế:
1. charging_sessions, charging_session_events, charging_session_meter_values.
2. charging_session_start_intents để serialize remote start theo vehicle/EVSE.
3. charging_tariffs và tariff version/effective period/assignment cần thiết.
4. charging_payment_methods chỉ lưu provider token/reference.
5. charging_payment_transactions và immutable payment events/webhook events.
6. charging_debts và hành vi resolve/reopen.
7. Pricing snapshot trên session: tariff/version, từng line item, subtotal, tax,
   total, currency, formula version.
8. State transition/invariant cho operational/pricing/payment/debt.
9. Unique/index/FK/idempotency/TimescaleDB constraints.
10. REST API monitoring, tariff, payment, webhook, debt và reconciliation.
11. Public service contract để charging_stations:
    - validate/reserve remote start;
    - validate remote stop;
    - cancel/confirm control intent;
    - ingest TransactionEvent/MeterValues;
    - kiểm tra active session trước delete topology.
12. Domain exception -> HTTP status và transaction/partial-failure behavior.

Không tạo dependency ngược sang charging_stations. Ghi contract cuối và câu hỏi
còn thiếu vào planner.
```

**Kiểm tra:**

- [ ] Operational/payment state tách riêng.
- [ ] Tariff snapshot giải thích được số tiền lịch sử.
- [ ] Có idempotency cho event, remote intent, payment request và webhook.
- [ ] Concurrent start/payment được xử lý ở DB/service.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 2: Tạo types, exceptions, models và migration lõi phiên

**Mục tiêu:** Tạo persistence cho session/event/meter/control intent.

**Prompt:**

```text
Thực hiện Bước 2 của planner charging sessions theo contract Bước 1.

1. Tạo package charging_sessions với __init__.py chỉ chứa docstring.
2. Tạo types.py và exceptions.py cho state/value object thuần Python.
3. Tạo models:
   charging_sessions
   charging_session_events
   charging_session_meter_values
   charging_session_start_intents
4. Dùng shared Base, UUID internal ID, FK internal ID, TIMESTAMPTZ UTC.
5. Session aggregate là bảng thường; event/meter là hypertable với key/index
   đúng constraint TimescaleDB.
6. Điện năng dùng NUMERIC/canonical Wh; raw payload JSONB có giới hạn ownership.
7. Thêm unique constraints chống duplicate theo station + transaction + seqNo
   và sampled-value identity đã chốt.
8. Tạo Alembic migration mới đầy đủ upgrade/downgrade; không sửa migration cũ.
9. Review concurrent active session per EVSE/vehicle và active start intent bằng
   partial unique index hoặc locking contract phù hợp.
10. Chạy format/lint/type/compile và migration smoke test; cập nhật planner.
```

**Kiểm tra:**

- [ ] Model/migration khớp state machine.
- [ ] Không dùng float cho meter aggregate.
- [ ] Idempotency được bảo vệ ở DB.
- [ ] Downgrade sạch hypertable/index/enum.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 3: Public ingestion service và state transition

**Mục tiêu:** Xử lý TransactionEvent/MeterValues idempotent.

**Prompt:**

```text
Thực hiện Bước 3 của planner charging sessions.

1. Tạo value objects thuần Python dùng nội bộ domain trong types.py; public
   function tại service.py nhận primitive/standard-library values từ domain trụ
   và tự chuyển sang value object nội bộ.
2. Tạo repository.py và service.py cho ingest Started/Updated/Ended/MeterValues.
3. Started tạo session hoặc nối pending start intent; unique theo station +
   ocpp_transaction_id.
4. Updated cập nhật snapshot/meter mà không làm state/timestamp lùi.
5. Ended chuyển ending và lưu dữ liệu cuối, chưa tự tính tiền trong transaction
   ingest.
6. Duplicate/out-of-order/reconnect phải idempotent; unknown transaction theo
   recovery policy Bước 0/1.
7. Normalize unit/multiplier về canonical unit nhưng giữ raw sampled value.
8. Service/repository không commit/rollback; caller OCPP sở hữu transaction.
9. Bổ sung public has_active_session_for_topology(...) để domain trụ kiểm tra
   trước deactivate/delete; function chỉ query domain phiên.
10. Structured log không lộ idToken/payment data.
11. Smoke test Started/Updated/Ended, duplicate seqNo, out-of-order, meter reset,
    two EVSE concurrent và rollback.
12. Chạy static checks, review import boundary và cập nhật planner.
```

**Kiểm tra:**

- [ ] Không import charging_stations internals.
- [ ] State không lùi.
- [ ] Duplicate không nhân đôi meter/event.
- [ ] Domain exception không phụ thuộc FastAPI.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 4: Reconciliation worker và chốt phiên

**Mục tiêu:** Chốt `completed`/`interrupted` từ dữ liệu trụ và xe.

**Prompt:**

```text
Thực hiện Bước 4 của planner charging sessions.

1. Implement reconciliation service và worker dùng shared async_session_factory.
2. Worker claim session ending an toàn khi có nhiều instance.
3. Lấy telemetry xe qua public telemetry.service khi vehicle mapping đáng tin
   cậy; không import repository/model telemetry.
4. Áp dụng source-of-truth, tolerance và timeout đúng business rules.
5. Tính meter_start_wh, meter_end_wh, energy_delivered_wh bằng Decimal.
6. Kết thúc completed hoặc interrupted; không hoàn tác phiên khi mất kết nối.
7. Đặt pricing_status=pending trong cùng transaction chốt phiên.
8. Partial failure rollback unit hiện tại; session khác tiếp tục hay worker dừng
   đúng policy đã chốt và ghi rõ.
9. Test đủ hai nguồn, station_only, mismatch, timeout, missing Ended, meter reset
   và concurrent worker.
10. Chạy static checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] Không chốt hai lần.
- [ ] Interrupted vẫn giữ meter cuối hợp lệ.
- [ ] Pricing chỉ được kích hoạt sau khi dữ liệu đã chốt.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 5: API giám sát phiên và meter values

**Mục tiêu:** Cung cấp active/history/detail cho dashboard và người dùng.

**Prompt:**

```text
Thực hiện Bước 5 của planner charging sessions.

1. Tạo schemas.py và router.py; bổ sung repository/service query.
2. Implement:
   GET /api/v1/charging-sessions
   GET /api/v1/charging-sessions/active
   GET /api/v1/charging-sessions/{session_id}
   GET /api/v1/charging-sessions/{session_id}/meter-values
   GET /api/v1/charging-sessions/{session_id}/events
3. Filter station/EVSE/connector/vehicle/operational/pricing/payment status và
   khoảng UTC; pagination/cursor có thứ tự ổn định.
4. Detail trả energy, pricing summary, payment summary và reconciliation nhưng
   không trả raw payload/token/provider secret.
5. Quyền driver chỉ thấy session của mình; Admin theo role contract identity đã
   chốt. Nếu identity chưa tồn tại, báo blocker, không tự thêm middleware.
6. Query tránh N+1 và dùng index/hypertable.
7. Smoke test empty/filter/pagination/authorization/not found.
8. Chạy static checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] API phân biệt ba nhóm trạng thái.
- [ ] Không lộ dữ liệu nhạy cảm/raw payload.
- [ ] Pagination không mất/trùng record.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 6: Models/migration biểu giá, payment và công nợ

**Mục tiêu:** Tạo persistence tiền tệ theo business rules.

**Prompt:**

```text
Thực hiện Bước 6 của planner charging sessions theo contract Bước 0/1.

Tạo models và một Alembic migration mới cho:
1. charging_tariffs/tariff versions và assignment đã chốt.
2. charging_payment_methods chỉ lưu provider customer/payment-method token,
   brand/last4 metadata được provider cho phép; không lưu PAN/CVV.
3. charging_payment_transactions với amount/currency/status/provider IDs,
   idempotency key và attempt number.
4. immutable charging_payment_events/webhook_events.
5. charging_debts với amount/status/due_at/resolved_at.
6. pricing snapshot/line items trên session hoặc bảng con theo contract.

Dùng NUMERIC với precision/scale đã chốt, unique/index/FK internal ID,
TIMESTAMPTZ UTC và constraint state hợp lệ. Tariff đã được tham chiếu không được
update/delete; thay đổi giá tạo version mới.

Review upgrade/downgrade, secret/token handling và migration trên database.
Chạy static checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] Không có thông tin thẻ thô.
- [ ] Tariff lịch sử bất biến.
- [ ] Currency/amount constraints rõ ràng.
- [ ] Webhook/provider event có unique idempotency key.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 7: CRUD và hiệu lực biểu giá

**Mục tiêu:** Quản lý tariff có version và chọn đúng giá cho phiên.

**Prompt:**

```text
Thực hiện Bước 7 của planner charging sessions.

1. Implement schema/repository/service/router cho create/list/detail/deactivate
   tariff và tạo tariff version mới.
2. Không PATCH version đã được dùng; thay đổi giá tạo version mới với effective
   period không overlap theo scope station/global đã chốt.
3. Implement tariff resolver chọn đúng version tại thời điểm business rule quy
   định và lưu tariff snapshot vào session.
4. Validate Decimal, currency, tax, timezone và rounding config.
5. Không cho xóa tariff/version đã được session tham chiếu.
6. Smoke test boundary effective_at, overlap, concurrent create, station
   override/global fallback và historical immutability.
7. Chạy static checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] Cùng input/time luôn resolve cùng tariff.
- [ ] Không sửa được giá lịch sử.
- [ ] Overlap được chặn ở service và DB khi khả thi.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 8: Pricing engine và chốt số tiền

**Mục tiêu:** Tính tiền deterministic, audit được.

**Prompt:**

```text
Thực hiện Bước 8 của planner charging sessions.

1. Implement pricing engine thuần Python, không I/O, theo
   docs/business-rules-billing.md.
2. Input gồm canonical energy Wh, duration/time components, tariff snapshot,
   tax và currency; output gồm line items, subtotal, tax, total và formula
   version bằng Decimal.
3. Implement pricing worker claim session pricing_status=pending an toàn.
4. Lưu toàn bộ pricing snapshot/result atomic và chuyển calculated; lỗi dữ liệu
   chuyển calculation_failed với error code có thể vận hành, không tạo payment.
5. Phiên completed/interrupted áp dụng đúng rule đã chốt.
6. Test ví dụ chuẩn trong business-rules doc, rounding boundary, zero energy,
   meter reset, tariff boundary, retry/idempotency và concurrent worker.
7. Chạy static checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] Không dùng float.
- [ ] Tính lại từ snapshot cho cùng kết quả.
- [ ] Pricing không chạy hai lần/tạo line item lặp.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 9: Payment provider adapter và phương thức thanh toán

**Mục tiêu:** Tích hợp provider đã chọn mà không làm lan SDK vào business layer.

**Prompt:**

```text
Thực hiện Bước 9 của planner charging sessions.

1. Thêm SDK/payment dependency bằng uv chỉ sau khi provider đã chốt ở Bước 0.
2. Tạo adapter/protocol trong domain charging_sessions; service business chỉ
   phụ thuộc interface/value object thuần Python.
3. Config namespace PAYMENT_ cho endpoint/public key/secret/webhook secret,
   timeout; .env.example chỉ có placeholder an toàn.
4. Implement API add/list/delete/set-default payment method theo tokenization
   flow của provider; không nhận/lưu PAN/CVV trong backend.
5. Redact token/secret khỏi schema, log và raw payload.
6. Map provider error thành domain error; không dùng except Exception ngoài
   adapter/process boundary.
7. Test bằng official sandbox/fake adapter theo contract; không gọi production.
8. Chạy dependency/static/security checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] Business service không import SDK provider.
- [ ] Không có card data/secret trong DB/log/API.
- [ ] Timeout/config không hard-code.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 10: Khởi tạo và retry thanh toán

**Mục tiêu:** Thanh toán session đã tính giá một cách idempotent.

**Prompt:**

```text
Thực hiện Bước 10 của planner charging sessions.

1. Implement:
   POST /api/v1/charging-sessions/{id}/payments
   GET  /api/v1/charging-sessions/{id}/payments
   GET  /api/v1/charging-sessions/{id}/payments/{payment_id}
2. Chỉ cho thanh toán khi operational terminal và pricing_status=calculated.
3. Bắt buộc Idempotency-Key; cùng key/payload trả transaction cũ, payload khác
   trả conflict.
4. Amount/currency lấy từ pricing snapshot server, không tin giá client gửi.
5. Mỗi retry tạo attempt/transaction mới theo rule; không sửa attempt failed.
6. External call và DB state phải có partial-failure contract rõ ràng. Dùng
   provider idempotency key để có thể recover khi timeout không biết kết quả.
7. Payment success chuyển payment_status=paid; failure giữ session vận hành
   completed/interrupted và cho retry đúng policy.
8. Tạo immutable payment event cho mọi transition.
9. Test success/failure/timeout/retry/concurrent/idempotency/wrong amount.
10. Chạy static checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] Client không điều khiển amount.
- [ ] Không double charge khi retry/timeout.
- [ ] Operational status không đổi thành paid.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 11: Webhook payment idempotent

**Mục tiêu:** Đồng bộ kết quả authoritative từ provider.

**Prompt:**

```text
Thực hiện Bước 11 của planner charging sessions.

1. Implement webhook endpoint đúng provider contract.
2. Đọc raw body theo cách provider yêu cầu và verify signature/timestamp trước
   khi parse hoặc thay đổi DB.
3. Reject signature sai/replay quá hạn; không log secret/raw sensitive payload.
4. Dedupe theo provider event ID bằng unique constraint.
5. Resolve payment transaction bằng provider reference, validate
   amount/currency/session trước transition.
6. Event đến lặp/out-of-order không làm state lùi hoặc paid thành failed.
7. Lưu immutable sanitized webhook/payment event và timestamp received/processed.
8. Unknown payment/event type xử lý theo provider response contract và có log
   vận hành rõ ràng.
9. Test official signed fixtures: valid, invalid signature, duplicate,
   out-of-order, amount mismatch và unknown transaction.
10. Chạy static checks/security review và cập nhật planner.
```

**Kiểm tra:**

- [ ] Signature được kiểm tra trước side effect.
- [ ] Duplicate webhook không lặp transition.
- [ ] Amount/currency mismatch không được chấp nhận.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 12: Quá hạn, công nợ và khóa phiên mới

**Mục tiêu:** Thực thi rule pending payment quá hạn.

**Prompt:**

```text
Thực hiện Bước 12 của planner charging sessions.

1. Implement overdue worker dùng shared async_session_factory và DB locking.
2. Khi unpaid/failed quá thời hạn đã chốt (đặc tả hiện là 24h), chuyển overdue
   và tạo/update charging_debt idempotent.
3. authorize_remote_start phải chặn driver/vehicle/account có debt active theo
   đúng scope business.
4. Implement Admin API list/filter debt, detail và resolve/reopen theo role.
5. Mọi điều chỉnh thủ công tạo immutable audit event gồm actor, reason, before,
   after và timestamp; không cho xóa lịch sử.
6. Thanh toán thành công sau overdue tự resolve debt đúng rule; không xóa debt.
7. Test boundary due_at, concurrent worker, retry payment, manual resolve,
   unauthorized và start bị chặn/mở lại.
8. Chạy static checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] Một session không tạo nhiều debt active.
- [ ] Manual change có actor/reason/audit.
- [ ] Start lock được gỡ đúng khi debt resolved.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 13: Public service điều kiện remote start/stop

**Mục tiêu:** Cung cấp business guard đầy đủ cho command API của domain trụ.

**Prompt:**

```text
Thực hiện Bước 13 của planner charging sessions sau khi session, pricing,
payment và debt services đã tồn tại.

Tạo public service functions để charging_stations gọi trong cùng AsyncSession:
1. authorize_remote_start(...):
   - validate actor/vehicle/idToken ownership;
   - chặn vehicle/driver có active session;
   - chặn unpaid debt quá hạn theo rule;
   - yêu cầu payment method hợp lệ nếu business rule đã chốt như vậy;
   - tạo start intent idempotent và chống concurrent request.
2. authorize_remote_stop(...):
   - resolve active session + OCPP transaction ID;
   - validate actor được phép dừng.
3. mark_control_command_result(...) và correlate_transaction_started(...):
   - release/confirm intent đúng state;
   - timeout/rejected không để intent khóa vĩnh viễn.
4. Giữ has_active_session_for_topology(...) tương thích với contract đã mở từ
   Bước 3.

Không gọi charging_stations và không import model/repository của domain đó.
Không gửi OCPP trong service này. Viết rõ transaction boundary, exception và
race invariant trong docstring/comment tiếng Việt.

Test concurrent start cùng vehicle, cùng EVSE, debt, payment method,
rejected/timeout/confirmed và stop sai actor. Chạy static checks và cập nhật
planner.
```

**Kiểm tra:**

- [ ] Concurrent start chỉ một intent thành công.
- [ ] Debt/payment guard dùng state authoritative trong database.
- [ ] Intent được giải phóng đúng mọi terminal path.
- [ ] Public contract không phụ thuộc OCPP dataclass/FastAPI.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 14: API đối soát và báo cáo tiền phiên

**Mục tiêu:** Cho Admin theo dõi doanh thu/payment mismatch.

**Prompt:**

```text
Thực hiện Bước 14 của planner charging sessions.

1. Implement read-only Admin APIs:
   - revenue summary theo station và khoảng thời gian;
   - payment transaction list/filter;
   - unpaid/failed/overdue debt list;
   - reconciliation mismatch/detail.
2. Định nghĩa rõ dùng started_at, completed_at hay paid_at cho từng báo cáo.
3. Tổng tiền dùng Decimal/SQL NUMERIC, timezone/filter UTC và currency không được
   cộng lẫn nếu chưa có exchange-rate contract.
4. Không N+1; có index/query plan cho filter phổ biến.
5. Export file nằm ngoài scope trừ khi đã được xác nhận; không thêm placeholder.
6. Test empty data, multi-station, multi-currency, refund nếu scope có, boundary
   date và quyền Admin kế toán.
7. Chạy static checks và cập nhật planner.
```

**Kiểm tra:**

- [ ] Báo cáo định nghĩa timestamp/currency rõ ràng.
- [ ] Tổng khớp payment/session fixtures.
- [ ] Không lộ provider secret/token.

**Kết quả thực tế:** Chưa thực hiện.

---

## Bước 15: E2E và nghiệm thu toàn bộ charging session

**Mục tiêu:** Kiểm chứng luồng từ remote start đến paid/debt.

**Prompt:**

```text
Thực hiện Bước 15, nghiệm thu toàn bộ planner charging sessions.

1. Chạy Black, isort, Ruff, mypy và compile.
2. Chạy migration upgrade/downgrade/upgrade.
3. Chạy E2E tối thiểu:
   a. remote start -> command accepted -> TransactionEvent Started;
   b. Updated/MeterValues -> Ended -> reconciliation -> pricing;
   c. payment success qua webhook -> paid;
   d. payment failure -> retry success;
   e. unpaid quá hạn -> debt -> chặn remote start -> resolve -> cho phép lại;
   f. remote stop accepted/rejected/timeout;
   g. hai EVSE chạy đồng thời;
   h. duplicate/out-of-order OCPP và webhook;
   i. station disconnect/interrupted session dùng meter cuối.
4. Dùng rg audit import boundary, __init__.py, HTTPException, commit/rollback,
   datetime.utcnow, float cho tiền/energy, logger f-string, secrets, card fields,
   raw payload và TODO/placeholder.
5. Review race/idempotency/partial failure/security/UTC/TimescaleDB/PostGIS.
6. Đối chiếu feature-list, business-rules-billing, planner stations và AGENTS.md.
7. Cập nhật kết quả thực tế từng bước; chỉ đánh dấu pass có bằng chứng.
8. Báo cáo rõ test dùng fake/sandbox provider và phần chưa test với trụ/provider
   production.
```

**Tiêu chí hoàn thành:**

- [ ] Phiên xử lý idempotent từ Started đến completed/interrupted.
- [ ] Remote start/stop có business guard và correlation.
- [ ] Giá deterministic, có snapshot và audit.
- [ ] Payment/webhook không double charge và không lộ dữ liệu thẻ.
- [ ] Overdue/debt/start lock đúng rule.
- [ ] API monitoring/reconciliation hoạt động và phân quyền đúng.
- [ ] Static checks, migration và E2E có bằng chứng.

**Kết quả thực tế:** Chưa thực hiện.
