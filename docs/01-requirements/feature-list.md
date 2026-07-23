# Danh sách chức năng — Hệ thống hỗ trợ tài xế xe tải điện

Trạng thái: ✅ Đã có | 🚧 Đang làm | 📋 Dự kiến | 💡 Đề xuất bổ sung (cần bạn xác nhận có cần không)

---

## 1. App màn hình trên xe (In-vehicle display)

### 1.1 Hiển thị trạng thái xe real-time
- Actor: Tài xế
- Trigger: Liên tục khi xe bật, mỗi khi có telemetry mới
- Input: telemetry stream (% pin, tốc độ, quãng đường còn lại)
- Output: Hiển thị trực quan trên màn hình xe
- Trạng thái: 🚧

### 1.2 Cảnh báo pin yếu
- Actor: Tài xế
- Trigger: % pin xuống dưới ngưỡng cấu hình
- Input: % pin hiện tại, vị trí GPS xe
- Output: Popup cảnh báo + gợi ý trụ sạc gần nhất
- Ràng buộc:
  - Ngưỡng cảnh báo nên cấu hình được, không hardcode — có thể khác nhau theo loại xe/tuyến đường
  - Gợi ý trụ sạc cần tính cả trạng thái trụ (còn slot trống hay không), không chỉ dựa vào khoảng cách
- Trạng thái: 📋

### 1.3 Xác nhận bắt đầu ca lái (đồng bộ với chấm công trên mobile)
- Actor: Tài xế
- Trigger: Khởi động xe
- Input: trạng thái chấm công của tài xế (lấy từ mobile app)
- Output: Cho phép hoặc chặn khởi động xe
- Cần chốt nghiệp vụ:
  - Có **bắt buộc** chấm công trước khi khởi động xe không, hay chỉ hiển thị cảnh báo?
  - Nếu bắt buộc: xử lý thế nào khi mobile app mất kết nối lúc chấm công (tránh tài xế bị kẹt không khởi động được xe)?
- Trạng thái: 📋

### 1.4 Cảnh báo lỗi kỹ thuật xe
- Actor: Tài xế
- Trigger: Telemetry báo mã lỗi
- Input: error_code từ xe
- Output: Cảnh báo phân theo mức độ (nhẹ/nghiêm trọng)
- Ràng buộc: Lỗi mức nghiêm trọng có thể chặn tiếp tục lái (an toàn), cần danh sách mã lỗi và mức độ tương ứng
- Trạng thái: 💡

### 1.5 Chỉ đường tới trụ sạc
- Actor: Tài xế
- Trigger: Tài xế chọn 1 trụ từ danh sách gợi ý
- Input: station_id, vị trí hiện tại của xe
- Output: Hiển thị bản đồ/chỉ đường trên màn hình xe
- Ràng buộc: Cần tích hợp nhà cung cấp bản đồ (Google Maps/Mapbox...)
- Trạng thái: 💡

### 1.6 Hiển thị trạng thái phiên sạc khi cắm
- Actor: Tài xế
- Trigger: Phiên sạc bắt đầu (session được tạo)
- Input: session_id, dữ liệu điện năng real-time
- Output: % pin hiện tại, thời gian ước tính đầy
- Trạng thái: 💡

### 1.7 Cảnh báo hết giờ lái theo quy định
- Actor: Tài xế
- Trigger: Ca lái vượt ngưỡng thời gian cho phép
- Input: shift_id, thời lượng ca hiện tại
- Output: Cảnh báo + nhắc nghỉ
- Ràng buộc: Liên quan đến quy định giờ lái/an toàn lao động, ngưỡng cần cấu hình được
- Trạng thái: 💡

---

## 2. App di động cho tài xế

### 2.1 Đăng nhập / chấm công (clock-in)
- Actor: Tài xế
- Trigger: Tài xế mở app, chọn xe khi lên ca
- Input: driver_id, truck_id, phương thức xác thực (PIN/tài khoản/NFC)
- Output: Tạo bản ghi "ca lái xe" mới, gắn driver_id + truck_id + thời gian bắt đầu
- Ràng buộc:
  - 1 tài xế không thể mở 2 ca cùng lúc
  - 1 xe không thể có 2 tài xế đang active cùng lúc
- Trạng thái: 🚧

### 2.2 Chấm công kết thúc ca (clock-out)
- Actor: Tài xế
- Trigger: Tài xế nhấn "kết thúc ca"
- Input: shift_id
- Output: Ca chuyển trạng thái `ended`
- Ràng buộc: Không cho kết thúc ca nếu đang trong phiên sạc dở dang
- Trạng thái: 🚧

### 2.3 Xem lịch sử phiên sạc & thanh toán
- Actor: Tài xế
- Trigger: Mở tab lịch sử trong app
- Input: driver_id
- Output: Danh sách phiên sạc đã thực hiện kèm số tiền, trạng thái thanh toán
- Trạng thái: 📋

