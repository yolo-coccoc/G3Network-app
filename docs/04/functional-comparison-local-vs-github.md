# So sánh chức năng giữa workspace hiện tại và repository GitHub

> Ngày so sánh: 2026-08-26
>
> Workspace hiện tại: commit `b03dc82`
>
> Repository đối chiếu: [`quanhlee123/g3-network`](https://github.com/quanhlee123/g3-network), commit `7a0a55a`

## 1. Kết luận ngắn

Hai repository không phải là hai phiên bản gần nhau của cùng một phạm vi chức
năng. Workspace hiện tại là backend MVP tập trung vào luồng:

```text
Vehicle → Telematic → Telemetry
Charging Station → Charging Session
```

Repository GitHub đã mở rộng thành Phase 1 simulator với nhiều chức năng hơn:
cảnh báo pin, phát hiện bất thường, policy sạc, vi phạm, đối soát, thanh toán
sandbox, RBAC, portal, SOS, provisioning, notification và observability.

Repository GitHub vẫn chưa phải production hoàn chỉnh. Tài liệu bàn giao của
repository đó ghi rõ trạng thái gồm chức năng hoàn thành trên mock, sandbox,
interface-only, một phần và chưa làm.

## 2. So sánh theo nhóm chức năng

| Nhóm chức năng | Workspace hiện tại | Repository GitHub |
|---|---|---|
| Quản lý xe | CRUD xe, soft delete, VIN, biển số, trạng thái | CRUD, lọc đội xe, xem bản đồ, giới hạn theo phạm vi đội |
| Quản lý tài xế | Chưa có | Chưa làm đầy đủ |
| Quản lý Telematic | CRUD, gán Telematic vào xe bằng VIN | Provisioning theo VIN, health scan, trạng thái thiết bị |
| Nhận telemetry | MQTT, validate payload, lưu telemetry, truy vấn bản ghi mới nhất | MQTT, version payload, quarantine bản tin lỗi, phát hiện lệch đồng hồ, LWT online/offline |
| Cảnh báo pin | Chưa có | Cảnh báo SOC 30% / 20% / 10%, chống spam theo vòng đời cảnh báo |
| Bất thường pin | Chưa có | Phát hiện nhiệt độ pin và lưu snapshot dữ liệu trước sự kiện |
| Vị trí/geofence | Lưu GPS trong telemetry | Geofence theo xe/đội, trạng thái vào/ra vùng |
| Sức khỏe pin SOH | Chưa có | Chưa làm |
| Trạm sạc | CRUD Station/EVSE/Connector | CRUD trạm, trạng thái trạm, vị trí trạm |
| Phiên sạc | Lifecycle `Started → MeterValues → Ended` | Phiên sạc, trạng thái realtime, giao tiếp CSMS |
| OCPP | OCPP 2.0.1 | OCPP 1.6J |
| Meter sạc | Lưu meter Wh và energy delivered | Lưu meter để đối soát và thanh toán |
| Chính sách sạc | Chưa có | Policy theo xe/đội, có version policy |
| Vi phạm sạc | Chưa có | Phát hiện sai khung giờ, SOC quá cao/thấp và sạc bất thường |
| Bảo hành | Chưa có | Cảnh báo nguy cơ mất bảo hành; bảng trạng thái bảo hành đầy đủ chưa làm |
| Đối soát điện | Chưa có | Đối soát 3 chiều: trụ sạc – xe – thanh toán |
| Thanh toán | Chưa có | Mock/VNPay sandbox, xử lý webhook trùng hoặc đến trễ |
| Hóa đơn điện tử | Chưa có | Chưa làm |
| Authentication/RBAC | Chưa có, nằm ngoài local MVP | OTP, JWT, RBAC mặc định từ chối, khóa tài khoản |
| Audit log | Chưa có | Có audit log truy cập vị trí; vẫn còn một số đường đi chưa audit đủ |
| Thông báo | Chưa có | Notification, push/SMS mock và rate limit |
| Portal đội xe | Chưa có source thực tế | Có portal: tổng quan, bản đồ, danh sách xe và cảnh báo |
| App tài xế | Chưa có source thực tế | Có khung React Native/Expo, chưa có màn hình hoàn chỉnh |
| SOS | Chưa có | SOS mock, tạo ticket kèm vị trí/SOC/mã lỗi |
| CSKH/Ticket | Chưa có | Có hạ tầng ticket một phần, SLA job, chưa hoàn thiện toàn bộ nghiệp vụ |
| Thiết bị offline/tamper | Chưa có | Phân biệt mất nguồn với mất sóng bằng MQTT LWT |
| KPI đội xe | Chưa có | Chưa làm đầy đủ |
| Báo cáo theo trạm | Chưa có | Chưa làm đầy đủ |
| Quan sát hệ thống | Chưa có Prometheus/Grafana | Có `/health`, `/metrics`, Prometheus và Grafana |
| Load test | Chưa có | Có simulator/load test 300 xe |

## 3. Các chức năng workspace hiện tại đã có

Workspace hiện tại đã hoàn thành và kiểm chứng được các chức năng lõi sau:

- CRUD vehicle và telematic.
- Gán thiết bị Telematic vào xe.
- Nhận telemetry qua MQTT.
- Validate và lưu telemetry vào TimescaleDB.
- Truy vấn telemetry mới nhất của xe.
- CRUD topology trạm sạc.
- OCPP happy path.
- Lưu charging session, event và meter value.
- Migration baseline cho database.
- Unit/smoke test và PostgreSQL integration test.
- End-to-end telemetry và charging qua MQTT, OCPP, API và PostgreSQL.

Nếu đối chiếu theo nhóm chức năng của repository GitHub, phần này tương ứng
chủ yếu với:

- `F-A1`: telemetry realtime.
- Một phần `F-C1`: quản lý trạm sạc.
- Một phần `F-C2`: trạng thái OCPP.
- `F-B2`: ghi nhận phiên sạc ở mức cơ bản.

## 4. Các chức năng repository GitHub có thêm

### 4.1. Cảnh báo và an toàn vận hành

Repository GitHub có các chức năng chưa tồn tại trong workspace hiện tại:

- Cảnh báo pin theo ngưỡng 30%, 20% và 10%.
- Chống gửi lặp cảnh báo trong cùng một vòng đời.
- Phát hiện nhiệt độ pin bất thường.
- Lưu snapshot dữ liệu trước thời điểm bất thường.
- Geofence theo xe và đội xe.
- Phân biệt thiết bị mất nguồn với thiết bị mất sóng.

### 4.2. Chính sách sạc, vi phạm và bảo hành

Repository GitHub có thêm một lớp nghiệp vụ phía trên charging session:

- Cấu hình policy sạc theo xe hoặc đội.
- Lưu version policy thay vì sửa đè policy cũ.
- Phát hiện sạc ngoài khung giờ cho phép.
- Phát hiện SOC vượt ngưỡng.
- Gắn cờ vi phạm và lưu bằng chứng.
- Cảnh báo nguy cơ mất bảo hành.
- Đối soát ba chiều giữa trụ sạc, xe và thanh toán.

Workspace hiện tại chỉ lưu lifecycle kỹ thuật của phiên sạc, chưa đánh giá
phiên sạc theo policy hoặc nghiệp vụ bảo hành.

### 4.3. Người dùng và vận hành

Repository GitHub có thêm:

- Đăng nhập OTP.
- JWT.
- RBAC theo vai trò và phạm vi đội xe.
- Khóa tài khoản có hiệu lực với token đang tồn tại.
- Audit log khi truy cập vị trí xe.
- Notification và rate limit cho push/SMS mock.
- Portal quản lý đội xe.
- Provisioning xe theo VIN.
- SOS và ticket hỗ trợ.

Workspace hiện tại chưa có identity, driver, fleet, support hoặc notification
domain.

### 4.4. Thanh toán và quan sát hệ thống

Repository GitHub có:

- Payment mock.
- VNPay sandbox.
- Xử lý webhook đến trùng hoặc đến trước khi phiên sạc đóng.
- Prometheus.
- Grafana.
- Metrics cho API, ingest và CSMS.
- Load test 300 xe.

Workspace hiện tại chưa có payment, metrics dashboard hoặc load test.

## 5. Khoảng chức năng còn thiếu nếu lấy GitHub làm mục tiêu

Theo thứ tự phụ thuộc nghiệp vụ, workspace hiện tại còn thiếu các lớp sau:

1. Cảnh báo pin và phát hiện bất thường.
2. Geofence và trạng thái thiết bị offline.
3. Policy sạc, vi phạm và cảnh báo bảo hành.
4. Đối soát sản lượng điện.
5. Authentication, RBAC và audit log.
6. Notification.
7. Portal đội xe.
8. Payment sandbox.
9. SOS/ticket.
10. Observability và load test.

Không nên triển khai tất cả cùng lúc. Cảnh báo pin là bước gần nhất với source
hiện tại vì có thể nhận trực tiếp từ dữ liệu telemetry đang được ingest.

## 6. Những điểm khác nhau cần lưu ý khi tham khảo code

### OCPP

Workspace dùng OCPP 2.0.1 với `TransactionEvent`. Repository GitHub dùng OCPP
1.6J với `StartTransaction` và `StopTransaction`. Simulator và gateway của hai
repository không tương thích trực tiếp.

### Telemetry

Workspace resolve theo `telematic_serial` và mapping sang `vehicle_id`.
Repository GitHub dùng VIN trong MQTT topic, có `schema_version`, quarantine,
status topic và LWT. Đây là khác biệt về contract dữ liệu, không chỉ khác tên
field.

### Charging

Workspace mới quản lý topology, session, event và meter. Repository GitHub đặt
thêm policy, thanh toán, vi phạm và đối soát lên trên phiên sạc.

### Migration

Workspace có 4 migration baseline để khởi tạo MVP. Repository GitHub có 29
migration mở rộng, bao gồm policy, payment, alert, audit, provisioning,
geofence và các trigger append-only.

## 7. Đánh giá trạng thái GitHub

Không nên hiểu “hoàn thành” trong repository GitHub là production-ready. Bảng
trạng thái của repository đó ghi rõ nhiều hạng mục mới chạy trên mock hoặc
sandbox:

- Chưa có thiết bị thật.
- Chưa có tiền thật.
- VNPay chỉ chạy sandbox.
- App tài xế mới có khung, chưa có màn hình hoàn chỉnh.
- Một số yêu cầu về encryption, mTLS, cold retention và Alertmanager còn thiếu.
- Một số vấn đề audit và bảo mật đã được ghi trong debt register.

Tham khảo:

- [Bảng trạng thái chức năng](https://github.com/quanhlee123/g3-network/blob/main/docs/handover/feature-status.md)
- [Tổng quan hệ thống](https://github.com/quanhlee123/g3-network/blob/main/docs/handover/system-overview.md)
- [Sổ nợ kỹ thuật](https://github.com/quanhlee123/g3-network/blob/main/docs/handover/debt-register.md)

## 8. Kết luận sử dụng

- Nếu mục tiêu là backend Charging MVP gọn: tiếp tục dùng workspace hiện tại.
- Nếu mục tiêu là mở rộng thành sản phẩm Phase 1: dùng danh sách chức năng của
  repository GitHub làm nguồn tham khảo về phạm vi, nhưng phải thiết kế lại
  theo stack và contract đã chọn trong workspace.
- Không nên copy từng file hoặc cherry-pick code trực tiếp giữa hai repository.
- Bước chức năng kế tiếp được đề xuất cho workspace hiện tại là **cảnh báo pin**,
  sau đó là **policy sạc và RBAC**.
