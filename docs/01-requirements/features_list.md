# Bảng tổng hợp chức năng — Hệ thống hỗ trợ tài xế lái xe tải điện

Tài liệu này tổng hợp, sắp xếp lại và bổ sung từ hai bảng chức năng gốc: **Chức_năng_origin.xlsx** (soạn theo luồng nghiệp vụ) và **Chức_năng_update.xlsx** (soạn theo actor), đã qua 2 vòng review. Các chức năng được sắp xếp theo mức độ ưu tiên: **(1) Streaming dữ liệu từ Xe điện & Trụ sạc về server → (2) Admin (Quản trị tổng) → (3) Tài xế (màn hình trên xe) → (4) Quản lý đội xe (web).**

Trạng thái của mỗi chức năng là **"Đang chờ review"** hoặc **"Cần thông tin"** (chờ bổ sung quy trình/số liệu nghiệp vụ cụ thể trước khi triển khai chi tiết — xem thêm ở mục **Ghi chú** của từng chức năng).

---

## NHÓM 1 — XE ĐIỆN TẢI (thiết bị telematics trên xe): Streaming dữ liệu về server — Ưu tiên cao nhất

### `V-01` — Thu thập & gửi dữ liệu vận hành xe realtime

- **Actor:** Xe điện tải
- **Chức năng:** Thu thập & gửi dữ liệu vận hành xe realtime
- **Mô tả:** Thiết bị telematics trên xe liên tục thu thập và gửi về server 4 nhóm dữ liệu: (1) Pin – SOC/SOH, điện áp/nhiệt độ, dòng sạc–xả; (2) Trạng thái xe – motor, odometer, mã lỗi BMS, trạng thái khởi động/tắt máy (ignition on/off); (3) Hành trình – GPS, tốc độ, lộ trình, cảnh báo ra/vào geofence; (4) Tín hiệu kết nối (heartbeat/last-seen) để server biết thiết bị còn hoạt động — được suy ra từ timestamp của mỗi lần gửi dữ liệu định kỳ, không cần một cơ chế gửi riêng.
- **Trigger:** Xe bật nguồn / thiết bị telematics có kết nối mạng; gửi định kỳ theo chu kỳ cấu hình.
- **Input:** tín hiệu cảm biến BMS, GPS, odometer, mã lỗi hệ thống xe, tín hiệu khởi động/tắt máy (ignition).
- **Output:** bản ghi telemetry gửi lên server qua giao diện IoT (interface thay được mock ↔ thật), kèm timestamp làm căn cứ last-seen và trạng thái ignition on/off.
- **Alternative Output:** Mất kết nối mạng → dữ liệu lưu đệm cục bộ (buffer) và đồng bộ bù khi có sóng trở lại; cờ trạng thái chuyển "offline". Mất nguồn đột ngột (khác với tắt máy chủ động) được xử lý như một sự kiện bất thường — xem V-02.
- **Ràng buộc nghiệp vụ:** Cập nhật ≤30s (p95) khi online; SOH + số chu kỳ cập nhật ≥1 lần/ngày; lưu lịch sử ≥12 tháng (hot), lộ trình ≥6 tháng; schema dữ liệu có version; geofence cấu hình theo xe/đội; phân biệt được trạng thái tắt máy chủ động (ignition off) với mất kết nối bất thường, làm căn cứ cho AD-02/AD-06 phân biệt xe im lặng tự nhiên và xe tắt máy.
- **Phụ thuộc (cần có chức năng nào trước):** Kích hoạt thiết bị theo VIN khi bàn giao xe (AD-05); hoàn tất đặc tả tích hợp telematics Tri-Ring (Gate 0).
- **Mô tả luồng:**  
  (1) Cảm biến & ECU trên xe sinh dữ liệu (kể cả tín hiệu ignition) → (2) Thiết bị đóng gói theo schema chuẩn → (3) Gửi định kỳ lên server qua kênh IoT → (4) Server xác thực, gắn version, cập nhật last-seen, lưu vào kho dữ liệu realtime & lịch sử → (5) Nếu mất sóng, dữ liệu giữ trong buffer thiết bị và gửi bù khi có kết nối lại.
- **Trạng thái:** Đang chờ review
- **Ghi chú:** Đã gộp nội dung heartbeat/last-seen từ V-03 cũ (V-03 đã bỏ, xem mục Ghi chú lược bỏ).

### `V-02` — Phát hiện & gửi cảnh báo bất thường tại thiết bị

- **Actor:** Xe điện tải
- **Chức năng:** Phát hiện & gửi cảnh báo bất thường tại thiết bị
- **Mô tả:** Thiết bị tự phát hiện các bất thường nghiêm trọng (nhiệt độ pin cao, sụt áp đột ngột, lỗi cell/module, lỗi motor, mất nguồn đột ngột/nghi bị tháo thiết bị) và gửi cảnh báo theo 2 luồng song song, không phụ thuộc lẫn nhau: (1) gửi ưu tiên về server để lưu log & kích hoạt các luồng liên quan (AD-02 giám sát, D-05 cảnh báo tài xế...); (2) gửi thẳng, tức thời tới màn hình trên xe qua kết nối cục bộ giữa thiết bị telematics và màn hình xe (2 phần cứng riêng biệt) — không cần đợi dữ liệu đi qua server rồi gửi ngược lại.
- **Trigger:** Giá trị cảm biến vượt ngưỡng an toàn cấu hình sẵn, hoặc phát hiện mất nguồn đột ngột không phải do tắt máy chủ động (đối chiếu với tín hiệu ignition từ V-01).
- **Input:** dữ liệu cảm biến pin/motor realtime, tín hiệu mất nguồn/tamper.
- **Output:** sự kiện cảnh báo (event) kèm snapshot dữ liệu, gửi đồng thời 2 hướng: (a) ưu tiên lên server (bỏ qua hàng đợi thường), (b) trực tiếp tới màn hình xe qua kết nối cục bộ.
- **Alternative Output:** Mất kết nối mạng lên server → cảnh báo lưu cục bộ và gửi bù khi có sóng trở lại; luồng gửi thẳng tới màn hình xe (kết nối cục bộ, không qua mạng ngoài) vẫn hoạt động bình thường. Cảnh báo pin ≤10% không có data lên server → dự phòng gửi qua SMS — [CẦN THÔNG TIN] do đã bỏ app di động cho tài xế, cần xác định lại đối tượng nhận SMS (admin/quản lý đội xe?) và quy trình gửi.
- **Ràng buộc nghiệp vụ:** Cảnh báo realtime khi vượt ngưỡng an toàn (an toàn cháy nổ pin = Must); log kèm snapshot dữ liệu; không trễ hơn luồng dữ liệu thường; phân biệt mất nguồn đột ngột và tắt máy chủ động dựa vào tín hiệu ignition (V-01); kết nối cục bộ thiết bị telematics ↔ màn hình xe cần đảm bảo độ trễ thấp cho cảnh báo an toàn.
- **Phụ thuộc (cần có chức năng nào trước):** V-01 (thu thập dữ liệu xe realtime, tín hiệu ignition).
- **Mô tả luồng:**  
  (1) Thiết bị so sánh giá trị cảm biến/tín hiệu nguồn với ngưỡng an toàn → (2) Nếu vượt ngưỡng, sinh sự kiện cảnh báo kèm snapshot → (3a) Gửi ưu tiên về server → Server ghi log & kích hoạt thông báo liên quan (AD-02, D-05) → (3b) Đồng thời gửi thẳng qua kết nối cục bộ tới màn hình xe để hiển thị ngay lập tức, không phụ thuộc luồng (3a).
- **Trạng thái:** Đang chờ review
- **Ghi chú:** Đã gộp nội dung phát hiện mất nguồn đột ngột/tamper từ V-03 cũ (đã bỏ). Giao thức kết nối cục bộ thiết bị telematics ↔ màn hình xe: CHƯA XÁC ĐỊNH (CAN bus/BLE/Wi-Fi nội bộ...). Kênh SMS dự phòng: CẦN THÔNG TIN (xem Alternative Output).

---

## NHÓM 2 — TRỤ SẠC: Streaming dữ liệu trạng thái & phiên sạc về server — Ưu tiên cao nhất

### `S-01` — Thu thập & gửi dữ liệu trạng thái trụ/súng sạc realtime