### 2.4 Thanh toán sau khi sạc
- Actor: Tài xế
- Trigger: Phiên sạc chuyển trạng thái `completed`
- Input: session_id, phương thức thanh toán đã lưu hoặc chọn mới
- Output: Tạo giao dịch thanh toán, session chuyển `paid`
- Ràng buộc: Không cho thanh toán khi session chưa `completed`; số tiền tính theo công thức tại `docs/business-rules-billing.md`
- Trường hợp đặc biệt:
  - Thanh toán thất bại → session giữ trạng thái `pending_payment`, KHÔNG tự huỷ, tài xế được thử lại
  - Quá 24h không thanh toán → hệ thống tạo công nợ, khoá tính năng bắt đầu phiên sạc mới (cần admin duyệt mở lại — liên quan mục 3.6)
- Trạng thái: 📋

### 2.5 Thông báo đẩy (push notification)
- Actor: Tài xế
- Trigger: Pin yếu, trụ sạc lỗi, nhắc thanh toán, sắp hết giờ ca quy định
- Input: —
- Output: Gửi thông báo qua FCM/APNs
- Trạng thái: 📋

### 2.6 Xem lịch sử ca lái
- Actor: Tài xế
- Trigger: Mở tab lịch sử ca
- Input: driver_id
- Output: Danh sách ca đã lái, giờ vào/ra, xe đã sử dụng
- Trạng thái: 💡

### 2.7 Quản lý phương thức thanh toán
- Actor: Tài xế
- Trigger: Vào mục cài đặt
- Input: thông tin thẻ/ví điện tử
- Output: Thêm/xoá/đặt mặc định phương thức thanh toán
- Ràng buộc: Không lưu thông tin thẻ thô trong hệ thống, xử lý qua cổng thanh toán (tokenization)
- Trạng thái: 💡

### 2.8 Tìm trụ sạc gần nhất
- Actor: Tài xế
- Trigger: Tài xế chủ động tìm kiếm (không phải do cảnh báo pin yếu)
- Input: vị trí hiện tại
- Output: Danh sách trụ sạc kèm trạng thái available/busy
- Ràng buộc: Cần dữ liệu trạng thái trụ theo thời gian thực
- Trạng thái: 💡

### 2.9 Đặt trước trụ sạc (reservation)
- Actor: Tài xế
- Trigger: Chọn 1 trụ và khung giờ mong muốn
- Input: station_id, thời gian đặt
- Output: Giữ chỗ trụ trong khoảng thời gian đã chọn
- Ràng buộc: Cần xử lý huỷ giữ chỗ tự động nếu tài xế không tới đúng giờ
- Trạng thái: 💡

### 2.10 Báo cáo sự cố (incident report)
- Actor: Tài xế
- Trigger: Gặp sự cố với xe hoặc trụ sạc
- Input: mô tả, ảnh đính kèm, vị trí
- Output: Tạo ticket gửi tới admin (liên quan mục 3.11)
- Trạng thái: 💡

### 2.11 Xem điểm hiệu suất lái xe
- Actor: Tài xế
- Trigger: Mở tab thống kê cá nhân
- Input: driver_id
- Output: Điểm số dựa trên hành vi lái (tốc độ, phanh gấp, tăng tốc đột ngột...)
- Ràng buộc: Cần định nghĩa rõ công thức chấm điểm trước khi triển khai (xem mục 4.10)
- Trạng thái: 💡

---

## 3. Trang web Admin

### 3.1 Dashboard giám sát real-time
- Actor: Admin
- Trigger: Mở dashboard
- Input: telemetry stream từ toàn bộ xe/trụ
- Output: Bản đồ vị trí xe, trạng thái từng trụ sạc, danh sách phiên sạc đang diễn ra
- Ràng buộc:
  - Cần quyết định cơ chế đẩy dữ liệu lên UI: WebSocket đẩy trực tiếp hay polling định kỳ (tuỳ số lượng xe/trụ)
  - Không hiển thị raw telemetry (tần suất mỗi vài giây) trực tiếp — cần tầng tổng hợp/throttle riêng trước khi lên UI
- Trạng thái: 🚧

### 3.2 Quản lý tài xế (CRUD)
- Actor: Admin
- Trạng thái: ✅

### 3.3 Quản lý xe (CRUD, gán thiết bị)
- Actor: Admin
- Trạng thái: ✅

### 3.4 Quản lý trụ sạc (CRUD, gán thiết bị)
- Actor: Admin
- Trạng thái: ✅

### 3.5 Báo cáo & thống kê
- Actor: Admin
- Trigger: Chọn bộ lọc (xe/tài xế/trụ, khoảng thời gian)
- Output: Biểu đồ giờ lái, mức tiêu hao pin, doanh thu theo trụ sạc
- Trạng thái: 📋

