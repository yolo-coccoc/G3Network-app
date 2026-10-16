/* Language, labels and formatting.
   Every visible string is written in both languages at the call site:
   t("English", "Tiếng Việt"). Vietnamese is the default (NF-17). */

window.G3 = window.G3 || {};

(function () {
  const stored = (() => {
    try { return localStorage.getItem("g3.lang"); } catch (e) { return null; }
  })();
  G3.lang = stored === "en" ? "en" : "vi";

  /** Pick the string of the current language. */
  window.t = function (en, vi) {
    return G3.lang === "en" ? en : (vi === undefined ? en : vi);
  };

  G3.setLang = function (lang) {
    G3.lang = lang;
    try { localStorage.setItem("g3.lang", lang); } catch (e) { /* private window */ }
  };

  /* Labels of the backend's enum values ([en, vi]), keyed by value.
     Values match backend/app/domains/<domain>/types.py. */
  const LABELS = {
    // generic statuses
    ACTIVE: ["Active", "Hoạt động"],
    INACTIVE: ["Inactive", "Ngừng hoạt động"],
    SUSPENDED: ["Suspended", "Tạm ngưng"],
    CLOSED: ["Closed", "Đã đóng"],
    INVITED: ["Invited", "Đã mời"],
    LOCKED: ["Locked", "Đã khóa"],
    BLOCKED: ["Blocked", "Đã chặn"],
    VOIDED: ["Voided", "Đã hủy"],
    PENDING: ["Pending", "Đang chờ"],
    COMPLETED: ["Completed", "Hoàn tất"],
    ABANDONED: ["Abandoned", "Bỏ dở"],
    CANCELLED: ["Cancelled", "Đã hủy"],
    PLANNED: ["Planned", "Đã lên kế hoạch"],
    IN_PROGRESS: ["In progress", "Đang chạy"],
    OVERDUE: ["Overdue", "Quá hạn"],
    EXPIRED: ["Expired", "Hết hạn"],
    DRAFT: ["Draft", "Nháp"],
    ISSUED: ["Issued", "Đã phát hành"],
    PAID: ["Paid", "Đã thanh toán"],
    // organizations
    COMPANY: ["Company", "Doanh nghiệp"],
    INDIVIDUAL: ["Individual", "Cá nhân"],
    INTERNAL: ["Internal", "Nội bộ"],
    CUSTOMER: ["Customer", "Khách hàng"],
    PARTNER: ["Partner", "Đối tác"],
    // vehicle operating state (MON-09)
    DRIVING: ["Driving", "Đang chạy"],
    PARKED: ["Parked", "Đang đỗ"],
    CHARGING: ["Charging", "Đang sạc"],
    OFF: ["Off", "Tắt máy"],
    OFFLINE: ["Offline", "Mất kết nối"],
    // activation (VEH-05)
    NO_DEVICE: ["No device", "Chưa gắn thiết bị"],
    AWAITING_DATA: ["Awaiting data", "Chờ dữ liệu"],
    ACTIVATED: ["Activated", "Đã kích hoạt"],
    // device health (DEV-04)
    NOT_MOUNTED: ["Not mounted", "Chưa lắp"],
    NO_DATA: ["No data", "Chưa có dữ liệu"],
    SILENT: ["Silent", "Im lặng"],
    ATTENTION: ["Needs attention", "Cần chú ý"],
    HEALTHY: ["Healthy", "Ổn định"],
    // connectors (OCPP labels, STN-04)
    Available: ["Available", "Sẵn sàng"],
    Occupied: ["Occupied", "Đang cắm"],
    Reserved: ["Reserved", "Đã đặt"],
    Unavailable: ["Unavailable", "Không khả dụng"],
    Faulted: ["Faulted", "Lỗi"],
    Preparing: ["Preparing", "Chuẩn bị"],
    Charging: ["Charging", "Đang sạc"],
    SuspendedEV: ["Suspended by truck", "Xe tạm dừng"],
    SuspendedEVSE: ["Suspended by charger", "Trụ tạm dừng"],
    Finishing: ["Finishing", "Kết thúc"],
    Unknown: ["Unknown", "Không rõ"],
    // bills (PAY-10)
    QUOTED: ["Quoted", "Đã báo giá"],
    BILLED: ["Billed", "Đã tính tiền"],
    ON_HOLD: ["On hold", "Tạm giữ"],
    VOID: ["Void", "Hủy"],
    // commands (STN-10)
    ACCEPTED: ["Accepted", "Chấp nhận"],
    REJECTED: ["Rejected", "Từ chối"],
    ERROR: ["Error", "Lỗi"],
    TIMEOUT: ["Timeout", "Hết thời gian"],
    NOT_SENT: ["Not sent", "Chưa gửi"],
    REMOTE_START: ["Remote start", "Bắt đầu từ xa"],
    REMOTE_STOP: ["Remote stop", "Dừng từ xa"],
    UNLOCK_CONNECTOR: ["Unlock connector", "Mở khóa súng"],
    RESET: ["Reset", "Khởi động lại"],
    CHANGE_AVAILABILITY: ["Change availability", "Đổi trạng thái"],
    CHANGE_CONFIGURATION: ["Change configuration", "Đổi cấu hình"],
    GET_CONFIGURATION: ["Get configuration", "Đọc cấu hình"],
    TRIGGER_MESSAGE: ["Trigger message", "Yêu cầu gửi tin"],
    // notifications (NTF-01)
    INFO: ["Info", "Thông tin"],
    WARNING: ["Warning", "Cảnh báo"],
    CRITICAL: ["Critical", "Nghiêm trọng"],
    BATTERY_ALERT: ["Battery alert", "Cảnh báo pin"],
    ANOMALY_ALERT: ["Battery safety", "An toàn pin"],
    SOH_ALERT: ["Battery health", "Sức khỏe pin"],
    DEVICE_OFFLINE_ALERT: ["Device silent", "Thiết bị mất tín hiệu"],
    SOS_ALERT: ["SOS", "SOS"],
    GEOFENCE_ALERT: ["Geofence", "Vùng địa lý"],
    NO_DRIVER_CHECK_IN_ALERT: ["No driver checked in", "Xe chạy không có tài xế"],
    OUTSIDE_DRIVER_CHECK_IN: ["Outside driver", "Tài xế ngoài tổ chức"],
    NO_TRIP_STARTED: ["No trip started", "Chưa bắt đầu chuyến"],
    TRIP_ASSIGNED: ["Trip assigned", "Giao chuyến"],
    LOW_WALLET_BALANCE: ["Low wallet balance", "Số dư ví thấp"],
    TOP_UP_RECEIVED: ["Top-up received", "Đã nạp tiền"],
    CHARGING_RECEIPT: ["Charging receipt", "Biên nhận sạc"],
    FAULT_ALERT: ["Component fault", "Lỗi linh kiện"],
    CHARGER_FAULT: ["Charger fault", "Lỗi trụ sạc"],
    POLICY_VIOLATION: ["Charging policy", "Chính sách sạc"],
    // support (SUP-01)
    TICKET: ["Ticket", "Yêu cầu"],
    SOS: ["SOS", "SOS"],
    TECHNICAL: ["Technical", "Kỹ thuật"],
    BATTERY: ["Battery", "Pin"],
    BREAKDOWN: ["Breakdown", "Hỏng xe"],
    ACCIDENT: ["Accident", "Tai nạn"],
    BILLING: ["Billing", "Thanh toán"],
    OTHER: ["Other", "Khác"],
    IN_APP: ["In app", "Ứng dụng"],
    ZALO: ["Zalo", "Zalo"],
    HOTLINE: ["Hotline", "Tổng đài"],
    OPEN: ["Open", "Mới"],
    ACKNOWLEDGED: ["Acknowledged", "Đã tiếp nhận"],
    RESOLVED: ["Resolved", "Đã xử lý"],
    // drivers (DRV-02)
    QR: ["QR scan", "Quét QR"],
    APP: ["App", "Ứng dụng"],
    PORTAL: ["Portal", "Cổng web"],
    CHECKED_OUT: ["Checked out", "Trả xe"],
    TAKEN_OVER: ["Taken over", "Tài xế khác nhận"],
    OTHER_TRUCK: ["Other truck", "Nhận xe khác"],
    AUTO_ENDED: ["Auto-ended", "Tự kết thúc"],
    DRIVER_REMOVED: ["Driver removed", "Tài xế rời đi"],
    OWNER_CHANGED: ["Owner changed", "Đổi chủ xe"],
    LOADED: ["Loaded", "Có hàng"],
    EMPTY: ["Empty", "Xe rỗng"],
    // wallet (PAY-07)
    TOP_UP: ["Top-up", "Nạp tiền"],
    SESSION_BILL: ["Charging", "Thanh toán sạc"],
    REFUND: ["Refund", "Hoàn tiền"],
    ADJUSTMENT: ["Adjustment", "Điều chỉnh"],
    // warranties (WAR-01)
    STANDARD: ["Standard", "Tiêu chuẩn"],
    EXTENDED: ["Extended", "Mở rộng"],
    VEHICLE: ["Vehicle", "Xe"],
    TELEMATIC: ["Device", "Thiết bị"],
    STATION: ["Charger", "Trụ sạc"],
    // audit (ACC-18)
    VIEW: ["Viewed", "Xem"],
    EXPORT: ["Exported", "Xuất dữ liệu"],
    LOGIN_SUCCESS: ["Login", "Đăng nhập"],
    LOGIN_FAILED: ["Failed login", "Đăng nhập lỗi"],
    LOGIN_LOCKED: ["Login locked", "Khóa đăng nhập"],
    LOGOUT: ["Logout", "Đăng xuất"],
    PASSWORD_CHANGED: ["Password changed", "Đổi mật khẩu"],
    PHONE_CHANGED: ["Phone changed", "Đổi số điện thoại"],
    // risk (POL-04)
    LOW: ["Low", "Thấp"],
    MEDIUM: ["Medium", "Trung bình"],
    HIGH: ["High", "Cao"],
  };

  const ROLE_LABELS = {
    HEAD_ADMIN: ["Head administrator", "Quản trị trưởng"],
    CO_ADMIN: ["Co-administrator", "Đồng quản trị"],
    ORG_ADMIN: ["Organization admin", "Quản trị tổ chức"],
    SALES: ["Sales", "Kinh doanh"],
    ACCOUNTANT: ["Accountant", "Kế toán"],
    CUSTOMER_CARE: ["Customer care", "CSKH"],
    OPERATIONS: ["Operations", "Vận hành"],
    MAINTENANCE: ["Maintenance", "Bảo trì"],
    WARRANTY: ["Warranty", "Bảo hành"],
    FLEET_MANAGER: ["Fleet manager", "Quản lý đội xe"],
    DISPATCHER: ["Dispatcher", "Điều vận"],
    DRIVER: ["Driver", "Tài xế"],
    TECHNICIAN: ["Partner technician", "Kỹ thuật viên đối tác"],
  };

  /** Label of an enum value in the current language (falls back to the value). */
  G3.label = function (value) {
    const pair = LABELS[value];
    return pair ? t(pair[0], pair[1]) : String(value);
  };
  G3.roleLabel = function (role) {
    const pair = ROLE_LABELS[role];
    return pair ? t(pair[0], pair[1]) : role;
  };
  G3.ROLES = Object.keys(ROLE_LABELS);

  /* ---------- Formatting ---------- */
  const locale = () => (G3.lang === "en" ? "en-GB" : "vi-VN");

  G3.fmt = {
    /** Number with thousands separators. */
    num(value, digits = 0) {
      if (value === null || value === undefined || Number.isNaN(value)) return "—";
      return Number(value).toLocaleString(locale(), {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
    },
    vnd(value) {
      if (value === null || value === undefined) return "—";
      return G3.fmt.num(Math.round(value)) + " ₫";
    },
    /** Compact VND: 1,2 tỷ / 350 tr. */
    vndShort(value) {
      const abs = Math.abs(value);
      if (abs >= 1e9) return G3.fmt.num(value / 1e9, 2) + t(" bn ₫", " tỷ ₫");
      if (abs >= 1e6) return G3.fmt.num(value / 1e6, 1) + t(" M ₫", " tr ₫");
      return G3.fmt.vnd(value);
    },
    kwh(value, digits = 1) { return G3.fmt.num(value, digits) + " kWh"; },
    km(value, digits = 0) { return G3.fmt.num(value, digits) + " km"; },
    pct(value, digits = 0) { return G3.fmt.num(value, digits) + "%"; },
    date(value) {
      if (!value) return "—";
      return new Date(value).toLocaleDateString(locale(), { day: "2-digit", month: "2-digit", year: "numeric" });
    },
    time(value) {
      if (!value) return "—";
      return new Date(value).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
    },
    dateTime(value) {
      if (!value) return "—";
      return G3.fmt.date(value) + " " + G3.fmt.time(value);
    },
    /** Relative time: "5 min ago" / "5 phút trước". */
    ago(value) {
      if (!value) return "—";
      const seconds = Math.round((Date.now() - new Date(value).getTime()) / 1000);
      const future = seconds < 0;
      const s = Math.abs(seconds);
      let text;
      if (s < 45) text = t("just now", "vừa xong");
      else if (s < 3600) text = Math.round(s / 60) + t(" min", " phút");
      else if (s < 86400) text = Math.round(s / 3600) + t(" h", " giờ");
      else text = Math.round(s / 86400) + t(" d", " ngày");
      if (s < 45) return text;
      return future ? t("in ", "còn ") + text : text + t(" ago", " trước");
    },
    duration(minutes) {
      if (minutes === null || minutes === undefined) return "—";
      const h = Math.floor(minutes / 60);
      const m = Math.round(minutes % 60);
      return h ? `${h}${t("h", "g")} ${String(m).padStart(2, "0")}${t("m", "p")}` : `${m} ${t("min", "phút")}`;
    },
  };

  /** Escape text for HTML string templates. */
  G3.esc = function (value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  };
})();