- **Actor:** Trụ sạc
- **Chức năng:** Thu thập & gửi dữ liệu trạng thái trụ/súng sạc realtime
- **Mô tả:** Trụ sạc gửi liên tục trạng thái vận hành về hệ thống qua chuẩn OCPP: số xe đang sạc, tình trạng từng súng (Available/Charging/Faulted), công suất khả dụng.
- **Trigger:** Trụ sạc có kết nối mạng; định kỳ hoặc theo sự kiện thay đổi trạng thái súng.
- **Input:** tín hiệu vận hành trụ (CSMS qua OCPP 1.6J tối thiểu, sẵn sàng 2.0.1).
- **Output:** bản ghi trạng thái trụ/súng realtime gửi lên CSMS/server.
- **Alternative Output:** Trụ mất kết nối → trạng thái chuyển "Faulted/Offline" và cảnh báo vận hành; dữ liệu bù khi kết nối lại.
- **Ràng buộc nghiệp vụ:** Cập nhật ≤30s; độ chính xác trạng thái súng ≥99%; trụ được nghiệm thu theo chuẩn OCPP khi mua sắm.
- **Phụ thuộc (cần có chức năng nào trước):** Danh mục trạm sạc đã được khai báo trong hệ thống (AD-05); hoàn tất tích hợp CSMS qua OCPP.
- **Mô tả luồng:**  
  (1) Trụ sạc cập nhật trạng thái nội bộ → (2) Gửi bản tin OCPP tới CSMS → (3) Server cập nhật trạng thái realtime → (4) Đồng bộ hiển thị cho tài xế (D-07 bản đồ trạm) & admin (AD-02 giám sát).
- **Trạng thái:** Đang chờ review

### `S-02` — Gửi dữ liệu phiên sạc khi xe cắm sạc

- **Actor:** Trụ sạc
- **Chức năng:** Gửi dữ liệu phiên sạc khi xe cắm sạc
- **Mô tả:** Ghi nhận và gửi toàn bộ dữ liệu một phiên sạc kể từ khi xe cắm vào súng cho tới khi rút ra: thời điểm, trạm/trụ/súng, công suất, kWh, SOC đầu–cuối, thời lượng, chi phí.
- **Trigger:** Xe cắm sạc (bắt đầu phiên) → kết thúc khi rút sạc.
- **Input:** tín hiệu cắm/rút sạc, đồng hồ đo kWh, công suất theo thời gian thực.
- **Output:** bản ghi phiên sạc đầy đủ gửi về server; đối soát chéo với dữ liệu SOC từ xe (V-01).
- **Alternative Output:** Mất kết nối giữa phiên → dữ liệu lưu tạm tại trụ và gửi bù khi có mạng; phiên bị đánh dấu "chưa đối soát" cho tới khi khớp đủ dữ liệu 3 chiều (trụ–xe–thanh toán).
- **Ràng buộc nghiệp vụ:** 100% phiên sạc qua mạng phải được ghi & đối soát chéo với telematics xe; khớp chính xác 3 chiều trụ–xe–thanh toán.
- **Phụ thuộc (cần có chức năng nào trước):** S-01 (trạng thái trụ); V-01 (dữ liệu SOC từ xe).
- **Mô tả luồng:**  
  (1) Xe cắm sạc → trụ khởi tạo phiên → (2) Trụ gửi cập nhật kWh/công suất theo thời gian thực → (3) Xe rút sạc → trụ đóng phiên, tính tổng kWh/chi phí → (4) Gửi bản ghi phiên hoàn chỉnh về server → (5) Server đối soát với dữ liệu SOC từ xe và kích hoạt luồng thanh toán (D-08).
- **Trạng thái:** 🔶 Cần thông tin
- **Ghi chú:** CẦN THÔNG TIN — cần cung cấp quy trình nghiệp vụ cụ thể: (a) làm thế nào để trụ sạc/server nhận diện được xe nào đang cắm sạc tại trụ nào (RFID/mã QR/kết nối BLE giữa xe và trụ...?); (b) làm thế nào để xác định & chốt thanh toán cho phiên sạc (ai là người trả, thời điểm chốt phí).

---

## NHÓM 3 — ADMIN (Quản trị tổng) — Web: Giám sát, đối soát, cấu hình hệ thống

### `AD-01` — Đăng nhập & phân quyền tài khoản hệ thống (RBAC)

- **Actor:** Admin
- **Chức năng:** Đăng nhập & phân quyền tài khoản hệ thống (RBAC)
- **Mô tả:** Quản lý tài khoản người dùng web (admin, quản lý đội xe) theo vai trò; mời/khóa tài khoản; ghi audit log truy cập dữ liệu nhạy cảm (vị trí, dữ liệu cá nhân).
- **Trigger:** Admin tạo/mời tài khoản mới, hoặc người dùng đăng nhập vào web portal.
- **Input:** thông tin tài khoản, vai trò.
- **Output:** phiên đăng nhập hợp lệ, phân quyền truy cập theo vai trò; bản ghi audit log.
- **Alternative Output:** Sai thông tin đăng nhập/hết hạn phiên → từ chối truy cập, yêu cầu đăng nhập lại; tài khoản bị khóa → chặn truy cập toàn bộ.
- **Ràng buộc nghiệp vụ:** RBAC đầy đủ theo vai trò; mọi truy cập dữ liệu nhạy cảm phải được ghi audit log; tuân thủ Nghị định 13/2023 về bảo vệ dữ liệu cá nhân.
- **Phụ thuộc (cần có chức năng nào trước):** Không (chức năng nền tảng, cần có trước các chức năng khác trên web).
- **Mô tả luồng:**  
  (1) Admin cấp tài khoản & gán vai trò → (2) Người dùng đăng nhập → (3) Hệ thống xác thực & áp quyền truy cập tương ứng → (4) Mọi thao tác truy cập dữ liệu nhạy cảm được ghi log.
- **Trạng thái:** Đang chờ review

### `AD-02` — Giám sát tổng quan realtime toàn bộ xe & trạm sạc

- **Actor:** Admin
- **Chức năng:** Giám sát tổng quan realtime toàn bộ xe & trạm sạc
- **Mô tả:** Xem danh sách và bản đồ realtime toàn bộ xe và trạm sạc trong hệ thống (không giới hạn theo đội xe); lọc/tìm kiếm theo trạng thái, khu vực. Đồng thời hiển thị tình trạng sức khỏe thiết bị telematics gắn theo từng xe (last-seen, firmware, tín hiệu SIM/nguồn) ngay trong cùng màn hình theo dõi xe — vì thiết bị đi theo vòng đời của xe nên không tách thành màn hình quản lý riêng.
- **Trigger:** Admin truy cập trang giám sát trên web.
- **Input:** dữ liệu realtime từ V-01 (gồm cả last-seen/ignition), V-02 (cảnh báo/tamper), S-01.
- **Output:** bản đồ + danh sách xe/trạm kèm trạng thái vận hành và sức khỏe thiết bị; chi tiết khi chọn từng đối tượng.
- **Alternative Output:** Đối tượng không có dữ liệu mới trong ngưỡng thời gian → hiển thị trạng thái "mất kết nối/không rõ". Thiết bị im lặng quá X giờ → hệ thống phân biệt "tắt máy chủ động" (có ignition off tương ứng từ V-01) và "nghi ngờ lỗi thiết bị/mất kết nối bất thường" (không có ignition off tương ứng) trước khi cảnh báo cho admin.
- **Ràng buộc nghiệp vụ:** Dữ liệu hiển thị không trễ hơn dữ liệu nguồn quá 30s; hỗ trợ lọc/tìm kiếm toàn hệ thống; phân biệt rõ xe tắt máy chủ động và thiết bị mất kết nối bất thường dựa trên dữ liệu ignition (V-01).
- **Phụ thuộc (cần có chức năng nào trước):** V-01, V-02, S-01, AD-01, AD-05 (hồ sơ xe/trạm để hiển thị kèm theo).
- **Mô tả luồng:**  
  (1) Admin mở trang giám sát → (2) Hệ thống truy vấn dữ liệu realtime & sức khỏe thiết bị mới nhất của xe/trạm → (3) Hiển thị trên bản đồ & danh sách → (4) Admin chọn 1 đối tượng để xem chi tiết vận hành + tình trạng thiết bị.