### 3.6 Quản lý công nợ / đối soát thanh toán
- Actor: Admin kế toán
- Trigger: Mở màn hình công nợ
- Input: driver_id hoặc khoảng thời gian
- Output: Danh sách công nợ, đối soát giao dịch thanh toán
- Ràng buộc: Liên quan trực tiếp tới trường hợp đặc biệt tại mục 2.4 (quá hạn thanh toán)
- Trạng thái: 📋

### 3.7 Quản lý cảnh báo hệ thống
- Actor: Admin
- Trigger: Alert được hệ thống tạo ra (mục 4.5)
- Output: Danh sách cảnh báo, trạng thái xử lý
- Ràng buộc:
  - Các loại alert tối thiểu: xe mất kết nối quá X phút, trụ sạc lỗi, pin dưới ngưỡng nguy hiểm, phiên sạc dừng bất thường
  - Cần cơ chế đánh dấu "đã xử lý/bỏ qua" để tránh admin bị spam cảnh báo lặp lại
- Trạng thái: 📋

### 3.8 Quản lý ca lái xe (xem/điều chỉnh)
- Actor: Admin
- Trigger: Xem lịch sử ca, hoặc phát hiện sai sót cần sửa
- Input: driver_id/truck_id
- Output: Danh sách ca lái, cho phép điều chỉnh thủ công
- Ràng buộc: Mọi điều chỉnh thủ công phải ghi vào audit log (mục 4.8) vì ảnh hưởng tới lương/công nợ
- Trạng thái: 💡

### 3.9 Phân quyền admin (role-based access)
- Actor: Admin cấp cao
- Trigger: Quản lý tài khoản admin khác
- Output: Giới hạn chức năng theo vai trò
- Ràng buộc:
  - Tối thiểu 2 role: admin vận hành (thiết bị, tài xế) và admin kế toán (công nợ, thanh toán)
  - Cần xác định rõ role nào được phép điều chỉnh ca lái (mục 3.8) vì đây là thao tác nhạy cảm
- Trạng thái: 💡

### 3.10 Quản lý bảo trì xe/trụ (maintenance)
- Actor: Admin vận hành
- Trigger: Lên lịch bảo trì hoặc ghi nhận đã bảo trì
- Input: truck_id/station_id, ngày bảo trì
- Output: Lịch sử bảo trì, nhắc lịch bảo trì định kỳ
- Ràng buộc: Có thể dựa trên số km đã đi hoặc số giờ vận hành, không chỉ theo ngày
- Trạng thái: 💡

### 3.11 Quản lý sự cố (incident) từ tài xế
- Actor: Admin
- Trigger: Ticket mới được tạo từ mục 2.10
- Output: Xem, phân công người xử lý, đóng ticket
- Trạng thái: 💡

### 3.12 Xuất báo cáo (export)
- Actor: Admin
- Trigger: Nhấn nút xuất trên màn hình báo cáo
- Output: File CSV/Excel/PDF
- Trạng thái: 💡

### 3.13 Quản lý depot/bãi xe
- Actor: Admin
- Output: Gán xe/trụ/tài xế theo từng địa điểm vận hành
- Ràng buộc: Chỉ cần triển khai nếu hệ thống vận hành nhiều địa điểm — cần xác nhận
- Trạng thái: 💡

### 3.14 Cấu hình ngưỡng cảnh báo hệ thống
- Actor: Admin
- Trigger: Vào mục cài đặt hệ thống
- Output: Cập nhật ngưỡng pin yếu, ngưỡng mất kết nối, ngưỡng thời gian ca lái...
- Trạng thái: 💡

---

## 4. Hệ thống nền (không có UI riêng, nhưng là chức năng cốt lõi)

### 4.1 Nhận & lưu telemetry xe tải (streaming)
- Trigger: Xe gửi dữ liệu định kỳ (mỗi vài giây)
- Input: truck_id, % pin, tốc độ, GPS, mã lỗi (nếu có)
- Output: Ghi vào time-series DB, cập nhật trạng thái mới nhất vào cache
- Xem chi tiết: `docs/data-flow-telemetry.md`
- Trạng thái: 🚧

### 4.2 Nhận & lưu telemetry trụ sạc (streaming)
- Trigger: Trụ gửi dữ liệu định kỳ
- Input: station_id, trạng thái, công suất đang cấp, nhiệt độ
- Output: Ghi vào time-series DB
- Xem chi tiết: `docs/data-flow-telemetry.md`
- Trạng thái: 🚧

