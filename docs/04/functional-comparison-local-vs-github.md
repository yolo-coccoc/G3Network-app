# So sánh chức năng giữa workspace hiện tại và repository GitHub

> Ngày rà soát: 2026-09-15
>
> Workspace hiện tại: nội dung source khớp baseline `55e1342`; HEAD hiện tại là
> commit revert `fa7f6fc`.
>
> Repository đối chiếu: [`quanhlee123/g3-network`](https://github.com/quanhlee123/g3-network)

## Kết luận

Workspace hiện tại là backend MVP tập trung vào hai luồng:

```text
Vehicle → Telematic → Telemetry
Charging Station → Charging Session
```

Repository GitHub có phạm vi rộng hơn, gồm simulator/phase 1 và các nhóm như
cảnh báo, policy sạc, đối soát, thanh toán, RBAC, notification, portal và
observability. Các chức năng đó chỉ là tài liệu tham khảo; không được xem là đã
có trong workspace hiện tại.

## So sánh phạm vi backend

| Nhóm | Workspace hiện tại | Repository GitHub |
|---|---|---|
| Xe và telematic | CRUD, soft delete, mapping thiết bị–xe | CRUD/provisioning và nghiệp vụ đội xe mở rộng |
| Telemetry | MQTT QoS 0, validate, lưu TimescaleDB, API latest | Query/aggregate mở rộng, alert và vận hành thiết bị |
| Lịch sử telemetry | Chưa có API đọc toàn bộ lịch sử | Có các luồng lịch sử/monitoring mở rộng |
| Bản đồ xe/trạm | Chưa có API bản đồ | Có bản đồ và dữ liệu địa lý |
| Cảnh báo pin/bất thường | Chưa có | Có cảnh báo và chống lặp theo vòng đời |
| Trạm sạc | CRUD topology Station/EVSE/Connector | CRUD, trạng thái, vị trí và monitoring mở rộng |
| OCPP | OCPP 2.0.1, gateway riêng | Contract/protocol khác theo repository GitHub |
| Charging session | `Started → Updated/MeterValues → Ended`, event và meter | Có thêm policy, thanh toán, đối soát và nghiệp vụ vận hành |
| User/RBAC/driver | Chưa có | Có các lớp identity và phạm vi truy cập mở rộng |
| Frontend | Chưa có source portal/vehicle app | Có các thành phần UI/simulator theo phạm vi repository |
| Observability | Health endpoint và structured log cơ bản | Có hướng metrics/dashboard/load test mở rộng |

## Chức năng active trong workspace hiện tại

- CRUD xe và thiết bị telematic.
- Nhận telemetry qua EMQX và lưu `vehicle_telemetry`.
- `GET /api/v1/telemetry/vehicles/{vehicle_id}/latest`.
- CRUD station, EVSE và connector đã pre-provision.
- OCPP 2.0.1 gateway.
- Lưu và đọc charging session, lifecycle event, meter value.
- Alembic baseline `0001` đến `0004` và bộ smoke/integration test tối thiểu.

## Chức năng chưa active

Không suy ra các chức năng sau từ việc database đã lưu telemetry hoặc charging
session:

- Toàn bộ lịch sử telemetry, hành trình và dữ liệu aggregate.
- Bản đồ toàn bộ xe/trạm và trạng thái connector tổng hợp.
- Cảnh báo SOC, bất thường pin, anti-repeat và push ngưỡng tới thiết bị.
- Geofence, device health, notification, policy, reconciliation và payment.
- Identity, RBAC, driver, portal và vehicle app.

Các mục này được ghi nhận trong
[`docs/01-requirements/future.md`](../01-requirements/future.md) khi đã xác
định là hướng mở rộng, nhưng chưa có API/schema/migration active.

## Nguyên tắc tham khảo repository GitHub

- Chỉ tham khảo use case, dữ liệu đầu vào và các edge case có liên quan.
- Giữ stack, domain boundary, naming, transaction ownership và migration
  convention của workspace hiện tại.
- Không copy trực tiếp OCPP, MQTT contract hoặc migration giữa hai repository
  nếu chưa đối chiếu khác biệt protocol và schema.