- **Trạng thái:** Đang chờ review
- **Ghi chú:** Đã gộp phần giám sát/dashboard sức khỏe thiết bị từ AD-06 cũ. Phần cấu hình ngưỡng/đẩy OTA (thao tác ghi) vẫn thuộc AD-06 mới (đã gộp với AD-07 cũ).

### `AD-03` — Đối chiếu phiên sạc & phát hiện vi phạm chính sách

- **Actor:** Admin
- **Chức năng:** Đối chiếu phiên sạc & phát hiện vi phạm chính sách
- **Mô tả:** Đối chiếu từng phiên sạc với chính sách sạc/bảo hành đã cấu hình để gắn cờ vi phạm (sạc ngoài khung giờ; thường xuyên sạc >90%/<20%; sạc nhanh quá mức); lưu bằng chứng bất biến và chuyển hồ sơ vi phạm cho bộ phận bảo hành xử lý.
- **Trigger:** Có phiên sạc mới hoàn tất (từ S-02), hoặc chạy đối chiếu định kỳ.
- **Input:** dữ liệu phiên sạc (S-02) + chính sách sạc cấu hình (AD-04).
- **Output:** cờ vi phạm (nếu có), hồ sơ bằng chứng, báo cáo gửi bộ phận bảo hành.
- **Alternative Output:** Phiên sạc không đủ dữ liệu đối chiếu → đánh dấu "chờ xác minh" thay vì kết luận vi phạm ngay.
- **Ràng buộc nghiệp vụ:** Tự động phát hiện & phân loại vi phạm; bằng chứng lưu bất biến (immutable) phục vụ đối chiếu hợp đồng bảo hành; báo cáo định kỳ & theo yêu cầu, lưu lịch sử xử lý.
- **Phụ thuộc (cần có chức năng nào trước):** S-02, AD-04 (chính sách sạc đã cấu hình).
- **Mô tả luồng:**  
  (1) Phiên sạc hoàn tất → (2) Hệ thống so khớp với chính sách cấu hình → (3) Nếu lệch ngưỡng, gắn cờ vi phạm & lưu bằng chứng → (4) Gửi cảnh báo cho tài xế (D-06) & quản lý đội xe (FM-05) → (5) Tổng hợp báo cáo định kỳ cho bộ phận bảo hành.
- **Trạng thái:** Đang chờ review
- **Ghi chú:** Phụ thuộc S-02 và AD-04 hiện đang ở trạng thái "Cần thông tin" — cần hoàn thiện 2 mục đó trước khi triển khai chi tiết AD-03.

### `AD-04` — Quản lý chính sách sạc theo hợp đồng bảo hành

- **Actor:** Admin
- **Chức năng:** Quản lý chính sách sạc theo hợp đồng bảo hành
- **Mô tả:** Cấu hình chính sách sạc theo hợp đồng/bảo hành cho từng xe/đội/dòng xe: khung giờ (ToU), SOC min–max, tần suất/thời lượng, công suất cho phép. Đây là dữ liệu cấu hình nền làm căn cứ cho AD-03.
- **Trigger:** Admin thiết lập/cập nhật chính sách mới.
- **Input:** thông số chính sách theo hợp đồng bảo hành.
- **Output:** bộ cấu hình chính sách được lưu phiên bản (versioned) và áp dụng ngay.
- **Alternative Output:** Chính sách mới xung đột với chính sách đang áp dụng cho xe/đội → cảnh báo xác nhận trước khi ghi đè.
- **Ràng buộc nghiệp vụ:** Cấu hình theo xe/đội/dòng; hiệu lực ngay sau khi lưu; lưu lịch sử phiên bản chính sách phục vụ audit.
- **Phụ thuộc (cần có chức năng nào trước):** AD-01.
- **Mô tả luồng:**  
  (1) Admin nhập thông số chính sách → (2) Hệ thống lưu phiên bản mới & gắn hiệu lực → (3) Chính sách được dùng làm căn cứ đối chiếu cho AD-03.
- **Trạng thái:** 🔶 Cần thông tin
- **Ghi chú:** CẦN THÔNG TIN — cần cung cấp thông tin chính sách sạc/bảo hành cụ thể (khung giờ ToU thực tế, ngưỡng SOC min-max theo từng dòng xe/hợp đồng, mức chế tài vi phạm...) từ bộ phận kinh doanh/bảo hành.

### `AD-05` — Quản lý hồ sơ & thiết bị theo xe/trạm sạc

- **Actor:** Admin
- **Chức năng:** Quản lý hồ sơ & thiết bị theo xe/trạm sạc
- **Mô tả:** Quản lý hồ sơ cố định (master data) của xe (VIN, biển số, chủ xe, dòng xe, ngày bàn giao...) và của trạm sạc (mã trạm, địa chỉ, chủ đầu tư, số trụ/súng...); kích hoạt thiết bị telematics theo VIN khi bàn giao xe mới; hủy kích hoạt khi xe không còn sử dụng được (hỏng, thanh lý, chuyển nhượng...).
- **Trigger:** Admin tạo/cập nhật hồ sơ xe hoặc trạm sạc; xe mới được bàn giao; xe ngừng sử dụng.
- **Input:** VIN, biển số, thông tin chủ xe/dòng xe, mã thiết bị, thông tin trạm sạc, lý do hủy kích hoạt (nếu có).
- **Output:** hồ sơ xe/trạm được lưu (CRUD); thiết bị được kích hoạt & liên kết với VIN (hoặc hủy liên kết); checklist bàn giao/hủy kích hoạt hoàn tất.
- **Alternative Output:** Không nhận được dữ liệu xác nhận sau kích hoạt → checklist báo "chưa thông suốt", chặn hoàn tất bàn giao. Hủy kích hoạt khi xe vẫn còn phiên đang mở (đang sạc, đang trong ca lái) → cảnh báo xác nhận trước khi hủy.
- **Ràng buộc nghiệp vụ:** Theo quy trình chuẩn theo VIN; tỷ lệ kích hoạt thành công ≥98%; hồ sơ tĩnh quản lý dạng CRUD, lưu lịch sử thay đổi; hủy kích hoạt phải ghi rõ lý do & thời điểm.
- **Phụ thuộc (cần có chức năng nào trước):** AD-01; là điều kiện tiên quyết của V-01 (xe), S-01 (trạm).
- **Mô tả luồng:**  
  (1) Admin nhập/cập nhật hồ sơ xe hoặc trạm → (2a) Nếu là bàn giao mới: gửi lệnh kích hoạt thiết bị, xác nhận bắt đầu gửi dữ liệu, hoàn tất checklist → (2b) Nếu là ngừng sử dụng: kiểm tra phiên đang mở, xác nhận, hủy kích hoạt thiết bị & đóng hồ sơ.
- **Trạng thái:** Đang chờ review
- **Ghi chú:** Mở rộng từ chức năng "Kích hoạt thiết bị theo VIN" cũ: bổ sung quản lý hồ sơ tĩnh (trước đây chưa có chỗ quản lý, đã dời khỏi AD-02 vì AD-02 chỉ nên là màn hình giám sát/xem) và bổ sung luồng hủy kích hoạt.

### `AD-06` — Cấu hình ngưỡng cảnh báo & kênh thông báo