### 4.3 Quản lý vòng đời phiên sạc
- Trigger: Xe cắm sạc vào trụ
- Input: truck_id, station_id, dữ liệu điện năng theo thời gian
- Output: Tạo/cập nhật/chốt phiên sạc (session)
- Trường hợp đặc biệt:
  - Trụ có thể gửi event `session_end` **trước khi** xe gửi telemetry cuối cùng — hệ thống phải đợi đủ dữ liệu từ cả 2 nguồn trước khi chốt phiên
  - Nếu mất kết nối giữa chừng: tính tiền theo dữ liệu cuối cùng nhận được, KHÔNG hoàn tác phiên sạc
- Xem chi tiết state machine đầy đủ: `docs/domain-glossary.md`
- Trạng thái: 🚧

### 4.4 Tính tiền phiên sạc
- Trigger: Phiên sạc chuyển trạng thái `completed`
- Input: dữ liệu điện năng tiêu thụ của session
- Output: Số tiền cần thanh toán
- Công thức: xem `docs/business-rules-billing.md`
- Trạng thái: 📋

### 4.5 Cảnh báo hệ thống (alerting)
- Trigger: Dữ liệu telemetry/trạng thái thiết bị vượt ngưỡng bất thường
- Output: Tạo alert, gửi notification tới admin/tài xế liên quan
- Xem chi tiết loại alert: mục 3.7
- Trạng thái: 📋

### 4.6 Xác thực & phân quyền thiết bị
- Trigger: Xe/trụ kết nối vào hệ thống
- Input: device certificate hoặc API key riêng cho từng thiết bị
- Output: Cho phép hoặc từ chối kết nối
- Ràng buộc: Cơ chế xác thực thiết bị khác hoàn toàn với xác thực người dùng (driver/admin)
- Trạng thái: 💡

### 4.7 Đồng bộ dữ liệu khi mất kết nối (offline sync)
- Trigger: Kết nối mạng của xe/trụ được khôi phục sau gián đoạn
- Input: dữ liệu buffer lưu cục bộ trong thời gian mất kết nối
- Output: Đẩy dữ liệu tồn đọng lên server
- Cần chốt nghiệp vụ:
  - Xe/trụ có buffer dữ liệu cục bộ khi mất mạng không, hay chấp nhận mất dữ liệu trong khoảng thời gian đó?
  - Nếu có buffer: dữ liệu gửi trễ (out-of-order) cần được xử lý theo đúng thời điểm phát sinh thực tế, không phải thời điểm nhận được ở server
- Trạng thái: 💡

### 4.8 Ghi audit log
- Trigger: Mọi thao tác quan trọng (điều chỉnh ca lái, thanh toán, thay đổi cấu hình...)
- Output: Bản ghi log không thể sửa/xoá, gồm actor, hành động, thời gian
- Ràng buộc: Bắt buộc áp dụng cho mục 3.8 (điều chỉnh ca lái) và mọi giao dịch thanh toán
- Trạng thái: 💡

### 4.9 Job dọn dẹp/nén dữ liệu telemetry cũ
- Trigger: Chạy định kỳ (cron job)
- Input: dữ liệu telemetry quá X ngày
- Output: Downsample hoặc archive dữ liệu cũ để giảm dung lượng
- Chính sách retention: xem `docs/data-schema.md`
- Trạng thái: 💡

### 4.10 Tính điểm hiệu suất lái xe
- Trigger: Sau mỗi ca lái kết thúc
- Input: telemetry trong ca (tốc độ, phanh gấp, tăng tốc đột ngột...)
- Output: Cập nhật điểm số hiển thị ở mục 2.11
- Ràng buộc: Cần định nghĩa rõ công thức chấm điểm trước khi code
- Trạng thái: 💡

### 4.11 Webhook/tích hợp cổng thanh toán
- Trigger: Cổng thanh toán trả kết quả giao dịch (callback)
- Input: payload từ payment gateway
- Output: Cập nhật trạng thái giao dịch tương ứng
- Trạng thái: 💡

---

## Các mục 💡 cần xác nhận trước khi triển khai

Trước khi giao việc cho Claude Code viết các chức năng này, nên tự trả lời (hoặc thảo luận với team) các câu hỏi nghiệp vụ sau — vì đây là quyết định chủ quan, Claude không thể tự đoán đúng:

1. Có bắt buộc chấm công trước khi khởi động xe không? (mục 1.3)
2. Xử lý thế nào khi quá hạn thanh toán? Có khoá tài khoản không? (mục 2.4, 3.6)
3. Có cần tính năng đặt trước trụ sạc không, hay chỉ hiển thị trạng thái? (mục 2.9)
4. Có cần chấm điểm hiệu suất lái xe không — nếu có, dựa trên tiêu chí gì? (mục 2.11, 4.10)
5. Hệ thống có nhiều depot/bãi xe hay chỉ 1 địa điểm? (mục 3.13)
6. Thiết bị (xe/trụ) có cần buffer dữ liệu khi mất mạng không? (mục 4.7)