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

## Quy tắc cập nhật

1. **Khi nào ghi nhận**: Khi developer hoặc AI agent quyết định bỏ qua/xóa một thành phần với lý do "hiện tại chưa cần, nhưng sau này chắc chắn phải thêm".
2. **Không ghi nhận khi**: Thành phần đó thực sự không cần cho hệ thống (không có kế hoạch thêm trong tương lai).
3. **Format**: Thêm entry mới theo mẫu trên, đánh số thứ tự tiếp theo.
4. **Rà soát**: Định kỳ (VD: mỗi sprint) review lại file này để lên kế hoạch triển khai.