- **Actor:** Admin
- **Chức năng:** Cấu hình ngưỡng cảnh báo & kênh thông báo
- **Mô tả:** Cấu hình tập trung gồm: (1) ngưỡng kích hoạt cho từng loại cảnh báo (pin yếu, an toàn pin/motor, vi phạm sạc, bảo dưỡng, thiết bị mất kết nối...) — áp dụng cả ở cấp đẩy xuống thiết bị (OTA, ngưỡng cảm biến cục bộ dùng cho V-02) lẫn cấp server; (2) kênh gửi thông báo cho từng loại cảnh báo, gồm web (admin/quản lý đội xe) và màn hình trên xe (tài xế).
- **Trigger:** Admin thiết lập/cập nhật cấu hình ngưỡng hoặc kênh thông báo.
- **Input:** danh mục loại cảnh báo, ngưỡng mong muốn, kênh gửi mong muốn (web/màn hình xe).
- **Output:** cấu hình được lưu & áp dụng cho các luồng liên quan (V-02 nhận ngưỡng cục bộ qua OTA; AD-02, AD-03, D-05, D-06 áp dụng kênh gửi); xác nhận áp dụng cấu hình xuống thiết bị (có thể rollback).
- **Alternative Output:** Thiết bị không phản hồi sau khi đẩy cấu hình ngưỡng mới → giữ nguyên cấu hình cũ và cảnh báo "áp dụng thất bại". [CẦN THÔNG TIN] Kênh SMS dự phòng trước đây gửi tới điện thoại tài xế — do đã bỏ app di động, cần xác định lại đối tượng nhận (admin/quản lý đội xe?) và liệu có còn cần kênh SMS hay không.
- **Ràng buộc nghiệp vụ:** Lưu lịch sử cấu hình & phiên bản; cấu hình đẩy theo xe/đội, xác nhận áp dụng, rollback được; luôn có kênh web (admin/quản lý) làm kênh dự phòng vì màn hình xe phụ thuộc xe có bật nguồn hay không.
- **Phụ thuộc (cần có chức năng nào trước):** AD-01, AD-05 (đối tượng thiết bị để đẩy cấu hình).
- **Mô tả luồng:**  
  (1) Admin chọn loại cảnh báo → (2) Gán ngưỡng kích hoạt & kênh gửi (web/màn hình xe) → (3) Lưu cấu hình, đẩy xuống thiết bị nếu cần (OTA) → (4) Thiết bị/server xác nhận áp dụng hoặc admin rollback nếu lỗi → (5) Các chức năng cảnh báo liên quan (V-02, AD-03, D-05, D-06...) áp dụng cấu hình này.
- **Trạng thái:** Đang chờ review
- **Ghi chú:** Gộp 2 chức năng cũ AD-06 (cấu hình ngưỡng + đẩy OTA thiết bị) và AD-07 (cấu hình kênh thông báo) vì cùng bản chất là cấu hình ngưỡng/kênh cảnh báo. Phần giám sát/dashboard sức khỏe thiết bị (không phải cấu hình, mà là xem) đã chuyển sang AD-02.

### `AD-07` — Quản lý gói dịch vụ thuê bao & billing

- **Actor:** Admin
- **Chức năng:** Quản lý gói dịch vụ thuê bao & billing
- **Mô tả:** Quản lý gói dịch vụ Standard theo xe/tháng: gán gói, chu kỳ thanh toán, nhắc hạn, khóa tính năng khi quá hạn thanh toán; theo dõi doanh thu thuê bao.
- **Trigger:** Admin gán gói mới cho khách/đội xe, hoặc tới chu kỳ thanh toán/hết hạn.
- **Input:** thông tin khách hàng/đội xe, loại gói.
- **Output:** trạng thái thuê bao, nhắc hạn, báo cáo doanh thu.
- **Alternative Output:** Quá hạn thanh toán không gia hạn → tự động khóa các tính năng ngoài gói cơ bản, thông báo cho khách hàng.
- **Ràng buộc nghiệp vụ:** Quản lý gói theo khách/đội; báo cáo doanh thu thuê bao định kỳ.
- **Phụ thuộc (cần có chức năng nào trước):** AD-01.
- **Mô tả luồng:**  
  (1) Admin gán gói dịch vụ cho xe/đội → (2) Hệ thống theo dõi chu kỳ thanh toán → (3) Nhắc hạn trước khi hết hạn → (4) Nếu không gia hạn, khóa tính năng liên quan.
- **Trạng thái:** 🔶 Cần thông tin
- **Ghi chú:** CẦN THÔNG TIN — cần thông tin cụ thể về các gói dịch vụ, mức phí thuê bao, chu kỳ thanh toán... từ bộ phận kinh doanh. Đã đẩy xuống mức ưu tiên thấp hơn trong nhóm Admin.

### `AD-08` — Quản lý ticket hỗ trợ khách hàng (CSKH)

- **Actor:** Admin
- **Chức năng:** Quản lý ticket hỗ trợ khách hàng (CSKH)
- **Mô tả:** Tiếp nhận, phân loại và xử lý ticket hỗ trợ từ tài xế (bao gồm cả yêu cầu hỗ trợ khẩn cấp và ticket thường từ D-12) theo SLA.
- **Trigger:** Có ticket mới gửi từ màn hình xe của tài xế, hoặc qua kênh Zalo/hotline được ghi nhận vào hệ thống.
- **Input:** nội dung yêu cầu, ngữ cảnh xe đính kèm (VIN, vị trí, mã lỗi).
- **Output:** ticket được tạo/phân loại/theo dõi tới khi đóng.
- **Alternative Output:** Ticket khẩn cấp (hết pin/sự cố) không được xử lý trong SLA → tự động leo thang & fallback gọi hotline trực tiếp.
- **Ràng buộc nghiệp vụ:** SLA phản hồi theo mức độ khẩn cấp; ticket khẩn cấp phải gọi lại tài xế ≤5 phút; lưu lịch sử xử lý.
- **Phụ thuộc (cần có chức năng nào trước):** D-12 (gộp yêu cầu hỗ trợ khẩn cấp & thường), AD-01.
- **Mô tả luồng:**  
  (1) Ticket được tạo (tự động từ nút khẩn cấp trên màn hình xe hoặc tài xế tự gửi yêu cầu thường) → (2) Hệ thống phân loại theo mức độ → (3) Nhân sự CSKH xử lý theo SLA → (4) Cập nhật trạng thái & đóng ticket, lưu lịch sử.
- **Trạng thái:** 🔶 Cần thông tin
- **Ghi chú:** CẦN THÔNG TIN — cần xác định rõ quy trình nghiệp vụ xử lý ticket (SLA cụ thể theo từng mức độ, đội ngũ phụ trách, kênh tiếp nhận ngoài màn hình xe...).

### `AD-09` — Chấm điểm hành vi lái xe an toàn

- **Actor:** Admin
- **Chức năng:** Chấm điểm hành vi lái xe an toàn
- **Mô tả:** Tính điểm an toàn theo tài xế dựa trên dữ liệu hành trình (phanh gấp, tăng tốc đột ngột, quá tốc độ, thời gian lái liên tục); xếp hạng trong đội, làm dữ liệu đầu vào cho các chương trình sau này (vd. bảo hiểm).
- **Trigger:** Định kỳ (hàng tuần) tổng hợp dữ liệu hành trình.
- **Input:** dữ liệu hành trình/tốc độ từ V-01.
- **Output:** điểm an toàn theo tài xế/tuần, bảng xếp hạng trong đội.
- **Alternative Output:** Tài xế không đủ dữ liệu hành trình trong kỳ → không tính điểm, đánh dấu "chưa đủ dữ liệu".
- **Ràng buộc nghiệp vụ:** Tính điểm theo tài xế/tuần; xếp hạng trong đội.
- **Phụ thuộc (cần có chức năng nào trước):** V-01.
- **Mô tả luồng:**  
  (1) Hệ thống tổng hợp dữ liệu hành trình theo tài xế → (2) Áp công thức chấm điểm → (3) Sinh bảng điểm & xếp hạng → (4) Hiển thị cho quản lý đội xe (FM) và làm dữ liệu tham khảo cho Admin.
- **Trạng thái:** 🔶 Cần thông tin
- **Ghi chú:** CẦN THÔNG TIN — cần xác định rõ quy trình/công thức chấm điểm cụ thể, và kết quả được sử dụng cho mục đích gì (bảo hiểm, khen thưởng, xếp loại...).

---

## NHÓM 4 — TÀI XẾ — Màn hình trên xe (không còn app di động riêng cho tài xế)

### `D-01` — Đăng nhập / Check-in đầu ca lái xe (màn hình trên xe)

- **Actor:** Tài xế
- **Chức năng:** Đăng nhập / Check-in đầu ca lái xe (màn hình trên xe)
- **Mô tả:** Tài xế đăng nhập trên màn hình xe khi lên xe bắt đầu ca lái, xác nhận gắn với xe cụ thể (tương tự chấm công), làm căn cứ cho các phiên hành trình và trách nhiệm sử dụng xe.
- **Trigger:** Tài xế thao tác đăng nhập/xác thực trên màn hình xe khi lên xe.
- **Input:** thông tin xác thực tài xế (hình thức cụ thể chưa xác định — mã PIN/thẻ từ/vân tay/tài khoản...).
- **Output:** phiên làm việc (check-in) được tạo, gắn tài xế–xe–thời điểm bắt đầu.
- **Alternative Output:** Xe đã có tài xế khác đang check-in (chưa check-out) → từ chối, yêu cầu xử lý trước; mất mạng khi check-in → lưu tạm cục bộ và đồng bộ khi có sóng.
- **Ràng buộc nghiệp vụ:** Dùng tốt ngoài trời; hỗ trợ tiếng Việt; một xe tại một thời điểm chỉ gắn với một tài xế đang check-in.
- **Phụ thuộc (cần có chức năng nào trước):** AD-01 (tài khoản tài xế đã được cấp), AD-05 (xe đã provisioning).
- **Mô tả luồng:**  
  (1) Tài xế thao tác đăng nhập trên màn hình xe → (2) Hệ thống xác thực tài xế → (3) Tạo phiên check-in gắn tài xế–xe → (4) Bắt đầu tính "đang trong ca" cho tới khi check-out (D-02).
