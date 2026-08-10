# G3Network

G3Network là hệ thống hỗ trợ tài xế và quản lý đội xe tải điện. Hệ thống gồm
backend, dữ liệu xe, trụ sạc OCPP và các simulator để chạy thử tại máy local.

## Cần chuẩn bị

- Docker Desktop đang được mở
- `uv`
- `make`

Mọi lệnh dưới đây được chạy tại thư mục gốc của dự án:

```bash
cd /home/duc/Workspace/G3Network-app
```

## 1. Khởi động toàn bộ hệ thống

### Chạy một lần khi cài đặt lần đầu

```bash
cp backend/.env.example backend/.env
make backend-install
```

### Khởi động hạ tầng

```bash
make infra-up
make db-migrate
```

`infra-up` khởi động PostgreSQL và EMQX bằng Docker. `db-migrate` tạo/cập nhật
các bảng database.

### Chạy các thành phần ứng dụng

Mỗi lệnh dưới đây chạy ở một Terminal riêng. Giữ các Terminal này hoạt động.

Terminal 1 — Backend API:

```bash
make backend-dev
```

Terminal 2 — Nhận dữ liệu từ thiết bị xe:

```bash
make telemetry-dev
```

Terminal 3 — Cổng kết nối trụ sạc OCPP 2.0.1:

```bash
make charging-ocpp-dev
```

Sau khi khởi động xong, hệ thống có các địa chỉ chính:

- API và Swagger: [http://localhost:8000/docs](http://localhost:8000/docs)
- Kiểm tra API: [http://localhost:8000/health](http://localhost:8000/health)
- Kết nối OCPP: `ws://localhost:9000/ocpp/<mã-trụ>`
- EMQX Dashboard: [http://localhost:18083](http://localhost:18083)

## 2. Chạy các simulator

Simulator chỉ dùng để tạo dữ liệu giả lập, không cần khi kết nối thiết bị xe
hoặc trụ sạc thật. Backend và EMQX phải đang chạy trước; riêng simulator trụ
sạc cần chạy thêm OCPP gateway.

### Simulator dữ liệu xe

Mở Terminal mới. Chạy lần đầu để tạo một xe và một thiết bị telematics mẫu:

```bash
cd backend
uv run python ../simulator/seed_simulator_devices.py
```

Sau đó chạy simulator gửi dữ liệu vị trí, tốc độ và pin qua MQTT:

```bash
uv run python ../simulator/telematic_simulator.py
```

Simulator chạy liên tục mỗi 5 giây. Nhấn `Ctrl+C` để dừng.

### Simulator trụ sạc và phiên sạc

Mở Terminal mới tại thư mục gốc. Tạo topology mẫu gồm một station, EVSE và
connector:

```bash
make charging-ocpp-seed
```

Topology mẫu có mã trụ `SIM-OCPP-001`, EVSE `1` và connector `1`. Lệnh này chỉ
cần chạy một lần; nếu gặp lỗi trùng mã thì topology đã tồn tại.

Chạy một phiên sạc giả lập:

```bash
make charging-ocpp-sim
```

Simulator sẽ kết nối vào gateway và gửi luồng bắt đầu sạc, chỉ số điện, cập
nhật rồi kết thúc sạc. Kết quả có thể xem trong log hoặc trên các API charging
trong Swagger.

### Kết nối trụ sạc thật

Trụ sạc thật cần được khai báo station, EVSE và connector trong Swagger trước,
sao cho mã OCPP trùng với cấu hình trên thiết bị. Sau đó cấu hình trụ sạc:

```text
OCPP URL:       ws://<địa-chỉ-máy-chạy-backend>:9000/ocpp/<mã-trụ>
Protocol:       ocpp2.0.1
```

Trong môi trường local, trụ sạc phải truy cập được máy đang chạy backend.

## Dừng hệ thống

Nhấn `Ctrl+C` ở các Terminal đang chạy ứng dụng, sau đó dừng Docker:

```bash
make infra-down
```

Dữ liệu vẫn được giữ lại. Không dùng `make infra-reset` hoặc `make db-reset`
nếu chưa muốn xoá dữ liệu.

## Cấu trúc chính

```text
backend/       Backend FastAPI
infra/         PostgreSQL và EMQX
simulator/     Simulator dữ liệu xe và trụ sạc
vehicle-app/   Ứng dụng Flutter trên xe
web-portal/    Cổng quản trị React
docs/          Tài liệu yêu cầu và kế hoạch triển khai
```

Xem thêm: [AGENTS.md](./AGENTS.md) và
[danh sách chức năng](./docs/01-requirements/feature-list.md).
