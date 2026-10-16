/* Support and platform screens:
   - support         /support       support console: SOS pinned on top, queue with live SLA timers,
                                    hotline/Zalo contact logging (SUP-01, SUP-02, SUP-03)
   - support-detail  /support/:id   one case: status flow, SLA, reporter, vehicle context and route,
                                    timeline, data-request handling, partner dispatch (SUP-01, SUP-02,
                                    SUP-05, MON-02, MON-10)
   - partners        /partners      repair and rescue partner directory, map, ratings (SUP-04, SUP-05)
   - system          /system        process health, ingestion delay, interruptions, data quality,
                                    data sovereignty (PLT-03, PLT-02, PLT-05) */

(function () {
  const { ui, db, fmt, scope } = G3;
  const esc = G3.esc;
  const MIN = 60 * 1000;
  const iso = (value) => new Date(value).toISOString();
  const ms = (value) => new Date(value).getTime();
  /** Text stored as [en, vi] (seeded) or as typed by a user (one language). */
  const tx = (value) => (Array.isArray(value) ? t(value[0], value[1]) : value || "");

  /** Small deterministic generator per key, for mock series that must not change between renders. */
  function seeded(key) {
    let h = 2166136261;
    for (const ch of String(key)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
    return function () {
      h = (h + 0x6d2b79f5) | 0;
      let x = Math.imul(h ^ (h >>> 15), 1 | h);
      x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
      return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* ---------- Labels this file needs beyond i18n.js ---------- */
  const LOCAL_LABELS = {
    DATA_REQUEST: ["Data request", "Yêu cầu dữ liệu"],
    CLAIM: ["Customer claim", "Khiếu nại"],
    BUG_REPORT: ["Bug report", "Báo lỗi phần mềm"],
    RESCUE: ["Rescue", "Cứu hộ"],
    WORKSHOP: ["Workshop", "Xưởng sửa chữa"],
    ARRIVED: ["On site", "Đã đến nơi"],
    SKIP: ["Skip reading", "Bỏ bản ghi"],
    FLAG: ["Store and flag", "Lưu và gắn cờ"],
    ALERT: ["Alert operations", "Báo vận hành"],
    NEW: ["New finding", "Mới phát hiện"],
    INVESTIGATING: ["Investigating", "Đang điều tra"],
    DECLARED: ["Declared", "Đã khai báo"],
    UNDECLARED: ["Undeclared", "Chưa khai báo"],
    OWNER_APPROVAL: ["Owner's written approval", "Văn bản đồng ý của chủ xe"],
    AUTHORITY_ORDER: ["Authority's order", "Yêu cầu của cơ quan có thẩm quyền"],
  };
  const label = (v) => (LOCAL_LABELS[v] ? t(LOCAL_LABELS[v][0], LOCAL_LABELS[v][1]) : G3.label(v));
  const badge = (v, tone) => ui.badge(v, tone, label(v));
  // CHARGING is also a vehicle state ("Đang sạc"); as a ticket category it reads differently.
  const catLabel = (v) => (v === "CHARGING" ? t("Charging", "Sạc điện") : label(v));
  const CATEGORIES = ["TECHNICAL", "BATTERY", "CHARGING", "BREAKDOWN", "ACCIDENT", "BILLING", "CLAIM", "BUG_REPORT", "DATA_REQUEST", "OTHER"];
  const CHANNELS = ["IN_APP", "ZALO", "HOTLINE"];
  const STATUSES = ["OPEN", "ACKNOWLEDGED", "RESOLVED", "CLOSED", "CANCELLED"];

  /* Fault codes the T-Box reports, translated for the console (MON-11 dictionary, illustrative). */
  const ERROR_CODES = {
    P0A3F: ["Drive motor position sensor A circuit", "Mạch cảm biến vị trí động cơ kéo A"],
    B1C42: ["Battery pack temperature sensor reads high", "Cảm biến nhiệt độ khối pin báo cao"],
    C1A2F: ["Tyre pressure sensor fault, rear axle", "Lỗi cảm biến áp suất lốp, cầu sau"],
    U0100: ["Lost communication with the vehicle control unit (VCU)", "Mất liên lạc với bộ điều khiển xe (VCU)"],
    P1A0B: ["High-voltage interlock loop open (HVIL)", "Hở mạch khóa liên động cao áp (HVIL)"],
    P0AA6: ["High-voltage isolation fault", "Lỗi cách điện hệ thống cao áp"],
  };
  const ACTIVE_ERRORS = { "veh-008": "P1A0B", "veh-013": "B1C42", "veh-018": "P0A3F", "veh-010": "C1A2F", "veh-024": "U0100", "veh-020": "P0AA6" };
  const errorText = (code) => (code && ERROR_CODES[code] ? t(ERROR_CODES[code][0], ERROR_CODES[code][1]) : t("Not in the fault dictionary", "Chưa có trong từ điển lỗi"));

  /* Partner capabilities: stored in Vietnamese like the mock data, shown in both languages. */
  const CAPS = [
    ["Kéo xe đầu kéo", "Tractor towing"],
    ["Kéo xe tải", "Truck towing"],
    ["Cứu hộ pin", "Battery rescue"],
    ["Sửa lốp", "Tyre repair"],
    ["Chẩn đoán HV", "High-voltage diagnostics"],
    ["Thay cell pin", "Battery cell replacement"],
    ["Bảo dưỡng định kỳ", "Scheduled maintenance"],
    ["Sạc lưu động", "Mobile charging"],
  ];
  const capLabel = (vi) => { const c = CAPS.find((x) => x[0] === vi); return c ? t(c[1], c[0]) : vi; };
  const PROVINCES = ["TP. HCM", "Bình Dương", "Đồng Nai", "Bà Rịa – Vũng Tàu", "Long An", "Tây Ninh"];
  const CAP_FOR_CATEGORY = { BREAKDOWN: "Kéo xe đầu kéo", ACCIDENT: "Kéo xe đầu kéo", BATTERY: "Cứu hộ pin", TECHNICAL: "Chẩn đoán HV", CHARGING: "Cứu hộ pin" };

  const STYLE = `<style>
    .sup-cd{font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
    .sup-cd.danger{color:var(--danger)} .sup-cd.warn{color:var(--warn)} .sup-cd.ok{color:var(--ok)} .sup-cd.calm{color:var(--text-2);font-weight:500}
    .sup-big{font-size:26px;letter-spacing:-.02em;line-height:1.15}
    .sup-sos{border:1.5px solid var(--danger);background:linear-gradient(135deg,var(--danger-soft),var(--surface) 65%);cursor:pointer}
    .sup-sos.live{animation:sup-pulse 1.6s ease-in-out infinite}
    @keyframes sup-pulse{0%,100%{box-shadow:0 0 0 0 rgba(220,38,38,.45)}50%{box-shadow:0 0 0 8px rgba(220,38,38,0)}}
    .tbl tr[data-sos] td:first-child{box-shadow:inset 3px 0 0 var(--danger)}
    .sup-ellipsis{display:block;max-width:150px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .sup-subject{display:block;max-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text-3);font-size:12px}
    .sup-steps{display:flex;margin:4px 0 2px}
    .sup-step{flex:1;text-align:center;position:relative;font-size:12px;color:var(--text-3)}
    .sup-step i{display:block;width:20px;height:20px;border-radius:50%;margin:0 auto 5px;background:var(--surface-3);border:2px solid var(--border-strong);position:relative;z-index:1}
    .sup-step.done i{background:var(--primary);border-color:var(--primary)}
    .sup-step.now i{background:var(--surface);border-color:var(--primary);box-shadow:0 0 0 4px var(--primary-soft)}
    .sup-step.now{color:var(--text);font-weight:600}
    .sup-step::after{content:"";position:absolute;top:9px;left:calc(50% + 12px);right:calc(-50% + 12px);height:2px;background:var(--border)}
    .sup-step:last-child::after{display:none}
    .sup-step.done::after{background:var(--primary)}
    .sup-photos{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
    .sup-photo{aspect-ratio:4/3;border-radius:8px;border:1px solid var(--border);display:grid;place-items:center;text-align:center;color:var(--text-3);font-size:11.5px;padding:6px;
      background:repeating-linear-gradient(45deg,var(--surface-3),var(--surface-3) 8px,var(--surface-2) 8px,var(--surface-2) 16px)}
    .sup-partner{border:1px solid var(--border);border-radius:9px;padding:10px 12px}
    .sup-partner + .sup-partner{margin-top:8px}
    .sup-partner.best{border-color:var(--primary);background:var(--primary-soft)}
    .sup-stars{color:#f59e0b;letter-spacing:1px;white-space:nowrap}
    .sup-flow text{font-family:var(--font)}
  </style>`;

  /* ---------- Case helpers ---------- */
  const SLA_MINUTES = (type, category) => (type === "SOS" ? 5 : ["BREAKDOWN", "ACCIDENT"].includes(category) ? 30 : ["BILLING", "CLAIM", "DATA_REQUEST"].includes(category) ? 24 * 60 : 4 * 60);
  const isTerminal = (c) => c.status === "CLOSED" || c.status === "CANCELLED";
  const isActive = (c) => c.status === "OPEN" || c.status === "ACKNOWLEDGED";
  /** Nobody has responded yet (backend is_awaiting_response). */
  const isAwaiting = (c) => !c.acknowledged_at && !isTerminal(c) && c.status !== "RESOLVED";
  /** Response SLA rule of the backend (calculate_is_sla_breached): first response vs. deadline, frozen at cancellation. */
  function isBreached(c, now = Date.now()) {
    const ref = c.acknowledged_at ? ms(c.acknowledged_at) : c.status === "CANCELLED" && c.closed_at ? ms(c.closed_at) : now;
    return ref > ms(c.sla_due_at);
  }
  const visibleCases = () => scope.byVehicle(db.support_cases);
  const findCase = (id) => visibleCases().find((c) => c.support_case_id === id);
  const me = () => (scope.user() ? scope.user().user_id : null);
  function nextCaseId() {
    const max = db.support_cases.reduce((a, c) => Math.max(a, Number(String(c.support_case_id).replace(/\D/g, "")) || 0), 2300);
    return `SC-${max + 1}`;
  }
  const normalize = (s) => String(s || "").toUpperCase().replace(/[\s.\-]/g, "");
  function findTruck(text) {
    const q = normalize(text);
    if (!q) return null;
    return scope.vehicles().find((v) => normalize(v.license_plate) === q || v.vin === q) || null;
  }
  const kmBetween = (a, b) => Math.hypot((a.lat - b.lat) * 111, (a.lon - b.lon) * 109.6);
  function nearestPlace(pos) {
    if (!pos) return null;
    const best = Object.values(db.PLACES).map((p) => ({ p, d: kmBetween(p, pos) })).sort((a, b) => a.d - b.d)[0];
    return best;
  }
  const placeText = (pos) => { const n = nearestPlace(pos); return n ? t(`near ${n.p.name} (${fmt.num(n.d, 1)} km)`, `gần ${n.p.name} (${fmt.num(n.d, 1)} km)`) : t("position unknown", "chưa rõ vị trí"); };
  const casePosition = (c) => { const v = db.vehicle(c.vehicle_id); return c.location || (v && v.live ? { lat: v.live.lat, lon: v.live.lon } : null); };

  /* ---------- Countdowns (updated every second by the page's own interval) ---------- */
  function spanText(diffMs) {
    const s = Math.round(Math.abs(diffMs) / 1000);
    if (s < 600) return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    if (s < 3600) return `${Math.round(s / 60)} ${t("min", "phút")}`;
    if (s < 48 * 3600) return `${Math.floor(s / 3600)} ${t("h", "giờ")} ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
    return `${Math.round(s / 86400)} ${t("days", "ngày")}`;
  }
  function countdown(dueMs, mode, now = Date.now()) {
    const diff = dueMs - now;
    const x = spanText(diff);
    if (mode === "accept") return diff > 0 ? { text: t(`answer within ${x}`, `còn ${x} để nhận`), tone: diff < 5 * MIN ? "warn" : "ok" } : { text: t("acceptance timed out", "quá hạn xác nhận"), tone: "danger" };
    if (mode === "eta") return diff > 0 ? { text: t(`arrives in ${x}`, `đến sau ${x}`), tone: "ok" } : { text: t(`late by ${x}`, `trễ ${x}`), tone: "warn" };
    return diff > 0 ? { text: t(`breaches in ${x}`, `còn ${x} đến hạn`), tone: diff < 15 * MIN ? "warn" : "calm" } : { text: t(`breached ${x} ago`, `quá hạn ${x}`), tone: "danger" };
  }
  function countdownEl(dueMs, mode = "sla", cls = "") {
    const c = countdown(dueMs, mode);
    return `<span class="sup-cd ${c.tone} ${cls}" data-due="${dueMs}" data-mode="${mode}">${esc(c.text)}</span>`;
  }
  function tickCountdowns(root) {
    const now = Date.now();
    root.querySelectorAll("[data-due]").forEach((el) => {
      const c = countdown(Number(el.dataset.due), el.dataset.mode, now);
      el.textContent = c.text;
      el.classList.remove("ok", "warn", "danger", "calm");
      el.classList.add(c.tone);
    });
    root.querySelectorAll("[data-window-from]").forEach((el) => {
      const from = Number(el.dataset.windowFrom), to = Number(el.dataset.windowTo);
      const left = Math.max(0, Math.min(100, ((to - now) / (to - from)) * 100));
      const bar = el.querySelector("span");
      bar.style.width = left + "%";
      el.className = "meter " + (left < 33 ? "danger" : left < 66 ? "warn" : "");
    });
  }
  /** Every-second interval for the current screen, cleared when the screen is left. */
  function everySecond(fn) {
    const id = setInterval(fn, 1000);
    G3.onLeave(() => clearInterval(id));
  }

  function slaCell(c) {
    if (isAwaiting(c)) return `${countdownEl(ms(c.sla_due_at))}<span class="sub">${esc(t("due", "hạn"))} ${fmt.time(c.sla_due_at)}</span>`;
    if (c.acknowledged_at) {
      const took = (ms(c.acknowledged_at) - ms(c.created_at)) / MIN;
      const late = isBreached(c);
      return `<span class="sup-cd ${late ? "danger" : "ok"}">${esc(late ? t("Late", "Trễ hạn") : t("Met", "Đạt"))}</span><span class="sub">${esc(t("responded in", "phản hồi sau"))} ${fmt.duration(took)}</span>`;
    }
    return '<span class="muted">—</span>';
  }

  /* ---------- Extra mock cases (so the console shows a live SOS, a ticket about to breach,
     one past SLA and a data request). Pushed once at load, relative to the data's NOW. ---------- */
  function seedCase(key, type, category, channel, vehicleId, minutesAgo, subject, extra = {}) {
    const v = db.vehicle(vehicleId);
    if (!v || db.support_cases.some((c) => c.seed_key === key)) return;
    const created = db.NOW - minutesAgo * MIN;
    const drv = v.driver_id ? db.driver(v.driver_id) : null;
    db.support_cases.push({
      support_case_id: nextCaseId(), seed_key: key, case_type: type, category, channel, status: "OPEN",
      vehicle_id: v.vehicle_id, vin: v.vin, organization_id: v.organization_id,
      driver_id: extra.reporter ? null : drv ? drv.driver_id : null,
      reporter_name: extra.reporter || (drv ? drv.full_name : "Nguyễn Văn Lâm"),
      reporter_phone: extra.phone || (drv ? drv.phone_number : "0903118842"),
      subject, error_code: extra.error_code || null,
      location: v.live ? { lat: v.live.lat, lon: v.live.lon } : null,
      created_at: iso(created), sla_due_at: iso(created + SLA_MINUTES(type, category) * MIN),
      acknowledged_at: null, resolved_at: null, assignee_user_id: null, partner_organization_id: null, notes: [],
    });
  }
  (function stopSosTruck() {
    // The hotline SOS is about a truck that lost power: park it on its road so the
    // live tick (which moves DRIVING trucks along road_f) keeps it at the reported spot.
    const v = db.vehicle("veh-008");
    if (!v || !v.live || db.support_cases.some((c) => c.seed_key === "sos-live")) return;
    const p = db.pointOnRoad(db.ROADS[v.live.road_index], v.live.road_f);
    Object.assign(v.live, { lat: p.lat, lon: p.lon, state: "PARKED", speed_kmh: 0, is_online: true, pack_current_a: 0 });
  })();
  const sosRoad = db.vehicle("veh-008") && db.vehicle("veh-008").live ? db.vehicle("veh-008").live.road : "QL51";
  seedCase("sos-live", "SOS", "BREAKDOWN", "HOTLINE", "veh-008", 1.5, [`SOS by hotline: truck lost power on ${sosRoad}`, `SOS qua tổng đài: xe mất nguồn trên ${sosRoad}`], { error_code: "P1A0B" });
  seedCase("near-breach", "TICKET", "TECHNICAL", "ZALO", "veh-002", 228, ["Regenerative braking weaker than usual", "Phanh tái sinh yếu hơn bình thường"]);
  seedCase("breached", "TICKET", "TECHNICAL", "HOTLINE", "veh-016", 270, ["Dashboard screen reboots while driving", "Màn hình táp-lô tự khởi động lại khi chạy"]);
  seedCase("data-request", "TICKET", "DATA_REQUEST", "HOTLINE", "veh-012", 50, ["Traffic police request: route and speed on 14/10, 06:00–09:00", "CSGT đề nghị cung cấp lộ trình và tốc độ ngày 14/10, 06:00–09:00"], { reporter: "Phòng CSGT Công an tỉnh Đồng Nai", phone: "0251382217" });
  db.support_cases.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));

  /* Seeded notes, resolutions and partner dispatches for the backend-shaped cases, filled once. */
  const SEED_NOTES = {
    BREAKDOWN: ["Called the driver: hazard lights on, truck on the hard shoulder, nobody hurt.", "Đã gọi tài xế: đã bật đèn khẩn cấp, xe đỗ làn khẩn cấp, không ai bị thương."],
    BATTERY: ["Asked the driver to stop and keep the truck parked away from other vehicles; temperature trend sent to Maintenance.", "Đã yêu cầu tài xế dừng xe ở nơi thoáng, tách xa xe khác; đã gửi diễn biến nhiệt độ cho bộ phận Bảo trì."],
    TECHNICAL: ["Reproduced from telemetry; booked a check at the partner workshop.", "Đã đối chiếu dữ liệu xe; đã hẹn kiểm tra tại xưởng đối tác."],
    ACCIDENT: ["Driver safe. Police on site; insurer informed.", "Tài xế an toàn. Công an đã có mặt; đã báo bảo hiểm."],
    CHARGING: ["Charger log checked: the session ended on a connector fault.", "Đã kiểm tra nhật ký trụ: phiên sạc dừng do lỗi súng sạc."],
    OTHER: ["Sent the QR guide by Zalo.", "Đã gửi hướng dẫn quét QR qua Zalo."],
  };
  const RESOLUTIONS = {
    ACCIDENT: ["Truck towed to the Biên Hòa workshop; insurance claim opened.", "Đã kéo xe về xưởng Biên Hòa; đã mở hồ sơ bảo hiểm."],
    TECHNICAL: ["Refrigerant refilled under warranty.", "Đã nạp lại gas điều hòa theo bảo hành."],
    CHARGING: ["Connector replaced; the customer was refunded for the stopped session.", "Đã thay súng sạc; đã hoàn tiền phiên sạc bị dừng."],
    OTHER: ["Camera permission was off in the phone settings; fixed with the driver.", "Quyền camera trên điện thoại bị tắt; đã hướng dẫn tài xế bật lại."],
  };
  let seededAll = false;
  function seedAll() {
    if (seededAll) return;
    seededAll = true;
    db.support_cases.forEach((c) => {
      c.notes = c.notes || [];
      if (c.acknowledged_at && !c.notes.length) {
        const text = SEED_NOTES[c.category] || SEED_NOTES.OTHER;
        c.notes.push({ kind: "NOTE", at: iso(ms(c.acknowledged_at) + 1 * MIN), user_id: c.assignee_user_id, text, internal: false });
      }
      if (c.resolved_at && !c.resolution) c.resolution = RESOLUTIONS[c.category] || RESOLUTIONS.OTHER;
      if (c.status === "CLOSED" && !c.closed_at) c.closed_at = iso(ms(c.resolved_at) + 6 * 60 * MIN);
      if (c.partner_organization_id && !c.dispatch) {
        const sent = ms(c.acknowledged_at || c.created_at) + 3 * MIN;
        const accepted = sent + 6 * MIN;
        const p = profile(c.partner_organization_id);
        const pos = casePosition(c);
        const eta = accepted + Math.round((pos && p ? kmBetween(pos, p) : 20) / 40 * 60 + 12) * MIN;
        const arrived = eta < Date.now();
        c.dispatch = {
          partner_organization_id: c.partner_organization_id, capability: CAP_FOR_CATEGORY[c.category] || "", status: c.resolved_at ? "COMPLETED" : arrived ? "ARRIVED" : "ACCEPTED",
          sent_at: iso(sent), accept_due_at: iso(sent + 15 * MIN), accepted_at: iso(accepted), eta_at: iso(eta), arrived_at: arrived ? iso(eta + 4 * MIN) : null,
          technician: technicianFor(c.partner_organization_id),
          attempts: [{ partner_organization_id: c.partner_organization_id, sent_at: iso(sent), outcome: "ACCEPTED", ended_at: iso(accepted) }],
        };
        c.notes.push({ kind: "DISPATCH", at: iso(sent), user_id: c.assignee_user_id, partner: c.partner_organization_id });
        c.notes.push({ kind: "ACCEPTED", at: iso(accepted), user_id: null, partner: c.partner_organization_id, eta_at: iso(eta) });
        if (arrived) c.notes.push({ kind: "ARRIVED", at: iso(eta + 4 * MIN), user_id: null, partner: c.partner_organization_id });
      }
    });
  }

  /* ---------- Partners ---------- */
  // Partner organizations are the network itself, not customer data: they are read
  // from db directly so the internal organization filter does not hide them.
  const partnerOrgs = () => db.organizations.filter((o) => o.kind === "PARTNER");
  const profile = (orgId) => db.partner_profiles.find((p) => p.organization_id === orgId) || null;
  const radiusKm = (p) => p.radius_km || (p.partner_type === "RESCUE" ? 45 : 30);
  function technicianFor(orgId) {
    if (orgId === "org-p1" && db.people.p1Tech) return { name: db.people.p1Tech.user.full_name, phone: db.people.p1Tech.user.phone_number };
    const r = seeded(orgId + "tech");
    const names = ["Trịnh Văn Khải", "Mai Quốc Bảo", "Lâm Hữu Phước", "Đinh Công Tú"];
    return { name: names[Math.floor(r() * names.length)], phone: "09" + String(Math.floor(r() * 9e7) + 1e7) };
  }
  function isOpenNow(hours) {
    if (!hours || hours === "24/7") return true;
    const m = hours.match(/(\d{1,2}):(\d{2})\s*[–-]\s*(\d{1,2}):(\d{2})/);
    if (!m) return true;
    const now = new Date();
    const mins = now.getHours() * 60 + now.getMinutes();
    return mins >= Number(m[1]) * 60 + Number(m[2]) && mins < Number(m[3]) * 60 + Number(m[4]);
  }
  /** Partners ranked for a case: capable first, then open now, then nearest (SUP-05). */
  function rankPartners(c, cap) {
    const pos = casePosition(c);
    return partnerOrgs().filter((o) => o.status === "ACTIVE").map((o) => {
      const p = profile(o.organization_id);
      if (!p) return null;
      const distance = pos ? kmBetween(pos, p) : null;
      return {
        org: o, p, distance,
        capable: !cap || p.capabilities.includes(cap),
        open: isOpenNow(p.opening_hours),
        inArea: distance === null || distance <= radiusKm(p),
        etaMin: distance === null ? null : Math.round(distance / 40 * 60 + 12),
      };
    }).filter(Boolean).sort((a, b) => (b.capable - a.capable) || (b.open - a.open) || ((a.distance ?? 999) - (b.distance ?? 999)) || (b.p.rating - a.p.rating));
  }
  const stars = (rating) => `<span class="sup-stars" title="${esc(fmt.num(rating, 1))}">${"★".repeat(Math.round(rating))}<span style="color:var(--border-strong)">${"★".repeat(5 - Math.round(rating))}</span></span>`;

  /* Ratings after each case (SUP-04): seeded history, plus the ones given in this session. */
  const COMMENTS = {
    5: ["Arrived ahead of the ETA, very professional.", "Đến sớm hơn dự kiến, rất chuyên nghiệp."],
    4: ["Good job, a bit late to answer the call.", "Làm tốt, nghe máy hơi chậm."],
    3: ["Fixed, but needed a second visit.", "Đã sửa nhưng phải quay lại lần hai."],
    2: ["Late by 40 minutes, no update to the driver.", "Trễ 40 phút, không báo tài xế."],
    1: ["Did not have the right towing equipment.", "Không có thiết bị kéo phù hợp."],
  };
  const ratings = [];
  db.partner_profiles.forEach((p) => {
    const r = seeded(p.organization_id + "ratings");
    for (let i = 0; i < 14; i++) {
      const stars5 = Math.max(1, Math.min(5, Math.round(p.rating + (r() - 0.45) * 1.8)));
      ratings.push({ partner: p.organization_id, at: iso(db.NOW - (i * 13 + r() * 10) * 24 * 60 * MIN), stars: stars5, case_id: `SC-${2100 + Math.floor(r() * 190)}`, comment: COMMENTS[stars5], user_id: db.people.care.user.user_id });
    }
  });
  function addRating(c, orgId, value, comment) {
    const p = profile(orgId);
    ratings.unshift({ partner: orgId, at: new Date().toISOString(), stars: value, case_id: c ? c.support_case_id : "—", comment, user_id: me() });
    if (p) { p.rating = Math.round(((p.rating * p.cases + value) / (p.cases + 1)) * 10) / 10; p.cases += 1; }
    if (c) c.notes.push({ kind: "RATED", at: new Date().toISOString(), user_id: me(), partner: orgId, stars: value });
  }

  /* ---------- Map overlays (partners, incidents) on top of ui.map ---------- */
  /** ui.mountMaps counter-scales markers (--k) as if the map were ~1000 px wide; on the
      smaller maps of this file markers would shrink, so --k is corrected for the real width. */
  function keepMarkerSize(root, mapId) {
    const wrap = root.querySelector(`[data-map-wrap=${mapId}]`);
    const svg = wrap && wrap.querySelector("svg");
    if (!svg) return;
    const fix = () => {
      const width = svg.getBoundingClientRect().width;
      const vbWidth = Number((svg.getAttribute("viewBox") || "").split(" ")[2]) || ui.MAP_SIZE.width;
      if (!width) return;
      const k = (Math.min(1.2, vbWidth / ui.MAP_SIZE.width) * Math.min(2.4, ui.MAP_SIZE.width / width)).toFixed(3);
      if (svg.style.getPropertyValue("--k") !== k) svg.style.setProperty("--k", k);
    };
    const observer = new MutationObserver(fix);
    observer.observe(svg, { attributes: true, attributeFilter: ["viewBox"] });
    G3.onLeave(() => observer.disconnect());
    fix();
  }
  function withOverlay(mapHtml, extra) {
    const i = mapHtml.lastIndexOf("</svg>");
    return mapHtml.slice(0, i) + extra + mapHtml.slice(i);
  }
  function partnerMarker(p, { selected, radius } = {}) {
    const [x, y] = ui.project(p.lat, p.lon);
    const [x2] = ui.project(p.lat, p.lon + radiusKm(p) / 109.6);
    const color = p.partner_type === "RESCUE" ? "#dc2626" : "#2563eb";
    const org = db.org(p.organization_id);
    const ring = radius ? `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(x2 - x).toFixed(1)}" fill="${color}" fill-opacity="0.06" stroke="${color}" stroke-opacity="0.5" stroke-dasharray="6 5" vector-effect="non-scaling-stroke"/>` : "";
    const glyph = p.partner_type === "RESCUE" ? `<path d="M-5,0H5M0,-5V5" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/>` : `<circle r="4" fill="none" stroke="#fff" stroke-width="2.2"/>`;
    return `${ring}<g class="map-station" data-partner="${p.organization_id}" style="transform:translate(${x.toFixed(1)}px,${y.toFixed(1)}px) scale(var(--k,1))" data-tip="${esc(`${org ? org.short_name : p.organization_id} · ${label(p.partner_type)} · ${p.opening_hours}`)}"><circle r="${selected ? 15 : 12}" fill="${color}" stroke="#fff" stroke-width="${selected ? 3.5 : 2}"/>${glyph}</g>`;
  }
  function incidentMarker(pos, tip) {
    const [x, y] = ui.project(pos.lat, pos.lon);
    return `<g style="transform:translate(${x.toFixed(1)}px,${y.toFixed(1)}px) scale(var(--k,1))" data-tip="${esc(tip)}"><circle r="15" fill="#dc2626" opacity="0.18"/><path d="M0,-18 C-8,-18 -11,-11 -11,-7 C-11,0 0,9 0,9 C0,9 11,0 11,-7 C11,-11 8,-18 0,-18Z" fill="#dc2626" stroke="#fff" stroke-width="2" transform="translate(0,-3)"/><text y="-6" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">!</text></g>`;
  }

  /* ---------- Creating a case (SUP-01, SUP-02) ---------- */
  function createCase({ type, category, channel, vehicle, organizationId, reporterName, reporterPhone, subject, description }) {
    const now = Date.now();
    const drv = vehicle && vehicle.driver_id ? db.driver(vehicle.driver_id) : null;
    const c = {
      support_case_id: nextCaseId(), case_type: type, category, channel, status: "OPEN",
      vehicle_id: vehicle ? vehicle.vehicle_id : null, vin: vehicle ? vehicle.vin : null,
      organization_id: vehicle ? vehicle.organization_id : organizationId,
      driver_id: drv ? drv.driver_id : null,
      reporter_name: reporterName || (drv ? drv.full_name : "—"),
      reporter_phone: reporterPhone || (drv ? drv.phone_number : ""),
      subject: [subject, subject],
      error_code: vehicle ? ACTIVE_ERRORS[vehicle.vehicle_id] || null : null,
      location: vehicle && vehicle.live ? { lat: vehicle.live.lat, lon: vehicle.live.lon } : null,
      created_at: iso(now), sla_due_at: iso(now + SLA_MINUTES(type, category) * MIN),
      acknowledged_at: null, resolved_at: null, assignee_user_id: null, partner_organization_id: null,
      created_by_user_id: me(), notes: [],
    };
    if (description) c.notes.push({ kind: "NOTE", at: iso(now), user_id: me(), text: description, internal: false });
    db.support_cases.unshift(c);
    if (type === "SOS") {
      // SUP-02: a critical alert reaches the support console at once (NTF-01 poll).
      db.notifications.unshift({ notification_id: 88000 + db.notifications.length + 900, notification_type: "SOS_ALERT", severity: "CRITICAL", vehicle_id: c.vehicle_id, organization_id: c.organization_id, title: [`SOS ${c.support_case_id}: ${subject}`, `SOS ${c.support_case_id}: ${subject}`], created_at: iso(now), read_at: null, payload: { support_case_id: c.support_case_id }, station_id: null });
    }
    return c;
  }

  function contextPreview(v, text) {
    if (!text) return `<div class="small muted">${esc(t("Type a plate or VIN: the truck's VIN, owner, checked-in driver, position and active fault are attached automatically.", "Nhập biển số hoặc VIN: VIN, chủ xe, tài xế đang nhận xe, vị trí và mã lỗi đang có sẽ được gắn tự động."))}</div>`;
    if (!v) return ui.callout("warn", esc(t("No visible truck matches this plate or VIN.", "Không tìm thấy xe có biển số hoặc VIN này.")));
    const drv = v.driver_id ? db.driver(v.driver_id) : null;
    const code = ACTIVE_ERRORS[v.vehicle_id];
    return ui.callout("ok", `<b>${esc(v.license_plate)}</b> · <span class="mono">${esc(v.vin)}</span> · ${ui.orgName(v.organization_id)}<br>${esc(t("Driver", "Tài xế"))}: ${drv ? `${esc(drv.full_name)} (${esc(drv.phone_number)})` : esc(t("nobody checked in", "chưa có tài xế nhận xe"))} · ${esc(v.live ? placeText(v.live) : t("no position", "chưa có vị trí"))}${v.live ? ` · ${esc(t("battery", "pin"))} ${Math.round(v.live.soc_percent)}%` : ""}${code ? `<br>${esc(t("Active fault", "Lỗi đang có"))}: <b class="mono">${esc(code)}</b> ${esc(errorText(code))}` : ""}`, "truck");
  }

  /** Internal staff log a hotline or Zalo contact as a ticket or an SOS (SUP-01, SUP-02). */
  function logContactForm(preset = {}) {
    const customers = db.organizations.filter((o) => o.kind === "CUSTOMER");
    const modal = ui.formModal({
      title: t("Log a hotline / Zalo contact", "Ghi nhận cuộc gọi / tin nhắn Zalo"),
      intro: `<div data-ctx>${contextPreview(null, "")}</div>`,
      wide: true,
      submitLabel: t("Create case", "Tạo yêu cầu"),
      fields: [
        { name: "type", label: t("Type", "Loại"), type: "select", value: preset.type || "TICKET", options: [["TICKET", label("TICKET")], ["SOS", t("SOS — call back within 5 min", "SOS — gọi lại trong 5 phút")]] },
        { name: "channel", label: t("Channel", "Kênh"), type: "select", value: "HOTLINE", options: [["HOTLINE", label("HOTLINE")], ["ZALO", label("ZALO")]] },
        { name: "truck", label: t("Truck (plate or VIN)", "Xe (biển số hoặc VIN)"), full: true, placeholder: "51C-123.45 / LZT262…", hint: t("VIN has 17 characters.", "VIN gồm 17 ký tự.") },
        { name: "organization", label: t("Organization (when the truck is unknown)", "Tổ chức (khi chưa rõ xe)"), type: "select", options: [["", t("— from the truck —", "— theo xe —")], ...customers.map((o) => [o.organization_id, o.short_name])] },
        { name: "category", label: t("Category", "Phân loại"), type: "select", value: preset.type === "SOS" ? "BREAKDOWN" : "TECHNICAL", options: CATEGORIES.map((k) => [k, catLabel(k)]) },
        { name: "reporter_name", label: t("Caller name", "Tên người gọi"), placeholder: t("Filled from the checked-in driver", "Lấy theo tài xế đang nhận xe") },
        { name: "reporter_phone", label: t("Call-back phone", "Số gọi lại"), type: "tel", placeholder: "09xx xxx xxx", hint: t("10 digits", "10 chữ số") },
        { name: "subject", label: t("Subject", "Tiêu đề"), required: true, full: true },
        { name: "description", label: t("What the caller said", "Nội dung cuộc gọi"), type: "textarea", full: true },
      ],
      onSubmit(values) {
        const truckText = values.truck;
        if (truckText && /^[A-Z0-9]{12,}$/i.test(normalize(truckText)) && normalize(truckText).length !== 17 && !findTruck(truckText)) return t("A VIN has exactly 17 characters.", "VIN phải gồm đúng 17 ký tự.");
        const vehicle = findTruck(truckText);
        if (truckText && !vehicle) return t("No visible truck matches this plate or VIN.", "Không tìm thấy xe có biển số hoặc VIN này.");
        if (!vehicle && !values.organization) return t("Pick the truck or, if it is unknown, the organization.", "Chọn xe, hoặc tổ chức nếu chưa rõ xe.");
        const phone = values.reporter_phone.replace(/\D/g, "");
        const drv = vehicle && vehicle.driver_id ? db.driver(vehicle.driver_id) : null;
        if (!phone && !drv) return t("A call-back phone is required when no driver is checked in.", "Cần số gọi lại khi xe chưa có tài xế nhận xe.");
        if (phone && phone.length !== 10) return t("The phone number must have 10 digits.", "Số điện thoại phải gồm 10 chữ số.");
        const c = createCase({ type: values.type, category: values.category, channel: values.channel, vehicle, organizationId: values.organization, reporterName: values.reporter_name, reporterPhone: phone, subject: values.subject, description: values.description });
        ui.toast(values.type === "SOS" ? `<b>${esc(t("SOS logged", "Đã ghi nhận SOS"))} ${esc(c.support_case_id)}</b><br>${esc(t("Call back within 5 minutes.", "Gọi lại trong vòng 5 phút."))}` : t(`Ticket ${c.support_case_id} created`, `Đã tạo yêu cầu ${c.support_case_id}`), values.type === "SOS" ? "alert" : "", values.type === "SOS" ? "sos" : "check");
        G3.refreshChrome();
        G3.navigate(`/support/${c.support_case_id}`);
      },
    });
    const truckInput = modal.el.querySelector("[name=truck]");
    const ctx = modal.el.querySelector("[data-ctx]");
    truckInput.addEventListener("input", () => {
      const v = findTruck(truckInput.value);
      ctx.innerHTML = contextPreview(v, truckInput.value.trim());
      if (v && v.driver_id) {
        const drv = db.driver(v.driver_id);
        const nameEl = modal.el.querySelector("[name=reporter_name]");
        const phoneEl = modal.el.querySelector("[name=reporter_phone]");
        if (!nameEl.value) nameEl.value = drv.full_name;
        if (!phoneEl.value) phoneEl.value = drv.phone_number;
      }
    });
  }

  /** A customer raises a request from the portal (SUP-01); it counts as the in-app channel. */
  function customerRequestForm() {
    const trucks = scope.vehicles();
    ui.formModal({
      title: t("New support request", "Tạo yêu cầu hỗ trợ"),
      intro: `<p class="small muted">${esc(t("The truck's VIN, position and active fault code are attached automatically. For an emergency, call 1900 6868 or use SOS in the driver app.", "VIN, vị trí và mã lỗi đang có của xe được gắn tự động. Khi khẩn cấp, gọi 1900 6868 hoặc bấm SOS trên ứng dụng tài xế."))}</p>`,
      submitLabel: t("Send request", "Gửi yêu cầu"),
      fields: [
        { name: "truck", label: t("Truck", "Xe"), type: "select", options: [["", t("— not about one truck —", "— không gắn với xe —")], ...trucks.map((v) => [v.vehicle_id, `${v.license_plate} · ${v.vin}`])] },
        { name: "category", label: t("Category", "Phân loại"), type: "select", value: "TECHNICAL", options: CATEGORIES.map((k) => [k, catLabel(k)]) },
        { name: "subject", label: t("Subject", "Tiêu đề"), required: true },
        { name: "description", label: t("Details", "Mô tả chi tiết"), type: "textarea", required: true },
      ],
      onSubmit(values) {
        const user = scope.user();
        const vehicle = values.truck ? db.vehicle(values.truck) : null;
        const c = createCase({ type: "TICKET", category: values.category, channel: "IN_APP", vehicle, organizationId: scope.org().organization_id, reporterName: user.full_name, reporterPhone: user.phone_number, subject: values.subject, description: values.description });
        ui.toast(t(`Request ${c.support_case_id} sent. Customer care answers within ${SLA_MINUTES("TICKET", values.category) / 60} h.`, `Đã gửi yêu cầu ${c.support_case_id}. CSKH phản hồi trong ${SLA_MINUTES("TICKET", values.category) / 60} giờ.`));
        G3.navigate(`/support/${c.support_case_id}`);
      },
    });
  }

  /* ---------- Status changes (decided: reasons kept in status_reason) ---------- */
  function acknowledge(c) {
    c.status = "ACKNOWLEDGED";
    c.acknowledged_at = new Date().toISOString();
    c.assignee_user_id = c.assignee_user_id || me();
    ui.toast(c.case_type === "SOS" ? t(`${c.support_case_id}: call-back recorded`, `${c.support_case_id}: đã ghi nhận gọi lại`) : t(`${c.support_case_id} acknowledged`, `Đã tiếp nhận ${c.support_case_id}`));
    G3.refreshChrome();
    G3.render();
  }
  function resolveCase(c) {
    const partnerId = c.dispatch && ["ACCEPTED", "ARRIVED"].includes(c.dispatch.status) ? c.dispatch.partner_organization_id : null;
    const partner = partnerId ? db.org(partnerId) : null;
    ui.formModal({
      title: t(`Resolve ${c.support_case_id}`, `Xử lý xong ${c.support_case_id}`),
      submitLabel: t("Mark resolved", "Đánh dấu đã xử lý"),
      fields: [
        { name: "resolution", label: t("Resolution", "Kết quả xử lý"), type: "textarea", required: true, hint: t("Shown to the customer and kept with the change history.", "Hiển thị cho khách hàng và lưu vào lịch sử thay đổi.") },
        partner ? { name: "stars", label: t(`Rate ${partner.short_name}`, `Đánh giá ${partner.short_name}`), type: "select", value: "5", options: [5, 4, 3, 2, 1].map((n) => [String(n), "★".repeat(n)]) } : null,
        partner ? { name: "comment", label: t("Rating comment", "Nhận xét"), placeholder: t("Optional", "Không bắt buộc") } : null,
      ].filter(Boolean),
      onSubmit(values) {
        const now = new Date().toISOString();
        if (!c.acknowledged_at) { c.acknowledged_at = now; c.assignee_user_id = c.assignee_user_id || me(); }
        c.status = "RESOLVED";
        c.resolved_at = now;
        c.resolution = values.resolution.slice(0, 200);
        c.status_reason = c.resolution;
        if (c.dispatch && ["PENDING", "ACCEPTED", "ARRIVED"].includes(c.dispatch.status)) { clearAcceptTimer(c); c.dispatch.status = "COMPLETED"; }
        if (partner) addRating(c, partnerId, Number(values.stars), values.comment || null);
        ui.toast(t(`${c.support_case_id} resolved`, `Đã xử lý xong ${c.support_case_id}`));
        G3.refreshChrome();
        G3.render();
      },
    });
  }
  function closeCase(c) {
    c.status = "CLOSED";
    c.closed_at = new Date().toISOString();
    ui.toast(t(`${c.support_case_id} closed`, `Đã đóng ${c.support_case_id}`));
    G3.render();
  }
  function reopenCase(c) {
    ui.reasonModal({
      title: t(`Reopen ${c.support_case_id}`, `Mở lại ${c.support_case_id}`), danger: false, submitLabel: t("Reopen", "Mở lại"),
      onSubmit(reason) {
        c.status = "ACKNOWLEDGED";
        c.resolved_at = null;
        c.status_reason = reason;
        c.notes.push({ kind: "REOPENED", at: new Date().toISOString(), user_id: me(), text: reason });
        ui.toast(t("Case reopened", "Đã mở lại yêu cầu"));
        G3.render();
      },
    });
  }
  function cancelCase(c) {
    ui.reasonModal({
      title: t(`Cancel ${c.support_case_id}`, `Hủy ${c.support_case_id}`),
      intro: `<p class="small muted">${esc(t("A cancelled case accepts no further change. If nobody had responded yet, its SLA verdict is frozen at the cancellation time.", "Yêu cầu đã hủy không thể thay đổi thêm. Nếu chưa ai phản hồi, kết quả SLA được chốt tại thời điểm hủy."))}</p>`,
      submitLabel: t("Cancel the case", "Hủy yêu cầu"),
      onSubmit(reason) {
        c.status = "CANCELLED";
        c.closed_at = new Date().toISOString();
        c.status_reason = reason;
        if (c.dispatch && c.dispatch.status === "PENDING") { clearAcceptTimer(c); c.dispatch.status = "CANCELLED"; }
        ui.toast(t(`${c.support_case_id} cancelled`, `Đã hủy ${c.support_case_id}`));
        G3.refreshChrome();
        G3.render();
      },
    });
  }

  /* ---------- Partner dispatch (SUP-05) ---------- */
  // Acceptance is simulated (SUP-06 intake is not built): timers live outside the
  // screen so a partner can still answer after the user navigates away.
  const acceptTimers = {};
  const clearAcceptTimer = (c) => { clearTimeout(acceptTimers[c.support_case_id]); delete acceptTimers[c.support_case_id]; };
  const onScreenOf = (c) => location.hash.replace(/^#/, "").split("?")[0] === `/support/${c.support_case_id}`;
  function dispatchTo(c, orgId, cap) {
    const now = Date.now();
    clearAcceptTimer(c);
    const d = c.dispatch || (c.dispatch = { attempts: [] });
    Object.assign(d, { partner_organization_id: orgId, capability: cap, status: "PENDING", sent_at: iso(now), accept_due_at: iso(now + 15 * MIN), accepted_at: null, eta_at: null, arrived_at: null, technician: null });
    d.attempts.push({ partner_organization_id: orgId, sent_at: iso(now), outcome: "PENDING", ended_at: null });
    c.partner_organization_id = orgId;
    c.notes.push({ kind: "DISPATCH", at: iso(now), user_id: me(), partner: orgId, capability: cap });
    if (!d.hold) acceptTimers[c.support_case_id] = setTimeout(() => partnerAccepts(c), 5000 + (orgId.charCodeAt(orgId.length - 1) % 3) * 1000);
    ui.toast(t(`Incident sent to ${db.org(orgId).short_name}: position, VIN, fault code and photos. Waiting for acceptance (15 min).`, `Đã gửi sự cố cho ${db.org(orgId).short_name}: vị trí, VIN, mã lỗi và ảnh. Chờ xác nhận (15 phút).`), "", "send");
  }
  function partnerAccepts(c) {
    const d = c.dispatch;
    if (!d || d.status !== "PENDING") return;
    const now = Date.now();
    const p = profile(d.partner_organization_id);
    const pos = casePosition(c);
    const etaMin = Math.round((pos && p ? kmBetween(pos, p) : 20) / 40 * 60 + 12);
    Object.assign(d, { status: "ACCEPTED", accepted_at: iso(now), eta_at: iso(now + etaMin * MIN), technician: technicianFor(d.partner_organization_id) });
    d.attempts[d.attempts.length - 1].outcome = "ACCEPTED";
    d.attempts[d.attempts.length - 1].ended_at = iso(now);
    c.notes.push({ kind: "ACCEPTED", at: iso(now), user_id: null, partner: d.partner_organization_id, eta_at: d.eta_at });
    ui.toast(t(`${db.org(d.partner_organization_id).short_name} accepted ${c.support_case_id} · ETA ${fmt.time(d.eta_at)}`, `${db.org(d.partner_organization_id).short_name} đã nhận ${c.support_case_id} · dự kiến đến ${fmt.time(d.eta_at)}`), "", "handshake");
    if (onScreenOf(c)) G3.render();
  }
  function acceptanceTimedOut(c) {
    const d = c.dispatch;
    if (!d || d.status !== "PENDING") return;
    clearAcceptTimer(c);
    const now = Date.now();
    const timedOut = d.partner_organization_id;
    d.status = "TIMEOUT";
    d.attempts[d.attempts.length - 1].outcome = "TIMEOUT";
    d.attempts[d.attempts.length - 1].ended_at = iso(now);
    c.notes.push({ kind: "TIMEOUT", at: iso(now), user_id: null, partner: timedOut });
    if (c.dispatch_auto_route !== false) {
      const tried = d.attempts.map((a) => a.partner_organization_id);
      const next = rankPartners(c, d.capability).find((r) => !tried.includes(r.org.organization_id));
      if (next) {
        d.hold = false;
        dispatchTo(c, next.org.organization_id, d.capability);
        ui.toast(t(`${db.org(timedOut).short_name} did not answer in 15 min: re-routed to ${next.org.short_name}.`, `${db.org(timedOut).short_name} không xác nhận trong 15 phút: đã chuyển sang ${next.org.short_name}.`), "alert", "refresh");
      } else ui.toast(t("No other capable partner left: call the partners by phone.", "Không còn đối tác phù hợp: hãy gọi điện trực tiếp cho đối tác."), "alert");
    } else ui.toast(t("Acceptance timed out. Pick another partner.", "Quá hạn xác nhận. Hãy chọn đối tác khác."), "alert");
    G3.render();
  }

  /* =====================================================================
     Screen 1: support console (SUP-01, SUP-02, SUP-03)
     ===================================================================== */
  function medianResponse(cases) {
    const since = Date.now() - 30 * 24 * 60 * MIN;
    const times = cases.filter((c) => c.acknowledged_at && ms(c.created_at) > since).map((c) => (ms(c.acknowledged_at) - ms(c.created_at)) / MIN).sort((a, b) => a - b);
    if (!times.length) return null;
    const m = Math.floor(times.length / 2);
    return times.length % 2 ? times[m] : (times[m - 1] + times[m]) / 2;
  }
  const navBadge = () => visibleCases().filter((c) => (c.case_type === "SOS" && isActive(c)) || (isActive(c) && isBreached(c))).length;

  function sosCard(c, internal) {
    const v = db.vehicle(c.vehicle_id);
    const pos = casePosition(c);
    const waiting = c.status === "OPEN";
    const took = c.acknowledged_at ? (ms(c.acknowledged_at) - ms(c.created_at)) / MIN : null;
    const phone = String(c.reporter_phone || "").replace(/\D/g, "");
    return `<div class="card sup-sos ${waiting ? "live" : ""}" data-href="/support/${esc(c.support_case_id)}"><div class="card-body">
      <div class="row between"><span class="row" style="gap:6px">${ui.badge("SOS")}<b>${esc(c.support_case_id)}</b>${ui.tag(label(c.channel))}</span><span class="small muted">${fmt.ago(c.created_at)}</span></div>
      <div class="strong" style="margin-top:8px">${esc(tx(c.subject))}</div>
      <div class="small muted">${v ? `<b>${esc(v.license_plate)}</b> · ` : ""}${ui.orgName(c.organization_id)} · ${esc(placeText(pos))}</div>
      ${c.error_code ? `<div class="small" style="margin-top:4px"><span class="mono">${esc(c.error_code)}</span> ${esc(errorText(c.error_code))}</div>` : ""}
      <div class="row between" style="margin-top:12px;align-items:flex-end">
        <div><div class="small muted">${esc(waiting ? t("Call back within 5 minutes", "Gọi lại trong vòng 5 phút") : t("Called back after", "Đã gọi lại sau"))}</div>
          <div class="sup-big">${waiting ? countdownEl(ms(c.sla_due_at), "sla") : `<span class="sup-cd ${isBreached(c) ? "danger" : "ok"}">${fmt.duration(took)}</span>`}</div></div>
        <div class="row">${phone ? ui.btn(c.reporter_name && c.reporter_name !== "—" ? c.reporter_name : c.reporter_phone, { href: `tel:${phone}`, icon: "phone", sm: true }) : ""}${internal && waiting ? ui.btn(t("Called back", "Đã gọi lại"), { action: "ack", kind: "primary", sm: true, attrs: `data-id="${esc(c.support_case_id)}"` }) : ""}</div>
      </div></div></div>`;
  }

  function queueFilters(all, query) {
    const q = (query.q || "").toLowerCase();
    const status = query.status || "open";
    return all.filter((c) => {
      if (status === "open" && !isActive(c)) return false;
      if (status !== "open" && status !== "all" && c.status !== status) return false;
      if (query.channel && c.channel !== query.channel) return false;
      if (query.category && c.category !== query.category) return false;
      if (query.type && c.case_type !== query.type) return false;
      if (query.awaiting && !isAwaiting(c)) return false;
      if (query.breached && !(isBreached(c) && !isTerminal(c))) return false;
      if (q) {
        const v = db.vehicle(c.vehicle_id);
        const hay = [c.support_case_id, tx(c.subject), c.reporter_name, c.reporter_phone, c.vin, v && v.license_plate].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    }).sort((a, b) => {
      const ra = isActive(a) ? 0 : 1, rb = isActive(b) ? 0 : 1;
      if (ra !== rb) return ra - rb;
      if (!ra) {
        const sa = a.case_type === "SOS" ? 0 : 1, sb = b.case_type === "SOS" ? 0 : 1;
        if (sa !== sb) return sa - sb;
        const da = isAwaiting(a) ? ms(a.sla_due_at) : Infinity, dbb = isAwaiting(b) ? ms(b.sla_due_at) : Infinity;
        if (da !== dbb) return da - dbb;
      }
      return a.created_at < b.created_at ? 1 : -1;
    });
  }

  function queueColumns(internal) {
    return [
      { label: t("Case", "Yêu cầu"), render: (c) => `<b>${esc(c.support_case_id)}</b>${c.case_type === "SOS" ? " " + ui.badge("SOS") : ""}<span class="sup-subject" title="${esc(tx(c.subject))}">${esc(tx(c.subject))}</span>` },
      { label: t("Category · channel", "Phân loại · kênh"), render: (c) => `${esc(catLabel(c.category))}<span class="sub">${esc(label(c.channel))}</span>` },
      { label: t("Truck", "Xe"), cls: "nowrap", render: (c) => { const v = db.vehicle(c.vehicle_id); return v ? `<b title="${esc(v.vin)}">${esc(v.license_plate)}</b><span class="sub mono" title="${esc(v.vin)}">…${esc(v.vin.slice(-6))}</span>` : '<span class="muted">—</span>'; } },
      internal ? { label: t("Organization", "Tổ chức"), cls: "nowrap", render: (c) => ui.orgName(c.organization_id) } : null,
      { label: t("Reporter", "Người báo"), render: (c) => `<span class="sup-ellipsis" title="${esc(c.reporter_name)}">${esc(c.reporter_name)}</span><span class="sub">${esc(c.reporter_phone || "")}</span>` },
      { label: t("Status", "Trạng thái"), render: (c) => badge(c.status) },
      { label: t("Response SLA", "SLA phản hồi"), cls: "nowrap", render: slaCell },
      { label: t("Assignee", "Người xử lý"), render: (c) => (c.assignee_user_id ? ui.userName(c.assignee_user_id) : `<span class="muted">${esc(t("Unassigned", "Chưa phân công"))}</span>`) },
    ].filter(Boolean);
  }

  function supportPage({ query }) {
    seedAll();
    const internal = scope.isInternal();
    const all = visibleCases();
    const now = Date.now();
    const active = all.filter(isActive);
    const sosOpen = active.filter((c) => c.case_type === "SOS");
    const awaiting = all.filter(isAwaiting);
    const breached = active.filter((c) => isBreached(c));
    const soon = awaiting.filter((c) => !isBreached(c) && ms(c.sla_due_at) - now < 15 * MIN);
    const median = medianResponse(all);
    const rows = queueFilters(all, query);
    const status = query.status || "open";

    const head = ui.pageHead({
      title: internal ? t("Support console", "Bảng điều khiển CSKH") : t("Support requests", "Yêu cầu hỗ trợ"),
      sub: esc(internal ? t("Tickets and SOS from the driver app, Zalo and the hotline, with their response SLA.", "Yêu cầu và SOS từ ứng dụng tài xế, Zalo và tổng đài, kèm SLA phản hồi.") : t(`Requests of ${scope.org().name}. Customer care answers 24/7 — hotline 1900 6868.`, `Yêu cầu của ${scope.org().name}. CSKH trực 24/7 — tổng đài 1900 6868.`)),
      features: internal ? ["SUP-01", "SUP-02", "SUP-03"] : ["SUP-01", "SUP-02"],
      actions: `${ui.btn(t("Export", "Xuất"), { icon: "download", action: "export" })}${internal ? `${ui.btn(t("Log hotline SOS", "Ghi nhận SOS"), { icon: "sos", kind: "danger", action: "log-sos" })}${ui.btn(t("Log a hotline/Zalo contact", "Ghi nhận cuộc gọi/Zalo"), { icon: "phone", kind: "primary", action: "log" })}` : ui.btn(t("New request", "Tạo yêu cầu"), { icon: "plus", kind: "primary", action: "new" })}`,
    });

    const kpis = `<div class="grid cols-5">
      ${ui.kpi({ label: t("Open cases", "Đang mở"), icon: "lifebuoy", value: active.length, foot: `${all.filter((c) => c.status === "RESOLVED").length} ${t("resolved, to close", "đã xử lý, chờ đóng")}` })}
      ${ui.kpi({ label: t("SOS open", "SOS đang mở"), icon: "sos", value: sosOpen.length, tone: sosOpen.length ? "danger" : "ok", foot: t("call back ≤ 5 min (SUP-02)", "gọi lại ≤ 5 phút (SUP-02)") })}
      ${ui.kpi({ label: t("Awaiting response", "Chờ phản hồi"), icon: "clock", value: awaiting.length, tone: soon.length ? "warn" : undefined, foot: `${soon.length} ${t("breach within 15 min", "sắp quá hạn trong 15 phút")}` })}
      ${ui.kpi({ label: t("Past SLA", "Quá SLA"), icon: "alert", value: breached.length, tone: breached.length ? "danger" : "ok", foot: t("escalated to the team lead", "đã báo trưởng nhóm") })}
      ${ui.kpi({ label: t("Median first response", "Trung vị phản hồi đầu"), icon: "activity", value: median === null ? "—" : fmt.num(median, 0), unit: t("min", "phút"), foot: t("last 30 days", "30 ngày qua") })}
    </div>`;

    const warnings = internal && (soon.length || breached.length) ? `<div class="mt">${ui.callout(breached.length ? "danger" : "warn", `${breached.length ? `<b>${esc(t(`${breached.length} case(s) past their response SLA`, `${breached.length} yêu cầu đã quá SLA phản hồi`))}</b> (${breached.map((c) => `<a href="#/support/${esc(c.support_case_id)}">${esc(c.support_case_id)}</a>`).join(", ")}) — ${esc(t("escalated to the customer-care lead (NTF-08).", "đã chuyển trưởng nhóm CSKH (NTF-08)."))} ` : ""}${soon.length ? `${esc(t(`${soon.length} more breach within 15 minutes:`, `${soon.length} yêu cầu khác sẽ quá hạn trong 15 phút:`))} ${soon.map((c) => `<a href="#/support/${esc(c.support_case_id)}">${esc(c.support_case_id)}</a> ${countdownEl(ms(c.sla_due_at))}`).join(", ")}` : ""}`)}</div>` : "";

    const sos = sosOpen.length ? `<div class="row between mt" style="margin-bottom:8px"><h3>${ui.icon("sos", 16, 'style="vertical-align:-3px;color:var(--danger)"')} ${esc(t("SOS — pinned until resolved", "SOS — ghim đến khi xử lý xong"))}</h3></div><div class="grid cols-3">${sosOpen.map((c) => sosCard(c, internal)).join("")}</div>` : "";

    const toggle = (key, text, count) => `<button class="btn sm ${query[key] ? "primary" : ""}" data-toggle="${key}">${esc(text)} <span class="pill-count" style="background:transparent">${count}</span></button>`;
    const toolbar = `<div class="toolbar">
      ${ui.search("q", query.q, t("Case, plate, VIN, reporter…", "Mã, biển số, VIN, người báo…"))}
      ${ui.select("status", [["open", t("Open (new + acknowledged)", "Đang mở (mới + đã tiếp nhận)")], ["all", t("All statuses", "Mọi trạng thái")], ...STATUSES.map((s) => [s, label(s)])], status)}
      ${ui.select("channel", [["", t("All channels", "Mọi kênh")], ...CHANNELS.map((s) => [s, label(s)])], query.channel || "")}
      ${ui.select("category", [["", t("All categories", "Mọi phân loại")], ...CATEGORIES.map((s) => [s, catLabel(s)])], query.category || "")}
      ${ui.select("type", [["", t("Tickets and SOS", "Yêu cầu và SOS")], ["TICKET", label("TICKET")], ["SOS", "SOS"]], query.type || "")}
      ${toggle("awaiting", t("Awaiting response", "Chờ phản hồi"), awaiting.length)}
      ${toggle("breached", t("Breached", "Quá hạn"), all.filter((c) => isBreached(c) && !isTerminal(c)).length)}
      <span class="grow"></span><span class="small muted">${rows.length} ${esc(t("cases", "yêu cầu"))}</span>
    </div>`;
    const queue = `<section class="card mt"><div class="card-head"><h3>${esc(t("Queue", "Hàng đợi"))} <span class="sub">${esc(t("sorted by SOS, then response deadline", "ưu tiên SOS, rồi đến hạn phản hồi"))}</span></h3></div>${toolbar}<div class="card-body flush">${ui.table({ rows, columns: queueColumns(internal), href: (c) => `/support/${c.support_case_id}`, rowAttrs: (c) => (c.case_type === "SOS" && isActive(c) ? "data-sos" : ""), empty: t("No case matches these filters", "Không có yêu cầu phù hợp bộ lọc") })}</div></section>`;

    return STYLE + head + kpis + warnings + sos + queue + (internal ? internalCharts(all) : customerCards(all));
  }

  function internalCharts(all) {
    const days = 14;
    const r = seeded("support-volume");
    const labels = Array.from({ length: days }, (_, i) => { const d = new Date(db.NOW - (days - 1 - i) * 24 * 60 * MIN); return `${d.getDate()}/${d.getMonth() + 1}`; });
    const vol = CHANNELS.map((ch, k) => ({ name: label(ch), values: labels.map((_, i) => Math.round([5, 3, 2][k] + r() * [6, 4, 3][k] + (i > 9 ? 2 : 0))), color: ui.PALETTE[[1, 0, 2][k]] }));
    const today = new Date().toISOString().slice(0, 10);
    all.filter((c) => c.created_at.slice(0, 10) === today).forEach((c) => { vol[CHANNELS.indexOf(c.channel)].values[days - 1] += 1; });
    const met = [["BREAKDOWN", 94], ["ACCIDENT", 100], ["BATTERY", 91], ["TECHNICAL", 88], ["CHARGING", 96], ["BILLING", 99]];
    const team = db.memberships.filter((m) => m.organization_id === "org-g3" && m.roles.some((x) => ["CUSTOMER_CARE", "OPERATIONS"].includes(x)));
    return `<div class="grid cols-3 mt">
      ${ui.card({ title: t("Cases per day", "Yêu cầu theo ngày"), sub: t(`by channel, ${days} days`, `theo kênh, ${days} ngày`), actions: ui.legend(vol.map((s) => [s.name, s.color])), body: ui.barChart({ labels, series: vol, stacked: true, height: 210 }) })}
      ${ui.card({ title: t("Response SLA met (30 d)", "Tỷ lệ đạt SLA phản hồi (30 ngày)"), sub: "SUP-03", body: ui.hBars({ labels: met.map(([k]) => catLabel(k)), series: [{ values: met.map(([, v]) => v), color: ui.PALETTE[0] }], unit: "%" }) + `<div class="small muted mt">${esc(t("SOS call-back within 5 min: 97% (63 of 65).", "SOS gọi lại trong 5 phút: 97% (63/65)."))}</div>` })}
      ${ui.card({ title: t("Workload", "Khối lượng công việc"), sub: t("open cases per agent", "yêu cầu đang mở theo nhân viên"), flush: true, body: ui.table({ rows: team, columns: [
        { label: t("Agent", "Nhân viên"), render: (m) => `${ui.userName(m.user_id)}<span class="sub">${esc(m.roles.map(G3.roleLabel).join(", "))}</span>` },
        { label: t("Open", "Đang mở"), align: "right", render: (m) => all.filter((c) => isActive(c) && c.assignee_user_id === m.user_id).length },
        { label: t("Resolved (7 d)", "Đã xử lý (7 ngày)"), align: "right", render: (m) => all.filter((c) => c.resolved_at && c.assignee_user_id === m.user_id && Date.now() - ms(c.resolved_at) < 7 * 24 * 60 * MIN).length + (m.roles.includes("CUSTOMER_CARE") ? 14 : 3) },
      ] }) + `<div class="card-body small muted">${esc(t("Unassigned open cases", "Yêu cầu chưa phân công"))}: <b>${all.filter((c) => isActive(c) && !c.assignee_user_id).length}</b></div>` })}
    </div>`;
  }

  function customerCards(all) {
    const byCat = CATEGORIES.map((k) => [k, all.filter((c) => c.category === k).length]).filter(([, n]) => n);
    return `<div class="grid cols-2 mt">
      ${ui.card({ title: t("Your requests by category", "Yêu cầu theo phân loại"), body: byCat.length ? ui.hBars({ labels: byCat.map(([k]) => catLabel(k)), series: [{ values: byCat.map(([, n]) => n), color: ui.PALETTE[1] }] }) : ui.empty() })}
      ${ui.card({ title: t("How to reach us", "Liên hệ CSKH"), body: `${ui.kv([
        [t("Hotline (24/7)", "Tổng đài (24/7)"), `<a href="tel:19006868">1900 6868</a>`],
        [t("Zalo", "Zalo"), esc(t("Official account “G3 Network”", "Tài khoản chính thức “G3 Network”"))],
        [t("SOS", "SOS"), esc(t("In the driver app: call back within 5 min", "Trên ứng dụng tài xế: gọi lại trong 5 phút"))],
        [t("Breakdown / accident", "Hỏng xe / tai nạn"), esc(t("response within 30 min", "phản hồi trong 30 phút"))],
        [t("Technical", "Kỹ thuật"), esc(t("response within 4 h", "phản hồi trong 4 giờ"))],
        [t("Billing, claims, data", "Thanh toán, khiếu nại, dữ liệu"), esc(t("response within 24 h", "phản hồi trong 24 giờ"))],
      ])}` })}
    </div>`;
  }

  function mountSupport(root, { query }) {
    const bind = (sel, fn) => root.querySelectorAll(sel).forEach((el) => el.addEventListener("click", (e) => fn(el, e)));
    root.querySelectorAll("[data-filter]").forEach((el) => el.addEventListener("change", () => G3.setQuery({ [el.dataset.filter]: el.value === "open" && el.dataset.filter === "status" ? null : el.value })));
    bind("[data-toggle]", (el) => G3.setQuery({ [el.dataset.toggle]: query[el.dataset.toggle] ? null : "1" }));
    bind("[data-action=log]", () => logContactForm());
    bind("[data-action=log-sos]", () => logContactForm({ type: "SOS" }));
    bind("[data-action=new]", () => customerRequestForm());
    bind("[data-action=ack]", (el) => { const c = findCase(el.dataset.id); if (c) acknowledge(c); });
    bind("[data-action=export]", () => {
      const rows = queueFilters(visibleCases(), query);
      ui.auditedExport({
        dataType: "SUPPORT_CASES", subject: null, filename: "support-cases.csv", rows,
        columns: [
          { label: "support_case_id", key: "support_case_id" }, { label: "case_type", key: "case_type" }, { label: "category", key: "category" }, { label: "channel", key: "channel" },
          { label: "license_plate", csv: (c) => (db.vehicle(c.vehicle_id) || {}).license_plate }, { label: "vin", key: "vin" }, { label: "organization", csv: (c) => (db.org(c.organization_id) || {}).short_name },
          { label: "reporter_name", key: "reporter_name" }, { label: "reporter_phone", key: "reporter_phone" }, { label: "status", key: "status" },
          { label: "created_at", key: "created_at" }, { label: "response_due_at", key: "sla_due_at" }, { label: "first_responded_at", key: "acknowledged_at" }, { label: "is_sla_breached", csv: (c) => isBreached(c) },
        ],
      });
    });
    everySecond(() => tickCountdowns(root));
  }

  G3.page({
    id: "support",
    path: "/support",
    title: ["Support", "Hỗ trợ khách hàng"],
    roles: ["CUSTOMER_CARE", "FLEET_MANAGER", "OPERATIONS"],
    features: ["SUP-01", "SUP-02", "SUP-03"],
    nav: { group: "support", order: 1, icon: "lifebuoy", badge: navBadge },
    render: supportPage,
    mount: mountSupport,
  });

  /* =====================================================================
     Screen 2: one support case (SUP-01, SUP-02, SUP-05, MON-02, MON-10)
     ===================================================================== */
  function stepper(c) {
    if (c.status === "CANCELLED") return ui.callout("warn", `<b>${esc(t("Cancelled", "Đã hủy"))}</b> ${fmt.dateTime(c.closed_at)} — ${esc(c.status_reason || "")}`, "x");
    const steps = ["OPEN", "ACKNOWLEDGED", "RESOLVED", "CLOSED"];
    const at = steps.indexOf(c.status);
    const when = [c.created_at, c.acknowledged_at, c.resolved_at, c.closed_at];
    return `<div class="sup-steps">${steps.map((s, i) => `<div class="sup-step ${i < at || c.status === "CLOSED" ? "done" : i === at ? "now" : ""}"><i></i>${esc(label(s))}<div class="small muted">${when[i] && i <= at ? fmt.time(when[i]) : "&nbsp;"}</div></div>`).join("")}</div>`;
  }

  function timelineHtml(c, internal) {
    const items = [{ at: c.created_at, title: t("Case created", "Tạo yêu cầu"), body: `${esc(label(c.channel))} · ${esc(c.reporter_name)}${c.created_by_user_id && c.channel !== "IN_APP" ? ` · ${esc(t("logged by", "ghi nhận bởi"))} ${ui.userName(c.created_by_user_id)}` : ""}` }];
    if (c.acknowledged_at) items.push({ at: c.acknowledged_at, title: c.case_type === "SOS" ? t("Driver called back · acknowledged", "Đã gọi lại tài xế · tiếp nhận") : t("Acknowledged", "Đã tiếp nhận"), body: c.assignee_user_id ? ui.userName(c.assignee_user_id) : "" });
    if (c.resolved_at) items.push({ at: c.resolved_at, title: t("Resolved", "Đã xử lý"), body: esc(tx(c.resolution)) });
    if (c.closed_at && c.status === "CLOSED") items.push({ at: c.closed_at, title: t("Closed", "Đã đóng"), body: "" });
    if (c.closed_at && c.status === "CANCELLED") items.push({ at: c.closed_at, title: t("Cancelled", "Đã hủy"), body: esc(c.status_reason || "") });
    const partnerName = (id) => esc((db.org(id) || {}).short_name || id);
    (c.notes || []).forEach((n) => {
      if (n.internal && !internal) return;
      const by = n.user_id ? ui.userName(n.user_id) : `<span class="muted">${esc(t("System", "Hệ thống"))}</span>`;
      const map = {
        NOTE: [n.internal ? `${t("Internal note", "Ghi chú nội bộ")} ${ui.tag(t("staff only", "chỉ nhân viên"))}` : t("Note", "Ghi chú"), esc(tx(n.text))],
        ASSIGN: [t("Assigned", "Phân công"), `${esc(t("to", "cho"))} ${ui.userName(n.assignee)}`],
        DISPATCH: [t(`Sent to ${(db.org(n.partner) || {}).short_name}`, `Đã gửi cho ${(db.org(n.partner) || {}).short_name}`), n.capability ? esc(t("Required: ", "Yêu cầu: ") + capLabel(n.capability)) : ""],
        ACCEPTED: [t("Partner accepted", "Đối tác đã nhận"), `${partnerName(n.partner)} · ETA ${fmt.time(n.eta_at)}`],
        TIMEOUT: [t("No answer within 15 min", "Không xác nhận trong 15 phút"), partnerName(n.partner)],
        ARRIVED: [t("Partner on site", "Đối tác đã đến nơi"), partnerName(n.partner)],
        DISPATCH_CANCELLED: [t("Dispatch withdrawn", "Rút yêu cầu điều phối"), partnerName(n.partner)],
        REOPENED: [t("Reopened", "Mở lại"), esc(tx(n.text))],
        DATA_RELEASED: [t("Driving data released", "Đã cung cấp dữ liệu hành trình"), `${esc(label(n.basis))} · ${esc(n.document)} · ${esc(n.recipient || "")}`],
        OWNER_APPROVED: [t("Owner's written approval received", "Đã nhận văn bản đồng ý của chủ xe"), `${esc(n.signatory)} — ${esc(n.scope || "")}`],
        RATED: [t("Partner rated", "Đánh giá đối tác"), `${partnerName(n.partner)} ${stars(n.stars)}`],
      };
      const [title, body] = map[n.kind] || [n.kind, ""];
      items.push({ at: n.at, title, body, by });
    });
    items.sort((a, b) => (a.at < b.at ? -1 : 1));
    return `<ul class="timeline">${items.map((it) => `<li><div class="row between"><b>${it.title.includes("<") ? it.title : esc(it.title)}</b><span class="when">${fmt.dateTime(it.at)}</span></div>${it.body ? `<div class="small">${it.body}</div>` : ""}${it.by ? `<div class="when">${it.by}</div>` : ""}</li>`).join("")}</ul>`;
  }

  function vehicleContextCard(c) {
    const v = db.vehicle(c.vehicle_id);
    const visible = v && scope.vehicleIds().has(v.vehicle_id);
    const pos = casePosition(c);
    if (!v) return ui.card({ title: t("Vehicle context", "Thông tin xe"), body: ui.empty(t("This case is not about one truck", "Yêu cầu không gắn với xe cụ thể")) });
    const model = db.model(v.vehicle_model_id);
    const drv = c.driver_id ? db.driver(c.driver_id) : v.driver_id ? db.driver(v.driver_id) : null;
    const live = visible && v.live ? v.live : null;
    // The mock route is generated along the truck's road; shift it so it ends where the truck is now.
    const rawRoute = live ? db.routeHistory(v.vehicle_id, 2) : [];
    const last = rawRoute[rawRoute.length - 1];
    const route = last ? rawRoute.map((r) => ({ ...r, lat: r.lat + live.lat - last.lat, lon: r.lon + live.lon - last.lon })) : null;
    let overlay = pos ? incidentMarker(pos, `${c.support_case_id} · ${t("reported here", "vị trí báo")} ${fmt.time(c.created_at)}`) : "";
    if (c.dispatch && scope.isInternal()) { const p = profile(c.dispatch.partner_organization_id); if (p) overlay += partnerMarker(p, { selected: true }); }
    const map = pos ? withOverlay(ui.map({ vehicles: live ? [v] : [], route, selectedVehicleId: v.vehicle_id, height: 300, legend: false, id: "case-map" }), overlay) : ui.empty(t("No position was sent with this case", "Yêu cầu không kèm vị trí"));
    const vehicleLink = G3.canSee("vehicle-detail") ? `<a href="#/vehicles/${v.vehicle_id}"><b>${esc(v.license_plate)}</b></a>` : `<b>${esc(v.license_plate)}</b>`;
    return ui.card({
      title: t("Vehicle context", "Thông tin xe"), sub: t("attached automatically · MON-02", "gắn tự động · MON-02"),
      actions: G3.canSee("vehicle-detail") ? ui.btn(t("Replay route", "Xem lại lộ trình"), { href: `#/vehicles/${v.vehicle_id}?tab=route`, icon: "route", sm: true, kind: "ghost" }) : "",
      body: `<div class="grid cols-2" style="gap:18px">
        <div>${ui.kv([
          [t("Truck", "Xe"), `${vehicleLink} <span class="muted small">${esc(model ? model.name : "")}</span>`],
          ["VIN", `<span class="mono">${esc(v.vin)}</span>`],
          [t("Owner", "Chủ xe"), ui.orgName(v.organization_id)],
          [t("Driver", "Tài xế"), drv ? `${esc(drv.full_name)} <span class="muted small">${esc(drv.phone_number)}</span>` : ui.driverName(null)],
          [t("Fault code", "Mã lỗi"), c.error_code ? `<b class="mono">${esc(c.error_code)}</b><div class="small muted">${esc(errorText(c.error_code))}</div>` : `<span class="muted">${esc(t("none reported", "không có"))}</span>`],
        ])}</div>
        <div>${ui.kv([
          [t("Reported at", "Vị trí khi báo"), pos ? `${esc(placeText(pos))}<div class="small muted mono">${fmt.num(pos.lat, 5)}, ${fmt.num(pos.lon, 5)}</div>` : null],
          [t("Battery now", "Pin hiện tại"), live ? `<span data-live-soc="${v.vehicle_id}">${ui.soc(live.soc_percent)}</span>` : null],
          [t("Live status", "Trạng thái"), live ? `<span data-live-state="${v.vehicle_id}">${ui.vehicleState(v)}</span> <span class="small muted" data-live-speed="${v.vehicle_id}">${live.is_online && live.state === "DRIVING" ? Math.round(live.speed_kmh) + " km/h" : ""}</span>` : `<span class="muted small">${esc(visible ? t("no live data", "chưa có dữ liệu") : t("not in your scope", "ngoài phạm vi xem"))}</span>`],
          [t("Last data", "Dữ liệu cuối"), live ? `<span data-live-ago="${v.vehicle_id}">${fmt.ago(live.received_at)}</span>` : null],
        ])}</div>
      </div>
      <div class="mt">${map}${route ? `<div class="small muted" style="margin-top:6px">${esc(t("Blue line: the last 2 h of route (MON-10). Red pin: where the case was reported.", "Đường xanh: lộ trình 2 giờ gần nhất (MON-10). Ghim đỏ: vị trí khi báo."))}</div>` : ""}</div>`,
    });
  }

  function reporterCard(c) {
    const phone = String(c.reporter_phone || "").replace(/\D/g, "");
    const name = c.reporter_name && c.reporter_name !== "—" ? c.reporter_name : t("Name not given", "Chưa rõ tên");
    return ui.card({
      title: t("Reporter", "Người báo"),
      body: `<div class="row" style="gap:12px;flex-wrap:nowrap"><span class="avatar" style="width:40px;height:40px">${esc(ui.initials(name))}</span><div class="grow" style="min-width:0"><b>${esc(name)}</b><div class="small muted">${esc(c.driver_id ? t("Driver checked in to the truck", "Tài xế đang nhận xe") : t("Caller", "Người gọi"))} · ${esc(c.reporter_phone || "—")}</div></div></div>
        <div class="row mt">${phone ? ui.btn(t("Call", "Gọi"), { href: `tel:${phone}`, icon: "phone", kind: "primary", sm: true }) : ""}${phone ? ui.btn("Zalo", { href: `https://zalo.me/${phone}`, icon: "send", sm: true, attrs: 'target="_blank" rel="noopener"' }) : ""}</div>`,
    });
  }

  function photosCard(c) {
    const fromApp = c.channel === "IN_APP";
    const shots = fromApp ? [[t("Dashboard warning", "Cảnh báo táp-lô"), 0], [t("Truck, front", "Đầu xe"), 2], [t("Close-up", "Cận cảnh"), 3]] : [];
    return ui.card({
      title: t("Photos", "Hình ảnh"), sub: fromApp ? `${shots.length} ${t("from the driver app", "từ ứng dụng tài xế")}` : "",
      actions: ui.btn(t("Add", "Thêm"), { icon: "upload", sm: true, kind: "ghost", action: "photo" }),
      body: shots.length ? `<div class="sup-photos">${shots.map(([name, k]) => `<div class="sup-photo">${ui.icon("camera", 22)}<div>${esc(name)}<br>${fmt.time(ms(c.created_at) + k * MIN)}</div></div>`).join("")}</div>` : `<div class="small muted">${esc(t("No photos: the case came by hotline or Zalo. Ask the driver to add them in the app, or attach the ones received on Zalo.", "Chưa có ảnh: yêu cầu đến qua tổng đài hoặc Zalo. Đề nghị tài xế gửi qua ứng dụng, hoặc đính kèm ảnh nhận qua Zalo."))}</div>`,
    });
  }

  function dataRequestCard(c, internal) {
    const approval = c.owner_approval;
    const releases = (c.notes || []).filter((n) => n.kind === "DATA_RELEASED");
    const isOwner = !internal && scope.org() && scope.org().organization_id === c.organization_id && scope.has("ORG_ADMIN", "FLEET_MANAGER");
    return ui.card({
      title: t("Data request", "Yêu cầu dữ liệu"), sub: "SUP-01 · ACC-18",
      body: `${ui.callout("warn", esc(t("A truck owner's driving data is released only with the owner's written approval or an authority's order. Every release is written to the access audit log.", "Dữ liệu hành trình của chủ xe chỉ được cung cấp khi có văn bản đồng ý của chủ xe hoặc yêu cầu của cơ quan có thẩm quyền. Mọi lần cung cấp đều được ghi vào nhật ký truy cập.")), "shield")}
        <div class="mt">${ui.kv([
          [t("Requested by", "Bên yêu cầu"), esc(c.reporter_name)],
          [t("Owner's approval", "Đồng ý của chủ xe"), approval ? `${ui.badge("ACCEPTED", "ok", t("On file", "Đã có"))} <span class="small muted">${esc(approval.signatory)} · ${fmt.date(approval.at)}</span>` : `<span class="muted">${esc(t("not received", "chưa có"))}</span>`],
          [t("Releases", "Số lần cung cấp"), releases.length ? releases.map((r) => `${fmt.dateTime(r.at)} · ${esc(label(r.basis))}`).join("<br>") : `<span class="muted">${esc(t("none", "chưa có"))}</span>`],
        ])}</div>
        <div class="row mt">${internal && !isTerminal(c) ? ui.btn(t("Release driving data", "Cung cấp dữ liệu hành trình"), { icon: "download", action: "release", sm: true }) : ""}${isOwner && !approval ? ui.btn(t("Give written approval", "Gửi văn bản đồng ý"), { icon: "edit", action: "approve", kind: "primary", sm: true }) : ""}</div>`,
    });
  }

  function partnerLine(r, c, cap) {
    const best = r.i === 0 && r.capable;
    return `<div class="sup-partner ${best ? "best" : ""}"><div class="row between" style="flex-wrap:nowrap;align-items:flex-start"><div style="min-width:0">
        <b>${esc(r.org.short_name)}</b> ${badge(r.p.partner_type, r.p.partner_type === "RESCUE" ? "danger" : "accent")} ${best ? ui.tag(t("Suggested", "Đề xuất")) : ""}
        <div class="small muted">${r.distance === null ? esc(t("distance unknown", "chưa rõ khoảng cách")) : `${fmt.num(r.distance, 1)} km · ${esc(t("ETA", "dự kiến"))} ~${r.etaMin} ${esc(t("min", "phút"))}`} · ${esc(t("SLA", "SLA"))} ${r.p.sla_minutes} ${esc(t("min", "phút"))} · ${stars(r.p.rating)} ${fmt.num(r.p.rating, 1)}</div>
        <div class="small" style="margin-top:3px">${r.capable ? `<span style="color:var(--ok)">✓ ${esc(cap ? capLabel(cap) : t("any capability", "mọi năng lực"))}</span>` : `<span style="color:var(--danger)">✗ ${esc(t("lacks", "thiếu"))} ${esc(capLabel(cap))}</span>`} · ${r.open ? `<span style="color:var(--ok)">${esc(t("open now", "đang mở cửa"))}</span>` : `<span style="color:var(--warn)">${esc(t("closed now", "đang đóng cửa"))} (${esc(r.p.opening_hours)})</span>`}${r.inArea ? "" : ` · <span style="color:var(--warn)">${esc(t("outside service area", "ngoài vùng phục vụ"))}</span>`}</div>
      </div>${ui.btn(t("Dispatch", "Điều phối"), { action: "dispatch", sm: true, kind: best ? "primary" : "", attrs: `data-partner="${r.org.organization_id}"` })}</div></div>`;
  }

  function dispatchCard(c, query) {
    const d = c.dispatch;
    const cap = query.cap !== undefined ? query.cap : (d && d.capability) || CAP_FOR_CATEGORY[c.category] || "";
    const canDispatch = isActive(c);
    let body = "";
    if (d && d.status === "PENDING") {
      const org = db.org(d.partner_organization_id);
      body += `<div class="sup-partner"><div class="row between"><b>${esc(org.short_name)}</b>${ui.badge("PENDING", "warn", t("Waiting for acceptance", "Chờ xác nhận"))}</div>
        <div class="small muted" style="margin:4px 0 8px">${esc(t("Sent", "Đã gửi"))} ${fmt.time(d.sent_at)} · ${esc(t("position, VIN, fault code, photos", "vị trí, VIN, mã lỗi, ảnh"))}${d.capability ? ` · ${esc(capLabel(d.capability))}` : ""}</div>
        <div class="row between small"><span>${esc(t("Acceptance SLA 15 min", "SLA xác nhận 15 phút"))}</span>${countdownEl(ms(d.accept_due_at), "accept")}</div>
        <div class="meter" style="height:8px;margin-top:6px" data-window-from="${ms(d.sent_at)}" data-window-to="${ms(d.accept_due_at)}"><span style="width:100%"></span></div>
        <div class="row mt">${ui.btn(t("Call partner", "Gọi đối tác"), { href: `tel:${String(profile(org.organization_id).hotline).replace(/\D/g, "")}`, icon: "phone", sm: true })}${ui.btn(t("Withdraw", "Rút lại"), { action: "withdraw", sm: true, kind: "ghost" })}${ui.btn(t("Simulate timeout", "Mô phỏng quá hạn"), { action: "simulate-timeout", sm: true, kind: "ghost", attrs: `title="${esc(t("Demo: skip to the end of the 15 minutes", "Demo: bỏ qua 15 phút chờ"))}"` })}</div>
        <div class="small muted" style="margin-top:6px">${esc(t("Prototype: the partner answers by itself after a few seconds.", "Bản mẫu: đối tác tự xác nhận sau vài giây."))}</div></div>`;
    } else if (d && ["ACCEPTED", "ARRIVED", "COMPLETED"].includes(d.status)) {
      const org = db.org(d.partner_organization_id);
      const p = profile(d.partner_organization_id);
      body += `<div class="sup-partner best"><div class="row between"><b>${esc(org.short_name)}</b>${d.status === "ACCEPTED" ? ui.badge("ACCEPTED") : d.status === "ARRIVED" ? badge("ARRIVED", "info") : ui.badge("COMPLETED")}</div>
        ${ui.kv([
          [t("Accepted", "Xác nhận"), `${fmt.time(d.accepted_at)} <span class="small muted">(${esc(t("after", "sau"))} ${fmt.duration((ms(d.accepted_at) - ms(d.sent_at)) / MIN)})</span>`],
          [t("ETA", "Dự kiến đến"), d.status === "ACCEPTED" ? `${fmt.time(d.eta_at)} · ${countdownEl(ms(d.eta_at), "eta")}` : d.arrived_at ? `${esc(t("on site at", "đến lúc"))} ${fmt.time(d.arrived_at)}` : fmt.time(d.eta_at)],
          [t("Technician", "Kỹ thuật viên"), d.technician ? `${esc(d.technician.name)} <a class="small" href="tel:${esc(d.technician.phone)}">${esc(d.technician.phone)}</a>` : null],
          [t("Partner hotline", "Tổng đài đối tác"), p ? `<a href="tel:${esc(p.hotline.replace(/\D/g, ""))}">${esc(p.hotline)}</a>` : null],
        ])}
        ${d.status === "ACCEPTED" && canDispatch ? `<div class="row mt">${ui.btn(t("Mark on site", "Xác nhận đã đến"), { action: "arrived", sm: true, kind: "primary" })}${ui.btn(t("Withdraw", "Rút lại"), { action: "withdraw", sm: true, kind: "ghost" })}</div>` : ""}</div>`;
    }
    const needsPartner = canDispatch && (!d || ["TIMEOUT", "REJECTED", "CANCELLED"].includes(d.status));
    if (needsPartner) {
      const tried = d ? d.attempts.map((a) => a.partner_organization_id) : [];
      const ranked = rankPartners(c, cap).filter((r) => !tried.includes(r.org.organization_id) || d.status === "CANCELLED").map((r, i) => ({ ...r, i }));
      body += `${d && d.status === "TIMEOUT" ? ui.callout("warn", esc(t("The last partner did not accept within 15 minutes. Pick the next one.", "Đối tác trước không xác nhận trong 15 phút. Hãy chọn đối tác tiếp theo."))) + '<div style="height:10px"></div>' : ""}
        <div class="row between" style="margin-bottom:10px"><label class="small strong">${esc(t("Required capability", "Năng lực cần"))}</label>${ui.select("cap", [["", t("Any", "Bất kỳ")], ...CAPS.map(([vi, en]) => [vi, t(en, vi)])], cap, 'style="max-width:200px"')}</div>
        ${ranked.length ? ranked.slice(0, 4).map((r) => partnerLine(r, c, cap)).join("") : ui.empty(t("No other active partner", "Không còn đối tác hoạt động"))}
        <label class="check small mt"><input type="checkbox" data-autoroute ${c.dispatch_auto_route !== false ? "checked" : ""}> ${esc(t("Re-route automatically to the next partner on timeout", "Tự chuyển đối tác tiếp theo khi quá hạn"))}</label>`;
    } else if (!d) body += `<div class="small muted">${esc(t("No partner was dispatched for this case.", "Yêu cầu này không điều phối đối tác."))}</div>`;
    if (d && d.attempts.length > 1) body += `<div class="divider"></div><div class="small strong">${esc(t("Dispatch attempts", "Các lần điều phối"))}</div><ul class="small" style="margin:6px 0 0;padding-left:18px">${d.attempts.map((a) => `<li>${fmt.time(a.sent_at)} · ${esc((db.org(a.partner_organization_id) || {}).short_name)} · ${a.outcome === "PENDING" ? esc(t("waiting", "đang chờ")) : esc(label(a.outcome))}</li>`).join("")}</ul>`;
    return ui.card({ title: t("Partner dispatch", "Điều phối đối tác"), sub: "SUP-05", actions: G3.canSee("partners") ? ui.btn(t("Directory", "Danh bạ"), { href: "#/partners", sm: true, kind: "ghost" }) : "", body });
  }

  function statusActions(c, internal) {
    const id = `data-id="${esc(c.support_case_id)}"`;
    if (internal) {
      if (c.status === "OPEN") return ui.btn(c.case_type === "SOS" ? t("Called back · acknowledge", "Đã gọi lại · tiếp nhận") : t("Acknowledge", "Tiếp nhận"), { action: "ack", kind: "primary", icon: "check", attrs: id }) + ui.btn(t("Cancel", "Hủy"), { action: "cancel", kind: "danger", attrs: id });
      if (c.status === "ACKNOWLEDGED") return ui.btn(t("Resolve", "Xử lý xong"), { action: "resolve", kind: "primary", icon: "check", attrs: id }) + ui.btn(t("Cancel", "Hủy"), { action: "cancel", kind: "danger", attrs: id });
      if (c.status === "RESOLVED") return ui.btn(t("Close", "Đóng"), { action: "close", kind: "primary", attrs: id }) + ui.btn(t("Reopen", "Mở lại"), { action: "reopen", attrs: id });
      return "";
    }
    if (isActive(c)) return ui.btn(t("Cancel request", "Hủy yêu cầu"), { action: "cancel", kind: "danger", attrs: id });
    if (c.status === "RESOLVED") return ui.btn(t("Confirm and close", "Xác nhận và đóng"), { action: "close", kind: "primary", attrs: id }) + ui.btn(t("Not solved, reopen", "Chưa xong, mở lại"), { action: "reopen", attrs: id });
    return "";
  }

  function supportDetailPage({ params, query, entering }) {
    seedAll();
    const c = findCase(params.id);
    const crumbs = [{ label: t("Support", "Hỗ trợ"), href: "#/support" }, { label: params.id }];
    if (!c) return ui.pageHead({ title: params.id, crumbs }) + ui.callout("warn", esc(t("This case does not exist or belongs to another organization.", "Yêu cầu không tồn tại hoặc thuộc tổ chức khác.")));
    if (entering) G3.audit("VIEW", "SUPPORT_CASE", c.support_case_id, null);
    const internal = scope.isInternal();
    const team = db.memberships.filter((m) => m.organization_id === "org-g3" && m.status === "ACTIVE" && m.roles.some((x) => ["CUSTOMER_CARE", "OPERATIONS", "HEAD_ADMIN"].includes(x)));
    const slaBlock = isAwaiting(c)
      ? `<div class="small muted">${esc(c.case_type === "SOS" ? t("Call-back deadline", "Hạn gọi lại") : t("Response deadline", "Hạn phản hồi"))} ${fmt.time(c.sla_due_at)}</div><div class="sup-big">${countdownEl(ms(c.sla_due_at))}</div>`
      : `<div class="small muted">${esc(t("Response SLA", "SLA phản hồi"))} (${SLA_MINUTES(c.case_type, c.category) >= 60 ? SLA_MINUTES(c.case_type, c.category) / 60 + " " + t("h", "giờ") : SLA_MINUTES(c.case_type, c.category) + " " + t("min", "phút")})</div><div class="sup-big">${slaCell(c).replace('<span class="sub">', '<span class="small muted" style="display:block;font-size:12.5px">')}</div>`;

    const summary = ui.card({
      title: t("Status", "Trạng thái"),
      actions: badge(c.status),
      body: `${stepper(c)}<div class="divider"></div><div class="grid cols-2" style="gap:16px;align-items:start"><div>${slaBlock}</div><div>${ui.kv([
        [t("Category", "Phân loại"), esc(catLabel(c.category))],
        [t("Channel", "Kênh"), esc(label(c.channel))],
        [t("Organization", "Tổ chức"), ui.orgName(c.organization_id)],
        [t("Assignee", "Người xử lý"), internal && !isTerminal(c) ? `<select class="input" data-assign style="max-width:190px"><option value="">${esc(t("Unassigned", "Chưa phân công"))}</option>${team.map((m) => `<option value="${m.user_id}" ${m.user_id === c.assignee_user_id ? "selected" : ""}>${esc(db.user(m.user_id).full_name)}</option>`).join("")}</select>` : c.assignee_user_id ? ui.userName(c.assignee_user_id) : `<span class="muted">${esc(t("Unassigned", "Chưa phân công"))}</span>`],
      ])}</div></div>`,
    });

    const notesCard = ui.card({
      title: t("Timeline", "Diễn biến"), sub: t("notes and status changes", "ghi chú và thay đổi trạng thái"),
      body: `${timelineHtml(c, internal)}${!isTerminal(c) ? `<div class="divider"></div><textarea class="input" data-note rows="2" style="width:100%" placeholder="${esc(t("Add a note…", "Thêm ghi chú…"))}"></textarea><div class="row between" style="margin-top:8px">${internal ? `<label class="check small"><input type="checkbox" data-note-internal> ${esc(t("Internal only (hidden from the customer)", "Chỉ nội bộ (khách hàng không thấy)"))}</label>` : "<span></span>"}${ui.btn(t("Add note", "Thêm ghi chú"), { action: "note", kind: "primary", sm: true })}</div>` : ""}`,
    });

    const right = [
      reporterCard(c),
      internal && c.vehicle_id ? dispatchCard(c, query) : c.dispatch && ["ACCEPTED", "ARRIVED"].includes(c.dispatch.status) ? ui.card({ title: t("Help on the way", "Cứu hộ đang đến"), body: ui.kv([[t("Partner", "Đối tác"), esc(db.org(c.dispatch.partner_organization_id).short_name)], [t("ETA", "Dự kiến đến"), c.dispatch.status === "ARRIVED" ? esc(t("on site", "đã đến nơi")) : `${fmt.time(c.dispatch.eta_at)} · ${countdownEl(ms(c.dispatch.eta_at), "eta")}`]]) }) : "",
      c.category === "DATA_REQUEST" ? dataRequestCard(c, internal) : "",
      photosCard(c),
    ].filter(Boolean).join("");

    return STYLE + ui.pageHead({
      crumbs,
      title: `${c.support_case_id} · ${tx(c.subject)}`,
      sub: `${ui.badge(c.case_type, c.case_type === "SOS" ? "danger" : "muted")} ${ui.tag(catLabel(c.category))} ${ui.tag(label(c.channel))} <span class="muted small">${esc(t("created", "tạo lúc"))} ${fmt.dateTime(c.created_at)} · ${fmt.ago(c.created_at)}</span>`,
      features: ["SUP-01", "SUP-02", "SUP-05", "MON-02", "MON-10"],
      actions: statusActions(c, internal),
    }) + (c.case_type === "SOS" && c.status === "OPEN" ? `<div class="mb">${ui.callout("danger", `<b>${esc(t("SOS: call the driver back now.", "SOS: gọi lại tài xế ngay."))}</b> ${esc(t("Then acknowledge the case so the call-back time is recorded.", "Sau đó bấm tiếp nhận để ghi lại thời điểm gọi lại."))}`, "sos")}</div>` : "")
      + `<div class="grid side"><div class="stack">${summary}${vehicleContextCard(c)}${notesCard}</div><div class="stack">${right}</div></div>`;
  }

  function mountSupportDetail(root, { params }) {
    const c = findCase(params.id);
    if (!c) return;
    const bind = (sel, fn) => root.querySelectorAll(sel).forEach((el) => el.addEventListener("click", () => fn(el)));
    bind("[data-action=ack]", () => acknowledge(c));
    bind("[data-action=resolve]", () => resolveCase(c));
    bind("[data-action=close]", () => closeCase(c));
    bind("[data-action=reopen]", () => reopenCase(c));
    bind("[data-action=cancel]", () => cancelCase(c));
    bind("[data-action=photo]", () => ui.toast(t("File upload is not part of the prototype.", "Bản mẫu chưa hỗ trợ tải tệp."), "", "info"));
    bind("[data-action=note]", () => {
      const text = root.querySelector("[data-note]").value.trim();
      if (!text) return ui.toast(t("Write the note first.", "Hãy nhập nội dung ghi chú."), "alert");
      const internalBox = root.querySelector("[data-note-internal]");
      c.notes.push({ kind: "NOTE", at: new Date().toISOString(), user_id: me(), text: text.slice(0, 1000), internal: !!(internalBox && internalBox.checked) });
      ui.toast(t("Note added", "Đã thêm ghi chú"));
      G3.render();
    });
    const assign = root.querySelector("[data-assign]");
    if (assign) assign.addEventListener("change", () => {
      c.assignee_user_id = assign.value || null;
      if (assign.value) c.notes.push({ kind: "ASSIGN", at: new Date().toISOString(), user_id: me(), assignee: assign.value });
      ui.toast(assign.value ? t(`Assigned to ${db.user(assign.value).full_name}`, `Đã phân công cho ${db.user(assign.value).full_name}`) : t("Unassigned", "Đã bỏ phân công"));
      G3.render();
    });
    const capSelect = root.querySelector("[data-filter=cap]");
    if (capSelect) capSelect.addEventListener("change", () => G3.setQuery({ cap: capSelect.value }));
    const auto = root.querySelector("[data-autoroute]");
    if (auto) auto.addEventListener("change", () => { c.dispatch_auto_route = auto.checked; });
    bind("[data-action=dispatch]", (el) => { dispatchTo(c, el.dataset.partner, capSelect ? capSelect.value : ""); G3.render(); });
    bind("[data-action=simulate-timeout]", () => acceptanceTimedOut(c));
    bind("[data-action=withdraw]", () => {
      clearAcceptTimer(c);
      c.notes.push({ kind: "DISPATCH_CANCELLED", at: new Date().toISOString(), user_id: me(), partner: c.dispatch.partner_organization_id });
      c.dispatch.status = "CANCELLED";
      c.dispatch.attempts[c.dispatch.attempts.length - 1].outcome = c.dispatch.attempts[c.dispatch.attempts.length - 1].outcome === "PENDING" ? "CANCELLED" : c.dispatch.attempts[c.dispatch.attempts.length - 1].outcome;
      ui.toast(t("Dispatch withdrawn", "Đã rút yêu cầu điều phối"));
      G3.render();
    });
    bind("[data-action=arrived]", () => {
      c.dispatch.status = "ARRIVED";
      c.dispatch.arrived_at = new Date().toISOString();
      c.notes.push({ kind: "ARRIVED", at: c.dispatch.arrived_at, user_id: me(), partner: c.dispatch.partner_organization_id });
      ui.toast(t("Partner on site", "Đối tác đã đến nơi"));
      G3.render();
    });
    bind("[data-action=release]", () => {
      const v = db.vehicle(c.vehicle_id);
      ui.formModal({
        title: t("Release driving data", "Cung cấp dữ liệu hành trình"),
        intro: ui.callout("info", esc(t("Choose the legal basis. The release and its document are written to the access audit log (ACC-18).", "Chọn căn cứ pháp lý. Lần cung cấp và văn bản kèm theo được ghi vào nhật ký truy cập (ACC-18)."))),
        submitLabel: t("Release and download", "Cung cấp và tải về"),
        fields: [
          { name: "basis", label: t("Legal basis", "Căn cứ"), type: "select", value: c.owner_approval ? "OWNER_APPROVAL" : "AUTHORITY_ORDER", options: [["OWNER_APPROVAL", label("OWNER_APPROVAL")], ["AUTHORITY_ORDER", label("AUTHORITY_ORDER")]] },
          { name: "document", label: t("Document number", "Số văn bản"), required: true, placeholder: "123/CV-CSGT" },
          { name: "recipient", label: t("Recipient", "Bên nhận"), value: c.reporter_name, required: true },
          { name: "period", label: t("Period covered", "Khoảng thời gian"), value: t("last 8 hours", "8 giờ gần nhất"), hint: t("Only the period in the document is released.", "Chỉ cung cấp đúng khoảng thời gian nêu trong văn bản.") },
        ],
        onSubmit(values) {
          if (values.basis === "OWNER_APPROVAL" && !c.owner_approval) return t("No written approval from the owner is on file. Ask the owner, or use an authority's order.", "Chưa có văn bản đồng ý của chủ xe. Hãy đề nghị chủ xe, hoặc dùng yêu cầu của cơ quan có thẩm quyền.");
          G3.audit("EXPORT", "DRIVING_DATA", v ? v.vin : c.support_case_id, `${c.support_case_id} · ${label(values.basis)} ${values.document}`);
          c.notes.push({ kind: "DATA_RELEASED", at: new Date().toISOString(), user_id: me(), basis: values.basis, document: values.document, recipient: values.recipient });
          if (v && v.live) ui.downloadCsv(`driving-data-${v.license_plate}.csv`, [{ label: "recorded_at", key: "at" }, { label: "lat", key: "lat" }, { label: "lon", key: "lon" }, { label: "speed_kmh", key: "speed_kmh" }, { label: "soc_percent", key: "soc_percent" }], db.routeHistory(v.vehicle_id, 8));
          G3.render();
        },
      });
    });
    bind("[data-action=approve]", () => {
      const user = scope.user();
      ui.formModal({
        title: t("Written approval of the owner", "Văn bản đồng ý của chủ xe"),
        intro: `<p class="small muted">${esc(t(`${scope.org().name} agrees that G3 Network releases the driving data described below to ${c.reporter_name}.`, `${scope.org().name} đồng ý để G3 Network cung cấp dữ liệu hành trình dưới đây cho ${c.reporter_name}.`))}</p>`,
        submitLabel: t("Sign and send", "Ký và gửi"),
        fields: [
          { name: "signatory", label: t("Signed by", "Người ký"), value: user.full_name, required: true },
          { name: "scope", label: t("Data and period covered", "Dữ liệu và thời gian"), type: "textarea", required: true, value: tx(c.subject) },
          { name: "agree", label: t("Confirmation", "Xác nhận"), type: "checkbox", checkLabel: t("I am authorised to approve for the organization.", "Tôi được ủy quyền chấp thuận thay mặt tổ chức.") },
        ],
        onSubmit(values) {
          if (!values.agree) return t("Tick the confirmation to sign.", "Hãy đánh dấu xác nhận để ký.");
          c.owner_approval = { signatory: values.signatory, scope: values.scope, at: new Date().toISOString(), user_id: me() };
          c.notes.push({ kind: "OWNER_APPROVED", at: c.owner_approval.at, user_id: me(), signatory: values.signatory, scope: values.scope });
          G3.audit("VIEW", "DATA_RELEASE_APPROVAL", c.support_case_id, values.scope.slice(0, 200));
          ui.toast(t("Approval sent to G3 customer care", "Đã gửi văn bản đồng ý cho CSKH G3"));
          G3.render();
        },
      });
    });
    const wrapZoom = () => {
      const wrap = root.querySelector("[data-map-wrap=case-map]");
      const pos = casePosition(c);
      if (wrap && wrap.zoomTo && pos) wrap.zoomTo(pos.lat, pos.lon, 360);
      keepMarkerSize(root, "case-map");
    };
    setTimeout(wrapZoom, 0);
    everySecond(() => {
      tickCountdowns(root);
      if (c.dispatch && c.dispatch.status === "PENDING" && Date.now() > ms(c.dispatch.accept_due_at)) acceptanceTimedOut(c);
    });
    G3.onTick(() => {
      ui.refreshMarkers(root);
      root.querySelectorAll("[data-live-soc]").forEach((el) => { const v = db.vehicle(el.dataset.liveSoc); if (v && v.live) el.innerHTML = ui.soc(v.live.soc_percent); });
      root.querySelectorAll("[data-live-state]").forEach((el) => { el.innerHTML = ui.vehicleState(db.vehicle(el.dataset.liveState)); });
      root.querySelectorAll("[data-live-speed]").forEach((el) => { const v = db.vehicle(el.dataset.liveSpeed); el.textContent = v.live.is_online && v.live.state === "DRIVING" ? Math.round(v.live.speed_kmh) + " km/h" : ""; });
      root.querySelectorAll("[data-live-ago]").forEach((el) => { el.textContent = fmt.ago(db.vehicle(el.dataset.liveAgo).live.received_at); });
    });
  }

  G3.page({
    id: "support-detail",
    path: "/support/:id",
    title: ["Support case", "Chi tiết yêu cầu"],
    roles: ["CUSTOMER_CARE", "FLEET_MANAGER", "OPERATIONS"],
    features: ["SUP-01", "SUP-02", "SUP-05", "MON-02", "MON-10"],
    navParent: "support",
    render: supportDetailPage,
    mount: mountSupportDetail,
  });

  /* =====================================================================
     Screen 3: partner directory (SUP-04, SUP-05)
     ===================================================================== */
  function partnerForm(org) {
    const p = org ? profile(org.organization_id) : null;
    const nearestKey = p ? Object.entries(db.PLACES).map(([k, pl]) => [k, kmBetween(pl, p)]).sort((a, b) => a[1] - b[1])[0][0] : "bienhoa";
    ui.formModal({
      title: org ? t(`Edit ${org.short_name}`, `Sửa ${org.short_name}`) : t("Add a partner", "Thêm đối tác"),
      intro: org ? "" : `<p class="small muted">${esc(t("Creates a partner organization with its profile. Its technicians are invited as members later (SUP-06).", "Tạo tổ chức đối tác kèm hồ sơ. Kỹ thuật viên được mời làm thành viên sau (SUP-06)."))}</p>`,
      wide: true,
      submitLabel: org ? t("Save", "Lưu") : t("Add partner", "Thêm đối tác"),
      fields: [
        { name: "name", label: t("Legal name", "Tên pháp lý"), value: org ? org.name : "", required: true },
        { name: "short_name", label: t("Short name", "Tên ngắn"), value: org ? org.short_name : "", required: true },
        org ? null : { name: "tax_code", label: t("Tax code", "Mã số thuế"), required: true, hint: t("10 or 13 digits", "10 hoặc 13 chữ số") },
        { name: "partner_type", label: t("Type", "Loại"), type: "select", value: p ? p.partner_type : "RESCUE", options: [["RESCUE", label("RESCUE")], ["WORKSHOP", label("WORKSHOP")]] },
        { name: "base", label: t("Base location", "Vị trí cơ sở"), type: "select", value: nearestKey, options: Object.entries(db.PLACES).map(([k, pl]) => [k, pl.name]) },
        { name: "radius_km", label: t("Service radius (km)", "Bán kính phục vụ (km)"), type: "number", value: p ? radiusKm(p) : 40 },
        { name: "service_area", label: t("Service area", "Khu vực phục vụ"), type: "checkboxes", full: true, value: p ? p.service_area : [], options: PROVINCES.map((x) => [x, x]), required: true },
        { name: "capabilities", label: t("Capabilities", "Năng lực"), type: "checkboxes", full: true, value: p ? p.capabilities : [], options: CAPS.map(([vi, en]) => [vi, t(en, vi)]), required: true },
        { name: "opening_hours", label: t("Opening hours", "Giờ làm việc"), value: p ? p.opening_hours : "24/7", placeholder: "24/7 / 07:00–19:00" },
        { name: "sla_minutes", label: t("Committed arrival SLA (min)", "SLA đến nơi cam kết (phút)"), type: "number", value: p ? p.sla_minutes : 45, required: true },
        { name: "revenue_share_percent", label: t("Revenue share to G3 (%)", "Tỷ lệ chia doanh thu cho G3 (%)"), type: "number", value: p ? p.revenue_share_percent : 12, required: true },
        { name: "hotline", label: t("24/7 hotline", "Đường dây nóng"), type: "tel", value: p ? p.hotline : "", required: true, hint: t("10–11 digits", "10–11 chữ số") },
      ].filter(Boolean),
      onSubmit(values) {
        const digits = values.hotline.replace(/\D/g, "");
        if (digits.length < 10 || digits.length > 11) return t("The hotline must have 10 or 11 digits.", "Đường dây nóng phải có 10 hoặc 11 chữ số.");
        if (!org && !/^\d{10}(\d{3})?$/.test(values.tax_code)) return t("The tax code must have 10 or 13 digits.", "Mã số thuế phải có 10 hoặc 13 chữ số.");
        if (!org && db.organizations.some((o) => o.tax_code === values.tax_code)) return t("An organization with this tax code already exists.", "Đã có tổ chức với mã số thuế này.");
        const sla = Number(values.sla_minutes), share = Number(values.revenue_share_percent), radius = Number(values.radius_km);
        if (!(sla >= 10 && sla <= 240)) return t("The SLA must be between 10 and 240 minutes.", "SLA phải từ 10 đến 240 phút.");
        if (!(share >= 0 && share <= 50)) return t("The revenue share must be between 0 and 50%.", "Tỷ lệ chia doanh thu phải từ 0 đến 50%.");
        if (!(radius >= 5 && radius <= 200)) return t("The radius must be between 5 and 200 km.", "Bán kính phải từ 5 đến 200 km.");
        const base = db.PLACES[values.base];
        const fields = { partner_type: values.partner_type, service_area: values.service_area, capabilities: values.capabilities, opening_hours: values.opening_hours || "24/7", sla_minutes: sla, revenue_share_percent: share, hotline: values.hotline, lat: base.lat, lon: base.lon, radius_km: radius };
        if (org) {
          Object.assign(org, { name: values.name, short_name: values.short_name });
          if (p) Object.assign(p, fields); else db.partner_profiles.push({ organization_id: org.organization_id, rating: 0, cases: 0, ...fields });
          ui.toast(t("Partner profile saved", "Đã lưu hồ sơ đối tác"));
        } else {
          const id = db.uid("org");
          db.organizations.push({ organization_id: id, name: values.name, short_name: values.short_name, legal_form: "COMPANY", tax_code: values.tax_code, kind: "PARTNER", is_internal: false, status: "ACTIVE", status_reason: null, address: base.name, created_at: new Date().toISOString() });
          db.partner_profiles.push({ organization_id: id, rating: 5, cases: 0, ...fields });
          G3.setQuery({ p: id });
          ui.toast(t(`${values.short_name} added to the network`, `Đã thêm ${values.short_name} vào mạng lưới`));
          return;
        }
        G3.render();
      },
    });
  }

  function partnersPage({ query }) {
    seedAll();
    const orgs = partnerOrgs();
    let rows = orgs.map((o) => ({ org: o, p: profile(o.organization_id) }));
    const q = (query.q || "").toLowerCase();
    if (query.type) rows = rows.filter((r) => r.p && r.p.partner_type === query.type);
    if (query.area) rows = rows.filter((r) => r.p && r.p.service_area.includes(query.area));
    if (query.capability) rows = rows.filter((r) => r.p && r.p.capabilities.includes(query.capability));
    if (q) rows = rows.filter((r) => (r.org.name + " " + r.org.short_name + " " + (r.p ? r.p.hotline : "")).toLowerCase().includes(q));
    const profiles = orgs.map((o) => profile(o.organization_id)).filter(Boolean);
    const active = orgs.filter((o) => o.status === "ACTIVE");
    const selected = db.org(query.p) && db.org(query.p).kind === "PARTNER" ? db.org(query.p) : orgs[0];
    const sp = selected ? profile(selected.organization_id) : null;
    const dispatches = db.support_cases.filter((c) => c.dispatch && ["PENDING", "ACCEPTED", "ARRIVED"].includes(c.dispatch.status));
    const openIncidents = db.support_cases.filter((c) => isActive(c) && casePosition(c) && ["BREAKDOWN", "ACCIDENT", "BATTERY"].includes(c.category));

    const kpis = `<div class="grid cols-5">
      ${ui.kpi({ label: t("Active partners", "Đối tác hoạt động"), icon: "handshake", value: `${active.length}<small>/ ${orgs.length}</small>`, foot: `${profiles.filter((p) => p.partner_type === "RESCUE").length} ${t("rescue", "cứu hộ")} · ${profiles.filter((p) => p.partner_type === "WORKSHOP").length} ${t("workshops", "xưởng")}` })}
      ${ui.kpi({ label: t("24/7 coverage", "Phục vụ 24/7"), icon: "clock", value: profiles.filter((p) => p.opening_hours === "24/7").length, foot: PROVINCES.filter((x) => profiles.some((p) => p.service_area.includes(x))).length + " / " + PROVINCES.length + " " + t("provinces covered", "tỉnh được phủ") })}
      ${ui.kpi({ label: t("Average rating", "Đánh giá trung bình"), icon: "check", value: fmt.num(profiles.reduce((a, p) => a + p.rating, 0) / (profiles.length || 1), 1), unit: "/ 5", foot: `${ratings.length} ${t("ratings", "lượt đánh giá")}` })}
      ${ui.kpi({ label: t("Committed SLA", "SLA cam kết"), icon: "gauge", value: fmt.num(profiles.reduce((a, p) => a + p.sla_minutes, 0) / (profiles.length || 1), 0), unit: t("min", "phút"), foot: t("acceptance ≤ 15 min: 93%", "xác nhận ≤ 15 phút: 93%") })}
      ${ui.kpi({ label: t("Dispatches in progress", "Đang điều phối"), icon: "send", value: dispatches.length, foot: `${openIncidents.length} ${t("open incidents on the road", "sự cố đang mở trên đường")}` })}
    </div>`;

    const columns = [
      { label: t("Partner", "Đối tác"), cls: "nowrap", render: (r) => `<b title="${esc(r.org.name)}">${esc(r.org.short_name)}</b> ${r.p ? badge(r.p.partner_type, r.p.partner_type === "RESCUE" ? "danger" : "accent") : ""}${r.org.status !== "ACTIVE" ? " " + ui.badge(r.org.status) : ""}<span class="sub">${r.p ? `<a href="tel:${esc(r.p.hotline.replace(/\D/g, ""))}">${esc(r.p.hotline)}</a> · ` : ""}${esc(t("Tax", "MST"))} ${esc(r.org.tax_code)}</span>${r.org.status_reason ? `<span class="sub" style="white-space:normal;max-width:240px">${esc(r.org.status_reason)}</span>` : ""}` },
      { label: t("Service area", "Khu vực"), render: (r) => (r.p ? `<span class="small" style="display:block;min-width:140px">${esc(r.p.service_area.join(", "))}</span><span class="sub">${esc(t("radius", "bán kính"))} ${radiusKm(r.p)} km</span>` : "—") },
      { label: t("Capabilities", "Năng lực"), render: (r) => (r.p ? `<div class="row" style="gap:4px;max-width:210px">${r.p.capabilities.map((x) => ui.tag(capLabel(x))).join("")}</div>` : "—") },
      { label: t("Hours · SLA", "Giờ · SLA"), cls: "nowrap", render: (r) => (r.p ? `${esc(r.p.opening_hours)} · <span style="color:var(--${isOpenNow(r.p.opening_hours) ? "ok" : "warn"})">${esc(isOpenNow(r.p.opening_hours) ? t("open", "đang mở") : t("closed", "đang đóng"))}</span><span class="sub">${esc(t("arrives within", "đến trong"))} ${r.p.sla_minutes} ${esc(t("min", "phút"))}</span>` : "—") },
      { label: t("Rating", "Đánh giá"), cls: "nowrap", render: (r) => (r.p ? `${stars(r.p.rating)} <b class="num">${fmt.num(r.p.rating, 1)}</b>` : "—") },
      { label: t("Cases", "Số ca"), align: "right", cls: "nowrap", render: (r) => (r.p ? `${r.p.cases}<span class="sub">${db.support_cases.filter((c) => c.partner_organization_id === r.org.organization_id && isActive(c)).length} ${esc(t("open", "đang mở"))}</span>` : "—") },
      { label: t("Revenue share", "Chia doanh thu"), align: "right", cls: "nowrap", render: (r) => (r.p ? `${r.p.revenue_share_percent}%<span class="sub" title="${esc(t("estimated share to G3, last 30 days", "ước tính phần của G3, 30 ngày qua"))}">≈ ${fmt.vndShort(Math.round(r.p.cases / 6) * 4500000 * r.p.revenue_share_percent / 100)}</span>` : "—") },
      { label: "", render: (r) => `<div style="display:flex;flex-direction:column;gap:2px">${ui.btn("", { icon: "edit", sm: true, kind: "ghost", action: "edit", attrs: `data-org="${r.org.organization_id}" title="${esc(t("Edit profile", "Sửa hồ sơ"))}"` })}${ui.btn("", { icon: r.org.status === "ACTIVE" ? "lock" : "unlock", sm: true, kind: "ghost", action: "toggle-status", attrs: `data-org="${r.org.organization_id}" title="${esc(r.org.status === "ACTIVE" ? t("Suspend", "Tạm ngưng") : t("Reactivate", "Kích hoạt lại"))}"` })}</div>` },
    ];
    const toolbar = `<div class="toolbar">${ui.search("q", query.q, t("Name or hotline", "Tên hoặc số điện thoại"))}
      ${ui.select("type", [["", t("All types", "Mọi loại")], ["RESCUE", label("RESCUE")], ["WORKSHOP", label("WORKSHOP")]], query.type || "")}
      ${ui.select("area", [["", t("All areas", "Mọi khu vực")], ...PROVINCES.map((x) => [x, x])], query.area || "")}
      ${ui.select("capability", [["", t("All capabilities", "Mọi năng lực")], ...CAPS.map(([vi, en]) => [vi, t(en, vi)])], query.capability || "")}
      <span class="grow"></span><span class="small muted">${esc(t("Click a row for its ratings", "Bấm vào dòng để xem đánh giá"))}</span></div>`;
    const directory = `<section class="card mt"><div class="card-head"><h3>${esc(t("Directory", "Danh bạ"))} <span class="sub">SUP-04</span></h3></div>${toolbar}<div class="card-body flush">${ui.table({ rows, columns, rowAttrs: (r) => `data-select="${r.org.organization_id}" style="cursor:pointer"`, selected: (r) => selected && r.org.organization_id === selected.organization_id, empty: t("No partner matches", "Không có đối tác phù hợp") })}</div></section>`;

    const overlay = profiles.map((p) => partnerMarker(p, { radius: true, selected: selected && p.organization_id === selected.organization_id })).join("") + openIncidents.map((c) => incidentMarker(casePosition(c), `${c.support_case_id} · ${catLabel(c.category)}`)).join("");
    const mapCard = ui.card({
      title: t("Partner network", "Mạng lưới đối tác"), sub: t("service radius and open road incidents", "bán kính phục vụ và sự cố đang mở"), flush: true,
      actions: ui.legend([[label("RESCUE"), "#dc2626"], [label("WORKSHOP"), "#2563eb"]]),
      body: withOverlay(ui.map({ height: 540, legend: false, id: "partners-map" }), overlay),
    });

    const own = sp ? ratings.filter((r) => r.partner === selected.organization_id).sort((a, b) => (a.at < b.at ? 1 : -1)) : [];
    const months = Array.from({ length: 6 }, (_, i) => { const d = new Date(db.NOW); d.setDate(1); d.setMonth(d.getMonth() - (5 - i)); return d; });
    const monthly = months.map((d) => { const xs = own.filter((r) => { const x = new Date(r.at); return x.getMonth() === d.getMonth() && x.getFullYear() === d.getFullYear(); }); return xs.length ? Math.round((xs.reduce((a, r) => a + r.stars, 0) / xs.length) * 10) / 10 : null; });
    const ratingCard = sp ? ui.card({
      title: t("Ratings", "Đánh giá"), sub: esc(selected.short_name),
      actions: ui.btn(t("Rate a case", "Đánh giá ca"), { icon: "plus", sm: true, action: "rate", attrs: `data-org="${selected.organization_id}"` }),
      body: `<div class="row between"><div><span class="sup-big">${fmt.num(sp.rating, 1)}</span> <span class="muted">/ 5</span></div>${stars(sp.rating)}</div>
        ${ui.lineChart({ labels: months.map((d) => `${d.getMonth() + 1}/${String(d.getFullYear()).slice(2)}`), series: [{ name: t("Average", "Trung bình"), values: monthly, area: true, color: "#f59e0b" }], height: 150, yMin: 1, yMax: 5 })}
        <ul class="list" style="margin:0 -16px -16px;border-top:1px solid var(--border)">${own.slice(0, 5).map((r) => `<li><div class="grow"><div class="row between"><span>${stars(r.stars)} <span class="small muted mono">${esc(r.case_id)}</span></span><span class="small muted">${fmt.date(r.at)}</span></div><div class="small">${esc(tx(r.comment))}</div></div></li>`).join("") || `<li>${ui.empty()}</li>`}</ul>`,
    }) : ui.card({ title: t("Ratings", "Đánh giá"), body: ui.empty(t("This partner has no profile yet", "Đối tác chưa có hồ sơ")) });

    return STYLE + ui.pageHead({
      title: t("Repair and rescue partners", "Đối tác sửa chữa & cứu hộ"),
      sub: esc(t("The network customer care dispatches to: type, service area, capabilities, hours, committed SLA, rating and revenue share.", "Mạng lưới CSKH điều phối: loại, khu vực phục vụ, năng lực, giờ làm việc, SLA cam kết, đánh giá và tỷ lệ chia doanh thu.")),
      features: ["SUP-04", "SUP-05"],
      actions: `${ui.btn(t("Export", "Xuất"), { icon: "download", action: "export" })}${ui.btn(t("Add partner", "Thêm đối tác"), { icon: "plus", kind: "primary", action: "add" })}`,
    }) + kpis + directory + `<div class="grid side mt">${mapCard}${ratingCard}</div>`;
  }

  function mountPartners(root, { query }) {
    const bind = (sel, fn) => root.querySelectorAll(sel).forEach((el) => el.addEventListener("click", (e) => fn(el, e)));
    root.querySelectorAll("[data-filter]").forEach((el) => el.addEventListener("change", () => G3.setQuery({ [el.dataset.filter]: el.value })));
    root.querySelectorAll("tr[data-select]").forEach((tr) => tr.addEventListener("click", (e) => { if (!e.target.closest("button, a")) G3.setQuery({ p: tr.dataset.select }); }));
    root.querySelectorAll("[data-partner]").forEach((g) => g.addEventListener("click", () => { const wrap = root.querySelector("[data-map-wrap]"); if (!wrap || !wrap.dataset.justDragged) G3.setQuery({ p: g.dataset.partner }); }));
    bind("[data-action=add]", () => partnerForm(null));
    bind("[data-action=edit]", (el) => partnerForm(db.org(el.dataset.org)));
    bind("[data-action=toggle-status]", (el) => {
      const org = db.org(el.dataset.org);
      const suspend = org.status === "ACTIVE";
      ui.reasonModal({
        title: suspend ? t(`Suspend ${org.short_name}`, `Tạm ngưng ${org.short_name}`) : t(`Reactivate ${org.short_name}`, `Kích hoạt lại ${org.short_name}`),
        intro: suspend ? `<p class="small muted">${esc(t("A suspended partner is no longer suggested for dispatch. Open dispatches are kept.", "Đối tác tạm ngưng sẽ không được đề xuất điều phối nữa. Các ca đang điều phối vẫn giữ nguyên."))}</p>` : "",
        danger: suspend, submitLabel: suspend ? t("Suspend", "Tạm ngưng") : t("Reactivate", "Kích hoạt lại"),
        onSubmit(reason) {
          org.status = suspend ? "SUSPENDED" : "ACTIVE";
          org.status_reason = suspend ? reason : null;
          ui.toast(suspend ? t(`${org.short_name} suspended`, `Đã tạm ngưng ${org.short_name}`) : t(`${org.short_name} reactivated`, `Đã kích hoạt lại ${org.short_name}`));
          G3.render();
        },
      });
    });
    bind("[data-action=rate]", (el) => {
      const orgId = el.dataset.org;
      const cases = db.support_cases.filter((c) => c.partner_organization_id === orgId);
      ui.formModal({
        title: t(`Rate ${db.org(orgId).short_name}`, `Đánh giá ${db.org(orgId).short_name}`),
        submitLabel: t("Save rating", "Lưu đánh giá"),
        fields: [
          { name: "case", label: t("Case", "Ca"), type: "select", options: [["", t("— not linked to a case —", "— không gắn với ca —")], ...cases.map((c) => [c.support_case_id, `${c.support_case_id} · ${tx(c.subject)}`])] },
          { name: "stars", label: t("Rating", "Điểm"), type: "select", value: "5", options: [5, 4, 3, 2, 1].map((n) => [String(n), "★".repeat(n)]) },
          { name: "comment", label: t("Comment", "Nhận xét"), type: "textarea", required: true },
        ],
        onSubmit(values) {
          addRating(cases.find((c) => c.support_case_id === values.case) || null, orgId, Number(values.stars), values.comment);
          ui.toast(t("Rating saved", "Đã lưu đánh giá"));
          G3.render();
        },
      });
    });
    bind("[data-action=export]", () => ui.downloadCsv("partners.csv", [
      { label: "name", csv: (o) => o.name }, { label: "tax_code", csv: (o) => o.tax_code }, { label: "status", csv: (o) => o.status },
      { label: "partner_type", csv: (o) => (profile(o.organization_id) || {}).partner_type }, { label: "service_area", csv: (o) => ((profile(o.organization_id) || {}).service_area || []).join("; ") },
      { label: "capabilities", csv: (o) => ((profile(o.organization_id) || {}).capabilities || []).join("; ") }, { label: "opening_hours", csv: (o) => (profile(o.organization_id) || {}).opening_hours },
      { label: "sla_minutes", csv: (o) => (profile(o.organization_id) || {}).sla_minutes }, { label: "rating", csv: (o) => (profile(o.organization_id) || {}).rating },
      { label: "revenue_share_percent", csv: (o) => (profile(o.organization_id) || {}).revenue_share_percent }, { label: "hotline", csv: (o) => (profile(o.organization_id) || {}).hotline },
    ], partnerOrgs()));
    const sp = profile(query.p);
    setTimeout(() => {
      const wrap = root.querySelector("[data-map-wrap=partners-map]");
      if (sp && wrap && wrap.zoomTo) wrap.zoomTo(sp.lat, sp.lon, 700);
      keepMarkerSize(root, "partners-map");
    }, 0);
  }

  G3.page({
    id: "partners",
    path: "/partners",
    title: ["Partners", "Đối tác cứu hộ"],
    roles: ["CUSTOMER_CARE", "OPERATIONS"],
    features: ["SUP-04", "SUP-05"],
    nav: { group: "support", order: 2, icon: "handshake" },
    render: partnersPage,
    mount: mountPartners,
  });

  /* =====================================================================
     Screen 4: system health, data quality, data sovereignty (PLT-03, PLT-02, PLT-05)
     ===================================================================== */
  /* English versions of the process metrics (the mock data holds them in Vietnamese). */
  const PROCESS_EN = {
    api: ["p95 84 ms", "1,240 req/min"],
    "telemetry-ingestion": ["p95 delay 3.8 s", "processed 2,410/min · skipped 3 · errors 0"],
    "telematics-status": ["48 reports/min", "g3network/telematics/+/status"],
    "ocpp-gateway": [null, null],
    "telematics-monitor": ["60 s loop", "2 devices silent"],
    "driving-sessions-autoend": ["300 s loop", "6 sessions ended today"],
    db: ["41 GB", "1-day telemetry chunks"],
    broker: ["29 clients", "QoS 0"],
  };
  function processMetric(proc) {
    if (proc.name === "ocpp-gateway") {
      const chargers = db.charging_stations.filter((s) => s.status === "ACTIVE");
      const offline = chargers.filter((s) => !s.is_online);
      return [t(`${chargers.length - offline.length}/${chargers.length} chargers connected`, `${chargers.length - offline.length}/${chargers.length} trụ kết nối`), offline.map((s) => t(`${s.ocpp_identity} offline ${fmt.ago(s.last_seen_at).replace(" ago", "")}`, `${s.ocpp_identity} mất kết nối ${fmt.ago(s.last_seen_at).replace(" trước", "")}`)).join(" · ") || t("all connected", "tất cả kết nối")];
    }
    const en = PROCESS_EN[proc.name];
    return G3.lang === "en" && en ? en : [proc.metric, proc.detail];
  }
  const uptime = (h) => (h >= 24 ? `${Math.floor(h / 24)} ${t("d", "ngày")} ${h % 24} ${t("h", "giờ")}` : `${h} ${t("h", "giờ")}`);
  const liveDelay = () => 3.4 + 0.5 * Math.sin(Date.now() / 17000);
  const hoursAgo = (h) => db.NOW - h * 60 * MIN;

  const alertRules = [
    { id: "ingest-stop", on: true, text: ["Telemetry ingestion stopped for more than 2 min", "Nhận dữ liệu xe dừng quá 2 phút"], to: "OPERATIONS · push + e-mail" },
    { id: "delay", on: true, text: ["Ingestion delay p95 above 10 s for 5 min", "Độ trễ p95 trên 10 s trong 5 phút"], to: "OPERATIONS · push" },
    { id: "charger", on: true, text: ["Charger disconnected for more than 15 min", "Trụ sạc mất kết nối quá 15 phút"], to: "OPERATIONS · push" },
    { id: "tbox", on: true, text: ["T-Box silent for 24 h (DEV-05)", "T-Box im lặng 24 giờ (DEV-05)"], to: "ORG_ADMIN, FLEET_MANAGER, OPERATIONS" },
    { id: "quality", on: false, text: ["Data quality score of a source below 95", "Điểm chất lượng một nguồn dưới 95"], to: "OPERATIONS · e-mail" },
    { id: "foreign", on: true, text: ["Undeclared foreign connection detected (PLT-05)", "Phát hiện kết nối ra nước ngoài chưa khai báo (PLT-05)"], to: "OPERATIONS, HEAD_ADMIN · push + e-mail" },
  ];

  function interruptions() {
    const rows = [];
    db.charging_stations.filter((s) => s.status === "ACTIVE" && !s.is_online).forEach((s) => rows.push({ started: ms(s.last_seen_at), ended: null, source: "OCPP", scope: `${s.ocpp_identity} · ${db.location(s.location_id).name}`, cause: t("Charger stopped heartbeats: 4G router at the site", "Trụ ngừng gửi heartbeat: router 4G tại trạm"), impact: t("2 connectors unavailable to drivers", "2 súng sạc không khả dụng") }));
    db.vehicles.filter((v) => v.live && !v.live.is_online && v.organization_id !== "org-g3").forEach((v) => rows.push({ started: ms(v.live.received_at), ended: null, source: "MQTT", scope: `T-Box ${v.license_plate}`, cause: t("Device silent (DEV-05): no telemetry, no status report", "Thiết bị im lặng (DEV-05): không có dữ liệu, không có báo cáo trạng thái"), impact: t("No live position or battery for this truck", "Không có vị trí và pin trực tuyến của xe") }));
    rows.push({ started: hoursAgo(5), ended: hoursAgo(5) + 3 * MIN, source: "MQTT", scope: t("EMQX broker", "Broker EMQX"), cause: t("Broker restart after a configuration change", "Khởi động lại broker sau khi đổi cấu hình"), impact: t("3-min gap on 24 trucks; readings buffered by the T-Box and re-sent", "Mất 3 phút dữ liệu của 24 xe; T-Box lưu đệm và gửi lại") });
    rows.push({ started: hoursAgo(49), ended: hoursAgo(49) + 6 * MIN, source: "INGESTION", scope: t("Telemetry ingestion", "Tiến trình nhận dữ liệu xe"), cause: t("Worker exited on a database error; restarted by the supervisor", "Worker dừng do lỗi cơ sở dữ liệu; được khởi động lại tự động"), impact: t("QoS 0: about 14,000 readings lost", "QoS 0: mất khoảng 14.000 bản ghi") });
    rows.push({ started: hoursAgo(146), ended: hoursAgo(146) + 40 * 1000, source: "API", scope: "API", cause: t("Release 0.18 rolling restart", "Khởi động lại khi phát hành bản 0.18"), impact: t("40 s of 503 answers on 2 % of requests", "40 giây trả lỗi 503 cho 2% yêu cầu") });
    return rows.sort((a, b) => b.started - a.started);
  }

  function healthTab() {
    const procs = db.system_processes;
    const healthy = procs.filter((p) => p.status === "HEALTHY").length;
    const log = interruptions();
    const r = seeded("ingest-delay");
    const hours = Array.from({ length: 24 }, (_, i) => new Date(db.NOW - (23 - i) * 60 * MIN));
    const p50 = hours.map((_, i) => Math.round((1.1 + r() * 0.5 + (i === 18 ? 4.8 : 0)) * 10) / 10);
    const p95 = hours.map((_, i) => Math.round((3.0 + r() * 1.4 + (i === 18 ? 11 : i === 19 ? 2.5 : 0)) * 10) / 10);
    const silentDevices = db.telematics.filter((d) => d.health === "SILENT").length;
    return `<div class="grid cols-5">
      ${ui.kpi({ label: t("Processes healthy", "Tiến trình ổn định"), icon: "activity", value: `${healthy}<small>/ ${procs.length}</small>`, tone: healthy < procs.length ? "warn" : "ok", foot: `${procs.length - healthy} ${t("need attention", "cần chú ý")}` })}
      ${ui.kpi({ label: t("Ingestion delay p95", "Độ trễ nhận dữ liệu p95"), icon: "clock", value: `<span data-live-delay>${fmt.num(liveDelay(), 1)}</span>`, unit: "s", foot: t("alert above 10 s", "cảnh báo khi trên 10 s") })}
      ${ui.kpi({ label: t("Telemetry / min", "Bản ghi / phút"), icon: "signal", value: `<span data-live-rate>${fmt.num(2410 + Math.round(40 * Math.sin(Date.now() / 9000)))}</span>`, foot: `${db.vehicles.filter((v) => v.live && v.live.is_online).length} ${t("trucks sending", "xe đang gửi")} · ${silentDevices} ${t("silent", "im lặng")}` })}
      ${ui.kpi({ label: t("API uptime (30 d)", "Thời gian hoạt động API (30 ngày)"), icon: "power", value: fmt.num(99.96, 2), unit: "%", foot: t("target 99.5%", "mục tiêu 99,5%") })}
      ${ui.kpi({ label: t("Interruptions (7 d)", "Gián đoạn (7 ngày)"), icon: "alert", value: log.length, tone: log.some((x) => !x.ended) ? "danger" : undefined, foot: `${log.filter((x) => !x.ended).length} ${t("ongoing", "đang diễn ra")}` })}
    </div>
    ${ui.card({
      title: t("Processes", "Tiến trình"), sub: t("one row per backend process", "mỗi tiến trình backend một dòng"), cls: "mt", flush: true,
      body: ui.table({ rows: procs, columns: [
        { label: t("Process", "Tiến trình"), render: (p) => `<b>${esc(p.label)}</b><span class="sub mono">${esc(p.name)}</span>` },
        { label: t("Status", "Trạng thái"), render: (p) => ui.badge(p.status) },
        { label: t("Uptime", "Đã chạy"), cls: "nowrap", render: (p) => uptime(p.uptime_hours) },
        { label: t("Key metric", "Chỉ số chính"), render: (p) => `<b>${esc(processMetric(p)[0])}</b><span class="sub">${esc(processMetric(p)[1])}</span>` },
        { label: t("Last heartbeat", "Heartbeat cuối"), render: (p, i) => `<span data-hb="${esc(p.name)}">${esc(t("just now", "vừa xong"))}</span>` },
        { label: t("Run locally", "Chạy cục bộ"), render: (p) => `<div class="row" style="gap:4px;flex-wrap:nowrap"><code>${esc(p.make)}</code>${ui.btn("", { icon: "file", sm: true, kind: "ghost", action: "copy", attrs: `data-cmd="${esc(p.make)}" title="${esc(t("Copy", "Sao chép"))}"` })}</div>` },
      ] }),
    })}
    <div class="grid side mt">
      ${ui.card({ title: t("Ingestion delay, last 24 h", "Độ trễ nhận dữ liệu, 24 giờ qua"), sub: t("MQTT receive → stored", "nhận MQTT → lưu"), actions: ui.legend([["p50", ui.PALETTE[0]], ["p95", ui.PALETTE[2]], [t("alert threshold", "ngưỡng cảnh báo"), "#dc2626"]]),
        body: ui.lineChart({ labels: hours.map((d) => `${String(d.getHours()).padStart(2, "0")}:00`), series: [{ name: "p50", values: p50, color: ui.PALETTE[0], area: true }, { name: "p95", values: p95, color: ui.PALETTE[2] }, { name: t("threshold", "ngưỡng"), values: hours.map(() => 10), color: "#dc2626", dashed: true }], height: 300, unit: " s", yMax: 16 }) + `<div class="small muted">${esc(t("The spike 5 h ago is the broker restart in the log below.", "Đỉnh 5 giờ trước là lần khởi động lại broker trong nhật ký bên dưới."))}</div>` })}
      ${ui.card({ title: t("Alerts to the operations team", "Cảnh báo cho đội vận hành"), sub: "PLT-03", flush: true,
        body: `<ul class="list">${alertRules.map((a) => `<li><label class="switch"><input type="checkbox" data-rule="${a.id}" ${a.on ? "checked" : ""}><span></span></label><div class="grow"><div>${esc(tx(a.text))}</div><div class="meta">${esc(a.to)}</div></div></li>`).join("")}</ul>` })}
    </div>
    ${ui.card({
      title: t("Data-flow interruptions", "Gián đoạn luồng dữ liệu"), sub: t("last 7 days", "7 ngày qua"), cls: "mt", flush: true,
      body: ui.table({ rows: log, columns: [
        { label: t("Started", "Bắt đầu"), render: (x) => `${fmt.dateTime(x.started)}<span class="sub">${fmt.ago(x.started)}</span>` },
        { label: t("Source", "Nguồn"), render: (x) => ui.tag(x.source) },
        { label: t("Scope", "Phạm vi"), render: (x) => `<b>${esc(x.scope)}</b>` },
        { label: t("Duration", "Thời lượng"), render: (x) => (x.ended ? fmt.duration((x.ended - x.started) / MIN) : ui.badge("CRITICAL", "danger", t("ongoing", "đang diễn ra")) + `<span class="sub">${fmt.duration((Date.now() - x.started) / MIN)}</span>`) },
        { label: t("Cause", "Nguyên nhân"), render: (x) => esc(x.cause) },
        { label: t("Impact", "Ảnh hưởng"), render: (x) => `<span class="small">${esc(x.impact)}</span>` },
        { label: t("Alert", "Cảnh báo"), render: (x) => ui.badge("ACCEPTED", "ok", t("sent", "đã gửi")) },
      ] }),
    })}`;
  }

  /* PLT-02: quality rules per source, with mock counts derived from the trucks and chargers. */
  const QUALITY_SOURCES = [
    { key: "telemetry", name: ["Vehicle telemetry (MQTT)", "Dữ liệu xe (MQTT)"], score: 98.6, checked: 3470000 },
    { key: "status", name: ["T-Box status reports", "Báo cáo trạng thái T-Box"], score: 99.4, checked: 69000 },
    { key: "ocpp", name: ["Charger messages (OCPP)", "Bản tin trụ sạc (OCPP)"], score: 97.1, checked: 412000 },
    { key: "app", name: ["Driver app check-ins", "Nhận xe trên ứng dụng"], score: 99.8, checked: 1900 },
  ];
  const qualityRules = [
    { id: "speed", source: "telemetry", rule: ["Impossible speed", "Tốc độ bất khả thi"], cond: "speed_kmh > 130", action: "SKIP", count: 41, on: true },
    { id: "soc", source: "telemetry", rule: ["Battery % jump without charging", "Pin nhảy % khi không sạc"], cond: "|Δ soc_percent| > 15 in 60 s", action: "FLAG", count: 12, on: true },
    { id: "gps", source: "telemetry", rule: ["GPS outside Vietnam", "GPS ngoài lãnh thổ Việt Nam"], cond: "lat ∉ [8.2, 23.4] or lon ∉ [102.1, 109.5]", action: "SKIP", count: 27, on: true },
    { id: "missing", source: "telemetry", rule: ["Missing required fields", "Thiếu trường bắt buộc"], cond: "soc_percent, lat, lon, recorded_at", action: "FLAG", count: 96, on: true },
    { id: "future", source: "telemetry", rule: ["Timestamp in the future", "Thời gian ở tương lai"], cond: "recorded_at > now + 300 s", action: "SKIP", count: 8, on: true },
    { id: "odo", source: "telemetry", rule: ["Odometer going backwards", "Odo giảm"], cond: "odometer_km < previous", action: "FLAG", count: 3, on: true },
    { id: "temp", source: "telemetry", rule: ["Battery temperature out of range", "Nhiệt độ pin ngoài dải"], cond: "-30 °C ≤ t ≤ 90 °C", action: "SKIP", count: 5, on: true },
    { id: "interval", source: "status", rule: ["Reported interval differs from the configured one", "Chu kỳ gửi khác cấu hình"], cond: "reported ≠ send_interval_seconds", action: "ALERT", count: 1, on: true },
    { id: "meter", source: "ocpp", rule: ["Energy meter decreased within a session", "Công tơ giảm trong phiên sạc"], cond: "Energy.Active.Import.Register < previous", action: "FLAG", count: 4, on: true },
    { id: "seq", source: "ocpp", rule: ["Duplicate or out-of-order seqNo", "seqNo trùng hoặc sai thứ tự"], cond: "seqNo ≤ last seqNo", action: "FLAG", count: 17, on: true },
    { id: "far", source: "app", rule: ["Check-in far from the truck", "Nhận xe ở xa vị trí xe"], cond: "distance(phone, truck) > 500 m", action: "ALERT", count: 2, on: false },
  ];
  function violations() {
    const trucks = db.vehicles.filter((v) => v.live && v.organization_id !== "org-g3");
    const plate = (i) => trucks[i % trucks.length].license_plate;
    const charger = (i) => db.charging_stations[i % db.charging_stations.length].ocpp_identity;
    return [
      { at: db.NOW - 6 * MIN, source: "telemetry", subject: plate(3), rule: "speed", value: "212 km/h", action: "SKIP" },
      { at: db.NOW - 19 * MIN, source: "telemetry", subject: plate(7), rule: "soc", value: "34% → 71% / 30 s", action: "FLAG" },
      { at: db.NOW - 33 * MIN, source: "ocpp", subject: charger(2), rule: "seq", value: "seqNo 1184 ≤ 1186", action: "FLAG" },
      { at: db.NOW - 48 * MIN, source: "telemetry", subject: plate(11), rule: "gps", value: "0.00000, 0.00000", action: "SKIP" },
      { at: db.NOW - 71 * MIN, source: "telemetry", subject: plate(5), rule: "missing", value: t("soc_percent missing", "thiếu soc_percent"), action: "FLAG" },
      { at: db.NOW - 95 * MIN, source: "telemetry", subject: plate(14), rule: "future", value: t("+42 min ahead", "nhanh 42 phút"), action: "SKIP" },
      { at: db.NOW - 140 * MIN, source: "ocpp", subject: charger(5), rule: "meter", value: "184 211 Wh → 184 090 Wh", action: "FLAG" },
      { at: db.NOW - 190 * MIN, source: "status", subject: plate(4), rule: "interval", value: t("30 s reported, 10 s configured", "báo 30 s, cấu hình 10 s"), action: "ALERT" },
      { at: db.NOW - 260 * MIN, source: "telemetry", subject: plate(9), rule: "temp", value: "255 °C", action: "SKIP" },
      { at: db.NOW - 330 * MIN, source: "telemetry", subject: plate(1), rule: "odo", value: "48 211 → 48 190 km", action: "FLAG" },
    ];
  }
  const ruleName = (id) => { const r = qualityRules.find((x) => x.id === id); return r ? tx(r.rule) : id; };

  function qualityTab() {
    const overall = QUALITY_SOURCES.reduce((a, s) => a + s.score * s.checked, 0) / QUALITY_SOURCES.reduce((a, s) => a + s.checked, 0);
    const flagged = qualityRules.reduce((a, r) => a + r.count, 0);
    const days = 14;
    const labels = Array.from({ length: days }, (_, i) => { const d = new Date(db.NOW - (days - 1 - i) * 24 * 60 * MIN); return `${d.getDate()}/${d.getMonth() + 1}`; });
    const r = seeded("quality-days");
    const top = ["missing", "speed", "gps", "seq"];
    const series = top.map((id, k) => ({ name: ruleName(id), values: labels.map((_, i) => Math.round(qualityRules.find((x) => x.id === id).count * (0.6 + r() * 0.8) * (id === "gps" && i === 9 ? 4 : 1))), color: ui.PALETTE[[2, 1, 4, 3][k]] }));
    return `${ui.featureChips(["PLT-02"])}<div class="grid cols-4 mt">
      ${ui.kpi({ label: t("Data quality score", "Điểm chất lượng dữ liệu"), icon: "check", value: fmt.num(overall, 1), unit: "/ 100", tone: overall >= 98 ? "ok" : "warn", foot: t("weighted by volume, 24 h", "theo khối lượng, 24 giờ") })}
      ${ui.kpi({ label: t("Records checked (24 h)", "Bản ghi đã kiểm (24 giờ)"), icon: "list", value: fmt.num(QUALITY_SOURCES.reduce((a, s) => a + s.checked, 0) / 1e6, 2), unit: t("M", "triệu"), foot: `${QUALITY_SOURCES.length} ${t("sources", "nguồn")}` })}
      ${ui.kpi({ label: t("Violations (24 h)", "Vi phạm (24 giờ)"), icon: "alert", value: fmt.num(flagged), foot: `${qualityRules.filter((x) => x.action === "SKIP").reduce((a, x) => a + x.count, 0)} ${t("readings skipped", "bản ghi bị bỏ")}` })}
      ${ui.kpi({ label: t("Rules active", "Quy tắc đang bật"), icon: "shield", value: `${qualityRules.filter((x) => x.on).length}<small>/ ${qualityRules.length}</small>`, foot: t("per data source", "theo từng nguồn dữ liệu") })}
    </div>
    <div class="grid cols-2 mt">
      ${ui.card({ title: t("Score per source", "Điểm theo nguồn"), body: `<div class="stack" style="gap:14px">${QUALITY_SOURCES.map((s) => { const rr = seeded(s.key); const trend = Array.from({ length: 14 }, () => s.score - 1.2 + rr() * 1.6); return `<div class="row between" style="flex-wrap:nowrap;gap:14px"><div style="min-width:0;flex:1"><div class="row between small"><span>${esc(tx(s.name))}</span><b class="num">${fmt.num(s.score, 1)}</b></div>${ui.meter(s.score, s.score < 98 ? "warn" : "")}<div class="small muted">${fmt.num(s.checked)} ${esc(t("records / 24 h", "bản ghi / 24 giờ"))}</div></div>${ui.sparkline(trend, s.score < 98 ? "var(--warn)" : "var(--primary)")}</div>`; }).join("")}</div>` })}
      ${ui.card({ title: t("Violations per day", "Vi phạm theo ngày"), sub: t("top 4 rules, 14 days", "4 quy tắc nhiều nhất, 14 ngày"), body: ui.legend(series.map((s) => [s.name, s.color])) + ui.barChart({ labels, series, stacked: true, height: 200 }) })}
    </div>
    ${ui.card({ title: t("Rules", "Quy tắc"), sub: t("what happens to a reading that breaks a rule", "xử lý bản ghi vi phạm"), cls: "mt", flush: true, body: ui.table({ rows: qualityRules, columns: [
      { label: t("On", "Bật"), render: (x) => `<label class="switch" style="display:inline-block"><input type="checkbox" data-qrule="${x.id}" ${x.on ? "checked" : ""}><span></span></label>` },
      { label: t("Source", "Nguồn"), render: (x) => esc(tx(QUALITY_SOURCES.find((s) => s.key === x.source).name)) },
      { label: t("Rule", "Quy tắc"), render: (x) => `<b>${esc(tx(x.rule))}</b>` },
      { label: t("Condition", "Điều kiện"), render: (x) => `<code>${esc(x.cond)}</code>` },
      { label: t("Action", "Xử lý"), render: (x) => badge(x.action, x.action === "SKIP" ? "warn" : x.action === "ALERT" ? "danger" : "info") },
      { label: t("Violations (24 h)", "Vi phạm (24 giờ)"), align: "right", render: (x) => (x.on ? fmt.num(x.count) : '<span class="muted">—</span>') },
    ] }) })}
    ${ui.card({ title: t("Recent violations", "Vi phạm gần đây"), cls: "mt", flush: true, actions: ui.btn(t("Export", "Xuất"), { icon: "download", sm: true, kind: "ghost", action: "export-violations" }), body: ui.table({ rows: violations(), columns: [
      { label: t("Time", "Thời gian"), render: (x) => `${fmt.time(x.at)}<span class="sub">${fmt.ago(x.at)}</span>` },
      { label: t("Source", "Nguồn"), render: (x) => esc(tx(QUALITY_SOURCES.find((s) => s.key === x.source).name)) },
      { label: t("Truck / charger", "Xe / trụ"), render: (x) => `<b>${esc(x.subject)}</b>` },
      { label: t("Rule", "Quy tắc"), render: (x) => esc(ruleName(x.rule)) },
      { label: t("Observed", "Giá trị ghi nhận"), render: (x) => `<code>${esc(x.value)}</code>` },
      { label: t("Action", "Xử lý"), render: (x) => badge(x.action, x.action === "SKIP" ? "warn" : x.action === "ALERT" ? "danger" : "info") },
    ] }) })}`;
  }

  /* PLT-05: declared data flows per device type, and what the SIM gateway actually observed. */
  const DEVICE_TYPES = [
    { key: "tbox", name: ["Truck T-Box (4G)", "T-Box trên xe (4G)"], sub: () => `${db.telematics.filter((d) => d.vehicle_id).length} ${t("devices", "thiết bị")} · MQTT` },
    { key: "charger", name: ["DC charger (OCPP)", "Trụ sạc DC (OCPP)"], sub: () => `${db.charging_stations.length} ${t("chargers", "trụ")} · WebSocket` },
    { key: "app", name: ["Driver app", "Ứng dụng tài xế"], sub: () => "Android / iOS · HTTPS" },
  ];
  const tboxFirmware = (versions) => db.telematics.filter((d) => d.vehicle_id && versions.includes(d.firmware_version)).length;
  const DESTINATIONS = [
    { device: "tbox", name: ["G3 MQTT broker", "Broker MQTT G3"], host: "mqtt.g3network.vn", ip: "103.97.125.18", country: "VN", operator: "G3 Network · Viettel IDC", declared: true, data: ["Position, speed, battery, faults", "Vị trí, tốc độ, pin, mã lỗi"], mb: 1840, seen: () => db.telematics.filter((d) => d.vehicle_id && d.health !== "SILENT").length, first: 210 },
    { device: "tbox", name: ["Firmware update server (OTA)", "Máy chủ cập nhật firmware (OTA)"], host: "ota.tbox-vendor.cn", ip: "47.112.18.201", country: "CN", operator: ["T-Box manufacturer", "Nhà sản xuất T-Box"], declared: true, data: ["Firmware download only", "Chỉ tải firmware"], mb: 12, seen: () => 26, first: 210 },
    { device: "tbox", name: ["Unknown log collector", "Máy chủ thu log chưa rõ"], host: "tlog.tbox-vendor-cloud.cn", ip: "120.55.73.9", country: "CN", operator: ["unknown", "chưa rõ"], declared: false, data: ["Encrypted uploads ~1.4 MB/device/day — size matches GPS + CAN logs", "Tải lên mã hóa ~1,4 MB/thiết bị/ngày — khớp kích thước log GPS + CAN"], mb: 37, seen: () => tboxFirmware(["v2.4.0-rc1", "v2.2.8"]), first: 9 },
    { device: "tbox", name: ["Time server (NTP)", "Máy chủ thời gian (NTP)"], host: "ntp.isp-hcm.vn", ip: "203.162.4.190", country: "VN", operator: ["Mobile operator", "Nhà mạng"], declared: false, data: ["Clock sync, no vehicle data", "Đồng bộ giờ, không có dữ liệu xe"], mb: 0.1, seen: () => 26, first: 210 },
    { device: "charger", name: ["G3 OCPP gateway", "Cổng OCPP G3"], host: "ocpp.g3network.vn", ip: "103.97.125.20", country: "VN", operator: "G3 Network · Viettel IDC", declared: true, data: ["Sessions, meter values, statuses", "Phiên sạc, công tơ, trạng thái"], mb: 420, seen: () => db.charging_stations.filter((s) => s.is_online).length, first: 200 },
    { device: "charger", name: ["Charger vendor remote service", "Dịch vụ từ xa của hãng trụ"], host: "support-vpn.charger-vendor.cn", ip: "116.62.40.77", country: "CN", operator: ["Charger manufacturer", "Hãng sản xuất trụ"], declared: true, data: ["Remote diagnostics on G3's request, over VPN", "Chẩn đoán từ xa khi G3 yêu cầu, qua VPN"], mb: 0, seen: () => 0, first: 200 },
    { device: "charger", name: ["Unknown analytics endpoint", "Điểm thu thập phân tích chưa rõ"], host: "metrics.evse-analytics.eu", ip: "18.196.44.12", country: "DE", operator: ["unknown", "chưa rõ"], declared: false, data: ["Hourly HTTPS posts, ~25 KB each", "Gửi HTTPS mỗi giờ, ~25 KB mỗi lần"], mb: 6.2, seen: () => db.charging_stations.filter((s) => s.firmware_version === "WD-3.5.0").length, first: 21 },
    { device: "app", name: ["G3 API", "API G3"], host: "api.g3network.vn", ip: "103.97.125.16", country: "VN", operator: "G3 Network · Viettel IDC", declared: true, data: ["Check-ins, trips, SOS, payments", "Nhận xe, chuyến, SOS, thanh toán"], mb: 610, seen: () => db.drivers.length, first: 180 },
    { device: "app", name: ["Push notifications", "Thông báo đẩy"], host: "fcm.googleapis.com", ip: "142.250.66.10", country: "US", operator: "Google FCM / Apple APNs", declared: true, data: ["Notification text, no position", "Nội dung thông báo, không có vị trí"], mb: 3, seen: () => db.drivers.length, first: 180 },
    { device: "app", name: ["Crash reports", "Báo cáo lỗi ứng dụng"], host: "crash.g3network.vn", ip: "103.97.125.22", country: "VN", operator: ["G3 Network (self-hosted)", "G3 Network (tự vận hành)"], declared: true, data: ["Stack traces, no personal data", "Vết lỗi, không có dữ liệu cá nhân"], mb: 1.1, seen: () => 7, first: 180 },
  ];
  const connState = {};
  const destState = (d) => (d.declared ? "DECLARED" : connState[d.host] || "NEW");

  function flowDiagram() {
    const W = 960, rowH = 40, top = 14;
    const H = top * 2 + DESTINATIONS.length * rowH;
    const nodeH = 70;
    const lx = 16, lw = 250, rx = 600, rw = 344;
    const leftY = {};
    DEVICE_TYPES.forEach((d, i) => {
      const rows = DESTINATIONS.map((x, j) => [x, j]).filter(([x]) => x.device === d.key).map(([, j]) => j);
      const mid = top + ((rows[0] + rows[rows.length - 1]) / 2) * rowH + 16;
      leftY[d.key] = mid;
    });
    const tone = (x) => (x.declared ? (x.country === "VN" ? "var(--primary)" : "var(--accent)") : x.country === "VN" ? "var(--warn)" : "var(--danger)");
    let svg = `<svg class="sup-flow" viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">`;
    DESTINATIONS.forEach((x, j) => {
      const y1 = leftY[x.device], y2 = top + j * rowH + 16;
      const state = destState(x);
      svg += `<path d="M${lx + lw},${y1} C${lx + lw + 170},${y1} ${rx - 170},${y2} ${rx},${y2}" fill="none" stroke="${tone(x)}" stroke-width="${x.declared ? 2.2 : 3}" ${x.declared ? "" : 'stroke-dasharray="7 5"'} opacity="${state === "BLOCKED" ? 0.3 : 0.85}" data-tip="${esc(`${tx(x.name)} · ${tx(x.data)} · ${fmt.num(x.mb, 1)} MB / 24 h`)}"/>`;
    });
    DEVICE_TYPES.forEach((d) => {
      const y = leftY[d.key] - nodeH / 2;
      svg += `<rect x="${lx}" y="${y}" width="${lw}" height="${nodeH}" rx="10" fill="var(--surface-2)" stroke="var(--border-strong)"/><text x="${lx + 16}" y="${y + 30}" font-size="15" font-weight="700" fill="var(--text)">${esc(tx(d.name))}</text><text x="${lx + 16}" y="${y + 52}" font-size="12.5" fill="var(--text-3)">${esc(d.sub())}</text>`;
    });
    DESTINATIONS.forEach((x, j) => {
      const y = top + j * rowH;
      const bad = !x.declared && x.country !== "VN";
      svg += `<rect x="${rx}" y="${y}" width="${rw}" height="32" rx="8" fill="${bad ? "var(--danger-soft)" : "var(--surface-2)"}" stroke="${bad ? "var(--danger)" : "var(--border)"}"/><text x="${rx + 12}" y="${y + 21}" font-size="13" font-weight="600" fill="var(--text)">${esc(tx(x.name))}</text><text x="${rx + rw - 12}" y="${y + 21}" font-size="12" font-weight="700" text-anchor="end" fill="${tone(x)}">${esc(x.country)}${x.declared ? "" : " · " + esc(t("undeclared", "chưa khai báo"))}</text>`;
    });
    return svg + "</svg>";
  }

  function sovereigntyTab() {
    const undeclaredForeign = DESTINATIONS.filter((x) => !x.declared && x.country !== "VN");
    const openFindings = undeclaredForeign.filter((x) => destState(x) !== "BLOCKED");
    const abroadMb = DESTINATIONS.filter((x) => x.country !== "VN" && destState(x) !== "BLOCKED").reduce((a, x) => a + x.mb, 0);
    const commitments = [
      { text: ["Vehicle data is sent only to servers in Vietnam run by G3.", "Dữ liệu xe chỉ gửi về máy chủ tại Việt Nam do G3 vận hành."], ref: "TBX-DC-0312", ok: destState(DESTINATIONS[2]) === "BLOCKED", evidence: ["T-Box uploads to tlog.tbox-vendor-cloud.cn (CN)", "T-Box tải lên tlog.tbox-vendor-cloud.cn (CN)"] },
      { text: ["The OTA server is used for firmware download only.", "Máy chủ OTA chỉ dùng để tải firmware."], ref: "TBX-DC-0312", ok: true, evidence: ["0.2 MB/day upload: TLS handshakes only", "Tải lên 0,2 MB/ngày: chỉ bắt tay TLS"] },
      { text: ["Chargers talk only to the G3 OCPP gateway; remote service only on request, over VPN.", "Trụ chỉ kết nối cổng OCPP của G3; dịch vụ từ xa chỉ khi được yêu cầu, qua VPN."], ref: "WD-DP-07", ok: destState(DESTINATIONS[6]) === "BLOCKED", evidence: ["Firmware WD-3.5.0 posts to metrics.evse-analytics.eu (DE)", "Firmware WD-3.5.0 gửi tới metrics.evse-analytics.eu (DE)"] },
      { text: ["The driver app sends no position to third parties.", "Ứng dụng tài xế không gửi vị trí cho bên thứ ba."], ref: "G3-APP-DPIA", ok: true, evidence: ["Push payloads checked: no coordinates", "Đã kiểm nội dung push: không có tọa độ"] },
    ];
    return `${ui.featureChips(["PLT-05"])}<div class="mt">${ui.callout(openFindings.length ? "danger" : "ok", openFindings.length ? `<b>${esc(t(`${openFindings.length} undeclared foreign connection(s)`, `${openFindings.length} kết nối ra nước ngoài chưa khai báo`))}</b> — ${openFindings.map((x) => `<span class="mono">${esc(x.host)}</span> (${esc(x.country)})`).join(", ")}. ${esc(t("Traffic is watched at the SIM / APN gateway during the pilot and compared with the manufacturers' written commitments.", "Lưu lượng được giám sát tại cổng SIM / APN trong giai đoạn thí điểm và đối chiếu với cam kết bằng văn bản của nhà sản xuất."))}` : esc(t("Every observed connection is declared or blocked.", "Mọi kết nối quan sát được đều đã khai báo hoặc đã chặn.")), "globe")}</div>
    <div class="grid cols-4 mt">
      ${ui.kpi({ label: t("Device types watched", "Loại thiết bị giám sát"), icon: "chip", value: DEVICE_TYPES.length, foot: t("SIM gateway mirror, pilot", "sao chép tại cổng SIM, thí điểm") })}
      ${ui.kpi({ label: t("Declared destinations", "Điểm đến đã khai báo"), icon: "check", value: DESTINATIONS.filter((x) => x.declared).length, foot: `${DESTINATIONS.filter((x) => x.declared && x.country !== "VN").length} ${t("abroad", "ở nước ngoài")}` })}
      ${ui.kpi({ label: t("Undeclared foreign", "Ra nước ngoài chưa khai báo"), icon: "alert", value: openFindings.length, tone: openFindings.length ? "danger" : "ok", foot: `${undeclaredForeign.length - openFindings.length} ${t("blocked", "đã chặn")}` })}
      ${ui.kpi({ label: t("Data abroad (24 h)", "Dữ liệu ra nước ngoài (24 giờ)"), icon: "globe", value: fmt.num(abroadMb, 1), unit: "MB", foot: t("all device types", "mọi loại thiết bị") })}
    </div>
    ${ui.card({ title: t("Declared data-flow map", "Bản đồ luồng dữ liệu đã khai báo"), sub: t("per device type → servers and countries", "theo loại thiết bị → máy chủ và quốc gia"), cls: "mt",
      actions: ui.legend([[t("Declared, Vietnam", "Đã khai báo, Việt Nam"), "#0e9f6e"], [t("Declared, abroad", "Đã khai báo, nước ngoài"), "#2563eb"], [t("Undeclared, Vietnam", "Chưa khai báo, Việt Nam"), "#d97706"], [t("Undeclared, abroad", "Chưa khai báo, nước ngoài"), "#dc2626"]]),
      body: flowDiagram() })}
    ${ui.card({ title: t("Observed connections (24 h)", "Kết nối quan sát được (24 giờ)"), cls: "mt", flush: true, actions: ui.btn(t("Export report", "Xuất báo cáo"), { icon: "download", sm: true, kind: "ghost", action: "export-sovereignty" }), body: ui.table({ rows: DESTINATIONS, rowAttrs: (x) => (!x.declared && x.country !== "VN" && destState(x) !== "BLOCKED" ? "data-sos" : ""), columns: [
      { label: t("Device", "Thiết bị"), render: (x) => esc(tx(DEVICE_TYPES.find((d) => d.key === x.device).name)) },
      { label: t("Destination", "Điểm đến"), render: (x) => `<b>${esc(tx(x.name))}</b><span class="sub mono" title="${esc(x.ip)}">${esc(x.host)}</span><span class="sub">${esc(tx(x.operator))}</span>` },
      { label: t("Country", "Quốc gia"), render: (x) => ui.tag(x.country) },
      { label: t("Data", "Dữ liệu"), render: (x) => `<span class="small" style="display:block;min-width:190px">${esc(tx(x.data))}</span>` },
      { label: t("Devices", "Thiết bị"), align: "right", render: (x) => fmt.num(x.seen()) },
      { label: t("Volume", "Dung lượng"), align: "right", cls: "nowrap", render: (x) => `${fmt.num(x.mb, x.mb < 10 ? 1 : 0)} MB` },
      { label: t("First seen", "Lần đầu"), render: (x) => fmt.date(db.NOW - x.first * 24 * 60 * MIN) },
      { label: t("State", "Tình trạng"), render: (x) => { const s = destState(x); return s === "DECLARED" ? badge("DECLARED", "ok") : s === "BLOCKED" ? ui.badge("BLOCKED") : s === "INVESTIGATING" ? badge("INVESTIGATING", "warn") : badge(x.country === "VN" ? "UNDECLARED" : "NEW", x.country === "VN" ? "warn" : "danger"); } },
      { label: "", render: (x) => (x.declared || destState(x) === "BLOCKED" ? "" : `<div style="display:flex;flex-direction:column;gap:4px">${destState(x) === "NEW" ? ui.btn(t("Investigate", "Điều tra"), { sm: true, action: "investigate", attrs: `data-host="${esc(x.host)}"` }) : ""}${x.country !== "VN" ? ui.btn(t("Block", "Chặn"), { sm: true, kind: "danger", action: "block", attrs: `data-host="${esc(x.host)}"` }) : ui.btn(t("Declare", "Khai báo"), { sm: true, action: "declare", attrs: `data-host="${esc(x.host)}"` })}</div>`) },
    ] }) })}
    ${ui.card({ title: t("Manufacturers' written commitments", "Cam kết bằng văn bản của nhà sản xuất"), sub: t("checked against observed traffic", "đối chiếu với lưu lượng thực tế"), cls: "mt", flush: true, body: ui.table({ rows: commitments, columns: [
      { label: t("Commitment", "Cam kết"), render: (x) => esc(tx(x.text)) },
      { label: t("Document", "Văn bản"), cls: "nowrap", render: (x) => `<span class="mono">${esc(x.ref)}</span>` },
      { label: t("Evidence", "Bằng chứng"), render: (x) => `<span class="small">${esc(tx(x.evidence))}</span>` },
      { label: t("Verdict", "Kết luận"), render: (x) => (x.ok ? ui.badge("OK", "ok", t("Kept", "Tuân thủ")) : ui.badge("CRITICAL", "danger", t("Broken", "Vi phạm"))) },
    ] }) })}`;
  }

  function systemPage({ query }) {
    const tab = ["health", "quality", "sovereignty"].includes(query.tab) ? query.tab : "health";
    const body = tab === "quality" ? qualityTab() : tab === "sovereignty" ? sovereigntyTab() : `${ui.featureChips(["PLT-03"])}<div class="mt">${healthTab()}</div>`;
    return STYLE + ui.pageHead({
      title: t("System health", "Sức khỏe hệ thống"),
      sub: esc(t("Internal: the backend processes, the data they receive and where device data goes.", "Nội bộ: các tiến trình backend, dữ liệu nhận được và nơi dữ liệu thiết bị được gửi đến.")),
      features: ["PLT-03", "PLT-02", "PLT-05"],
      actions: `<span class="small muted">${esc(t("Updated", "Cập nhật"))} <span data-updated>${fmt.time(Date.now())}</span></span>`,
    }) + ui.tabs([["health", t("Processes & ingestion", "Tiến trình & nhận dữ liệu")], ["quality", t("Data quality", "Chất lượng dữ liệu")], ["sovereignty", t("Data sovereignty", "Chủ quyền dữ liệu")]], tab) + body;
  }

  function mountSystem(root) {
    const bind = (sel, fn) => root.querySelectorAll(sel).forEach((el) => el.addEventListener("click", () => fn(el)));
    bind("[data-tabs] [data-tab]", (el) => G3.setQuery({ tab: el.dataset.tab === "health" ? null : el.dataset.tab }));
    bind("[data-action=copy]", (el) => {
      try { navigator.clipboard.writeText(el.dataset.cmd); } catch (e) { /* clipboard blocked on file:// */ }
      ui.toast(t(`Copied: ${el.dataset.cmd}`, `Đã sao chép: ${el.dataset.cmd}`));
    });
    root.querySelectorAll("[data-rule]").forEach((el) => el.addEventListener("change", () => {
      const rule = alertRules.find((a) => a.id === el.dataset.rule);
      rule.on = el.checked;
      ui.toast(el.checked ? t("Alert rule on", "Đã bật quy tắc cảnh báo") : t("Alert rule off", "Đã tắt quy tắc cảnh báo"));
    }));
    root.querySelectorAll("[data-qrule]").forEach((el) => el.addEventListener("change", () => {
      const rule = qualityRules.find((a) => a.id === el.dataset.qrule);
      rule.on = el.checked;
      ui.toast(t(`Rule “${tx(rule.rule)}” ${el.checked ? "on" : "off"}`, `Quy tắc “${tx(rule.rule)}” ${el.checked ? "đã bật" : "đã tắt"}`));
      G3.render();
    }));
    bind("[data-action=export-violations]", () => ui.downloadCsv("data-quality-violations.csv", [
      { label: "at", csv: (x) => iso(x.at) }, { label: "source", key: "source" }, { label: "subject", key: "subject" }, { label: "rule", csv: (x) => x.rule }, { label: "observed", key: "value" }, { label: "action", key: "action" },
    ], violations()));
    bind("[data-action=export-sovereignty]", () => ui.downloadCsv("data-sovereignty-connections.csv", [
      { label: "device_type", key: "device" }, { label: "host", key: "host" }, { label: "ip", key: "ip" }, { label: "country", key: "country" }, { label: "declared", csv: (x) => x.declared },
      { label: "state", csv: (x) => destState(x) }, { label: "devices", csv: (x) => x.seen() }, { label: "mb_24h", key: "mb" },
    ], DESTINATIONS));
    bind("[data-action=investigate]", (el) => {
      const host = el.dataset.host;
      ui.formModal({
        title: t(`Investigate ${host}`, `Điều tra ${host}`),
        submitLabel: t("Open investigation", "Mở điều tra"),
        fields: [
          { name: "owner", label: t("Owner", "Người phụ trách"), type: "select", options: db.memberships.filter((m) => m.organization_id === "org-g3" && m.roles.some((r) => ["OPERATIONS", "HEAD_ADMIN"].includes(r))).map((m) => [m.user_id, db.user(m.user_id).full_name]) },
          { name: "note", label: t("First step", "Bước đầu tiên"), type: "textarea", required: true, value: t("Ask the manufacturer for the purpose and content of this connection in writing.", "Đề nghị nhà sản xuất giải trình bằng văn bản mục đích và nội dung kết nối này.") },
        ],
        onSubmit(values) {
          connState[host] = "INVESTIGATING";
          G3.audit("VIEW", "DATA_FLOW", host, values.note.slice(0, 200));
          ui.toast(t(`Investigation opened, owner ${db.user(values.owner).full_name}`, `Đã mở điều tra, phụ trách ${db.user(values.owner).full_name}`));
          G3.render();
        },
      });
    });
    bind("[data-action=block]", (el) => ui.reasonModal({
      title: t(`Block ${el.dataset.host} at the SIM gateway`, `Chặn ${el.dataset.host} tại cổng SIM`),
      intro: `<p class="small muted">${esc(t("Every device's traffic to this host is dropped at the APN. Declared flows are not affected.", "Mọi lưu lượng của thiết bị tới máy chủ này bị chặn tại APN. Các luồng đã khai báo không bị ảnh hưởng."))}</p>`,
      submitLabel: t("Block", "Chặn"),
      onSubmit(reason) { connState[el.dataset.host] = "BLOCKED"; G3.audit("VIEW", "DATA_FLOW_BLOCK", el.dataset.host, reason); ui.toast(t("Blocked at the SIM gateway", "Đã chặn tại cổng SIM")); G3.render(); },
    }));
    bind("[data-action=declare]", (el) => ui.reasonModal({
      title: t(`Declare ${el.dataset.host}`, `Khai báo ${el.dataset.host}`), danger: false, submitLabel: t("Declare", "Khai báo"),
      onSubmit(reason) { const d = DESTINATIONS.find((x) => x.host === el.dataset.host); d.declared = true; d.declared_reason = reason; ui.toast(t("Added to the declared map", "Đã thêm vào bản đồ khai báo")); G3.render(); },
    }));
    const tick = () => {
      const delay = root.querySelector("[data-live-delay]");
      if (delay) delay.textContent = fmt.num(liveDelay(), 1);
      const rate = root.querySelector("[data-live-rate]");
      if (rate) rate.textContent = fmt.num(2410 + Math.round(40 * Math.sin(Date.now() / 9000)));
      root.querySelectorAll("[data-hb]").forEach((el, i) => {
        const proc = db.system_processes.find((p) => p.name === el.dataset.hb);
        const s = (Math.floor(Date.now() / 1000) + i * 7) % (proc && proc.status !== "HEALTHY" ? 40 : 15);
        el.textContent = t(`${s} s ago`, `${s} giây trước`);
      });
      const upd = root.querySelector("[data-updated]");
      if (upd) upd.textContent = fmt.time(Date.now());
    };
    tick();
    G3.onTick(tick);
  }

  G3.page({
    id: "system",
    path: "/system",
    title: ["System health", "Sức khỏe hệ thống"],
    roles: ["OPERATIONS", "HEAD_ADMIN"],
    features: ["PLT-03", "PLT-02", "PLT-05"],
    nav: { group: "admin", order: 6, icon: "activity" },
    render: systemPage,
    mount: mountSystem,
  });
})();