- **Trạng thái:** 🔶 Cần thông tin
- **Ghi chú:** CẦN THÔNG TIN — do không còn app di động cá nhân để xác thực trước, cần xác định rõ quy trình nghiệp vụ: hình thức đăng nhập cụ thể trên màn hình xe (mã PIN, thẻ từ/RFID, vân tay, tài khoản+mật khẩu...).

### `D-02` — Đăng xuất / Check-out cuối ca lái xe

- **Actor:** Tài xế
- **Chức năng:** Đăng xuất / Check-out cuối ca lái xe
- **Mô tả:** Tài xế kết thúc ca lái trên màn hình xe, đóng phiên làm việc, chốt số liệu hành trình trong ca.
- **Trigger:** Tài xế chủ động đăng xuất trên màn hình xe, hoặc hết ca theo cấu hình.
- **Input:** yêu cầu check-out.
- **Output:** phiên làm việc được đóng, tổng hợp số liệu ca (quãng đường, thời gian lái).
- **Alternative Output:** Xe vẫn đang trong phiên sạc/di chuyển khi check-out → cảnh báo xác nhận trước khi đóng phiên.
- **Ràng buộc nghiệp vụ:** Phiên làm việc phải được đóng rõ ràng để phục vụ đối soát hành vi lái (AD-09) và phân công (FM-04).
- **Phụ thuộc (cần có chức năng nào trước):** D-01.
- **Mô tả luồng:**  
  (1) Tài xế chọn check-out trên màn hình xe → (2) Hệ thống kiểm tra trạng thái xe → (3) Đóng phiên, lưu tổng hợp số liệu ca.
- **Trạng thái:** 🔶 Cần thông tin
- **Ghi chú:** CẦN THÔNG TIN — tương tự D-01, cần xác định quy trình xác thực/đăng xuất cụ thể trên màn hình xe.

### `D-03` — Xem thông tin realtime của xe (màn hình trên xe)

- **Actor:** Tài xế
- **Chức năng:** Xem thông tin realtime của xe (màn hình trên xe)
- **Mô tả:** Hiển thị trên màn hình xe các thông tin realtime: pin (SOC/SOH, nhiệt độ), trạng thái xe (motor, odometer, mã lỗi), hành trình (GPS, tốc độ). Dữ liệu này được nhận TRỰC TIẾP từ thiết bị telematics qua kết nối cục bộ trên xe, song song và độc lập với luồng V-01 gửi lên server.
- **Trigger:** Xe bật nguồn / tài xế đã check-in.
- **Input:** dữ liệu từ thiết bị telematics qua kết nối cục bộ (không qua server).
- **Output:** giao diện hiển thị thông tin realtime trên màn hình xe.
- **Alternative Output:** Kết nối cục bộ giữa thiết bị và màn hình gián đoạn → hiển thị dữ liệu gần nhất kèm timestamp.
- **Ràng buộc nghiệp vụ:** Độ trễ hiển thị rất thấp do lấy trực tiếp, không qua server; giao diện dễ đọc khi lái xe.
- **Phụ thuộc (cần có chức năng nào trước):** V-01 (nguồn dữ liệu gốc trên xe); kết nối cục bộ thiết bị telematics ↔ màn hình xe (xem Ghi chú của V-02 — giao thức cụ thể chưa xác định).
- **Mô tả luồng:**  
  (1) Cảm biến/thiết bị telematics sinh dữ liệu → (2) Gửi trực tiếp qua kết nối cục bộ tới màn hình xe (không qua server) → (3) Màn hình cập nhật hiển thị liên tục.
- **Trạng thái:** Đang chờ review
- **Ghi chú:** Luồng hiển thị này KHÔNG đi qua luồng V-01 lên server — là một đường dữ liệu riêng, cục bộ trên xe.

### `D-04` — Xem hồ sơ xe & lịch sử lộ trình

- **Actor:** Tài xế
- **Chức năng:** Xem hồ sơ xe & lịch sử lộ trình
- **Mô tả:** Xem thông tin hồ sơ xe (biển số, mã khung/VIN) và xem lại lộ trình đã đi qua theo thời gian, trên màn hình xe.
- **Trigger:** Tài xế mở mục "Hồ sơ xe" trên màn hình xe.
- **Input:** yêu cầu xem hồ sơ/lộ trình, khoảng thời gian.
- **Output:** thông tin hồ sơ xe (từ AD-05); bản đồ lộ trình lịch sử (từ V-01).
- **Alternative Output:** Không có dữ liệu lộ trình trong khoảng thời gian chọn → hiển thị thông báo trống dữ liệu.
- **Ràng buộc nghiệp vụ:** Lộ trình lưu trữ tối thiểu 6 tháng để tra cứu.
- **Phụ thuộc (cần có chức năng nào trước):** V-01, AD-05.
- **Mô tả luồng:**  
  (1) Tài xế chọn xem hồ sơ/lộ trình → (2) Hệ thống truy vấn hồ sơ xe (AD-05) + lịch sử GPS (V-01) → (3) Hiển thị kết quả.
- **Trạng thái:** Đang chờ review

### `D-05` — Nhận cảnh báo tình trạng xe/pin

- **Actor:** Tài xế
- **Chức năng:** Nhận cảnh báo tình trạng xe/pin
- **Mô tả:** Nhận cảnh báo phân cấp về pin (30%/20%/10%) kèm gợi ý trạm gần nhất còn trống, và cảnh báo an toàn (nhiệt độ pin cao, sụt áp, lỗi cell, lỗi motor). Cảnh báo được tính toán dựa trên số liệu nhận trực tiếp từ xe kết hợp với cấu hình ngưỡng cảnh báo (AD-06), và hiển thị trực tiếp lên màn hình xe (giống cơ chế của V-02).
- **Trigger:** Số liệu nhận trực tiếp từ xe chạm ngưỡng cảnh báo đã cấu hình (AD-06).
- **Input:** số liệu trực tiếp từ xe (qua kết nối cục bộ), cấu hình ngưỡng từ AD-06.
- **Output:** cảnh báo hiển thị trực tiếp trên màn hình xe kèm khoảng cách & gợi ý trạm gần nhất còn trống.
- **Alternative Output:** Không nhận được số liệu trực tiếp từ xe → không hiển thị được cảnh báo cục bộ, phụ thuộc vào luồng cảnh báo qua server (V-02/AD-02) làm phương án dự phòng.
- **Ràng buộc nghiệp vụ:** Cảnh báo hiển thị gần như tức thời do không qua server; chống spam (1 lần/ngưỡng/chuyến).
- **Phụ thuộc (cần có chức năng nào trước):** V-02 (nguồn cảnh báo trực tiếp từ thiết bị), AD-06 (cấu hình ngưỡng).
- **Mô tả luồng:**  
  (1) Số liệu từ xe chạm ngưỡng đã cấu hình (AD-06) → (2) Thiết bị/màn hình xe tự xử lý & hiển thị cảnh báo ngay tại chỗ → (3) Tài xế có thể xem gợi ý trạm sạc gần nhất (liên kết D-07).
- **Trạng thái:** Đang chờ review

### `D-06` — Nhận cảnh báo vi phạm chính sách sạc

- **Actor:** Tài xế
- **Chức năng:** Nhận cảnh báo vi phạm chính sách sạc
- **Mô tả:** Nhận thông báo trên màn hình xe khi hành vi sạc bị gắn cờ vi phạm chính sách, kèm khuyến nghị khắc phục để không ảnh hưởng quyền lợi bảo hành.
- **Trigger:** AD-03 gắn cờ vi phạm cho phiên sạc liên quan tới tài xế.
- **Input:** sự kiện vi phạm từ AD-03.
- **Output:** thông báo trên màn hình xe nêu rõ hành vi vi phạm & cách khắc phục.
- **Alternative Output:** Vi phạm ở mức "chờ xác minh" → chưa gửi thông báo chính thức, chỉ ghi nhận nội bộ.
- **Ràng buộc nghiệp vụ:** Gửi realtime khi phát hiện + tổng hợp định kỳ; nêu rõ hành vi & cách khắc phục.
- **Phụ thuộc (cần có chức năng nào trước):** AD-03, AD-06 (kênh gửi).
- **Mô tả luồng:**  
  (1) AD-03 phát hiện vi phạm → (2) Server gửi thông báo qua kênh cấu hình (AD-06) tới màn hình xe của tài xế liên quan → (3) Tài xế nhận thông báo kèm khuyến nghị khắc phục.
- **Trạng thái:** Đang chờ review

### `D-07` — Xem bản đồ trạm sạc & điều hướng

- **Actor:** Tài xế
- **Chức năng:** Xem bản đồ trạm sạc & điều hướng
- **Mô tả:** Xem bản đồ các trạm sạc ngay trong phần mềm hệ thống trên màn hình xe (dùng API bản đồ Google hoặc Vietmap), lọc theo trạng thái khả dụng/công suất/chuẩn sạc; ước tính khả năng tới trạm theo SOC hiện tại (range-aware); mở điều hướng tới trạm phù hợp/gần nhất còn trống.
- **Trigger:** Tài xế mở mục bản đồ trạm sạc, hoặc nhận gợi ý từ cảnh báo pin (D-05).
- **Input:** dữ liệu trạm/trụ realtime (S-01), vị trí & SOC hiện tại của xe (D-03).
- **Output:** bản đồ trạm (nhúng qua API Google/Vietmap) kèm bộ lọc; điều hướng trong cùng giao diện.
- **Alternative Output:** SOC không đủ để tới trạm đã chọn → cảnh báo & gợi ý trạm khác trong tầm với. Khi mất kết nối mạng: do phần lớn dữ liệu xe (SOC, vị trí...) đã nhận trực tiếp từ xe qua kết nối cục bộ (không qua mạng ngoài — xem D-03), chức năng này chỉ còn cần cache trước dữ liệu BẢN ĐỒ (tile khu vực hay đi) để vẫn hiển thị được khi mất sóng; các dữ liệu khác không bị ảnh hưởng bởi việc mất mạng.
- **Ràng buộc nghiệp vụ:** Ưu tiên trạm còn trống; tính khả năng tới đích theo SOC & quãng đường; cache bản đồ khu vực thường đi để dùng khi mất sóng.
- **Phụ thuộc (cần có chức năng nào trước):** S-01, D-03 (dữ liệu SOC hiện tại).
- **Mô tả luồng:**  
  (1) Tài xế mở bản đồ trạm → (2) Hệ thống hiển thị trạm theo trạng thái realtime trên nền bản đồ Google/Vietmap → (3) Tài xế chọn trạm → (4) Hệ thống kiểm tra SOC đủ tới trạm không → (5) Mở điều hướng nếu đủ, hoặc gợi ý trạm khác nếu không đủ → (6) Nếu mất mạng, dùng tile bản đồ đã cache trước đó để vẫn hiển thị được.
- **Trạng thái:** Đang chờ review
- **Ghi chú:** Đã gộp nội dung "Chế độ offline" (D-08 cũ, đã bỏ) vào đây — vì phần lớn dữ liệu xe đã chuyển sang nhận trực tiếp (không qua server), chế độ offline chỉ còn ý nghĩa với việc cache bản đồ.

### `D-08` — Thanh toán phiên sạc

- **Actor:** Tài xế
- **Chức năng:** Thanh toán phiên sạc
- **Mô tả:** Sau khi sạc xong, tài xế thanh toán phiên sạc trên màn hình xe (hoặc theo quy trình khác chưa xác định) bằng ví/VNPay/Momo; nhận biên nhận kWh.
- **Trigger:** Phiên sạc kết thúc (từ S-02).
- **Input:** thông tin phiên sạc (kWh, chi phí), phương thức thanh toán.
- **Output:** giao dịch thanh toán thành công; biên nhận kWh.
- **Alternative Output:** Sóng yếu khi thanh toán → giữ phiên, ghi nợ và thu sau khi có kết nối; thanh toán thất bại → giữ trạng thái "chờ thanh toán" và nhắc lại.
- **Ràng buộc nghiệp vụ:** Không lưu thông tin thẻ trên hệ thống (tokenization qua cổng thanh toán); hoạt động được khi sóng yếu.
- **Phụ thuộc (cần có chức năng nào trước):** S-02.
- **Mô tả luồng:**  
  (1) Phiên sạc kết thúc, hệ thống tính chi phí → (2) Tài xế chọn phương thức thanh toán → (3) Xác nhận thanh toán → (4) Hệ thống xuất biên nhận kWh & cập nhật trạng thái phiên "đã thanh toán".
- **Trạng thái:** 🔶 Cần thông tin
- **Ghi chú:** CẦN THÔNG TIN — do đã bỏ app di động (ví cá nhân trên điện thoại), cần xác định quy trình nghiệp vụ: thanh toán có thực hiện ngay tại màn hình xe theo từng tài xế/phiên, hay chuyển hẳn về cấp đội xe/công ty thanh toán tập trung?

### `D-09` — Ví & lịch sử giao dịch

- **Actor:** Tài xế
- **Chức năng:** Ví & lịch sử giao dịch
- **Mô tả:** Quản lý ví nạp trước (cho cá nhân hoặc đội xe trả tập trung); xem lịch sử phiên sạc & giao dịch.
- **Trigger:** Tài xế nạp/rút ví, hoặc xem lại lịch sử.
- **Input:** yêu cầu nạp/rút, khoảng thời gian tra cứu.
- **Output:** số dư ví cập nhật; danh sách lịch sử giao dịch/phiên sạc.
- **Alternative Output:** Số dư không đủ khi thanh toán tự động trừ ví → chuyển sang phương thức thanh toán khác hoặc ghi nợ tạm.
- **Ràng buộc nghiệp vụ:** Nạp/rút theo quy định; có hạn mức; đối soát ví khớp với phiên sạc.
- **Phụ thuộc (cần có chức năng nào trước):** D-08.
- **Mô tả luồng:**  
  (1) Tài xế nạp tiền vào ví → (2) Số dư được cập nhật → (3) Khi thanh toán (D-08), hệ thống trừ ví tương ứng → (4) Lịch sử giao dịch được ghi lại để tra cứu.
- **Trạng thái:** 🔶 Cần thông tin
- **Ghi chú:** CẦN THÔNG TIN — do đã bỏ app di động, cần xác định ví cá nhân trên màn hình xe có còn cần thiết không nếu thanh toán chuyển về cấp đội xe (D-08). Đã đẩy xuống mức ưu tiên thấp hơn.

### `D-10` — Xem hóa đơn điện tử kWh

- **Actor:** Tài xế
- **Chức năng:** Xem hóa đơn điện tử kWh
- **Mô tả:** Xem/nhận hóa đơn điện tử hợp lệ cho từng phiên sạc (khách lẻ) hoặc tổng hợp theo tháng (đội xe).
- **Trigger:** Sau khi thanh toán phiên sạc (D-08), hoặc cuối kỳ tổng hợp tháng.
- **Input:** dữ liệu phiên sạc đã thanh toán.
- **Output:** hóa đơn điện tử hợp lệ theo quy định VN.
- **Alternative Output:** Lỗi tích hợp với nhà cung cấp hóa đơn điện tử → hóa đơn ở trạng thái "đang xử lý", gửi lại sau khi hệ thống hóa đơn phục hồi.
- **Ràng buộc nghiệp vụ:** Tích hợp nhà cung cấp hóa đơn điện tử; khớp đối soát với dữ liệu điện sử dụng theo khách hàng.
- **Phụ thuộc (cần có chức năng nào trước):** D-08.
- **Mô tả luồng:**  
  (1) Phiên sạc được thanh toán → (2) Hệ thống gửi yêu cầu xuất hóa đơn tới nhà cung cấp → (3) Hóa đơn được tạo & gửi cho tài xế/đội xe → (4) Cuối tháng, tổng hợp hóa đơn cho đội xe (nếu áp dụng).
- **Trạng thái:** 🔶 Cần thông tin
- **Ghi chú:** CẦN THÔNG TIN — cần xác định quy trình nghiệp vụ (gắn với việc D-08 chưa rõ mô hình thanh toán). Đã đẩy xuống mức ưu tiên thấp hơn.

### `D-11` — Nhận nhắc bảo dưỡng & ưu đãi

- **Actor:** Tài xế
- **Chức năng:** Nhận nhắc bảo dưỡng & ưu đãi
- **Mô tả:** Nhận nhắc lịch bảo dưỡng theo km/thời gian, và các ưu đãi/chiến dịch liên quan, hiển thị trên màn hình xe.
- **Trigger:** Xe đạt ngưỡng km/thời gian cấu hình cho bảo dưỡng, hoặc có chiến dịch ưu đãi mới.
- **Input:** dữ liệu odometer (V-01), lịch bảo dưỡng cấu hình.
- **Output:** thông báo nhắc bảo dưỡng/ưu đãi trên màn hình xe.
- **Alternative Output:** Tài xế bỏ qua/tắt nhắc → hệ thống vẫn lưu lại để hiển thị trong mục hồ sơ xe (D-04).
- **Ràng buộc nghiệp vụ:** Lịch nhắc theo km/thời gian.
- **Phụ thuộc (cần có chức năng nào trước):** V-01.
- **Mô tả luồng:**  
  (1) Hệ thống theo dõi odometer/thời gian → (2) Khi đạt ngưỡng cấu hình → (3) Gửi thông báo nhắc cho tài xế trên màn hình xe → (4) Tài xế có thể đặt lịch bảo dưỡng (D-13) từ thông báo.
- **Trạng thái:** 🔶 Cần thông tin
- **Ghi chú:** CẦN THÔNG TIN — cần xác định quy trình nghiệp vụ cụ thể (nguồn ưu đãi, tần suất nhắc...). Đã đẩy xuống mức ưu tiên thấp hơn.

### `D-12` — Gửi yêu cầu hỗ trợ (khẩn cấp & thông thường)

- **Actor:** Tài xế
- **Chức năng:** Gửi yêu cầu hỗ trợ (khẩn cấp & thông thường)
- **Mô tả:** Tài xế gửi yêu cầu hỗ trợ từ màn hình xe, chia 2 mức: (1) Khẩn cấp — khi gặp sự cố/hết pin, nhấn nút SOS để gửi vị trí + mã lỗi, nhân sự CSKH liên hệ lại trong SLA nghiêm ngặt; (2) Thông thường — gửi yêu cầu hỗ trợ không khẩn cấp kèm ngữ cảnh xe tự động đính kèm, theo dõi trạng thái tới khi đóng.
- **Trigger:** Tài xế nhấn nút hỗ trợ khẩn cấp, hoặc tạo yêu cầu hỗ trợ thường từ màn hình xe.
- **Input:** loại yêu cầu (khẩn cấp/thường), vị trí GPS, mã lỗi (nếu có), nội dung mô tả (nếu là yêu cầu thường).
- **Output:** ticket được tạo & phân loại theo mức độ (AD-08); với ticket khẩn cấp có thêm yêu cầu gọi lại tài xế ngay.
- **Alternative Output:** ⚠️ RỦI RO AN TOÀN: do tài xế không còn điện thoại/app cá nhân riêng, khi xe mất nguồn hoặc gặp tai nạn nghiêm trọng, màn hình xe có thể KHÔNG còn hoạt động được, khiến tài xế mất khả năng gửi yêu cầu khẩn cấp qua hệ thống. Cần xác định kênh cứu hộ thay thế độc lập với xe (VD: quy định tài xế dùng điện thoại cá nhân gọi trực tiếp số hotline ngoài hệ thống) trước khi triển khai. Ticket thường không có phản hồi trong SLA → tự nhắc/leo thang.
- **Ràng buộc nghiệp vụ:** Nút khẩn cấp luôn hiển thị khi màn hình còn hoạt động; gọi lại tài xế ≤5 phút cho yêu cầu khẩn cấp; SLA riêng cho yêu cầu thường; lưu lịch sử xử lý.
- **Phụ thuộc (cần có chức năng nào trước):** D-03 (vị trí xe), AD-08 (quản lý ticket).
- **Mô tả luồng:**  
  (1) Tài xế nhấn nút khẩn cấp hoặc tạo yêu cầu thường trên màn hình xe → (2) Hệ thống đính kèm vị trí/mã lỗi (nếu khẩn cấp) hoặc nội dung mô tả (nếu thường) → (3) Ticket được tạo & phân loại (AD-08) → (4) Với ticket khẩn cấp: nhân sự CSKH gọi lại trong SLA; với ticket thường: tài xế theo dõi trạng thái tới khi đóng.
- **Trạng thái:** 🔶 Cần thông tin
- **Ghi chú:** Gộp 2 chức năng cũ "Gửi yêu cầu hỗ trợ sự cố khẩn cấp" và "Gửi ticket hỗ trợ in-app". CẦN THÔNG TIN — quy trình nghiệp vụ cụ thể; ĐẶC BIỆT cần xác định kênh cứu hộ khẩn cấp thay thế khi màn hình xe không hoạt động được — đây là RỦI RO AN TOÀN, không chỉ là vấn đề quy trình nghiệp vụ thông thường (xem Alternative Output).

### `D-13` — Đặt lịch bảo dưỡng

- **Actor:** Tài xế
- **Chức năng:** Đặt lịch bảo dưỡng
- **Mô tả:** Đặt lịch bảo dưỡng tại xưởng/mạng lưới bảo hành từ thông báo nhắc bảo dưỡng, thực hiện trên màn hình xe.
- **Trigger:** Tài xế chọn đặt lịch từ thông báo nhắc (D-11) hoặc chủ động trên màn hình xe.
- **Input:** xưởng, khung giờ mong muốn.
- **Output:** lịch hẹn được xác nhận; lưu vào lịch sử bảo dưỡng theo xe.
- **Alternative Output:** Khung giờ đã đầy → gợi ý khung giờ/xưởng khác gần nhất.
- **Ràng buộc nghiệp vụ:** Chọn xưởng/khung giờ; xác nhận; lịch sử bảo dưỡng theo xe.
- **Phụ thuộc (cần có chức năng nào trước):** D-11.
- **Mô tả luồng:**  
  (1) Tài xế chọn đặt lịch trên màn hình xe → (2) Hệ thống hiển thị xưởng/khung giờ khả dụng → (3) Tài xế xác nhận → (4) Lịch hẹn lưu vào hồ sơ xe (D-04).
- **Trạng thái:** 🔶 Cần thông tin
- **Ghi chú:** CẦN THÔNG TIN — cần xác định quy trình nghiệp vụ cụ thể (kết nối với hệ thống xưởng dịch vụ). Đã đẩy xuống mức ưu tiên thấp hơn.

---

## NHÓM 5 — QUẢN LÝ ĐỘI XE — Web portal

### `FM-01` — Xem danh sách & bản đồ đội xe realtime

- **Actor:** Quản lý đội xe
- **Chức năng:** Xem danh sách & bản đồ đội xe realtime
- **Mô tả:** Xem danh sách, trạng thái và vị trí realtime của các xe thuộc đội mình quản lý trên web portal; lọc/tìm kiếm.
- **Trigger:** Quản lý đội xe truy cập portal.
- **Input:** dữ liệu realtime từ V-01 (giới hạn theo đội xe được phân quyền).
- **Output:** danh sách + bản đồ xe trong đội.
- **Alternative Output:** Xe không có dữ liệu mới → hiển thị "mất kết nối".
- **Ràng buộc nghiệp vụ:** Chỉ xem được xe thuộc đội được phân quyền; lọc/tìm kiếm cơ bản.
- **Phụ thuộc (cần có chức năng nào trước):** V-01, AD-01, FM-04 (phân công tài xế–xe để xác định phạm vi đội).
- **Mô tả luồng:**  
  (1) Quản lý đội xe đăng nhập → (2) Hệ thống lọc dữ liệu theo đội được phân quyền → (3) Hiển thị danh sách & bản đồ realtime.
- **Trạng thái:** Đang chờ review

### `FM-02` — Dashboard KPI vận hành đội xe

- **Actor:** Quản lý đội xe
- **Chức năng:** Dashboard KPI vận hành đội xe
- **Mô tả:** Xem tổng hợp KPI vận hành đội xe: km, kWh tiêu thụ, chi phí/km, SOH, tỷ lệ sử dụng, số lượng cảnh báo; lọc theo thời gian, xuất báo cáo.
- **Trigger:** Quản lý đội xe mở dashboard, hoặc theo lịch báo cáo định kỳ.
- **Input:** dữ liệu tổng hợp từ V-01, S-02.
- **Output:** dashboard KPI theo đội/theo xe; file xuất CSV.
- **Alternative Output:** Thiếu dữ liệu trong kỳ báo cáo (xe offline dài ngày) → loại trừ và ghi chú rõ trong báo cáo.
- **Ràng buộc nghiệp vụ:** Tổng hợp & xem theo từng xe; lọc theo thời gian; công thức chi phí/km cấu hình được (giá điện); xuất được CSV.
- **Phụ thuộc (cần có chức năng nào trước):** V-01, S-02.
- **Mô tả luồng:**  
  (1) Hệ thống tổng hợp dữ liệu vận hành theo đội → (2) Tính các chỉ số KPI → (3) Hiển thị dashboard → (4) Quản lý đội xe xuất báo cáo khi cần.
- **Trạng thái:** Đang chờ review

### `FM-03` — Báo cáo sạc & tuân thủ bảo hành theo đội xe

- **Actor:** Quản lý đội xe
- **Chức năng:** Báo cáo sạc & tuân thủ bảo hành theo đội xe
- **Mô tả:** Xem báo cáo phiên sạc, mức độ tuân thủ chính sách, trạng thái bảo hành (điểm tuân thủ, số vi phạm, mức nguy cơ) theo từng xe trong đội.
- **Trigger:** Quản lý đội xe mở mục báo cáo, hoặc theo lịch định kỳ.
- **Input:** dữ liệu từ S-02, AD-03.
- **Output:** báo cáo/dashboard bảo hành theo xe; xuất CSV/PDF.
- **Alternative Output:** Không có vi phạm trong kỳ → báo cáo hiển thị "không phát sinh vi phạm".
- **Ràng buộc nghiệp vụ:** Dashboard theo xe, lọc theo mức nguy cơ; xuất báo cáo CSV/PDF theo xe/thời gian.
- **Phụ thuộc (cần có chức năng nào trước):** S-02, AD-03.
- **Mô tả luồng:**  
  (1) Hệ thống tổng hợp dữ liệu sạc & vi phạm theo xe trong đội → (2) Tính điểm tuân thủ & mức nguy cơ → (3) Hiển thị dashboard → (4) Xuất báo cáo khi cần.
- **Trạng thái:** Đang chờ review

### `FM-04` — Quản lý tài xế & phân công xe

- **Actor:** Quản lý đội xe
- **Chức năng:** Quản lý tài xế & phân công xe
- **Mô tả:** Thêm/sửa thông tin tài xế, gán/đổi xe cho tài xế, xem lịch sử hoạt động theo tài xế (dựa trên các phiên check-in/check-out D-01/D-02).
- **Trigger:** Quản lý đội xe cần thêm tài xế mới hoặc thay đổi phân công.
- **Input:** thông tin tài xế, xe được gán.
- **Output:** hồ sơ tài xế được cập nhật; phân công xe–tài xế.
- **Alternative Output:** Xe được gán đang phân công cho tài xế khác → cảnh báo xung đột trước khi xác nhận.
- **Ràng buộc nghiệp vụ:** CRUD tài xế đầy đủ; gán/đổi xe; lưu lịch sử hoạt động theo tài xế.
- **Phụ thuộc (cần có chức năng nào trước):** AD-01, D-01/D-02 (dữ liệu lịch sử ca lái để hiển thị hoạt động).
- **Mô tả luồng:**  
  (1) Quản lý đội xe tạo/sửa hồ sơ tài xế → (2) Gán xe cho tài xế → (3) Hệ thống lưu phân công → (4) Lịch sử hoạt động của tài xế được tổng hợp từ các phiên check-in/check-out.
- **Trạng thái:** Đang chờ review

### `FM-05` — Nhận cảnh báo vi phạm/sự cố của đội xe

- **Actor:** Quản lý đội xe
- **Chức năng:** Nhận cảnh báo vi phạm/sự cố của đội xe
- **Mô tả:** Nhận cảnh báo khi có xe/tài xế trong đội vi phạm chính sách sạc, hoặc có sự cố/thiết bị bất thường, để chủ động xử lý ở cấp đội trước khi lên tới Admin.
- **Trigger:** AD-03 gắn cờ vi phạm, hoặc V-02 phát sinh sự cố/thiết bị bất thường cho xe thuộc đội.
- **Input:** sự kiện cảnh báo/vi phạm liên quan tới xe trong đội.
- **Output:** thông báo cho quản lý đội xe kèm chi tiết.
- **Alternative Output:** Sự cố nghiêm trọng vượt phạm vi xử lý đội → tự động đồng thời báo lên Admin (AD-03/AD-02) chứ không chỉ dừng ở đội.
- **Ràng buộc nghiệp vụ:** Realtime + tổng hợp định kỳ; nêu rõ hành vi & cách khắc phục; lưu lịch sử xử lý.
- **Phụ thuộc (cần có chức năng nào trước):** AD-03, V-02.
- **Mô tả luồng:**  
  (1) Có sự kiện vi phạm/sự cố với xe trong đội → (2) Hệ thống lọc theo phạm vi đội quản lý → (3) Gửi thông báo cho quản lý đội xe → (4) Quản lý đội xe xử lý hoặc chuyển tiếp cho Admin nếu vượt thẩm quyền.
- **Trạng thái:** Đang chờ review
- **Ghi chú:** Đã cập nhật phụ thuộc: bỏ tham chiếu V-03 (đã gộp vào V-01/V-02).

### `FM-06` — Xem hàng đợi & thời gian chờ tại trạm

- **Actor:** Quản lý đội xe
- **Chức năng:** Xem hàng đợi & thời gian chờ tại trạm
- **Mô tả:** Ước tính số xe đang chờ và thời gian chờ dự kiến tại các trạm sạc, hỗ trợ điều phối xe trong đội tránh dồn ứ.
- **Trigger:** Quản lý đội xe mở mục theo dõi trạm sạc.
- **Input:** dữ liệu trạng thái trụ realtime (S-01).
- **Output:** số xe chờ & ETA trống trụ theo từng trạm.
- **Alternative Output:** Trạm không đủ dữ liệu lịch sử để ước tính ETA → hiển thị "chưa đủ dữ liệu ước tính".
- **Ràng buộc nghiệp vụ:** Hiển thị số xe chờ & ETA trống trụ (mức độ ưu tiên thấp – Should).
- **Phụ thuộc (cần có chức năng nào trước):** S-01.
- **Mô tả luồng:**  
  (1) Hệ thống tổng hợp trạng thái trụ theo thời gian thực → (2) Ước tính hàng đợi/ETA → (3) Hiển thị cho quản lý đội xe để điều phối.
- **Trạng thái:** Đang chờ review

### `FM-07` — Đặt chỗ trụ sạc trước (tùy chọn)

- **Actor:** Quản lý đội xe
- **Chức năng:** Đặt chỗ trụ sạc trước (tùy chọn)
- **Mô tả:** Đặt trước trụ sạc theo khung giờ cho xe trong đội nhằm chủ động lịch sạc, giảm thời gian chờ.
- **Trigger:** Quản lý đội xe (hoặc tài xế) chọn đặt chỗ trụ sạc trước.
- **Input:** trạm/trụ, khung giờ mong muốn.
- **Output:** chỗ được giữ có thời hạn; xác nhận đặt/hủy.
- **Alternative Output:** Không tới đúng giờ đã đặt (no-show) → áp dụng phạt theo cấu hình; chỗ được giải phóng cho xe khác.
- **Ràng buộc nghiệp vụ:** Đặt/hủy được; giữ chỗ có thời hạn; phạt no-show cấu hình được (mức độ ưu tiên thấp – Could).
- **Phụ thuộc (cần có chức năng nào trước):** S-01, FM-06.
- **Mô tả luồng:**  
  (1) Chọn trạm/trụ & khung giờ → (2) Hệ thống giữ chỗ có thời hạn → (3) Xe tới đúng giờ để sạc, hoặc bị hủy/phạt nếu no-show.
- **Trạng thái:** Đang chờ review

---