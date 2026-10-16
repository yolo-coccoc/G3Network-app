/* Charging network screens:
   - stations           /stations            STN-01, STN-04, STN-06, STN-12
   - station-detail     /stations/:id        STN-01, STN-02, STN-04, STN-05, STN-10, STN-11, STN-12, STN-13, STN-15, PAY-09
   - charging-sessions  /charging-sessions   CHG-02, CHG-04, CHG-05, CHG-06, PAY-10
   - tariffs            /tariffs             PAY-09, STN-12
   - energy             /energy              STN-14, STN-08, STN-13
   - policy             /policy              POL-01, POL-03, POL-04, POL-05, POL-06
   Vocabulary (CS-09): a "station" is the place drivers go (charging_locations,
   "trạm"), a "charger" is one OCPP device (charging_stations, "trụ"), and each
   charger has guns (connectors, "súng"). */

(function () {
  const { ui, db, fmt, scope } = G3;
  const esc = G3.esc;

  /* ---------- Small deterministic helpers ---------- */
  function hash(text) {
    let h = 2166136261;
    for (const ch of String(text)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  /** A tiny seeded generator, so the extra mock data is the same on every load. */
  function seeded(key) {
    let s = hash(key) || 1;
    return () => { s = (Math.imul(s, 1103515245) + 12345) >>> 0; return s / 4294967296; };
  }
  const r01 = (key) => hash(key) / 4294967296;
  const round1 = (v) => Math.round(v * 10) / 10;
  const hm = (d) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const toMin = (s) => { const [h, m] = s.split(":").map(Number); return h * 60 + m; };
  const timeSec = (iso) => new Date(iso).toLocaleTimeString(G3.lang === "en" ? "en-GB" : "vi-VN");
  const dayLabel = (ms) => { const d = new Date(ms); return `${d.getDate()}/${d.getMonth() + 1}`; };
  const km = (a, b) => Math.hypot((a.lat - b.lat) * 111, (a.lon - b.lon) * 109);

  /* Labels for values the shared table does not have. */
  const LOCAL_LABELS = {
    GRID: ["Grid", "Lưới điện"], SOLAR: ["Solar", "Điện mặt trời"], STORAGE: ["Storage", "Pin lưu trữ"],
    OFF_PEAK: ["Off-peak", "Thấp điểm"], NORMAL: ["Normal", "Bình thường"], PEAK: ["Peak", "Cao điểm"],
    SCHEDULED: ["Scheduled", "Đã lên lịch"], SUPERSEDED: ["Superseded", "Đã thay thế"],
    PUBLIC: ["Public", "Công cộng"], PRIVATE: ["Private", "Riêng"],
    MODEL: ["Model", "Dòng xe"], FLEET: ["Fleet", "Đội xe"], TRUCK: ["Truck", "Xe"],
    SOC_ABOVE_MAX: ["Battery above max", "Sạc vượt mức tối đa"], SOC_BELOW_MIN: ["Battery below min", "Pin dưới mức tối thiểu"],
    OUTSIDE_WINDOW: ["Outside allowed hours", "Ngoài khung giờ cho phép"], TOO_MANY_DC: ["Too many fast charges", "Sạc nhanh quá nhiều"],
    POWER_ABOVE_MAX: ["Power above max", "Công suất vượt mức"],
    UNDER_REVIEW: ["Under review", "Đang xem xét"], SENT_TO_WARRANTY: ["Sent to warranty", "Đã gửi bảo hành"],
    FAULT: ["Fault", "Lỗi"], DISCONNECTED: ["Disconnected", "Mất kết nối"], ABNORMAL_POWER: ["Abnormal power", "Công suất bất thường"],
    IN: ["Charger → CSMS", "Trụ → CSMS"], OUT: ["CSMS → charger", "CSMS → trụ"],
    OK: ["Matched", "Khớp"], FLAGGED: ["Flagged", "Lệch"], NO_TRUCK_DATA: ["No truck data", "Không có dữ liệu xe"],
  };
  const L = (v) => (LOCAL_LABELS[v] ? t(LOCAL_LABELS[v][0], LOCAL_LABELS[v][1]) : G3.label(v));
  const lb = (v, tone) => ui.badge(v, tone, L(v));

  const SOURCE_COLORS = { GRID: "#2563eb", SOLAR: "#d97706", STORAGE: "#7c3aed" };
  const STATUS_COLORS = { Available: "#0e9f6e", BUSY: "#7c3aed", Faulted: "#dc2626", Unknown: "#94a3b8" };
  const BUSY = ["Charging", "Preparing", "SuspendedEV", "SuspendedEVSE", "Finishing", "Occupied", "Reserved"];
  const REGIONS = ["TP. HCM", "Bình Dương", "Đồng Nai", "Bà Rịa – Vũng Tàu", "Long An"];
  const EXTRA_COLORS = ["#0e9f6e", "#2563eb", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d", "#64748b", "#ea580c", "#4f46e5"];

  /* Willdigits vendor codes and OCPP 1.6 error codes, translated to a readable cause (STN-05).
     The vendor's 80-code mapping is still to come (open question 4); these are illustrative. */
  const VENDOR_CAUSES = {
    "E-0x2F14": ["Insulation monitor tripped on the DC output — check the cable and the truck inlet insulation", "Giám sát cách điện ngắt ở đầu ra DC — kiểm tra cách điện cáp và cổng sạc của xe"],
    "E-0x1A03": ["Power module over temperature — check the cooling fans and air filters", "Mô-đun công suất quá nhiệt — kiểm tra quạt làm mát và lưới lọc"],
    "E-0x0C21": ["Gun lock did not engage — reseat the gun in the inlet", "Khóa súng không đóng — cắm lại súng vào cổng sạc"],
    "E-0x3305": ["DC contactor did not close — vendor service visit needed", "Khởi động từ DC không đóng — cần hãng đến xử lý"],
    "E-0x2B10": ["Output current above the limit — the truck's BMS request did not match", "Dòng ra vượt giới hạn — yêu cầu từ BMS của xe không khớp"],
    "E-0x4410": ["Lost communication with the truck (CCS PLC) — check the gun's signal pins", "Mất giao tiếp với xe (CCS PLC) — kiểm tra chân tín hiệu của súng"],
  };
  const FAULT_CODES = [["GroundFailure", "E-0x2F14"], ["HighTemperature", "E-0x1A03"], ["ConnectorLockFailure", "E-0x0C21"], ["PowerSwitchFailure", "E-0x3305"], ["OverCurrentFailure", "E-0x2B10"], ["EVCommunicationError", "E-0x4410"]];
  const causeOf = (vendorCode) => (VENDOR_CAUSES[vendorCode] ? t(VENDOR_CAUSES[vendorCode][0], VENDOR_CAUSES[vendorCode][1]) : t("Unknown vendor code — see the vendor manual", "Mã lỗi chưa có trong bảng — xem tài liệu của hãng"));

  /* ---------- Roles ---------- */
  const isOps = () => scope.isInternal() && scope.has("OPERATIONS", "HEAD_ADMIN");
  const canEditAccess = () => scope.isInternal() && scope.has("OPERATIONS", "SALES", "HEAD_ADMIN");
  const canPolicy = () => scope.isInternal() && scope.has("WARRANTY", "HEAD_ADMIN");
  const canCloseSession = () => scope.isInternal() && scope.has("OPERATIONS", "CUSTOMER_CARE", "HEAD_ADMIN");
  const me = () => { const u = scope.user(); return u ? u.user_id : null; };

  /* ---------- Extra mock data (seeded once at load) ---------- */
  // A session the charger never stopped: G3-VSI-03 lost power mid-charge and is still offline (CHG-02).
  if (!db.charging_sessions.some((s) => s.session_id === "ses-stuck-1")) {
    const v = db.vehicles.find((x) => x.organization_id === "org-mn" && x.live);
    const d = db.drivers.find((x) => x.organization_id === "org-mn" && x.status === "ACTIVE");
    const start = db.NOW - 9.4 * db.HOUR;
    db.charging_sessions.push({
      session_id: "ses-stuck-1", ocpp_transaction_id: "5099", station_id: "chg-vsip-3", location_id: "loc-vsip", connector_id: "chg-vsip-3-g1",
      vehicle_id: v ? v.vehicle_id : null, organization_id: v ? v.organization_id : "org-mn", driver_id: d ? d.driver_id : null,
      started_at: new Date(start).toISOString(), ended_at: null, status: "ACTIVE", energy_kwh: 142.6, vehicle_reported_kwh: null,
      soc_start_percent: 22, soc_end_percent: 81, peak_power_kw: 205, duration_minutes: 0, price_vnd_kwh: db.priceAt("loc-vsip", start),
      cost_vnd: null, bill_status: "QUOTED", start_method: "QR",
    });
    db.charging_sessions.sort((a, b) => (a.started_at < b.started_at ? 1 : -1));
  }
  // A truck-scoped policy for the truck with the weakest battery (POL-01: scope truck).
  if (!db.charging_policies.some((p) => p.policy_id === "pol-3")) {
    db.charging_policies.push({ policy_id: "pol-3", name: "Theo dõi đặc biệt – pin SOH thấp", scope: "TRUCK", scope_ids: ["veh-022"], soc_min: 20, soc_max: 85, allowed_windows: ["00:00–24:00"], max_dc_sessions_per_day: 1, max_power_kw: 150, version: 1, effective_from: new Date(db.NOW - 28 * db.DAY).toISOString(), status: "ACTIVE" });
  }

  /* ---------- Stations, chargers and guns ---------- */
  const chargersAt = (locId) => db.charging_stations.filter((s) => s.location_id === locId);
  const gunsAt = (locId) => chargersAt(locId).flatMap((s) => s.connectors.map((c) => ({ c, s })));
  const gunStatus = (s, c) => (s.is_online ? c.status : "Unknown");
  const activeSessionOn = (connectorId) => db.charging_sessions.find((x) => x.connector_id === connectorId && x.status === "ACTIVE");
  const chargerName = (s) => t(s.display_name[0], s.display_name[1]);

  function canUseLocation(loc) {
    if (scope.isInternal()) return true;
    const orgId = scope.org().organization_id;
    return loc.is_public || loc.organization_id === orgId || (loc.allowed_organization_ids || []).includes(orgId);
  }
  const visibleLocations = () => db.charging_locations.filter(canUseLocation);

  function gunCounts(locId) {
    const out = { total: 0, Available: 0, BUSY: 0, Faulted: 0, Unknown: 0 };
    gunsAt(locId).forEach(({ c, s }) => {
      out.total++;
      const st = gunStatus(s, c);
      if (st === "Available") out.Available++;
      else if (BUSY.includes(st)) out.BUSY++;
      else if (st === "Faulted") out.Faulted++;
      else out.Unknown++;
    });
    return out;
  }
  const maxGunPower = (locId) => Math.max(0, ...gunsAt(locId).map(({ c }) => c.max_power_kw));

  function gunCountsCell(counts) {
    const piece = (key, tone, label) => (counts[key] ? `<span title="${esc(label)}">${ui.badge(key, tone, String(counts[key]))}</span>` : "");
    const html = piece("Available", "ok", G3.label("Available")) + piece("BUSY", "violet", t("In use", "Đang dùng")) + piece("Faulted", "danger", G3.label("Faulted")) + piece("Unknown", "muted", G3.label("Unknown"));
    return `<span class="row" style="gap:4px;flex-wrap:nowrap">${html || '<span class="muted">—</span>'}</span>`;
  }

  function accessCell(loc) {
    if (loc.is_public) return lb("PUBLIC", "info");
    const names = (loc.allowed_organization_ids || []).map((id) => (db.org(id) || {}).short_name).filter(Boolean);
    return `${lb("PRIVATE", "violet")}<span class="sub">${esc(names.join(", ") || t("Owner only", "Chỉ chủ trạm"))}</span>`;
  }

  function conditionText(loc) {
    if (loc.status !== "ACTIVE") return `<span class="muted">${esc(t("Closed", "Đóng cửa"))}</span>`;
    const chargers = chargersAt(loc.location_id);
    const offline = chargers.filter((s) => !s.is_online).length;
    const counts = gunCounts(loc.location_id);
    const issues = [];
    if (offline) issues.push(t(`${offline} charger offline`, `${offline} trụ mất kết nối`));
    if (counts.Faulted) issues.push(t(`${counts.Faulted} gun faulted`, `${counts.Faulted} súng lỗi`));
    if (!chargers.length) issues.push(t("No charger yet", "Chưa có trụ"));
    return issues.length ? `<span class="sub" style="color:var(--warn)">${esc(issues.join(" · "))}</span>` : `<span class="sub">${esc(t("Open 24/7 · normal", "Mở 24/7 · bình thường"))}</span>`;
  }

  /* ---------- Tariffs (PAY-09) ---------- */
  const tariffOf = (locId) => db.tariffs.find((x) => x.location_id === locId && x.status === "ACTIVE");
  function slotAt(tariff, ms) {
    const x = hm(new Date(ms));
    return tariff.slots.find((s) => x >= s.from && x < s.to) || tariff.slots[0];
  }
  /** The lowest-price windows of the day, wrapping 22:00–24:00 + 00:00–04:00 into one. */
  function cheapest(tariff) {
    const price = Math.min(...tariff.slots.map((s) => s.price_vnd_kwh));
    const runs = [];
    tariff.slots.forEach((s) => {
      if (s.price_vnd_kwh !== price) return;
      const last = runs[runs.length - 1];
      if (last && last.to === s.from) last.to = s.to; else runs.push({ from: s.from, to: s.to });
    });
    if (runs.length > 1 && runs[0].from === "00:00" && runs[runs.length - 1].to === "24:00") {
      const tail = runs.pop();
      runs[0].from = tail.from;
    }
    return { price, windows: runs.map((r) => `${r.from}–${r.to === "24:00" ? "24:00" : r.to}`) };
  }

  /** The 00–24 h band: one segment per slot, coloured by source, striped at peak. */
  function bandHtml(slots, { height = 30, now = true, prices = true } = {}) {
    const segs = slots.map((s) => {
      const w = (toMin(s.to) - toMin(s.from)) / 14.4;
      const color = SOURCE_COLORS[s.source] || "#64748b";
      const bg = s.label === "PEAK" ? `repeating-linear-gradient(135deg, ${color} 0 7px, rgba(0,0,0,.28) 7px 10px)` : color;
      const tip = `${s.from}–${s.to} · ${L(s.source)} · ${L(s.label)} · ${fmt.num(s.price_vnd_kwh)} ₫/kWh`;
      return `<div data-tip="${esc(tip)}" style="width:${w}%;background:${bg};opacity:${s.label === "OFF_PEAK" ? 0.72 : 1};color:#fff;font-size:11px;font-weight:600;display:flex;align-items:center;justify-content:center;overflow:hidden;white-space:nowrap;border-right:1px solid var(--surface)">${prices && w > 7 ? fmt.num(s.price_vnd_kwh) : ""}</div>`;
    }).join("");
    const nowPct = (toMin(hm(new Date())) / 14.4).toFixed(2);
    const marker = now ? `<div title="${esc(t("Now", "Hiện tại"))}" style="position:absolute;top:-4px;bottom:-4px;left:${nowPct}%;width:2px;background:var(--text);border-radius:2px"></div>` : "";
    const ticks = height >= 24 ? `<div class="row between small muted" style="margin-top:4px;flex-wrap:nowrap">${[0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) => `<span>${h}h</span>`).join("")}</div>` : "";
    return `<div><div style="position:relative;display:flex;height:${height}px;border-radius:6px;overflow:visible"><div style="display:flex;width:100%;border-radius:6px;overflow:hidden">${segs}</div>${marker}</div>${ticks}</div>`;
  }
  const bandLegend = () => ui.legend([[L("GRID"), SOURCE_COLORS.GRID], [L("SOLAR"), SOURCE_COLORS.SOLAR], [L("STORAGE"), SOURCE_COLORS.STORAGE]]) + `<span class="small muted">${esc(t("Striped = peak · lighter = off-peak", "Sọc = cao điểm · nhạt = thấp điểm"))}</span>`;

  /* Version history per tariff, generated once (older versions were dearer at off-peak). */
  const tariffHistory = {};
  function historyOf(tariff) {
    if (!tariffHistory[tariff.tariff_id]) {
      const out = [];
      const start = new Date(tariff.effective_from).getTime();
      for (let v = tariff.version - 1; v >= 1; v--) {
        const age = tariff.version - v;
        out.push({
          version: v, status: "SUPERSEDED", created_by: db.people.ops.user.user_id,
          effective_from: new Date(start - age * 75 * db.DAY).toISOString(), effective_to: new Date(start - (age - 1) * 75 * db.DAY).toISOString(),
          slots: tariff.slots.map((s) => ({ ...s, price_vnd_kwh: Math.round((s.price_vnd_kwh * (1 + 0.035 * age)) / 10) * 10, source: age > 1 && s.source !== "GRID" ? "GRID" : s.source })),
        });
      }
      tariffHistory[tariff.tariff_id] = out;
    }
    // A scheduled version whose date has come becomes the active one.
    const due = tariffHistory[tariff.tariff_id].find((h) => h.status === "SCHEDULED" && new Date(h.effective_from).getTime() <= Date.now());
    if (due) activateVersion(tariff, due);
    return tariffHistory[tariff.tariff_id];
  }
  function activateVersion(tariff, entry) {
    const list = tariffHistory[tariff.tariff_id];
    list.unshift({ version: tariff.version, status: "SUPERSEDED", created_by: tariff.created_by || db.people.ops.user.user_id, effective_from: tariff.effective_from, effective_to: entry.effective_from, slots: tariff.slots });
    const i = list.indexOf(entry);
    if (i >= 0) list.splice(i, 1);
    Object.assign(tariff, { version: entry.version, slots: entry.slots, effective_from: entry.effective_from, created_by: entry.created_by });
  }

  /** The tariff version in force when a session started (PAY-10): the price billed is that version's. */
  function tariffVersionAt(locId, iso) {
    const tariff = tariffOf(locId);
    if (!tariff) return null;
    if (iso >= tariff.effective_from) return tariff;
    const old = historyOf(tariff).find((h) => h.status === "SUPERSEDED" && h.effective_from <= iso && (!h.effective_to || iso < h.effective_to));
    return old ? { ...old, name: tariff.name } : tariff;
  }

  function tariffCard(loc) {
    const tariff = tariffOf(loc.location_id);
    if (!tariff) return ui.card({ title: t("Tariff", "Biểu giá"), sub: "PAY-09", body: ui.callout("warn", esc(t("No tariff yet: drivers cannot start a charge here until operations publishes one.", "Chưa có biểu giá: tài xế chưa thể sạc tại đây cho đến khi vận hành ban hành biểu giá."))) + (G3.canSee("tariffs") ? `<div class="mt">${ui.btn(t("Open tariffs", "Mở biểu giá"), { href: `#/tariffs?loc=${loc.location_id}`, icon: "tag", sm: true })}</div>` : "") });
    historyOf(tariff);
    const slot = slotAt(tariff, Date.now());
    const low = cheapest(tariff);
    return ui.card({
      title: t("Current tariff", "Biểu giá hiện hành"), sub: `v${tariff.version} · PAY-09`,
      actions: G3.canSee("tariffs") ? ui.btn(t("Tariffs", "Biểu giá"), { href: `#/tariffs?loc=${loc.location_id}`, sm: true, kind: "ghost" }) : "",
      body: `<div class="row between"><div><div class="small muted">${esc(t("Price now", "Giá hiện tại"))}</div><div style="font-size:26px;font-weight:700" class="num">${fmt.num(slot.price_vnd_kwh)}<small class="muted" style="font-size:13px;font-weight:500"> ₫/kWh</small></div></div><div class="stack" style="gap:4px;align-items:flex-end">${lb(slot.source, slot.source === "GRID" ? "info" : slot.source === "SOLAR" ? "warn" : "violet")}${slot.label !== slot.source ? lb(slot.label, slot.label === "PEAK" ? "danger" : slot.label === "OFF_PEAK" ? "ok" : "muted") : ""}</div></div>
        <div class="mt">${bandHtml(tariff.slots, { height: 22, prices: false })}</div>
        <div class="divider"></div>
        ${ui.kv([
          [t("Until", "Đến"), slot.to],
          [t("Cheapest window", "Khung rẻ nhất"), `${esc(low.windows.join(", "))} · ${fmt.num(low.price)} ₫`],
          [t("In force since", "Hiệu lực từ"), fmt.date(tariff.effective_from)],
        ])}
        <p class="small muted" style="margin:10px 0 0">${esc(t("The price shown when a charge starts is the price billed.", "Giá hiển thị khi bắt đầu sạc là giá được tính tiền."))}</p>`,
    });
  }

  /* ---------- Shared page bits ---------- */
  function bindCommon(host) {
    host.querySelectorAll("select[data-filter], input[data-filter]").forEach((el) => el.addEventListener("change", () => G3.setQuery({ [el.dataset.filter]: el.type === "checkbox" ? (el.checked ? "1" : "") : el.value })));
    host.querySelectorAll("[data-seg]").forEach((seg) => seg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => G3.setQuery({ [seg.dataset.seg]: b.dataset.value }))));
    host.querySelectorAll("[data-tabs]").forEach((tabs) => tabs.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => G3.setQuery({ [tabs.dataset.tabs]: b.dataset.tab }))));
  }
  /** Click delegation on the page wrapper (recreated on each render, so no listener piles up). */
  function delegate(host, handlers) {
    host.addEventListener("click", (e) => {
      const el = e.target.closest("[data-action]");
      if (!el || !host.contains(el) || !handlers[el.dataset.action]) return;
      e.preventDefault();
      handlers[el.dataset.action](el, e);
    });
  }
  const checkbox = (name, checked, label) => `<label class="check small"><input type="checkbox" data-filter="${name}" ${checked ? "checked" : ""}> ${esc(label)}</label>`;
  const orgOptions = () => db.organizations.filter((o) => o.kind !== "PARTNER").map((o) => [o.organization_id, o.short_name]);
  const customerOptions = () => db.organizations.filter((o) => o.kind === "CUSTOMER").map((o) => [o.organization_id, o.short_name]);

  /* =====================================================================
     1. Stations (STN-01, STN-04, STN-06, STN-12)
     ===================================================================== */
  function filterLocations(query) {
    const q = (query.q || "").toLowerCase();
    return visibleLocations().filter((loc) => {
      const counts = gunCounts(loc.location_id);
      if (query.avail && !(loc.status === "ACTIVE" && counts.Available > 0)) return false;
      if (query.minkw && maxGunPower(loc.location_id) < Number(query.minkw)) return false;
      if (query.region && loc.region !== query.region) return false;
      if (query.access === "public" && !loc.is_public) return false;
      if (query.access === "private" && loc.is_public) return false;
      if (q && !(loc.name.toLowerCase().includes(q) || loc.address.toLowerCase().includes(q))) return false;
      return true;
    });
  }

  function priceNow(loc) {
    const tariff = tariffOf(loc.location_id);
    if (!tariff) return '<span class="muted">—</span>';
    const slot = slotAt(tariff, Date.now());
    return `<b class="num">${fmt.num(slot.price_vnd_kwh)}</b><span class="sub">${esc(L(slot.source))}${slot.label !== slot.source ? " · " + esc(L(slot.label)) : ""}</span>`;
  }

  function stationsTable(locations) {
    const columns = [
      { label: t("Station", "Trạm"), render: (l) => `<div style="min-width:170px;max-width:210px"><b>${esc(l.name)}</b><span class="sub" style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(l.address)}"><b>${esc(l.region)}</b> · ${esc(l.address)}</span></div>` },
      { label: t("Owner", "Chủ trạm"), render: (l) => ui.orgName(l.organization_id) },
      { label: t("Access", "Quyền sử dụng"), render: accessCell },
      { label: t("Chargers", "Trụ"), align: "right", render: (l) => { const ch = chargersAt(l.location_id); return `${ch.filter((s) => s.is_online).length}<span class="muted">/${ch.length}</span>`; } },
      { label: t("Guns by status", "Súng theo trạng thái"), render: (l) => gunCountsCell(gunCounts(l.location_id)) },
      { label: t("Max kW", "kW tối đa"), align: "right", render: (l) => { const p = maxGunPower(l.location_id); return p ? fmt.num(p) : "—"; } },
      { label: t("₫/kWh now", "₫/kWh hiện tại"), align: "right", render: priceNow },
      { label: t("Status", "Trạng thái"), render: (l) => `<div style="max-width:170px">${ui.badge(l.status)}${l.status_reason ? `<span class="sub">${esc(l.status_reason)}</span>` : conditionText(l)}</div>` },
    ];
    if (canEditAccess() || isOps()) {
      columns.push({ label: "", render: (l) => `<span class="row nowrap" style="gap:4px;flex-wrap:nowrap">${canEditAccess() ? ui.btn("", { icon: "edit", sm: true, kind: "ghost", action: "edit-station", attrs: `data-id="${l.location_id}" title="${esc(t("Edit", "Sửa"))}"` }) : ""}${isOps() ? (l.status === "ACTIVE" ? ui.btn("", { icon: "power", sm: true, kind: "ghost", action: "retire-station", attrs: `data-id="${l.location_id}" title="${esc(t("Retire", "Ngừng hoạt động"))}"` }) : ui.btn("", { icon: "refresh", sm: true, kind: "ghost", action: "reactivate-station", attrs: `data-id="${l.location_id}" title="${esc(t("Reactivate", "Hoạt động lại"))}"` })) : ""}</span>` });
    }
    return ui.table({ columns, rows: locations, href: (l) => `/stations/${l.location_id}`, empty: t("No station matches the filters", "Không có trạm phù hợp bộ lọc") });
  }

  function stationsMapView(locations) {
    const legend = ui.legend([[t("Gun free", "Còn súng trống"), "#0e9f6e"], [t("All guns busy", "Hết súng trống"), "#d97706"], [t("Fault or offline", "Lỗi hoặc mất kết nối"), "#dc2626"], [t("Inactive", "Ngừng hoạt động"), "#94a3b8"], [t("Private (violet dot)", "Trạm riêng (chấm tím)"), "#7c3aed"]]);
    const list = locations.map((l) => {
      const c = gunCounts(l.location_id);
      return `<li class="clickable" data-href="/stations/${l.location_id}"><span class="dot-status" style="background:${ui.stationTone(l)};margin-top:6px;border-radius:3px"></span><div class="grow"><div class="row between"><span class="title">${esc(l.name)}</span>${l.status === "ACTIVE" ? `<span class="small"><b style="color:var(--ok)">${c.Available}</b>/${c.total} ${esc(t("free", "trống"))}</span>` : ui.badge(l.status)}</div><div class="meta">${esc(l.region)} · ${fmt.num(maxGunPower(l.location_id))} kW${l.is_public ? "" : " · " + esc(L("PRIVATE"))}${tariffOf(l.location_id) ? ` · ${fmt.num(slotAt(tariffOf(l.location_id), Date.now()).price_vnd_kwh)} ₫/kWh` : ""}</div></div></li>`;
    }).join("");
    return `<div class="grid side">
      <section class="card" style="overflow:hidden">${ui.map({ locations, height: 540, legend: false, id: "stations" })}<div style="padding:10px 14px;border-top:1px solid var(--border)">${legend}</div></section>
      ${ui.card({ title: t("Stations on the map", "Trạm trên bản đồ"), sub: String(locations.length), flush: true, body: locations.length ? `<ul class="list" style="max-height:540px;overflow:auto">${list}</ul>` : ui.empty(t("No station matches the filters", "Không có trạm phù hợp bộ lọc")) })}
    </div>`;
  }

  function stationForm(loc) {
    const isNew = !loc;
    const fields = [
      { name: "name", label: t("Station name", "Tên trạm"), required: true, value: loc ? loc.name : "", placeholder: "G3 Dầu Giây" },
      { name: "address", label: t("Address", "Địa chỉ"), required: true, value: loc ? loc.address : "", full: true },
    ];
    if (isNew) {
      fields.push({ name: "place", label: t("Map position (nearest place)", "Vị trí trên bản đồ (địa danh gần nhất)"), type: "select", options: Object.entries(db.PLACES).map(([k, p]) => [k, p.name]), value: "daugiay" });
      fields.push({ name: "region", label: t("Region", "Khu vực"), type: "select", options: REGIONS.map((r) => [r, r]), value: "Đồng Nai" });
      fields.push({ name: "owner", label: t("Owner (managing unit)", "Chủ trạm (đơn vị quản lý)"), type: "select", options: orgOptions(), value: "org-g3" });
    }
    fields.push(
      { name: "contact", label: t("Incident contact", "Liên hệ khi có sự cố"), required: true, value: loc ? loc.incident_contact.split(" – ")[0] : "Trực vận hành G3" },
      { name: "phone", label: t("Contact phone", "Số điện thoại liên hệ"), type: "tel", required: true, value: loc ? (loc.incident_contact.split(" – ")[1] || "") : "", hint: t("10-digit mobile or a 1900/1800 hotline", "Di động 10 số hoặc tổng đài 1900/1800") },
      { name: "access", label: t("Access", "Quyền sử dụng"), type: "select", options: [["PUBLIC", L("PUBLIC")], ["PRIVATE", t("Private — allowed organizations only", "Riêng — chỉ tổ chức được phép")]], value: loc && !loc.is_public ? "PRIVATE" : "PUBLIC" },
      { name: "allowed", label: t("Allowed organizations (private station)", "Tổ chức được phép (trạm riêng)"), type: "checkboxes", options: customerOptions(), value: loc ? loc.allowed_organization_ids || [] : [], full: true },
    );
    if (isNew) {
      fields.push({ name: "solar_kw", label: t("Solar capacity (kWp)", "Công suất điện mặt trời (kWp)"), type: "number", value: 0 });
      fields.push({ name: "storage_kwh", label: t("Storage capacity (kWh)", "Dung lượng pin lưu trữ (kWh)"), type: "number", value: 0 });
    } else {
      fields.push({ name: "reason", label: t("Reason for the change", "Lý do thay đổi"), type: "textarea", required: true, full: true, hint: t("Saved with the change history.", "Lưu vào lịch sử thay đổi.") });
    }
    ui.formModal({
      title: isNew ? t("Add a station", "Thêm trạm sạc") : t(`Edit ${loc.name}`, `Sửa ${loc.name}`),
      intro: isNew ? ui.callout("info", esc(t("Stations are open 24/7. Chargers and guns are provisioned on the station page before they connect (STN-02).", "Trạm mở cửa 24/7. Trụ và súng được khai báo ở trang trạm trước khi kết nối (STN-02)."))) : "",
      wide: true, fields,
      submitLabel: isNew ? t("Create station", "Tạo trạm") : t("Save", "Lưu"),
      onSubmit(values) {
        const phone = values.phone.replace(/[\s.]/g, "");
        if (!/^(0\d{9}|1[89]00\d{4})$/.test(phone)) return t("Contact phone must be a 10-digit number or a 1900/1800 hotline.", "Số điện thoại phải gồm 10 chữ số hoặc là tổng đài 1900/1800.");
        if (db.charging_locations.some((l) => l !== loc && l.name.toLowerCase() === values.name.toLowerCase())) return t("Another station already has this name.", "Đã có trạm khác dùng tên này.");
        if (values.access === "PRIVATE" && !values.allowed.length) return t("A private station needs at least one allowed organization.", "Trạm riêng cần ít nhất một tổ chức được phép.");
        const contact = `${values.contact} – ${phone.length === 8 ? phone.slice(0, 4) + " " + phone.slice(4) : phone}`;
        if (isNew) {
          const place = db.PLACES[values.place];
          const id = db.uid("loc");
          db.charging_locations.push({
            location_id: id, name: values.name, place: values.place, address: values.address, organization_id: values.owner,
            is_public: values.access === "PUBLIC", allowed_organization_ids: values.access === "PRIVATE" ? Array.from(new Set([...values.allowed])) : undefined,
            chargers: 0, solar_kw: Number(values.solar_kw) || 0, storage_kwh: Number(values.storage_kwh) || 0,
            lat: place.lat + 0.006, lon: place.lon + 0.006, status: "ACTIVE", status_reason: null, incident_contact: contact, region: values.region,
          });
          ui.toast(t(`Station ${values.name} created — now provision its chargers`, `Đã tạo trạm ${values.name} — tiếp theo khai báo trụ sạc`));
          G3.navigate(`/stations/${id}`);
          return true;
        }
        Object.assign(loc, { name: values.name, address: values.address, incident_contact: contact, is_public: values.access === "PUBLIC", allowed_organization_ids: values.access === "PRIVATE" ? values.allowed : undefined, change_reason: values.reason.slice(0, 200) });
        G3.render();
        ui.toast(t("Station updated", "Đã cập nhật trạm"));
        return true;
      },
    });
  }

  function retireStation(loc) {
    ui.reasonModal({
      title: t(`Retire ${loc.name}`, `Ngừng hoạt động ${loc.name}`),
      intro: ui.callout("warn", esc(t("The station is marked inactive: it leaves the drivers' map and no new charge can start. Its chargers stay provisioned.", "Trạm chuyển sang ngừng hoạt động: không còn trên bản đồ của tài xế và không thể bắt đầu phiên sạc mới. Các trụ vẫn được giữ khai báo."))),
      submitLabel: t("Retire station", "Ngừng hoạt động"),
      onSubmit(reason) {
        loc.status = "INACTIVE"; loc.status_reason = reason;
        chargersAt(loc.location_id).forEach((s) => { s.status = "INACTIVE"; });
        G3.render();
        ui.toast(t(`${loc.name} is now inactive`, `${loc.name} đã ngừng hoạt động`));
      },
    });
  }
  function reactivateStation(loc) {
    ui.reasonModal({
      title: t(`Reactivate ${loc.name}`, `Cho ${loc.name} hoạt động lại`), danger: false,
      submitLabel: t("Reactivate", "Hoạt động lại"),
      onSubmit() {
        loc.status = "ACTIVE"; loc.status_reason = null;
        chargersAt(loc.location_id).forEach((s) => { s.status = "ACTIVE"; });
        G3.render();
        ui.toast(t(`${loc.name} is active again`, `${loc.name} đã hoạt động lại`));
      },
    });
  }

  G3.page({
    id: "stations",
    path: "/stations",
    title: ["Charging stations", "Trạm sạc"],
    roles: ["OPERATIONS", "SALES", "DISPATCHER", "FLEET_MANAGER", "ORG_ADMIN", "CUSTOMER_CARE"],
    features: ["STN-01", "STN-04", "STN-06", "STN-12"],
    nav: { group: "charging", order: 1, icon: "station" },
    render({ query }) {
      const all = visibleLocations();
      const locations = filterLocations(query);
      const view = query.view === "map" ? "map" : "list";
      const chargers = all.flatMap((l) => chargersAt(l.location_id)).filter((s) => s.status === "ACTIVE");
      const counts = all.reduce((acc, l) => { const c = gunCounts(l.location_id); Object.keys(c).forEach((k) => { acc[k] = (acc[k] || 0) + c[k]; }); return acc; }, {});
      const regions = Array.from(new Set(all.map((l) => l.region))).sort();
      return `<div data-cp>
        ${ui.pageHead({
          title: t("Charging stations", "Trạm sạc"),
          sub: esc(scope.isInternal() ? t("Every station of the network, public and private. Statuses come live from the chargers (OCPP).", "Toàn bộ trạm của mạng lưới, công cộng và riêng. Trạng thái cập nhật trực tiếp từ trụ sạc (OCPP).") : t("Public stations plus the private stations your organization is allowed to use.", "Các trạm công cộng và trạm riêng mà tổ chức của bạn được phép sử dụng.")),
          features: ["STN-01", "STN-04", "STN-06", "STN-12"],
          actions: `${ui.segmented([["list", t("Table", "Bảng")], ["map", t("Map", "Bản đồ")]], view, "view")}${isOps() ? ui.btn(t("Add a station", "Thêm trạm"), { icon: "plus", kind: "primary", action: "add-station" }) : ""}`,
        })}
        <div class="grid cols-5">
          ${ui.kpi({ label: t("Stations", "Trạm"), icon: "station", value: all.filter((l) => l.status === "ACTIVE").length, unit: `/ ${all.length}`, foot: `${all.filter((l) => !l.is_public).length} ${t("private", "riêng")} · ${all.filter((l) => l.status !== "ACTIVE").length} ${t("inactive", "ngừng")}` })}
          ${ui.kpi({ label: t("Chargers online", "Trụ trực tuyến"), icon: "signal", value: `${chargers.filter((s) => s.is_online).length}<small>/ ${chargers.length}</small>`, tone: chargers.some((s) => !s.is_online) ? "warn" : "ok", foot: t("Online = a message in the last 5 min", "Trực tuyến = có bản tin trong 5 phút") })}
          ${ui.kpi({ label: t("Guns available", "Súng sẵn sàng"), icon: "plug", value: counts.Available || 0, tone: "ok", foot: `${t("of", "trên")} ${counts.total || 0} ${t("guns", "súng")}` })}
          ${ui.kpi({ label: t("Guns charging", "Súng đang sạc"), icon: "bolt", value: counts.BUSY || 0, tone: "violet", foot: t("incl. preparing / finishing", "gồm chuẩn bị / kết thúc") })}
          ${ui.kpi({ label: t("Guns faulted", "Súng lỗi"), icon: "alert", value: counts.Faulted || 0, tone: counts.Faulted ? "danger" : undefined, foot: `${counts.Unknown || 0} ${t("unknown (charger offline)", "không rõ (trụ mất kết nối)")}` })}
        </div>
        <section class="card mt">
          <div class="toolbar">
            ${ui.search("q", query.q, t("Station name or address", "Tên trạm hoặc địa chỉ"))}
            ${checkbox("avail", query.avail, t("Available now", "Còn súng trống"))}
            ${ui.select("minkw", [["", t("Any power", "Mọi công suất")], ["120", "≥ 120 kW"], ["240", "≥ 240 kW"]], query.minkw || "")}
            ${ui.select("region", [["", t("All regions", "Mọi khu vực")], ...regions.map((r) => [r, r])], query.region || "")}
            ${ui.select("access", [["", t("Public and private", "Công cộng và riêng")], ["public", L("PUBLIC")], ["private", L("PRIVATE")]], query.access || "")}
            <span class="grow"></span>
            <span class="small muted">${locations.length} / ${all.length} ${esc(t("stations", "trạm"))}</span>
          </div>
          ${view === "list" ? stationsTable(locations) : ""}
        </section>
        ${view === "map" ? `<div class="mt">${stationsMapView(locations)}</div>` : ""}
        ${!scope.isInternal() ? `<div class="mt">${ui.callout("info", esc(t("Station details are managed by G3 Network operations. A private station appears here only if your organization is on its allowed list (STN-12).", "Thông tin trạm do bộ phận vận hành G3 Network quản lý. Trạm riêng chỉ hiện ở đây nếu tổ chức của bạn nằm trong danh sách được phép (STN-12).")))}</div>` : ""}
      </div>`;
    },
    mount(root) {
      const host = root.querySelector("[data-cp]");
      bindCommon(host);
      delegate(host, {
        "add-station": () => stationForm(null),
        "edit-station": (el) => stationForm(db.location(el.dataset.id)),
        "retire-station": (el) => retireStation(db.location(el.dataset.id)),
        "reactivate-station": (el) => reactivateStation(db.location(el.dataset.id)),
      });
      const wrap = host.querySelector("[data-map-wrap]");
      host.querySelectorAll("[data-location]").forEach((g) => g.addEventListener("click", () => { if (!wrap.dataset.justDragged) G3.navigate(`/stations/${g.dataset.location}`); }));
    },
  });

  /* =====================================================================
     2. Station detail (STN-01/02/04/05/10/11/12/13/15, PAY-09)
     ===================================================================== */
  function gunRowInner(s, c) {
    const st = gunStatus(s, c);
    let meta;
    if (st === "Charging") {
      const ses = activeSessionOn(c.connector_id);
      const v = ses && ses.vehicle_id && scope.vehicleIds().has(ses.vehicle_id) ? db.vehicle(ses.vehicle_id) : null;
      meta = `<b style="color:var(--violet)">${fmt.num(c.current_power_kw)} kW</b>${ses ? ` · ${fmt.kwh(ses.energy_kwh)} ${esc(t("so far", "đã sạc"))}` : ""}${v ? ` · ${esc(v.license_plate)}` : ""} · ${esc(t("since", "từ"))} ${fmt.time(ses ? ses.started_at : c.status_at)}`;
    } else if (st === "Faulted") {
      meta = `<span style="color:var(--danger)"><b>${esc(c.error_code || "—")}</b> · <span class="mono">${esc(c.vendor_error_code || "—")}</span></span><br>${esc(causeOf(c.vendor_error_code))}`;
    } else if (st === "Unknown") {
      meta = s.last_seen_at ? esc(t(`Charger offline — last report ${fmt.ago(s.last_seen_at)} (${G3.label(c.status === "Unknown" ? "Unknown" : c.status)})`, `Trụ mất kết nối — báo cáo cuối ${fmt.ago(s.last_seen_at)} (${G3.label(c.status === "Unknown" ? "Unknown" : c.status)})`)) : esc(t("No status reported yet — waiting for the first connection", "Chưa có trạng thái — đang chờ kết nối đầu tiên"));
    } else {
      meta = `${esc(G3.label(st))} ${esc(t("since", "từ"))} ${fmt.ago(c.status_at)}`;
    }
    const cmd = isOps() && s.status === "ACTIVE" ? ui.btn("", { icon: "settings", sm: true, kind: "ghost", action: "gun-menu", attrs: `data-station="${s.station_id}" data-gun="${c.ocpp_evse_id}" title="${esc(t("Commands for this gun", "Lệnh cho súng này"))}"` }) : "";
    return `<div class="sev-icon" style="background:${STATUS_COLORS[st] || (BUSY.includes(st) ? STATUS_COLORS.BUSY : "#94a3b8")}22;color:${STATUS_COLORS[st] || (BUSY.includes(st) ? STATUS_COLORS.BUSY : "#94a3b8")}">${ui.icon("plug", 16)}</div>
      <div class="grow"><div class="title">${esc(t("Gun", "Súng"))} ${c.ocpp_evse_id} · ${esc(c.standard)} · ${fmt.num(c.max_power_kw)} kW</div><div class="meta">${meta}</div></div>${ui.badge(st)}${cmd}`;
  }

  function chargerCard(s, internal) {
    const online = s.is_online;
    const statusBadge = !s.last_seen_at && !online ? ui.badge("PENDING", "warn", t("Awaiting first connection", "Chờ kết nối đầu tiên")) : online ? ui.badge("ACTIVE", "ok", t("Online", "Trực tuyến")) : ui.badge("OFFLINE");
    const cmds = isOps() && s.status === "ACTIVE" ? ui.btn(t("Commands", "Lệnh"), { icon: "send", sm: true, action: "charger-menu", attrs: `data-station="${s.station_id}"` }) : "";
    const kv = internal ? ui.kv([
      [t("Protocol", "Giao thức"), `OCPP ${esc(s.ocpp_version)}`],
      [t("Vendor / model", "Hãng / mẫu"), `${esc(s.vendor)} ${esc(s.model)}`],
      [t("Serial", "Số sê-ri"), `<span class="mono">${esc(s.serial_number)}</span>`],
      [t("Firmware", "Phần mềm"), s.firmware_version ? `<span class="mono">${esc(s.firmware_version)}</span>` : null],
      [t("Last seen", "Lần cuối"), `<span data-seen="${s.station_id}">${s.last_seen_at ? fmt.ago(s.last_seen_at) : "—"}</span>`],
      [t("Connected since", "Kết nối từ"), s.connected_at ? fmt.dateTime(s.connected_at) : null],
      [t("Delivered (lifetime)", "Đã cấp (tích lũy)"), `${fmt.num(s.energy_delivered_kwh_total)} kWh · ${fmt.num(s.sessions_total)} ${t("sessions", "phiên")}`],
    ]) : ui.kv([[t("Max power", "Công suất tối đa"), `${fmt.num(s.max_power_kw)} kW`], [t("Last update", "Cập nhật"), `<span data-seen="${s.station_id}">${s.last_seen_at ? fmt.ago(s.last_seen_at) : "—"}</span>`]]);
    return `<section class="card"><div class="card-head"><h3>${esc(chargerName(s))} <span class="sub mono">${esc(s.ocpp_identity)}</span></h3>${statusBadge}${cmds}</div>
      <div class="card-body">${kv}</div>
      <ul class="list" style="border-top:1px solid var(--border)">${s.connectors.map((c) => `<li data-gun-row="${c.connector_id}">${gunRowInner(s, c)}</li>`).join("") || `<li>${ui.empty(t("No gun provisioned", "Chưa khai báo súng"))}</li>`}</ul></section>`;
  }

  /* ----- Remote commands (STN-10) ----- */
  const COMMAND_TYPES = ["REMOTE_START", "REMOTE_STOP", "UNLOCK_CONNECTOR", "RESET", "CHANGE_AVAILABILITY", "GET_CONFIGURATION", "CHANGE_CONFIGURATION", "TRIGGER_MESSAGE"];
  const GUN_COMMANDS = ["REMOTE_START", "REMOTE_STOP", "UNLOCK_CONNECTOR"];

  function commandForm(loc, { stationId, gun, type } = {}) {
    const chargers = chargersAt(loc.location_id).filter((s) => s.status === "ACTIVE");
    if (!chargers.length) { ui.toast(t("No active charger at this station", "Trạm chưa có trụ hoạt động"), "alert"); return; }
    ui.formModal({
      title: t("Send a command to a charger", "Gửi lệnh tới trụ sạc"),
      intro: ui.callout("info", esc(t("The command is queued; the OCPP gateway sends it on its next poll and logs the charger's answer. A charger that is not connected in time ends NOT_SENT.", "Lệnh được xếp hàng; cổng OCPP gửi đi ở lần quét kế tiếp và ghi lại câu trả lời của trụ. Trụ không kết nối kịp sẽ có kết quả CHƯA GỬI."))),
      wide: true,
      fields: [
        { name: "station", label: t("Charger", "Trụ sạc"), type: "select", options: chargers.map((s) => [s.station_id, `${s.ocpp_identity} · ${chargerName(s)}${s.is_online ? "" : " (" + t("offline", "mất kết nối") + ")"}`]), value: stationId || chargers[0].station_id },
        { name: "type", label: t("Command", "Lệnh"), type: "select", options: COMMAND_TYPES.map((c) => [c, G3.label(c)]), value: type || "REMOTE_STOP" },
        { name: "gun", label: t("Gun", "Súng"), type: "select", options: [["0", t("Whole charger (0)", "Cả trụ (0)")], ["1", `${t("Gun", "Súng")} 1`], ["2", `${t("Gun", "Súng")} 2`], ["3", `${t("Gun", "Súng")} 3`]], value: gun !== undefined ? String(gun) : "1" },
        { name: "mode", label: t("Option", "Tùy chọn"), type: "select", options: [["", "—"], ["Soft", t("Reset: soft", "Khởi động lại: mềm")], ["Hard", t("Reset: hard", "Khởi động lại: cứng")], ["Operative", t("Availability: in service", "Trạng thái: phục vụ")], ["Inoperative", t("Availability: out of service", "Trạng thái: ngừng phục vụ")], ["StatusNotification", t("Trigger: StatusNotification", "Yêu cầu: StatusNotification")], ["MeterValues", t("Trigger: MeterValues", "Yêu cầu: MeterValues")], ["BootNotification", t("Trigger: BootNotification", "Yêu cầu: BootNotification")]], value: type === "RESET" ? "Soft" : type === "CHANGE_AVAILABILITY" ? "Inoperative" : "", hint: t("Reset, availability and trigger commands need one.", "Bắt buộc với lệnh khởi động lại, đổi trạng thái và yêu cầu gửi tin.") },
        { name: "key", label: t("Configuration key", "Khóa cấu hình"), placeholder: "HeartbeatInterval", hint: t("For get / change configuration", "Cho lệnh đọc / đổi cấu hình") },
        { name: "value", label: t("New value", "Giá trị mới"), placeholder: "300" },
        { name: "reason", label: t("Reason", "Lý do"), type: "textarea", required: true, full: true, placeholder: t("Gun 2 stuck, unlocking for the driver", "Súng 2 kẹt, mở khóa cho tài xế"), hint: t("Logged with the command (max 200 characters).", "Lưu cùng lệnh (tối đa 200 ký tự).") },
      ],
      submitLabel: t("Queue command", "Xếp hàng lệnh"),
      onSubmit(values) {
        const s = db.station(values.station);
        const gunNo = Number(values.gun);
        const c = s.connectors.find((x) => x.ocpp_evse_id === gunNo);
        if (GUN_COMMANDS.includes(values.type) && !c) return t("Choose a gun of this charger.", "Hãy chọn một súng của trụ này.");
        if (gunNo && !c) return t(`This charger has no gun ${gunNo}.`, `Trụ này không có súng ${gunNo}.`);
        if (values.type === "REMOTE_STOP" && !activeSessionOn(c.connector_id)) return t("No session is running on this gun.", "Không có phiên sạc nào đang chạy trên súng này.");
        if (values.type === "REMOTE_START" && !["Available", "Preparing"].includes(c.status)) return t("The gun must be available or preparing to start a charge.", "Súng phải sẵn sàng hoặc đang chuẩn bị mới bắt đầu sạc được.");
        if (values.type === "RESET" && !["Soft", "Hard"].includes(values.mode)) return t("Choose a soft or hard reset.", "Hãy chọn khởi động lại mềm hoặc cứng.");
        if (values.type === "CHANGE_AVAILABILITY" && !["Operative", "Inoperative"].includes(values.mode)) return t("Choose in service or out of service.", "Hãy chọn phục vụ hoặc ngừng phục vụ.");
        if (values.type === "TRIGGER_MESSAGE" && !["StatusNotification", "MeterValues", "BootNotification"].includes(values.mode)) return t("Choose the message to trigger.", "Hãy chọn bản tin cần yêu cầu.");
        if (values.type === "CHANGE_CONFIGURATION" && (!values.key || !values.value)) return t("A configuration change needs a key and a value.", "Đổi cấu hình cần có khóa và giá trị.");
        const parameters = {};
        if (values.mode) parameters[values.type === "RESET" ? "type" : values.type === "TRIGGER_MESSAGE" ? "requested_message" : "availability"] = values.mode;
        if (values.key) parameters.key = values.key;
        if (values.value && values.type === "CHANGE_CONFIGURATION") parameters.value = values.value;
        const usesGun = [...GUN_COMMANDS, "CHANGE_AVAILABILITY", "TRIGGER_MESSAGE"].includes(values.type);
        queueCommand(s, values.type, usesGun && c && gunNo ? c : null, parameters, values.reason);
        return true;
      },
    });
  }

  function queueCommand(s, type, connector, parameters, reason) {
    db.station_commands.unshift({
      command_id: db.uid("cmd"), station_id: s.station_id, connector_id: connector ? connector.connector_id : null, command_type: type,
      parameters: Object.keys(parameters || {}).length ? parameters : null, reason: (reason || "").slice(0, 200) || null,
      outcome: "PENDING", response_status: null, requested_by: me(), requested_at: new Date().toISOString(), answered_at: null,
    });
    G3.render();
    ui.toast(t(`${G3.label(type)} queued for ${s.ocpp_identity}`, `Đã xếp hàng lệnh ${G3.label(type)} cho ${s.ocpp_identity}`), "", "send");
  }

  /** Charger's answer arrives ~2 s later; the effect on the guns is applied here. */
  function resolveCommand(cmd) {
    if (cmd.outcome !== "PENDING") return;
    const s = db.station(cmd.station_id);
    const c = cmd.connector_id ? s.connectors.find((x) => x.connector_id === cmd.connector_id) : null;
    const now = new Date().toISOString();
    const p = cmd.parameters || {};
    cmd.answered_at = now;
    if (!s.is_online) { cmd.outcome = "NOT_SENT"; cmd.answered_at = null; }
    else if (cmd.command_type === "UNLOCK_CONNECTOR" && c && c.status === "Charging") { cmd.outcome = "REJECTED"; cmd.response_status = "UnlockFailed"; }
    else {
      cmd.outcome = "ACCEPTED"; cmd.response_status = "Accepted";
      if (cmd.command_type === "REMOTE_STOP" && c) {
        const ses = activeSessionOn(c.connector_id);
        if (ses) Object.assign(ses, { status: "COMPLETED", ended_at: now, duration_minutes: Math.round((Date.now() - new Date(ses.started_at).getTime()) / db.MIN), cost_vnd: Math.round(ses.energy_kwh * ses.price_vnd_kwh), bill_status: "BILLED" });
        Object.assign(c, { status: "Finishing", current_power_kw: 0, status_at: now });
      } else if (cmd.command_type === "REMOTE_START" && c) {
        Object.assign(c, { status: "Preparing", status_at: now });
      } else if (cmd.command_type === "CHANGE_AVAILABILITY") {
        (c ? [c] : s.connectors).forEach((x) => {
          if (p.availability === "Inoperative" && x.status !== "Charging") Object.assign(x, { status: "Unavailable", status_at: now });
          if (p.availability === "Operative" && x.status === "Unavailable") Object.assign(x, { status: "Available", status_at: now });
        });
        if (p.availability === "Inoperative" && (c ? c.status === "Charging" : s.connectors.some((x) => x.status === "Charging"))) cmd.response_status = "Scheduled";
      } else if (cmd.command_type === "RESET") {
        s.connected_at = now;
        if (p.type === "Hard") s.connectors.forEach((x) => { if (x.status === "Faulted") Object.assign(x, { status: "Available", error_code: null, vendor_error_code: null, status_at: now }); });
      } else if (cmd.command_type === "GET_CONFIGURATION") {
        cmd.answer = p.key ? `${p.key}=${p.key === "HeartbeatInterval" ? "300" : p.key === "MeterValueSampleInterval" ? "60" : "—"}` : "HeartbeatInterval=300, MeterValueSampleInterval=60, ConnectionTimeOut=90, AuthorizeRemoteTxRequests=false, NumberOfConnectors=" + s.connectors.length;
      } else if (cmd.command_type === "CHANGE_CONFIGURATION") {
        cmd.answer = `${p.key}=${p.key === "AuthorizationKey" ? "••••" : p.value}`;
      }
    }
    G3.render();
    const ok = cmd.outcome === "ACCEPTED";
    ui.toast(`<b>${esc(s.ocpp_identity)}</b> · ${esc(G3.label(cmd.command_type))}: ${esc(G3.label(cmd.outcome))}${cmd.response_status && cmd.response_status !== "Accepted" ? ` (${esc(cmd.response_status)})` : ""}`, ok ? "" : "alert", ok ? "check" : "alert");
  }
  function scheduleCommandAnswers() {
    db.station_commands.filter((c) => c.outcome === "PENDING").forEach((cmd) => {
      const wait = Math.max(300, 2000 - (Date.now() - new Date(cmd.requested_at).getTime()));
      const timer = setTimeout(() => resolveCommand(cmd), wait);
      G3.onLeave(() => clearTimeout(timer));
    });
  }

  function commandsTab(loc) {
    const ids = new Set(chargersAt(loc.location_id).map((s) => s.station_id));
    const rows = db.station_commands.filter((c) => ids.has(c.station_id));
    return `${ui.featureChips(["STN-10"])}<section class="card">
      <div class="toolbar"><span class="small muted grow">${esc(t("Every command and its answer is logged. Manual commands carry the operator's reason.", "Mọi lệnh và câu trả lời đều được lưu. Lệnh thủ công kèm lý do của nhân viên vận hành."))}</span>${isOps() ? ui.btn(t("Send command", "Gửi lệnh"), { icon: "send", kind: "primary", sm: true, action: "send-command" }) : ""}</div>
      ${ui.table({
        rows, empty: t("No command sent to this station's chargers yet", "Chưa gửi lệnh nào tới các trụ của trạm"),
        columns: [
          { label: t("Requested", "Yêu cầu lúc"), render: (c) => `${fmt.dateTime(c.requested_at)}<span class="sub">${fmt.ago(c.requested_at)}</span>` },
          { label: t("Charger", "Trụ"), render: (c) => `<span class="mono">${esc(db.station(c.station_id).ocpp_identity)}</span>` },
          { label: t("Gun", "Súng"), render: (c) => { const gun = c.connector_id ? db.station(c.station_id).connectors.find((x) => x.connector_id === c.connector_id) : null; return gun ? String(gun.ocpp_evse_id) : '<span class="muted">—</span>'; } },
          { label: t("Command", "Lệnh"), render: (c) => `<b>${esc(G3.label(c.command_type))}</b>${c.parameters ? `<span class="sub mono">${esc(Object.entries(c.parameters).map(([k, v]) => `${k}=${v}`).join(" "))}</span>` : ""}` },
          { label: t("Reason", "Lý do"), render: (c) => (c.reason ? esc(c.reason) : c.requested_by ? '<span class="muted">—</span>' : `<span class="muted">${esc(t("Routine (system)", "Thường lệ (hệ thống)"))}</span>`) },
          { label: t("By", "Người gửi"), render: (c) => ui.userName(c.requested_by) },
          { label: t("Outcome", "Kết quả"), render: (c) => `${ui.badge(c.outcome)}${c.response_status && c.response_status !== "Accepted" ? `<span class="sub mono">${esc(c.response_status)}</span>` : ""}` },
          { label: t("Answer", "Trả lời"), render: (c) => (c.outcome === "PENDING" ? `<span class="muted">${esc(t("waiting…", "đang chờ…"))}</span>` : c.answered_at ? `${fmt.num((new Date(c.answered_at) - new Date(c.requested_at)) / 1000, 1)} s${c.answer ? `<span class="sub mono">${esc(c.answer)}</span>` : ""}` : '<span class="muted">—</span>') },
        ],
      })}</section>`;
  }

  /* ----- Fault alerts (STN-05) ----- */
  function faultHistory(loc) {
    const rows = [];
    chargersAt(loc.location_id).forEach((s) => {
      if (!s.last_seen_at) return; // provisioned, never connected: no history yet
      s.connectors.forEach((c) => { if (c.status === "Faulted") rows.push({ at: c.status_at, s, gun: c.ocpp_evse_id, kind: "FAULT", error_code: c.error_code, vendor_code: c.vendor_error_code, open: true }); });
      if (!s.is_online && s.last_seen_at && s.status === "ACTIVE") rows.push({ at: new Date(new Date(s.last_seen_at).getTime() + 5 * db.MIN).toISOString(), s, gun: null, kind: "DISCONNECTED", open: true });
      const rnd = seeded(s.station_id + "faults");
      const n = 1 + Math.floor(rnd() * 3);
      for (let k = 0; k < n; k++) {
        const roll = rnd();
        const [code, vendor] = FAULT_CODES[Math.floor(rnd() * FAULT_CODES.length)];
        const kind = roll < 0.2 ? "ABNORMAL_POWER" : roll < 0.38 ? "DISCONNECTED" : "FAULT";
        rows.push({ at: new Date(db.NOW - (1 + rnd() * 28) * db.DAY).toISOString(), s, gun: kind === "DISCONNECTED" ? null : 1 + Math.floor(rnd() * s.connectors.length), kind, error_code: kind === "FAULT" ? code : null, vendor_code: kind === "FAULT" ? vendor : null, open: false, duration: Math.round(8 + rnd() * 240) });
      }
    });
    return rows.sort((a, b) => (a.at < b.at ? 1 : -1));
  }

  function faultsTab(loc) {
    const rows = faultHistory(loc);
    const open = rows.filter((r) => r.open);
    const closed = rows.filter((r) => !r.open);
    const mttr = closed.length ? closed.reduce((a, r) => a + r.duration, 0) / closed.length : 0;
    return `${ui.featureChips(["STN-05"])}<div class="grid cols-4 mb">
        ${ui.kpi({ label: t("Open faults", "Lỗi đang mở"), icon: "alert", value: open.length, tone: open.length ? "danger" : "ok" })}
        ${ui.kpi({ label: t("Faults (30 d)", "Lỗi (30 ngày)"), icon: "activity", value: rows.length })}
        ${ui.kpi({ label: t("Mean time to repair", "Thời gian khắc phục TB"), icon: "clock", value: fmt.duration(mttr) })}
        ${ui.kpi({ label: t("Alerts go to", "Cảnh báo gửi tới"), icon: "phone", value: `<span style="font-size:14px">${esc(loc.incident_contact)}</span>`, foot: `${t("and the operations staff of", "và nhân viên vận hành của")} ${ui.orgName(loc.organization_id)}` })}
      </div>
      <section class="card">${ui.table({
        rows, empty: t("No fault in the last 30 days", "Không có lỗi trong 30 ngày"),
        columns: [
          { label: t("Raised", "Phát sinh"), render: (r) => `${fmt.dateTime(r.at)}<span class="sub">${fmt.ago(r.at)}</span>` },
          { label: t("Charger / gun", "Trụ / súng"), render: (r) => `<span class="mono">${esc(r.s.ocpp_identity)}</span>${r.gun ? `<span class="sub">${esc(t("Gun", "Súng"))} ${r.gun}</span>` : `<span class="sub">${esc(t("Whole charger", "Cả trụ"))}</span>`}` },
          { label: t("Kind", "Loại"), render: (r) => lb(r.kind, r.kind === "FAULT" ? "danger" : "warn") },
          { label: t("OCPP / vendor code", "Mã OCPP / hãng"), render: (r) => (r.error_code ? `<b>${esc(r.error_code)}</b><span class="sub mono">${esc(r.vendor_code)}</span>` : '<span class="muted">—</span>') },
          { label: t("Readable cause", "Nguyên nhân"), render: (r) => esc(r.kind === "FAULT" ? causeOf(r.vendor_code) : r.kind === "DISCONNECTED" ? t("No message from the charger for more than 5 minutes — check power and the 4G router", "Không nhận được bản tin từ trụ quá 5 phút — kiểm tra nguồn điện và bộ phát 4G") : t("Delivered power below 50% of the offered limit for 15 min — check the power modules", "Công suất cấp dưới 50% mức cho phép trong 15 phút — kiểm tra mô-đun công suất")) },
          { label: t("Routed to", "Đã báo"), render: () => `<span class="small">${esc(loc.incident_contact)}</span>` },
          { label: t("Status", "Trạng thái"), render: (r) => (r.open ? ui.badge("OPEN") : `${ui.badge("RESOLVED")}<span class="sub">${esc(t("after", "sau"))} ${fmt.duration(r.duration)}</span>`) },
        ],
      })}</section>`;
  }

  /* ----- OCPP message log (STN-15) ----- */
  // Card IDs never leave this function unmasked: only the last two characters are kept.
  const maskToken = (rnd) => "••••••••" + "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"[Math.floor(rnd() * 32)] + "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"[Math.floor(rnd() * 32)];
  function messageLog(s) {
    const rnd = seeded(s.station_id + "frames");
    const v201 = s.ocpp_version === "2.0.1";
    // Anchored to page load (online) or the last message (offline), so the log does not shift on each tick.
    const end = !s.last_seen_at ? null : s.is_online ? db.NOW : new Date(s.last_seen_at).getTime();
    if (!end) return [];
    const rows = [];
    const msgId = () => Array.from({ length: 8 }, () => "0123456789abcdef"[Math.floor(rnd() * 16)]).join("") + "-" + Array.from({ length: 4 }, () => "0123456789abcdef"[Math.floor(rnd() * 16)]).join("");
    let at = end - 70 * db.MIN;
    const pair = (action, payload, answer, dir = "IN") => {
      const id = msgId();
      rows.push({ at: new Date(at).toISOString(), dir, type: 2, action, frame: [2, id, action, payload] });
      rows.push({ at: new Date(at + 180 + rnd() * 600).toISOString(), dir: dir === "IN" ? "OUT" : "IN", type: 3, action, frame: [3, id, answer] });
      at += (1 + rnd() * 3) * db.MIN;
    };
    const iso = () => new Date(at).toISOString();
    const gun = 1 + Math.floor(rnd() * Math.max(1, s.connectors.length));
    let meter = 18000000 + Math.floor(rnd() * 9000000);
    const tx = 5200 + Math.floor(rnd() * 300);
    const txUuid = msgId() + "-" + msgId();
    pair("Heartbeat", {}, { currentTime: iso() });
    if (v201) pair("StatusNotification", { timestamp: iso(), connectorStatus: "Occupied", evseId: gun, connectorId: 1 }, {});
    else pair("StatusNotification", { connectorId: gun, errorCode: "NoError", status: "Preparing", timestamp: iso() }, {});
    const token = maskToken(rnd);
    if (v201) pair("RequestStartTransaction", { idToken: { idToken: token, type: "Central" }, remoteStartId: 1000 + Math.floor(rnd() * 900) }, { status: "Accepted" }, "OUT");
    else pair("RemoteStartTransaction", { idTag: token }, { status: "Accepted" }, "OUT");
    if (v201) pair("TransactionEvent", { eventType: "Started", timestamp: iso(), triggerReason: "RemoteStart", seqNo: 0, transactionInfo: { transactionId: txUuid, chargingState: "Charging" }, idToken: { idToken: token, type: "Central" }, evse: { id: gun, connectorId: 1 } }, { idTokenInfo: { status: "Accepted" } });
    else pair("StartTransaction", { connectorId: gun, idTag: token, meterStart: meter, timestamp: iso() }, { transactionId: tx, idTagInfo: { status: "Accepted" } });
    for (let k = 0; k < 5; k++) {
      meter += Math.floor(14000 + rnd() * 9000);
      const sampled = [{ value: String(meter), measurand: "Energy.Active.Import.Register", unit: "Wh" }, { value: String(Math.floor(150000 + rnd() * 70000)), measurand: "Power.Active.Import", unit: "W" }, { value: String(38 + k * 9), measurand: "SoC", unit: "Percent" }];
      if (v201) pair("TransactionEvent", { eventType: "Updated", timestamp: iso(), triggerReason: "MeterValuePeriodic", seqNo: k + 1, transactionInfo: { transactionId: txUuid, chargingState: "Charging" }, meterValue: [{ timestamp: iso(), sampledValue: sampled }] }, {});
      else pair("MeterValues", { connectorId: gun, transactionId: tx, meterValue: [{ timestamp: iso(), sampledValue: sampled }] }, {});
      if (k === 2) pair("Heartbeat", {}, { currentTime: iso() });
    }
    if (v201) pair("TransactionEvent", { eventType: "Ended", timestamp: iso(), triggerReason: "StopAuthorized", seqNo: 6, transactionInfo: { transactionId: txUuid, stoppedReason: "Local" }, idToken: { idToken: token, type: "Central" } }, {});
    else pair("StopTransaction", { transactionId: tx, idTag: token, meterStop: meter, timestamp: iso(), reason: "Local" }, { idTagInfo: { status: "Accepted" } });
    const faulted = s.connectors.find((c) => c.status === "Faulted");
    if (faulted && !v201) pair("StatusNotification", { connectorId: faulted.ocpp_evse_id, errorCode: faulted.error_code, vendorErrorCode: faulted.vendor_error_code, status: "Faulted", timestamp: iso() }, {});
    else if (v201) pair("StatusNotification", { timestamp: iso(), connectorStatus: "Available", evseId: gun, connectorId: 1 }, {});
    else pair("StatusNotification", { connectorId: gun, errorCode: "NoError", status: "Available", timestamp: iso() }, {});
    // Heartbeats every 5 minutes (HeartbeatInterval=300) until the last message.
    while (at < end) { pair("Heartbeat", {}, { currentTime: iso() }); at += 3 * db.MIN; }
    return rows.filter((r) => new Date(r.at).getTime() <= end + 1000).reverse();
  }

  let shownLog = [];
  function messagesTab(loc, query) {
    const chargers = chargersAt(loc.location_id);
    if (!chargers.length) return ui.empty(t("No charger provisioned", "Chưa khai báo trụ"));
    const s = chargers.find((x) => x.station_id === query.charger) || chargers[0];
    const all = messageLog(s);
    shownLog = all;
    const actions = Array.from(new Set(all.map((r) => r.action))).sort();
    const rows = all.filter((r) => (!query.action || r.action === query.action) && (!query.dir || r.dir === query.dir));
    return `${ui.featureChips(["STN-15"])}<div class="grid side-l">
      <div class="stack">
        ${ui.card({ title: t("Charger", "Trụ sạc"), body: `<div class="stack" style="gap:8px">${chargers.map((x) => `<button class="btn ${x.station_id === s.station_id ? "primary" : ""}" data-action="pick-charger" data-id="${x.station_id}" style="justify-content:space-between"><span class="mono">${esc(x.ocpp_identity)}</span><span class="small">OCPP ${esc(x.ocpp_version)}</span></button>`).join("")}</div>` })}
        ${ui.card({ title: t("Privacy and retention", "Bảo mật và lưu trữ"), body: `${ui.kv([[t("Card IDs", "Mã thẻ"), esc(t("Masked (idTag / idToken)", "Đã che (idTag / idToken)"))], [t("Kept for", "Lưu trong"), esc(t("90 days, then deleted", "90 ngày, sau đó xóa"))], [t("Oldest message", "Bản tin cũ nhất"), fmt.date(db.NOW - 89 * db.DAY)], [t("Due for deletion", "Sắp xóa"), `${fmt.num(1200 + (hash(s.station_id) % 900))} ${t("messages tonight", "bản tin đêm nay")}`]])}<p class="small muted" style="margin:10px 0 0">${esc(t("Raw frames are never copied into application logs.", "Khung bản tin gốc không bao giờ được chép vào nhật ký ứng dụng."))}</p>` })}
      </div>
      <section class="card">
        <div class="toolbar">
          ${ui.select("action", [["", t("All messages", "Mọi bản tin")], ...actions.map((a) => [a, a])], query.action || "")}
          ${ui.select("dir", [["", t("Both directions", "Cả hai chiều")], ["IN", L("IN")], ["OUT", L("OUT")]], query.dir || "")}
          <span class="grow"></span><span class="small muted">${rows.length} ${esc(t("latest messages", "bản tin gần nhất"))}${s.is_online ? "" : " · " + esc(t("charger offline", "trụ mất kết nối"))}</span>
        </div>
        ${ui.table({
          rows: rows.slice(0, 80), empty: t("No message", "Không có bản tin"),
          rowAttrs: (r) => `data-action="frame" data-i="${all.indexOf(r)}" style="cursor:pointer"`,
          columns: [
            { label: t("Time", "Thời gian"), render: (r) => `<span class="mono">${esc(timeSec(r.at))}</span>` },
            { label: t("Direction", "Chiều"), render: (r) => lb(r.dir, r.dir === "IN" ? "info" : "violet") },
            { label: t("Action", "Bản tin"), render: (r) => `<b>${esc(r.action)}</b><span class="sub mono">${r.type === 2 ? "CALL" : "CALLRESULT"}</span>` },
            { label: t("Payload", "Nội dung"), render: (r) => { const text = JSON.stringify(r.frame[r.type === 2 ? 3 : 2]); return `<span class="mono small" style="display:inline-block;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom">${esc(text)}</span>`; } },
          ],
        })}
      </section></div>`;
  }

  /* ----- Security (STN-11) ----- */
  const securityState = {};
  function securityOf(s) {
    if (!securityState[s.station_id]) {
      const r = r01(s.station_id + "sec");
      const pwAt = new Date(db.NOW - (20 + Math.floor(r * 200)) * db.DAY).toISOString();
      securityState[s.station_id] = s.ocpp_version === "2.0.1"
        ? { scheme: "wss", profile: r < 0.5 ? 3 : 2, password_set_at: pwAt, cert: r < 0.5 ? { cn: s.ocpp_identity, issuer: "G3 Network Charger CA", expires_at: new Date(db.NOW + (40 + Math.floor(r * 600)) * db.DAY).toISOString() } : null }
        : r < 0.55 ? { scheme: "ws", profile: 1, password_set_at: r < 0.3 ? pwAt : null, cert: null } : { scheme: "wss", profile: 2, password_set_at: pwAt, cert: null };
    }
    return securityState[s.station_id];
  }
  const PROFILE_LABELS = { 1: ["1 · No TLS", "1 · Không mã hóa"], 2: ["2 · TLS + password", "2 · TLS + mật khẩu"], 3: ["3 · TLS + certificate", "3 · TLS + chứng thư"] };

  function securityTab(loc) {
    const chargers = chargersAt(loc.location_id);
    const weak = chargers.filter((s) => securityOf(s).scheme === "ws");
    return `${ui.featureChips(["STN-11"])}${weak.length ? ui.callout("danger", esc(t(`${weak.length} charger(s) still connect over ws:// without encryption. Real deployment needs wss:// and a password or certificate per charger.`, `${weak.length} trụ vẫn kết nối qua ws:// không mã hóa. Triển khai thật cần wss:// và mật khẩu hoặc chứng thư riêng cho từng trụ.`))) : ui.callout("ok", esc(t("Every charger connects over wss:// and authenticates.", "Mọi trụ đều kết nối qua wss:// và có xác thực.")))}
      <section class="card mt">${ui.table({
        rows: chargers, empty: t("No charger provisioned", "Chưa khai báo trụ"),
        columns: [
          { label: t("Charger", "Trụ"), render: (s) => `<span class="mono nowrap">${esc(s.ocpp_identity)}</span><span class="sub nowrap">OCPP ${esc(s.ocpp_version)}</span>` },
          { label: t("Endpoint", "Địa chỉ kết nối"), render: (s) => { const sec = securityOf(s); return `<b class="mono" style="color:var(--${sec.scheme === "wss" ? "ok" : "danger"})">${sec.scheme}://</b><span class="sub mono nowrap">${sec.scheme === "wss" ? "ocpp.g3network.vn/ocpp/" : "ocpp.g3network.vn:9000/"}${esc(s.ocpp_identity)}</span>`; } },
          { label: t("Security profile", "Mức bảo mật"), render: (s) => { const p = securityOf(s).profile; return ui.badge(String(p), p === 1 ? "danger" : p === 2 ? "info" : "ok", t(PROFILE_LABELS[p][0], PROFILE_LABELS[p][1])); } },
          { label: t("Password", "Mật khẩu"), render: (s) => { const sec = securityOf(s); return sec.password_set_at ? `${ui.badge("OK", "ok", t("Set", "Đã đặt"))}<span class="sub">${esc(t("rotated", "đổi lúc"))} ${fmt.date(sec.password_set_at)}</span>` : ui.badge("NONE", "danger", t("Not set", "Chưa đặt")); } },
          { label: t("Client certificate", "Chứng thư"), render: (s) => { const c = securityOf(s).cert; if (!c) return '<span class="muted">—</span>'; const days = Math.round((new Date(c.expires_at) - Date.now()) / db.DAY); return `<span class="mono small nowrap">CN=${esc(c.cn)}</span><span class="sub nowrap" style="${days < 60 ? "color:var(--warn)" : ""}">${esc(t(`expires in ${days} d`, `hết hạn sau ${days} ngày`))}</span>`; } },
          { label: t("Assessment", "Đánh giá"), render: (s) => (securityOf(s).scheme === "ws" ? ui.badge("BLOCKED", "danger", t("Not production-ready", "Chưa đạt")) : ui.badge("OK", "ok", t("Ready", "Đạt"))) },
          ...(isOps() ? [{ label: "", render: (s) => `<span class="row" style="gap:4px;flex-wrap:nowrap">${ui.btn("", { sm: true, icon: "key", action: "set-password", attrs: `data-id="${s.station_id}" title="${esc(t("Set password", "Đặt mật khẩu"))}"` })}${securityOf(s).scheme === "ws" ? ui.btn("wss", { sm: true, icon: "lock", kind: "primary", action: "upgrade-wss", attrs: `data-id="${s.station_id}" title="${esc(t("Move to wss://", "Chuyển sang wss://"))}"` }) : ""}</span>` }] : []),
        ],
      })}</section>`;
  }

  function setPasswordForm(s) {
    ui.formModal({
      title: t(`Charger password · ${s.ocpp_identity}`, `Mật khẩu trụ · ${s.ocpp_identity}`),
      intro: ui.callout("info", esc(t("Sent to the charger with ChangeConfiguration (AuthorizationKey) over the current connection, then stored hashed. It is never shown again.", "Gửi tới trụ bằng ChangeConfiguration (AuthorizationKey) qua kết nối hiện tại, sau đó lưu dạng băm. Mật khẩu không bao giờ hiển thị lại."))),
      fields: [
        { name: "password", label: t("New password", "Mật khẩu mới"), type: "password", required: true, hint: t("16–40 letters and digits", "16–40 chữ cái và chữ số") },
        { name: "confirm", label: t("Repeat password", "Nhập lại mật khẩu"), type: "password", required: true },
      ],
      submitLabel: t("Send to charger", "Gửi tới trụ"),
      onSubmit(values) {
        if (!/^[A-Za-z0-9]{16,40}$/.test(values.password)) return t("Use 16 to 40 letters and digits.", "Dùng từ 16 đến 40 chữ cái và chữ số.");
        if (values.password !== values.confirm) return t("The two passwords differ.", "Hai mật khẩu không khớp.");
        securityOf(s).password_set_at = new Date().toISOString();
        queueCommand(s, "CHANGE_CONFIGURATION", null, { key: "AuthorizationKey", value: "••••" }, t("Rotate the charger password (STN-11)", "Đổi mật khẩu trụ (STN-11)"));
        return true;
      },
    });
  }
  function upgradeWss(s) {
    const sec = securityOf(s);
    if (!sec.password_set_at) { ui.toast(t("Set a password first: profile 2 needs TLS and a password.", "Hãy đặt mật khẩu trước: mức 2 cần TLS và mật khẩu."), "alert"); return; }
    Object.assign(sec, { scheme: "wss", profile: 2 });
    queueCommand(s, "CHANGE_CONFIGURATION", null, { key: "SecurityProfile", value: "2" }, t("Move to wss:// (security profile 2)", "Chuyển sang wss:// (mức bảo mật 2)"));
  }

  /* ----- Green generation and storage (STN-13) ----- */
  function greenProfile(loc) {
    const rnd = seeded(loc.location_id + "solar");
    const hourNow = new Date().getHours();
    const energy = db.stationEnergy(loc.location_id, 14);
    const solar = [], forecast = [], load = [], storage = [];
    let soc = 32;
    for (let h = 0; h < 24; h++) {
      const sun = Math.max(0, Math.sin((Math.PI * (h - 6)) / 12));
      const base = loc.solar_kw * Math.pow(sun, 1.3) * 0.82;
      const actual = Math.round(base * (0.72 + rnd() * 0.28));
      solar.push(h <= hourNow ? actual : null);
      forecast.push(Math.round(base * (0.8 + rnd() * 0.15)));
      // Charging load: off-peak night and midday humps plus the station's own session history.
      load.push(0.5 + (h >= 22 || h <= 4 ? 1 : 0) + (h >= 10 && h <= 14 ? 0.8 : 0) + energy.hourly[h].energy_kwh / (1 + Math.max(...energy.hourly.map((x) => x.energy_kwh))));
      if (loc.storage_kwh) {
        if (h >= 8 && h <= 14) soc = Math.min(96, soc + 9);
        else if (h >= 17 && h <= 21) soc = Math.max(18, soc - 13);
        else if (h >= 22 || h <= 3) soc = Math.min(45, soc + 2);
        storage.push(h <= hourNow ? soc : null);
      }
    }
    const scale = (chargersAt(loc.location_id).length * 650) / load.reduce((a, v) => a + v, 0);
    const loadKw = load.map((v) => Math.round(v * scale));
    const discharge = (h) => (loc.storage_kwh && h >= 17 && h <= 21 ? loc.storage_kwh * 0.13 : 0);
    let green = 0, used = 0;
    for (let h = 0; h <= hourNow; h++) { green += Math.min(loadKw[h], (solar[h] || 0) + discharge(h)); used += loadKw[h]; }
    return { solar, forecast, load: loadKw, storage, hourNow, share: used ? (green / used) * 100 : 0 };
  }

  function greenTab(loc) {
    if (!loc.solar_kw && !loc.storage_kwh) return `${ui.featureChips(["STN-13"])}${ui.card({ title: t("Power sources", "Nguồn điện"), body: `${ui.kv([[L("GRID"), esc(t("EVN 22 kV, contract capacity 2,000 kVA", "EVN 22 kV, công suất hợp đồng 2.000 kVA"))]])}<div class="mt">${ui.callout("info", esc(t("Grid only: no solar or storage at this station yet.", "Chỉ dùng điện lưới: trạm chưa có điện mặt trời hay pin lưu trữ.")))}</div>` })}`;
    const g = greenProfile(loc);
    const solarToday = g.solar.reduce((a, v) => a + (v || 0), 0);
    const share = g.share;
    const labels = Array.from({ length: 24 }, (_, h) => `${h}h`);
    const storageNow = g.storage.filter((v) => v !== null).slice(-1)[0];
    return `${ui.featureChips(["STN-13"])}<div class="grid cols-4">
        ${ui.kpi({ label: t("Solar capacity", "Công suất mặt trời"), icon: "sun", value: fmt.num(loc.solar_kw), unit: "kWp" })}
        ${ui.kpi({ label: t("Solar output today", "Sản lượng mặt trời hôm nay"), icon: "bolt", value: fmt.num(solarToday), unit: "kWh", foot: `${t("forecast", "dự báo")} ${fmt.num(g.forecast.reduce((a, v) => a + v, 0))} kWh ${t("for the full day", "cả ngày")}` })}
        ${ui.kpi({ label: t("Storage", "Pin lưu trữ"), icon: "battery", value: loc.storage_kwh ? fmt.pct(storageNow) : "—", unit: "", foot: loc.storage_kwh ? `${fmt.num(loc.storage_kwh)} kWh ${t("capacity", "dung lượng")}` : t("No storage", "Không có pin lưu trữ") })}
        ${ui.kpi({ label: t("Green share of charging", "Tỷ lệ điện xanh khi sạc"), icon: "leaf", value: fmt.pct(share), tone: "ok", foot: t("solar and storage used ÷ energy delivered today", "điện mặt trời và pin lưu trữ dùng ÷ điện cấp hôm nay") })}
      </div>
      <div class="grid side mt">
        ${ui.card({ title: t("Hourly output today", "Sản lượng theo giờ hôm nay"), sub: "kW", actions: ui.legend([[t("Solar (measured)", "Mặt trời (đo)"), SOURCE_COLORS.SOLAR], [t("Solar forecast", "Dự báo mặt trời"), "#f59e0b"], [t("Charging load", "Tải sạc"), SOURCE_COLORS.GRID]]), body: ui.lineChart({ labels, height: 230, unit: " kW", series: [{ name: t("Solar", "Mặt trời"), values: g.solar, color: SOURCE_COLORS.SOLAR, area: true }, { name: t("Forecast", "Dự báo"), values: g.forecast, color: "#f59e0b", dashed: true }, { name: t("Load", "Tải"), values: g.load, color: SOURCE_COLORS.GRID }] }) })}
        ${ui.card({ title: t("Storage charge level", "Mức sạc pin lưu trữ"), sub: "%", body: loc.storage_kwh ? `${ui.lineChart({ labels, height: 160, yMin: 0, yMax: 100, unit: "%", series: [{ name: "SOC", values: g.storage, color: SOURCE_COLORS.STORAGE, area: true }] })}<p class="small muted" style="margin:8px 0 0">${esc(t("Charges from solar at midday, discharges at the 17:00–20:00 peak (tariff source STORAGE).", "Sạc từ điện mặt trời buổi trưa, xả vào cao điểm 17:00–20:00 (nguồn STORAGE trong biểu giá)."))}</p>` : ui.empty(t("No storage at this station", "Trạm không có pin lưu trữ")) })}
      </div>
      ${ui.card({ title: t("Power sources", "Nguồn điện"), cls: "mt", body: ui.kv([[L("GRID"), esc(t("EVN 22 kV, contract capacity 2,000 kVA", "EVN 22 kV, công suất hợp đồng 2.000 kVA"))], loc.solar_kw ? [L("SOLAR"), esc(t(`Rooftop PV ${fmt.num(loc.solar_kw)} kWp over the parking bays`, `Điện mặt trời áp mái ${fmt.num(loc.solar_kw)} kWp trên mái che bãi đỗ`))] : null, loc.storage_kwh ? [L("STORAGE"), esc(t(`BESS ${fmt.num(loc.storage_kwh)} kWh (LFP), 0.5C`, `Pin lưu trữ ${fmt.num(loc.storage_kwh)} kWh (LFP), 0,5C`))] : null]) })}`;
  }

  /* ----- Provisioning (STN-02) ----- */
  function provisionCharger(loc) {
    ui.formModal({
      title: t(`Provision a charger · ${loc.name}`, `Khai báo trụ sạc · ${loc.name}`),
      intro: ui.callout("info", esc(t("Declare the charger before it connects: its OCPP identity must match the one configured on the charger, so every status and session lands on the right gun.", "Khai báo trụ trước khi kết nối: định danh OCPP phải trùng với cấu hình trên trụ, để mọi trạng thái và phiên sạc ghi đúng súng."))),
      wide: true,
      fields: [
        { name: "identity", label: t("OCPP identity", "Định danh OCPP"), required: true, placeholder: `G3-${loc.location_id.slice(4, 7).toUpperCase()}-0${chargersAt(loc.location_id).length + 1}`, hint: t("Capitals, digits and dashes, 4–48 characters", "Chữ in hoa, số và gạch ngang, 4–48 ký tự") },
        { name: "serial", label: t("Serial number", "Số sê-ri"), required: true, placeholder: "WD480-239999" },
        { name: "vendor", label: t("Vendor", "Hãng"), value: "Willdigits", required: true },
        { name: "power", label: t("Model", "Mẫu"), type: "select", options: [["240", "DC 240 kW"], ["480", "DC 480 kW"]], value: "480" },
        { name: "version", label: t("Protocol", "Giao thức"), type: "select", options: [["1.6J", "OCPP 1.6J"], ["2.0.1", "OCPP 2.0.1"]], value: "1.6J" },
        { name: "guns", label: t("Guns", "Số súng"), type: "select", options: [["1", "1"], ["2", "2"]], value: "2" },
        { name: "standard", label: t("Connector standard", "Chuẩn đầu sạc"), type: "select", options: [["CCS2", "CCS2"], ["GB/T", "GB/T"]], value: "CCS2" },
      ],
      submitLabel: t("Provision charger", "Khai báo trụ"),
      onSubmit(values) {
        const identity = values.identity.toUpperCase();
        if (!/^[A-Z0-9-]{4,48}$/.test(identity)) return t("The OCPP identity uses capitals, digits and dashes (4–48 characters).", "Định danh OCPP chỉ gồm chữ in hoa, số và gạch ngang (4–48 ký tự).");
        if (db.charging_stations.some((s) => s.ocpp_identity === identity)) return t("Another charger already uses this identity.", "Đã có trụ khác dùng định danh này.");
        const power = Number(values.power);
        const n = chargersAt(loc.location_id).length + 1;
        const id = db.uid("chg");
        const station = {
          station_id: id, location_id: loc.location_id, ocpp_identity: identity, display_name: [`Charger ${n}`, `Trụ ${n}`], vendor: values.vendor, model: `DC ${power} kW`, max_power_kw: power,
          ocpp_version: values.version, firmware_version: null, serial_number: values.serial, status: "ACTIVE", last_seen_at: null, is_online: false, connected_at: null,
          connectors: [], energy_delivered_kwh_total: 0, sessions_total: 0,
        };
        for (let g = 1; g <= Number(values.guns); g++) station.connectors.push({ connector_id: `${id}-g${g}`, ocpp_evse_id: g, ocpp_connector_id: 1, standard: values.standard, max_power_kw: power / Number(values.guns), status: "Unknown", error_code: null, vendor_error_code: null, status_at: new Date().toISOString(), current_power_kw: 0 });
        db.charging_stations.push(station);
        loc.chargers = chargersAt(loc.location_id).length;
        G3.render();
        ui.toast(t(`${identity} provisioned — it appears online after its first BootNotification`, `Đã khai báo ${identity} — trụ sẽ trực tuyến sau BootNotification đầu tiên`));
        return true;
      },
    });
  }
  function provisionGun(loc) {
    const chargers = chargersAt(loc.location_id);
    if (!chargers.length) { ui.toast(t("Provision a charger first", "Hãy khai báo trụ trước"), "alert"); return; }
    ui.formModal({
      title: t("Provision a gun", "Khai báo súng sạc"),
      fields: [
        { name: "station", label: t("Charger", "Trụ sạc"), type: "select", options: chargers.map((s) => [s.station_id, `${s.ocpp_identity} · ${s.connectors.length} ${t("gun(s)", "súng")}`]) },
        { name: "standard", label: t("Connector standard", "Chuẩn đầu sạc"), type: "select", options: [["CCS2", "CCS2"], ["GB/T", "GB/T"]], value: "CCS2" },
        { name: "power", label: t("Max power (kW)", "Công suất tối đa (kW)"), type: "number", required: true, value: 120 },
      ],
      submitLabel: t("Add gun", "Thêm súng"),
      onSubmit(values) {
        const s = db.station(values.station);
        const power = Number(values.power);
        if (s.connectors.length >= 4) return t("A charger has at most 4 guns.", "Một trụ có tối đa 4 súng.");
        if (!(power > 0 && power <= s.max_power_kw)) return t(`Power must be between 1 and ${s.max_power_kw} kW.`, `Công suất phải từ 1 đến ${s.max_power_kw} kW.`);
        const g = s.connectors.length + 1;
        s.connectors.push({ connector_id: `${s.station_id}-g${g}`, ocpp_evse_id: g, ocpp_connector_id: 1, standard: values.standard, max_power_kw: power, status: "Unknown", error_code: null, vendor_error_code: null, status_at: new Date().toISOString(), current_power_kw: 0 });
        G3.render();
        ui.toast(t(`Gun ${g} added to ${s.ocpp_identity}`, `Đã thêm súng ${g} cho ${s.ocpp_identity}`));
        return true;
      },
    });
  }

  function frameModal(row) {
    ui.modal({
      title: `${row.action} · ${row.type === 2 ? "CALL" : "CALLRESULT"}`, wide: true,
      body: `${ui.kv([[t("Time", "Thời gian"), `<span class="mono">${esc(new Date(row.at).toISOString())}</span>`], [t("Direction", "Chiều"), esc(L(row.dir))], [t("Message ID", "Mã bản tin"), `<span class="mono">${esc(row.frame[1])}</span>`]])}
        <pre class="mono" style="margin-top:14px;background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:12px;font-size:12px;overflow:auto;max-height:360px">${esc(JSON.stringify(row.frame, null, 2))}</pre>
        <p class="small muted">${esc(t("idTag / idToken values are masked before display (STN-15).", "Giá trị idTag / idToken được che trước khi hiển thị (STN-15)."))}</p>`,
    });
  }

  G3.page({
    id: "station-detail",
    path: "/stations/:id",
    title: ["Station", "Trạm sạc"],
    roles: ["OPERATIONS", "SALES", "DISPATCHER", "FLEET_MANAGER", "ORG_ADMIN", "CUSTOMER_CARE"],
    features: ["STN-01", "STN-02", "STN-04", "STN-05", "STN-10", "STN-11", "STN-12", "STN-13", "STN-15", "PAY-09"],
    navParent: "stations",
    render({ params, query }) {
      const loc = db.location(params.id);
      if (!loc || !canUseLocation(loc)) return ui.pageHead({ title: t("Station not found", "Không tìm thấy trạm"), crumbs: [{ label: t("Stations", "Trạm sạc"), href: "#/stations" }] }) + ui.empty(t("This station does not exist or is not open to your organization.", "Trạm không tồn tại hoặc không mở cho tổ chức của bạn."));
      const internal = scope.isInternal();
      const chargers = chargersAt(loc.location_id);
      const counts = gunCounts(loc.location_id);
      const todayKey = new Date().toISOString().slice(0, 10);
      const today = db.charging_sessions.filter((s) => s.location_id === loc.location_id && s.started_at.slice(0, 10) === todayKey);
      const tabs = [["chargers", t("Chargers & guns", "Trụ & súng")]];
      if (internal) {
        tabs.push(["commands", t("Commands", "Lệnh điều khiển")], ["faults", t("Fault alerts", "Cảnh báo lỗi")]);
        if (isOps()) tabs.push(["messages", t("OCPP messages", "Bản tin OCPP")], ["security", t("Security", "Bảo mật")]);
        tabs.push(["green", t("Green energy", "Năng lượng xanh")]);
      }
      const tab = tabs.some(([k]) => k === query.tab) ? query.tab : "chargers";
      let body;
      if (tab === "commands") body = commandsTab(loc);
      else if (tab === "faults") body = faultsTab(loc);
      else if (tab === "messages") body = messagesTab(loc, query);
      else if (tab === "security") body = securityTab(loc);
      else if (tab === "green") body = greenTab(loc);
      else body = `${ui.featureChips(["STN-02", "STN-04"])}${chargers.length ? `<div class="grid cols-2">${chargers.map((s) => chargerCard(s, internal)).join("")}</div>` : ui.card({ body: ui.empty(t("No charger provisioned yet", "Chưa khai báo trụ nào")) })}`;

      const actions = isOps() ? `${ui.btn(t("Provision charger", "Khai báo trụ"), { icon: "plus", kind: "primary", action: "prov-charger" })}${ui.btn(t("Provision gun", "Khai báo súng"), { icon: "plug", action: "prov-gun" })}${ui.btn(t("Edit", "Sửa"), { icon: "edit", action: "edit-station" })}${loc.status === "ACTIVE" ? ui.btn(t("Retire", "Ngừng hoạt động"), { icon: "power", kind: "danger", action: "retire-station" }) : ui.btn(t("Reactivate", "Hoạt động lại"), { icon: "refresh", action: "reactivate-station" })}` : canEditAccess() ? ui.btn(t("Edit access", "Sửa quyền sử dụng"), { icon: "edit", action: "edit-station" }) : "";
      const allowed = (loc.allowed_organization_ids || []).map((id) => ui.orgName(id)).join(", ");
      return `<div data-cp>
        ${ui.pageHead({
          crumbs: [{ label: t("Stations", "Trạm sạc"), href: "#/stations" }, { label: loc.name }],
          title: loc.name,
          sub: `${esc(loc.address)} · ${esc(loc.region)} · ${loc.is_public ? esc(L("PUBLIC")) : esc(L("PRIVATE"))}`,
          features: ["STN-01", "STN-04", "STN-12", "PAY-09"],
          actions,
        })}
        ${loc.status !== "ACTIVE" ? `<div class="mb">${ui.callout("warn", `<b>${esc(G3.label(loc.status))}</b> — ${esc(loc.status_reason || "")}`)}</div>` : ""}
        <div class="grid cols-3">
          ${ui.card({ title: t("Station", "Thông tin trạm"), sub: "STN-01", body: ui.kv([
            [t("Owner", "Chủ trạm"), ui.orgName(loc.organization_id)],
            [t("Incident contact", "Liên hệ sự cố"), `${ui.icon("phone", 13)} ${esc(loc.incident_contact)}`],
            [t("Access", "Quyền sử dụng"), loc.is_public ? lb("PUBLIC", "info") : `${lb("PRIVATE", "violet")} <span class="small">${allowed}</span>`],
            [t("Opening hours", "Giờ mở cửa"), "24/7"],
            [t("Status", "Trạng thái"), ui.badge(loc.status)],
            [t("Chargers", "Trụ sạc"), `${chargers.filter((s) => s.is_online).length} ${t("online", "trực tuyến")} / ${chargers.length}`],
            [t("Connectors", "Đầu sạc"), esc(Array.from(new Set(gunsAt(loc.location_id).map(({ c }) => c.standard))).join(", ") || "—")],
            [t("Coordinates", "Tọa độ"), `<span class="mono small">${loc.lat.toFixed(4)}, ${loc.lon.toFixed(4)}</span>`],
          ]) })}
          ${ui.card({ title: t("Guns now", "Súng hiện tại"), sub: "STN-04", body: `${ui.donut({ size: 130, segments: [{ label: G3.label("Available"), value: counts.Available, color: STATUS_COLORS.Available }, { label: t("In use", "Đang dùng"), value: counts.BUSY, color: STATUS_COLORS.BUSY }, { label: G3.label("Faulted"), value: counts.Faulted, color: STATUS_COLORS.Faulted }, { label: G3.label("Unknown"), value: counts.Unknown, color: STATUS_COLORS.Unknown }], center: `${counts.Available}/${counts.total}`, centerSub: t("free", "trống") })}<div class="divider"></div>${ui.kv([[t("Sessions today", "Phiên hôm nay"), fmt.num(today.length)], [t("Energy today", "Điện năng hôm nay"), fmt.kwh(today.reduce((a, s) => a + s.energy_kwh, 0), 0)], [t("Max power per gun", "Công suất tối đa/súng"), `${fmt.num(maxGunPower(loc.location_id))} kW`]])}` })}
          ${tariffCard(loc)}
        </div>
        <div class="mt">${tabs.length > 1 ? ui.tabs(tabs, tab) : ""}${body}</div>
      </div>`;
    },
    mount(root, { params }) {
      const loc = db.location(params.id);
      const host = root.querySelector("[data-cp]");
      if (!loc || !host) return;
      bindCommon(host);
      const menuFor = (el, items) => ui.popover(el, items.map(([type, extra]) => ({ label: G3.label(type), icon: "send", onClick: () => commandForm(loc, { stationId: el.dataset.station, gun: extra, type }) })), { align: "right" });
      delegate(host, {
        "prov-charger": () => provisionCharger(loc),
        "prov-gun": () => provisionGun(loc),
        "edit-station": () => stationForm(loc),
        "retire-station": () => retireStation(loc),
        "reactivate-station": () => reactivateStation(loc),
        "send-command": () => commandForm(loc),
        "charger-menu": (el) => menuFor(el, [["RESET", 0], ["CHANGE_AVAILABILITY", 0], ["GET_CONFIGURATION", 0], ["CHANGE_CONFIGURATION", 0], ["TRIGGER_MESSAGE", 0]]),
        "gun-menu": (el) => menuFor(el, [["REMOTE_START", el.dataset.gun], ["REMOTE_STOP", el.dataset.gun], ["UNLOCK_CONNECTOR", el.dataset.gun], ["CHANGE_AVAILABILITY", el.dataset.gun]]),
        "pick-charger": (el) => G3.setQuery({ charger: el.dataset.id, action: "", dir: "" }),
        frame: (el) => { const row = shownLog[Number(el.dataset.i)]; if (row) frameModal(row); },
        "set-password": (el) => setPasswordForm(db.station(el.dataset.id)),
        "upgrade-wss": (el) => upgradeWss(db.station(el.dataset.id)),
      });
      scheduleCommandAnswers();
      // Live tick: power and kWh of running charges, heartbeats, guns finishing (stands in for OCPP StatusNotification / MeterValues).
      G3.onTick(() => {
        const now = new Date().toISOString();
        chargersAt(loc.location_id).forEach((s) => {
          if (!s.is_online || s.status !== "ACTIVE") return;
          s.last_seen_at = now;
          const seen = host.querySelector(`[data-seen="${s.station_id}"]`);
          if (seen) seen.textContent = fmt.ago(now);
          s.connectors.forEach((c) => {
            if (c.status === "Charging") {
              c.current_power_kw = Math.max(20, Math.min(c.max_power_kw, Math.round(c.current_power_kw * (0.96 + Math.random() * 0.08))));
              const ses = activeSessionOn(c.connector_id);
              if (ses) { ses.energy_kwh = round1(ses.energy_kwh + (c.current_power_kw * 3) / 3600); ses.peak_power_kw = Math.max(ses.peak_power_kw, c.current_power_kw); }
            } else if (c.status === "Finishing" && Math.random() < 0.12) Object.assign(c, { status: "Available", status_at: now });
            else if (c.status === "SuspendedEV" && Math.random() < 0.1) Object.assign(c, { status: "Charging", current_power_kw: Math.round(c.max_power_kw * 0.7), status_at: now });
            const row = host.querySelector(`[data-gun-row="${c.connector_id}"]`);
            if (row) row.innerHTML = gunRowInner(s, c);
          });
        });
      });
    },
  });

  /* =====================================================================
     3. Charging sessions (CHG-02, CHG-04, CHG-05, CHG-06, PAY-10)
     ===================================================================== */
  const LOSS_ALLOWANCE = 4.5; // % charger → battery loss accepted before the 1% tolerance (CHG-06)
  const minutesOf = (s) => (s.status === "ACTIVE" ? Math.round((Date.now() - new Date(s.started_at).getTime()) / db.MIN) : s.duration_minutes);
  const isStuck = (s) => s.status === "ACTIVE" && (minutesOf(s) > 6 * 60 || !(db.station(s.station_id) || {}).is_online);
  const billKwh = (s) => (s.bill_status === "ON_HOLD" ? round1(s.energy_kwh * 1.023) : s.price_vnd_kwh && s.cost_vnd ? round1(s.cost_vnd / s.price_vnd_kwh) : s.energy_kwh);

  function reconcile(s) {
    const charger = s.energy_kwh;
    const truck = s.vehicle_reported_kwh;
    const bill = billKwh(s);
    const loss = truck === null || truck === undefined || !charger ? null : ((charger - truck) / charger) * 100;
    const truckFlag = loss !== null && (loss - LOSS_ALLOWANCE >= 1 || loss < -1);
    const billDev = charger ? ((bill - charger) / charger) * 100 : 0;
    const billFlag = Math.abs(billDev) >= 1;
    return { charger, truck, bill, loss, billDev, truckFlag, billFlag, flagged: truckFlag || billFlag };
  }

  function sessionRows(query) {
    const period = query.period || "30";
    const from = period === "all" ? 0 : Date.now() - Number(period) * db.DAY;
    return scope.byVehicle(db.charging_sessions).filter((s) => {
      if (new Date(s.started_at).getTime() < from) return false;
      if (query.loc && s.location_id !== query.loc) return false;
      if (query.truck && s.vehicle_id !== query.truck) return false;
      if (query.driver && s.driver_id !== query.driver) return false;
      if (query.status && s.status !== query.status) return false;
      if (query.org && s.organization_id !== query.org) return false;
      return true;
    });
  }

  /** Mock meter readings for one session: power ramps up, holds, tapers above 80% (CHG-02). */
  function measurementSeries(s) {
    const minutes = minutesOf(s);
    if (!minutes || s.status === "ABANDONED") return [];
    const rnd = seeded(s.session_id + "mv");
    const n = Math.max(8, Math.min(36, Math.round(minutes / 4)));
    const start = new Date(s.started_at).getTime();
    return Array.from({ length: n + 1 }, (_, i) => {
      const frac = i / n;
      const soc = s.soc_start_percent + (s.soc_end_percent - s.soc_start_percent) * (1 - Math.pow(1 - frac, 1.15));
      const power = i === 0 ? 0 : soc < 80 ? s.peak_power_kw * (i === 1 ? 0.6 : 0.9 + rnd() * 0.1) : s.peak_power_kw * Math.max(0.3, ((100 - soc) / 20) * 0.9);
      return { at: start + frac * minutes * db.MIN, soc: Math.round(soc), power: Math.round(power) };
    });
  }

  function sessionModal(s) {
    const station = db.station(s.station_id);
    const loc = db.location(s.location_id);
    const gun = station ? station.connectors.find((c) => c.connector_id === s.connector_id) : null;
    const tariff = tariffVersionAt(s.location_id, s.started_at);
    const slot = tariff ? slotAt(tariff, new Date(s.started_at).getTime()) : null;
    const series = measurementSeries(s);
    const labels = series.map((p) => fmt.time(p.at));
    const stuck = isStuck(s);
    const foot = `${ui.btn(t("Export measurements", "Xuất số đo"), { icon: "download", action: "m-export" })}${stuck && canCloseSession() ? ui.btn(t("Close the session", "Đóng phiên"), { icon: "x", kind: "danger", action: "m-close" }) : ""}<button class="btn" data-close>${esc(t("Close", "Đóng"))}</button>`;
    ui.modal({
      title: t(`Charging session ${s.ocpp_transaction_id}`, `Phiên sạc ${s.ocpp_transaction_id}`), wide: true, foot,
      body: `${stuck ? `<div class="mb">${ui.callout("warn", esc(t(`The charger never reported the end of this session (charger ${station && station.is_online ? "online" : "offline since " + fmt.ago(station && station.last_seen_at)}). Staff can close it with a reason; the last meter reading is billed.`, `Trụ chưa báo kết thúc phiên này (trụ ${station && station.is_online ? "trực tuyến" : "mất kết nối từ " + fmt.ago(station && station.last_seen_at)}). Nhân viên có thể đóng phiên kèm lý do; chỉ số công tơ cuối cùng được tính tiền.`)))}</div>` : ""}
        <div class="grid cols-2">
          <div>${ui.kv([
            [t("Station", "Trạm"), loc ? esc(loc.name) : "—"],
            [t("Charger / gun", "Trụ / súng"), `<span class="mono">${esc(station ? station.ocpp_identity : "—")}</span> · ${esc(t("Gun", "Súng"))} ${gun ? gun.ocpp_evse_id : "—"}`],
            [t("Truck", "Xe"), s.vehicle_id && scope.vehicleIds().has(s.vehicle_id) ? `<a href="#/vehicles/${s.vehicle_id}">${esc(db.vehicle(s.vehicle_id).license_plate)}</a>` : esc(t("Guest truck", "Xe vãng lai"))],
            [t("Driver", "Tài xế"), s.driver_id ? ui.driverName(s.driver_id) : '<span class="muted">—</span>'],
            [t("Paid by", "Tổ chức thanh toán"), ui.orgName(s.organization_id)],
            [t("Started by", "Khởi động bằng"), esc(s.start_method === "QR" ? t("QR scan in the app", "Quét QR trong ứng dụng") : t("Remote start (operations)", "Bắt đầu từ xa (vận hành)"))],
            [t("Start → end", "Bắt đầu → kết thúc"), `${fmt.dateTime(s.started_at)} → ${s.ended_at ? fmt.time(s.ended_at) : esc(t("running", "đang chạy"))}`],
            [t("Duration", "Thời lượng"), fmt.duration(minutesOf(s))],
          ])}</div>
          <div>${ui.kv([
            [t("Energy (charger meter)", "Điện năng (công tơ trụ)"), `<b>${fmt.kwh(s.energy_kwh)}</b>`],
            [t("Battery", "Pin"), `${fmt.pct(s.soc_start_percent)} → ${fmt.pct(s.soc_end_percent)}`],
            [t("Peak power", "Công suất đỉnh"), `${fmt.num(s.peak_power_kw)} kW`],
            [t("Tariff applied", "Biểu giá áp dụng"), tariff ? `${esc(t(tariff.name[0], tariff.name[1]))} · v${tariff.version}` : "—"],
            [t("Time slot", "Khung giờ"), slot ? `${slot.from}–${slot.to} · ${esc(L(slot.source))} · ${esc(L(slot.label))}` : "—"],
            [t("Unit price (at start)", "Đơn giá (lúc bắt đầu)"), `${fmt.num(s.price_vnd_kwh)} ₫/kWh`],
            [t("Amount", "Thành tiền"), s.cost_vnd !== null ? `<b>${fmt.vnd(s.cost_vnd)}</b>` : `<span class="muted">${esc(t("estimate", "tạm tính"))} ${fmt.vnd(s.energy_kwh * s.price_vnd_kwh)}</span>`],
            [t("Bill", "Hóa đơn"), ui.badge(s.bill_status)],
          ])}</div>
        </div>
        ${series.length ? `<div class="grid cols-2 mt">
          <div><div class="small muted mb" style="margin-bottom:4px">${esc(t("Power (kW)", "Công suất (kW)"))}</div>${ui.lineChart({ labels, height: 170, unit: " kW", series: [{ name: t("Power", "Công suất"), values: series.map((p) => p.power), color: "#7c3aed", area: true }] })}</div>
          <div><div class="small muted" style="margin-bottom:4px">${esc(t("Battery (%)", "Pin (%)"))}</div>${ui.lineChart({ labels, height: 170, yMin: 0, yMax: 100, unit: "%", series: [{ name: "SOC", values: series.map((p) => p.soc), color: "#0e9f6e", area: true }] })}</div>
        </div>` : `<div class="mt">${ui.empty(t("No measurement: the charge never started", "Không có số đo: phiên sạc chưa bắt đầu"))}</div>`}
        <div class="mt">${ui.callout("info", esc(t("This record is append-only once finished (CHG-02): measurements, the tariff version and the unit price shown at start cannot be edited. A correction is a separate credit note (PAY-10).", "Bản ghi chỉ được ghi thêm sau khi kết thúc (CHG-02): số đo, phiên bản biểu giá và đơn giá hiển thị lúc bắt đầu không thể sửa. Điều chỉnh được lập thành phiếu ghi có riêng (PAY-10).")), "lock")}</div>`,
      onMount(el, close) {
        const exp = el.querySelector("[data-action=m-export]");
        if (exp) exp.addEventListener("click", () => ui.downloadCsv(`session-${s.ocpp_transaction_id}-measurements.csv`, [{ label: "timestamp", csv: (p) => new Date(p.at).toISOString() }, { label: "power_kw", key: "power" }, { label: "soc_percent", key: "soc" }], series));
        const btn = el.querySelector("[data-action=m-close]");
        if (btn) btn.addEventListener("click", () => { close(); closeSessionForm(s); });
      },
    });
  }

  function closeSessionForm(s) {
    ui.reasonModal({
      title: t(`Close session ${s.ocpp_transaction_id}`, `Đóng phiên ${s.ocpp_transaction_id}`),
      intro: ui.callout("warn", esc(t(`The session ends now with the last meter reading (${fmt.kwh(s.energy_kwh)}) and is billed at the price shown at its start.`, `Phiên kết thúc ngay với chỉ số công tơ cuối (${fmt.kwh(s.energy_kwh)}) và được tính tiền theo giá hiển thị lúc bắt đầu.`))),
      submitLabel: t("Close the session", "Đóng phiên"),
      onSubmit(reason) {
        const now = new Date().toISOString();
        Object.assign(s, { status: "COMPLETED", ended_at: now, end_cause: "CLOSED_BY_STAFF", status_reason: reason, closed_by: me(), duration_minutes: minutesOf(s), cost_vnd: Math.round(s.energy_kwh * s.price_vnd_kwh), bill_status: "BILLED" });
        G3.render();
        ui.toast(t(`Session ${s.ocpp_transaction_id} closed and billed`, `Đã đóng và tính tiền phiên ${s.ocpp_transaction_id}`));
      },
    });
  }

  function sessionsTab(rows, query) {
    const shown = rows.slice(0, 100);
    const active = rows.filter((s) => s.status === "ACTIVE");
    const kwh = rows.reduce((a, s) => a + s.energy_kwh, 0);
    const cost = rows.reduce((a, s) => a + (s.cost_vnd || 0), 0);
    const billedKwh = rows.filter((s) => s.cost_vnd).reduce((a, s) => a + s.energy_kwh, 0);
    const stuck = rows.filter(isStuck);
    return `<div class="grid cols-5">
        ${ui.kpi({ label: t("Sessions", "Phiên sạc"), icon: "plug", value: fmt.num(rows.length), foot: `${rows.filter((s) => s.status === "ABANDONED").length} ${t("abandoned", "bỏ dở")}` })}
        ${ui.kpi({ label: t("Energy", "Điện năng"), icon: "bolt", value: fmt.num(kwh), unit: "kWh", foot: `${fmt.num(rows.length ? kwh / rows.length : 0, 1)} kWh / ${t("session", "phiên")}` })}
        ${ui.kpi({ label: t("Cost", "Chi phí"), icon: "receipt", value: fmt.vndShort(cost), foot: `${fmt.num(billedKwh ? cost / billedKwh : 0)} ₫/kWh ${t("average", "trung bình")}` })}
        ${ui.kpi({ label: t("Charging now", "Đang sạc"), icon: "activity", value: active.length, tone: "violet", foot: `${fmt.num(active.reduce((a, s) => a + s.energy_kwh, 0))} kWh ${t("so far", "đã cấp")}` })}
        ${ui.kpi({ label: t("Never stopped", "Chưa kết thúc"), icon: "alert", value: stuck.length, tone: stuck.length ? "warn" : "ok", foot: t("charger offline or > 6 h", "trụ mất kết nối hoặc > 6 giờ") })}
      </div>
      ${stuck.length && canCloseSession() ? `<div class="mt">${ui.callout("warn", `${esc(t(`${stuck.length} session(s) were never stopped by the charger (reboot or power loss). Open one to close it with a reason.`, `${stuck.length} phiên chưa được trụ kết thúc (khởi động lại hoặc mất điện). Mở phiên để đóng kèm lý do.`))}`)}</div>` : ""}
      <section class="card mt">
        ${ui.table({
          rows: shown, empty: t("No session matches the filters", "Không có phiên sạc phù hợp bộ lọc"),
          rowAttrs: (s) => `data-action="session" data-id="${s.session_id}" style="cursor:pointer"`,
          columns: [
            { label: t("Started", "Bắt đầu"), render: (s) => `<span class="nowrap">${fmt.dateTime(s.started_at)}</span><span class="sub nowrap">${fmt.duration(minutesOf(s))} · ${esc(s.start_method === "QR" ? G3.label("QR") : G3.label("REMOTE_START"))}</span>` },
            { label: t("Station / charger / gun", "Trạm / trụ / súng"), render: (s) => { const st = db.station(s.station_id); const gun = st && st.connectors.find((c) => c.connector_id === s.connector_id); return `<div style="max-width:190px"><b>${esc((db.location(s.location_id) || {}).name || "—")}</b><span class="sub mono nowrap">${esc(st ? st.ocpp_identity : "—")} · ${esc(t("gun", "súng"))} ${gun ? gun.ocpp_evse_id : "—"}</span></div>`; } },
            { label: t("Truck", "Xe"), render: (s) => (s.vehicle_id ? `<b class="nowrap">${esc(db.vehicle(s.vehicle_id).license_plate)}</b>` : `<span class="muted">${esc(t("Guest", "Vãng lai"))}</span>`) },
            { label: t("Driver", "Tài xế"), render: (s) => { const d = db.driver(s.driver_id); return d ? `<span class="nowrap">${esc(d.full_name)}</span>` : '<span class="muted">—</span>'; } },
            { label: "kWh", align: "right", render: (s) => fmt.num(s.energy_kwh, 1) },
            { label: t("Battery", "Pin"), render: (s) => `<span class="nowrap">${s.soc_start_percent}% → <b>${s.soc_end_percent}%</b></span>` },
            { label: t("Peak kW", "kW đỉnh"), align: "right", render: (s) => fmt.num(s.peak_power_kw) },
            { label: t("Cost", "Chi phí"), align: "right", render: (s) => `${s.cost_vnd !== null ? `<span class="nowrap">${fmt.vnd(s.cost_vnd)}</span>` : '<span class="muted">—</span>'}<span class="sub nowrap">${fmt.num(s.price_vnd_kwh)} ₫/kWh</span>` },
            { label: t("Bill", "Hóa đơn"), render: (s) => `${s.status === "ACTIVE" ? ui.badge("Charging") : s.status === "ABANDONED" ? ui.badge("ABANDONED") : ui.badge(s.bill_status)}${isStuck(s) ? `<span class="sub">${ui.badge("WARNING", "warn", t("Never stopped?", "Chưa kết thúc?"))}</span>` : ""}` },
          ],
        })}
        ${rows.length > shown.length ? `<div class="small muted" style="padding:10px 16px">${esc(t(`Showing 100 of ${rows.length} sessions — narrow the filters or export.`, `Hiển thị 100 / ${rows.length} phiên — thu hẹp bộ lọc hoặc xuất dữ liệu.`))}</div>` : ""}
      </section>`;
  }

  function energyTab(rows) {
    const done = rows.filter((s) => s.status === "COMPLETED");
    const byOrg = {};
    const byTruck = {};
    done.forEach((s) => {
      const o = (byOrg[s.organization_id] = byOrg[s.organization_id] || { organization_id: s.organization_id, sessions: 0, kwh: 0, cost: 0, trucks: new Set() });
      o.sessions++; o.kwh += s.energy_kwh; o.cost += s.cost_vnd || 0; if (s.vehicle_id) o.trucks.add(s.vehicle_id);
      const key = s.vehicle_id || "guest-" + s.organization_id;
      const tr = (byTruck[key] = byTruck[key] || { vehicle_id: s.vehicle_id, organization_id: s.organization_id, sessions: 0, kwh: 0, cost: 0 });
      tr.sessions++; tr.kwh += s.energy_kwh; tr.cost += s.cost_vnd || 0;
    });
    const orgs = Object.values(byOrg).sort((a, b) => b.kwh - a.kwh);
    const trucks = Object.values(byTruck).sort((a, b) => b.kwh - a.kwh);
    return `${ui.featureChips(["CHG-05"])}${ui.callout("info", esc(t("Station-metered figures: the charger's meter start and stop of each completed session, not estimates from the truck.", "Số liệu đo tại trạm: chỉ số công tơ đầu và cuối của trụ cho từng phiên đã hoàn tất, không phải ước tính từ xe.")))}
      <div class="grid side-l mt">
        ${ui.card({ title: t("kWh per organization", "kWh theo tổ chức"), body: orgs.length ? ui.hBars({ labels: orgs.map((o) => db.org(o.organization_id).short_name), series: [{ values: orgs.map((o) => Math.round(o.kwh)) }], unit: " kWh" }) : ui.empty() })}
        ${ui.card({
          title: t("Per organization", "Theo tổ chức"), flush: true, actions: ui.btn(t("Export", "Xuất"), { icon: "download", sm: true, action: "export-energy" }),
          body: ui.table({ rows: orgs, columns: [
            { label: t("Organization", "Tổ chức"), render: (o) => `<b>${ui.orgName(o.organization_id)}</b>` },
            { label: t("Trucks", "Xe"), align: "right", render: (o) => o.trucks.size },
            { label: t("Sessions", "Phiên"), align: "right", render: (o) => fmt.num(o.sessions) },
            { label: "kWh", align: "right", render: (o) => fmt.num(o.kwh, 1) },
            { label: t("kWh / session", "kWh / phiên"), align: "right", render: (o) => fmt.num(o.kwh / o.sessions, 1) },
            { label: t("Amount", "Thành tiền"), align: "right", render: (o) => fmt.vnd(o.cost) },
          ] }),
        })}
      </div>
      ${ui.card({ title: t("Per truck", "Theo xe"), sub: `${trucks.length}`, cls: "mt", flush: true, body: ui.table({ rows: trucks, columns: [
        { label: t("Truck", "Xe"), render: (r) => (r.vehicle_id ? ui.vehicleCell(db.vehicle(r.vehicle_id)) : `<span class="muted">${esc(t("Guest trucks", "Xe vãng lai"))}</span>`) },
        { label: t("Organization", "Tổ chức"), render: (r) => ui.orgName(r.organization_id) },
        { label: t("Sessions", "Phiên"), align: "right", render: (r) => r.sessions },
        { label: "kWh", align: "right", render: (r) => fmt.num(r.kwh, 1) },
        { label: t("kWh / session", "kWh / phiên"), align: "right", render: (r) => fmt.num(r.kwh / r.sessions, 1) },
        { label: t("Amount", "Thành tiền"), align: "right", render: (r) => fmt.vnd(r.cost) },
      ] }) })}`;
  }

  function reconciliationTab(rows, query) {
    const done = rows.filter((s) => s.status === "COMPLETED" && s.energy_kwh > 0).map((s) => ({ s, r: reconcile(s) }));
    const withTruck = done.filter((x) => x.r.loss !== null);
    const truckFlags = done.filter((x) => x.r.truckFlag);
    const billFlags = done.filter((x) => x.r.billFlag);
    const flagged = done.filter((x) => x.r.flagged);
    const atStake = billFlags.reduce((a, x) => a + Math.abs(x.r.bill - x.r.charger), 0);
    const n1 = (v) => fmt.num(v, v % 1 ? 1 : 0);
    const buckets = [["< 0%", (l) => l < 0], [`0–${n1(1.5)}%`, (l) => l >= 0 && l < 1.5], [`${n1(1.5)}–3%`, (l) => l >= 1.5 && l < 3], [`3–${n1(4.5)}%`, (l) => l >= 3 && l < 4.5], [`${n1(4.5)}–${n1(5.5)}%`, (l) => l >= 4.5 && l < 5.5], [`≥ ${n1(5.5)}%`, (l) => l >= 5.5]];
    const list = (query.flagged ? flagged : done).sort((a, b) => Number(b.r.flagged) - Number(a.r.flagged) || (a.s.started_at < b.s.started_at ? 1 : -1)).slice(0, 120);
    const pctCell = (v, flag) => (v === null ? '<span class="muted">—</span>' : `<span style="${flag ? "color:var(--danger);font-weight:600" : ""}">${v > 0 ? "+" : ""}${fmt.num(v, 1)}%</span>`);
    return `${ui.featureChips(["CHG-06"])}<div class="grid cols-5">
        ${ui.kpi({ label: t("Sessions checked", "Phiên đã đối soát"), icon: "check", value: fmt.num(done.length), foot: `${withTruck.length} ${t("with truck data", "có dữ liệu xe")}` })}
        ${ui.kpi({ label: t("Matched", "Khớp"), icon: "shield", value: fmt.pct(done.length ? ((done.length - flagged.length) / done.length) * 100 : 100, 1), tone: "ok" })}
        ${ui.kpi({ label: t("Charger ≠ truck", "Trụ ≠ xe"), icon: "truck", value: truckFlags.length, tone: truckFlags.length ? "warn" : undefined, foot: t(`loss > ${fmt.num(LOSS_ALLOWANCE, 1)}% + 1%`, `hao hụt > ${fmt.num(LOSS_ALLOWANCE, 1)}% + 1%`) })}
        ${ui.kpi({ label: t("Charger ≠ bill", "Trụ ≠ hóa đơn"), icon: "receipt", value: billFlags.length, tone: billFlags.length ? "danger" : undefined, foot: t("difference ≥ 1%", "chênh lệch ≥ 1%") })}
        ${ui.kpi({ label: t("kWh at stake", "kWh cần xử lý"), icon: "bolt", value: fmt.num(atStake, 1), unit: "kWh", foot: t("bills held until resolved", "hóa đơn tạm giữ đến khi xử lý") })}
      </div>
      <div class="grid side mt">
        ${ui.card({ title: t("Charger → truck loss per session", "Hao hụt trụ → xe theo phiên"), sub: t("number of sessions", "số phiên"), body: ui.barChart({ labels: buckets.map((b) => b[0]), height: 190, series: [{ name: t("Sessions", "Phiên"), values: buckets.map((b) => withTruck.filter((x) => b[1](x.r.loss)).length), color: "#2563eb" }] }) })}
        ${ui.card({ title: t("Rules", "Quy tắc"), body: `${ui.kv([[t("Reference", "Chuẩn"), esc(t("Charger meter (station-metered)", "Công tơ trụ (đo tại trạm)"))], [t("Loss allowance", "Hao hụt cho phép"), `${fmt.num(LOSS_ALLOWANCE, 1)}% ${esc(t("charger → battery", "trụ → pin"))}`], [t("Tolerance", "Dung sai"), "1%"], [t("Bill check", "Kiểm hóa đơn"), esc(t("billed kWh = charger kWh ± 1%", "kWh hóa đơn = kWh trụ ± 1%"))]])}<p class="small muted" style="margin:10px 0 0">${esc(t("A flagged session raises an alert to accounting and operations; its bill is held.", "Phiên bị lệch tạo cảnh báo cho kế toán và vận hành; hóa đơn được tạm giữ."))}</p>` })}
      </div>
      <section class="card mt">
        <div class="toolbar">${checkbox("flagged", query.flagged, t("Flagged only", "Chỉ phiên lệch"))}<span class="grow"></span>${ui.btn(t("Export report", "Xuất báo cáo"), { icon: "download", sm: true, action: "export-recon" })}</div>
        ${ui.table({
          rows: list, empty: t("Nothing to reconcile in this period", "Không có phiên cần đối soát"),
          rowAttrs: (x) => `data-action="session" data-id="${x.s.session_id}" style="cursor:pointer"`,
          columns: [
            { label: t("Session", "Phiên"), render: (x) => `${fmt.dateTime(x.s.started_at)}<span class="sub mono">#${esc(x.s.ocpp_transaction_id)}</span>` },
            { label: t("Station", "Trạm"), render: (x) => esc((db.location(x.s.location_id) || {}).name || "—") },
            { label: t("Truck", "Xe"), render: (x) => (x.s.vehicle_id ? esc(db.vehicle(x.s.vehicle_id).license_plate) : "—") },
            { label: t("Charger kWh", "kWh trụ"), align: "right", render: (x) => fmt.num(x.r.charger, 1) },
            { label: t("Truck kWh", "kWh xe"), align: "right", render: (x) => (x.r.truck === null || x.r.truck === undefined ? '<span class="muted">—</span>' : fmt.num(x.r.truck, 1)) },
            { label: t("Loss", "Hao hụt"), align: "right", render: (x) => pctCell(x.r.loss, x.r.truckFlag) },
            { label: t("Bill kWh", "kWh hóa đơn"), align: "right", render: (x) => fmt.num(x.r.bill, 1) },
            { label: t("Bill vs charger", "HĐ so với trụ"), align: "right", render: (x) => pctCell(x.r.billDev, x.r.billFlag) },
            { label: t("Result", "Kết quả"), render: (x) => (x.r.flagged ? lb("FLAGGED", "danger") : x.r.loss === null ? lb("NO_TRUCK_DATA", "muted") : lb("OK", "ok")) },
            { label: t("Bill", "Hóa đơn"), render: (x) => ui.badge(x.s.bill_status) },
          ],
        })}
      </section>`;
  }

  G3.page({
    id: "charging-sessions",
    path: "/charging-sessions",
    title: ["Charging sessions", "Phiên sạc"],
    roles: ["OPERATIONS", "CUSTOMER_CARE", "FLEET_MANAGER", "ACCOUNTANT", "ORG_ADMIN"],
    features: ["CHG-02", "CHG-04", "CHG-05", "CHG-06", "PAY-10"],
    nav: { group: "charging", order: 2, icon: "plug" },
    render({ query }) {
      const internal = scope.isInternal();
      const tabs = [["sessions", t("Sessions", "Phiên sạc")]];
      if (internal) tabs.push(["energy", t("Energy per customer", "Điện năng theo khách hàng")], ["reconciliation", t("Reconciliation", "Đối soát")]);
      const tab = tabs.some(([k]) => k === query.tab) ? query.tab : "sessions";
      const rows = sessionRows(query);
      const all = scope.byVehicle(db.charging_sessions);
      const locIds = Array.from(new Set(all.map((s) => s.location_id)));
      const trucks = scope.vehicles().filter((v) => all.some((s) => s.vehicle_id === v.vehicle_id));
      const drivers = scope.filter(db.drivers).filter((d) => all.some((s) => s.driver_id === d.driver_id));
      const body = tab === "energy" ? energyTab(rows) : tab === "reconciliation" ? reconciliationTab(rows, query) : sessionsTab(rows, query);
      return `<div data-cp>
        ${ui.pageHead({
          title: t("Charging sessions", "Phiên sạc"),
          sub: esc(internal ? t("Every session on the network, from the chargers' messages. Finished sessions are immutable.", "Mọi phiên sạc trên mạng lưới, từ bản tin của trụ. Phiên đã kết thúc không thể sửa.") : t(`Sessions paid by ${scope.org().name}${scope.fleetLimit() ? " (trucks of your fleets)" : ""}.`, `Các phiên sạc do ${scope.org().name} thanh toán${scope.fleetLimit() ? " (xe thuộc đội của bạn)" : ""}.`)),
          features: ["CHG-02", "CHG-04", "PAY-10"],
          actions: ui.btn(t("Export", "Xuất"), { icon: "download", action: "export-sessions" }),
        })}
        ${tabs.length > 1 ? ui.tabs(tabs, tab) : ""}
        <section class="card mb"><div class="toolbar">
          ${ui.segmented([["7", t("7 days", "7 ngày")], ["30", t("30 days", "30 ngày")], ["all", t("All", "Tất cả")]], query.period || "30", "period")}
          ${ui.select("loc", [["", t("All stations", "Mọi trạm")], ...locIds.map((id) => [id, (db.location(id) || {}).name || id])], query.loc || "")}
          ${ui.select("truck", [["", t("All trucks", "Mọi xe")], ...trucks.map((v) => [v.vehicle_id, v.license_plate])], query.truck || "")}
          ${ui.select("driver", [["", t("All drivers", "Mọi tài xế")], ...drivers.map((d) => [d.driver_id, d.full_name])], query.driver || "")}
          ${ui.select("status", [["", t("All statuses", "Mọi trạng thái")], ["ACTIVE", t("Charging now", "Đang sạc")], ["COMPLETED", G3.label("COMPLETED")], ["ABANDONED", G3.label("ABANDONED")]], query.status || "")}
          ${internal ? ui.select("org", [["", t("All organizations", "Mọi tổ chức")], ...scope.filter(db.organizations).filter((o) => o.kind === "CUSTOMER").map((o) => [o.organization_id, o.short_name])], query.org || "") : ""}
        </div></section>
        ${body}
      </div>`;
    },
    mount(root, { query }) {
      const host = root.querySelector("[data-cp]");
      bindCommon(host);
      delegate(host, {
        session: (el) => sessionModal(db.find("charging_sessions", el.dataset.id)),
        "export-sessions": () => {
          const rows = sessionRows(query);
          ui.auditedExport({
            dataType: "CHARGING_SESSIONS", subject: null, filename: "charging-sessions.csv", rows,
            columns: [
              { label: "started_at", key: "started_at" }, { label: "ended_at", key: "ended_at" }, { label: "station", csv: (s) => (db.location(s.location_id) || {}).name },
              { label: "charger", csv: (s) => (db.station(s.station_id) || {}).ocpp_identity }, { label: "plate", csv: (s) => (s.vehicle_id ? db.vehicle(s.vehicle_id).license_plate : "") },
              { label: "driver", csv: (s) => (db.driver(s.driver_id) || {}).full_name }, { label: "energy_kwh", key: "energy_kwh" }, { label: "soc_start_percent", key: "soc_start_percent" },
              { label: "soc_end_percent", key: "soc_end_percent" }, { label: "peak_power_kw", key: "peak_power_kw" }, { label: "price_vnd_kwh", key: "price_vnd_kwh" }, { label: "cost_vnd", key: "cost_vnd" }, { label: "bill_status", key: "bill_status" },
            ],
          });
        },
        "export-energy": () => {
          const rows = sessionRows(query).filter((s) => s.status === "COMPLETED");
          const orgs = Array.from(new Set(rows.map((s) => s.organization_id))).map((id) => { const own = rows.filter((s) => s.organization_id === id); return { organization: db.org(id).name, sessions: own.length, kwh: round1(own.reduce((a, s) => a + s.energy_kwh, 0)), cost: own.reduce((a, s) => a + (s.cost_vnd || 0), 0) }; });
          ui.downloadCsv("energy-per-organization.csv", [{ label: "organization", key: "organization" }, { label: "sessions", key: "sessions" }, { label: "energy_kwh", key: "kwh" }, { label: "amount_vnd", key: "cost" }], orgs);
        },
        "export-recon": () => {
          const rows = sessionRows(query).filter((s) => s.status === "COMPLETED" && s.energy_kwh > 0);
          ui.downloadCsv("reconciliation.csv", [
            { label: "ocpp_transaction_id", key: "ocpp_transaction_id" }, { label: "started_at", key: "started_at" }, { label: "charger_kwh", key: "energy_kwh" },
            { label: "truck_kwh", key: "vehicle_reported_kwh" }, { label: "bill_kwh", csv: (s) => billKwh(s) }, { label: "loss_percent", csv: (s) => { const r = reconcile(s); return r.loss === null ? "" : r.loss.toFixed(2); } },
            { label: "flagged", csv: (s) => (reconcile(s).flagged ? "yes" : "no") }, { label: "bill_status", key: "bill_status" },
          ], rows);
        },
      });
    },
  });

  /* =====================================================================
     4. Tariffs (PAY-09)
     ===================================================================== */
  const SLOT_SOURCES = ["GRID", "SOLAR", "STORAGE"];
  const SLOT_LABELS = ["OFF_PEAK", "NORMAL", "PEAK", "SOLAR"];

  function tariffEditor(loc) {
    const tariff = tariffOf(loc.location_id);
    let slots = (tariff ? tariff.slots : [{ from: "00:00", to: "24:00", price_vnd_kwh: 4450, source: "GRID", label: "NORMAL" }]).map((s) => ({ ...s }));
    const today = new Date().toISOString().slice(0, 10);
    const rowsHtml = () => slots.map((s, i) => `<tr data-i="${i}">
        <td><input class="input" data-k="from" value="${esc(s.from)}" style="width:72px" ${i === 0 ? "readonly" : ""}></td>
        <td><input class="input" data-k="to" value="${esc(s.to)}" style="width:72px" ${i === slots.length - 1 ? "readonly" : ""}></td>
        <td><select class="input" data-k="source">${SLOT_SOURCES.map((x) => `<option value="${x}" ${x === s.source ? "selected" : ""}>${esc(L(x))}</option>`).join("")}</select></td>
        <td><select class="input" data-k="label">${SLOT_LABELS.map((x) => `<option value="${x}" ${x === s.label ? "selected" : ""}>${esc(x === "SOLAR" ? L("SOLAR") : L(x))}</option>`).join("")}</select></td>
        <td><input class="input" data-k="price_vnd_kwh" type="number" value="${s.price_vnd_kwh}" style="width:96px"></td>
        <td>${slots.length > 1 ? `<button class="icon-btn" data-remove="${i}" title="${esc(t("Remove", "Xóa"))}">${ui.icon("trash", 15)}</button>` : ""}</td></tr>`).join("");
    const read = (el) => {
      el.querySelectorAll("tr[data-i]").forEach((tr) => {
        const s = slots[Number(tr.dataset.i)];
        tr.querySelectorAll("[data-k]").forEach((inp) => { s[inp.dataset.k] = inp.dataset.k === "price_vnd_kwh" ? Number(inp.value) : inp.value.trim(); });
      });
    };
    ui.modal({
      title: t(`New tariff version · ${loc.name}`, `Phiên bản biểu giá mới · ${loc.name}`), wide: true,
      foot: `<button class="btn" data-close>${esc(t("Cancel", "Hủy"))}</button><button class="btn primary" data-save>${esc(t("Publish version", "Ban hành phiên bản"))}</button>`,
      body: `${ui.callout("info", esc(t("A new version applies to sessions that start after its effective time; running sessions keep the price shown at their start. Every version is kept.", "Phiên bản mới áp dụng cho phiên bắt đầu sau thời điểm hiệu lực; phiên đang chạy giữ giá đã hiển thị lúc bắt đầu. Mọi phiên bản đều được lưu.")))}
        <div class="form cols-2 mt"><div class="field"><label>${esc(t("Effective from", "Hiệu lực từ"))} <span style="color:var(--danger)">*</span></label><input class="input" type="date" data-eff value="${today}" min="${today}"><span class="hint">${esc(t("Today = immediately", "Hôm nay = áp dụng ngay"))}</span></div>
        <div class="field"><label>${esc(t("Preview", "Xem trước"))}</label><div data-preview>${bandHtml(slots, { height: 22, now: false, prices: false })}</div></div></div>
        <div class="table-wrap mt"><table class="tbl"><thead><tr><th>${esc(t("From", "Từ"))}</th><th>${esc(t("To", "Đến"))}</th><th>${esc(t("Source", "Nguồn"))}</th><th>${esc(t("Band", "Khung"))}</th><th>₫/kWh</th><th></th></tr></thead><tbody data-rows>${rowsHtml()}</tbody></table></div>
        <div class="row mt">${ui.btn(t("Split last slot", "Tách khung cuối"), { icon: "plus", sm: true, attrs: "data-add" })}<span class="small muted">${esc(t("Slots must cover 00:00–24:00 without gaps; prices 500–20,000 ₫/kWh.", "Các khung phải phủ kín 00:00–24:00; giá từ 500 đến 20.000 ₫/kWh."))}</span></div>
        <div class="form-error small" style="color:var(--danger);margin-top:10px;display:none"></div>`,
      onMount(el, close) {
        const tbody = el.querySelector("[data-rows]");
        const err = el.querySelector(".form-error");
        const refresh = () => { tbody.innerHTML = rowsHtml(); try { el.querySelector("[data-preview]").innerHTML = bandHtml(slots, { height: 22, now: false, prices: false }); } catch (e) { /* invalid time while typing */ } };
        tbody.addEventListener("change", () => { read(el); for (let i = 1; i < slots.length; i++) slots[i].from = slots[i - 1].to; refresh(); });
        tbody.addEventListener("click", (e) => { const b = e.target.closest("[data-remove]"); if (!b) return; read(el); const i = Number(b.dataset.remove); const removed = slots.splice(i, 1)[0]; if (i > 0) slots[i - 1].to = removed.to; else slots[0].from = "00:00"; refresh(); });
        el.querySelector("[data-add]").addEventListener("click", () => {
          read(el);
          const last = slots[slots.length - 1];
          const mid = Math.round((toMin(last.from) + toMin(last.to)) / 120) * 60;
          if (mid <= toMin(last.from) || mid >= toMin(last.to)) return;
          const midText = `${String(mid / 60).padStart(2, "0")}:00`;
          slots.push({ ...last, from: midText });
          last.to = midText;
          refresh();
        });
        el.querySelector("[data-save]").addEventListener("click", () => {
          read(el);
          const eff = el.querySelector("[data-eff]").value;
          const fail = (msg) => { err.textContent = msg; err.style.display = "block"; };
          if (!eff || eff < today) return fail(t("The effective date cannot be in the past.", "Ngày hiệu lực không được ở quá khứ."));
          const timeOk = (x) => /^([01]\d|2[0-3]):[0-5]\d$|^24:00$/.test(x);
          if (slots[0].from !== "00:00" || slots[slots.length - 1].to !== "24:00") return fail(t("Slots must start at 00:00 and end at 24:00.", "Các khung phải bắt đầu lúc 00:00 và kết thúc lúc 24:00."));
          for (let i = 0; i < slots.length; i++) {
            const s = slots[i];
            if (!timeOk(s.from) || !timeOk(s.to)) return fail(t(`Slot ${i + 1}: use HH:MM times.`, `Khung ${i + 1}: dùng định dạng giờ HH:MM.`));
            if (toMin(s.to) <= toMin(s.from)) return fail(t(`Slot ${i + 1} ends before it starts.`, `Khung ${i + 1} kết thúc trước khi bắt đầu.`));
            if (i && s.from !== slots[i - 1].to) return fail(t(`Gap or overlap before slot ${i + 1}.`, `Có khoảng trống hoặc chồng lấn trước khung ${i + 1}.`));
            if (!(s.price_vnd_kwh >= 500 && s.price_vnd_kwh <= 20000)) return fail(t(`Slot ${i + 1}: price must be 500–20,000 ₫/kWh.`, `Khung ${i + 1}: giá phải từ 500 đến 20.000 ₫/kWh.`));
          }
          const clean = slots.map((s) => ({ from: s.from, to: s.to, source: s.source, label: s.label, price_vnd_kwh: s.price_vnd_kwh }));
          if (!tariff) {
            db.tariffs.push({ tariff_id: `trf-${loc.location_id.slice(4)}`, location_id: loc.location_id, organization_id: loc.organization_id, name: loc.is_public ? ["Public time-of-use", "Giá công cộng theo giờ"] : ["Private station price", "Giá trạm riêng"], status: "ACTIVE", effective_from: new Date().toISOString(), version: 1, slots: clean, created_by: me() });
            ui.toast(t("Tariff v1 published", "Đã ban hành biểu giá v1"));
          } else {
            const list = historyOf(tariff);
            const version = Math.max(tariff.version, ...list.map((h) => h.version)) + 1;
            const entry = { version, status: "SCHEDULED", effective_from: eff === today ? new Date().toISOString() : new Date(`${eff}T00:00:00`).toISOString(), effective_to: null, slots: clean, created_by: me() };
            if (eff === today) { activateVersion(tariff, entry); ui.toast(t(`Version ${version} is now in force`, `Phiên bản ${version} đã có hiệu lực`)); }
            else { list.unshift(entry); ui.toast(t(`Version ${version} scheduled for ${fmt.date(entry.effective_from)}`, `Đã lên lịch phiên bản ${version} từ ${fmt.date(entry.effective_from)}`)); }
          }
          close();
          G3.render();
        });
      },
    });
  }

  G3.page({
    id: "tariffs",
    path: "/tariffs",
    title: ["Tariffs", "Biểu giá"],
    roles: ["OPERATIONS", "FLEET_MANAGER", "ACCOUNTANT"],
    features: ["PAY-09", "STN-12"],
    nav: { group: "charging", order: 3, icon: "tag" },
    render({ query }) {
      const locs = visibleLocations();
      const loc = locs.find((l) => l.location_id === query.loc) || locs[0];
      const tariff = loc ? tariffOf(loc.location_id) : null;
      const history = tariff ? historyOf(tariff) : [];
      const now = Date.now();
      const overview = ui.table({
        rows: locs, href: (l) => `/tariffs?loc=${l.location_id}`, selected: (l) => loc && l.location_id === loc.location_id,
        columns: [
          { label: t("Station", "Trạm"), render: (l) => `<b>${esc(l.name)}</b><span class="sub">${l.is_public ? esc(L("PUBLIC")) : esc(L("PRIVATE"))}</span>` },
          { label: t("Version", "Phiên bản"), render: (l) => { const tr = tariffOf(l.location_id); return tr ? `v${tr.version}<span class="sub">${fmt.date(tr.effective_from)}</span>` : '<span class="muted">—</span>'; } },
          { label: t("Price now", "Giá hiện tại"), align: "right", render: (l) => priceNow(l) },
          { label: t("Cheapest window today", "Khung rẻ nhất hôm nay"), render: (l) => { const tr = tariffOf(l.location_id); if (!tr) return '<span class="muted">—</span>'; const c = cheapest(tr); return `${esc(c.windows.join(", "))}<span class="sub">${fmt.num(c.price)} ₫/kWh</span>`; } },
          { label: t("Day profile", "Biểu đồ ngày"), render: (l) => { const tr = tariffOf(l.location_id); return tr ? `<div style="width:180px">${bandHtml(tr.slots, { height: 12, prices: false })}</div>` : ui.badge("DRAFT", "warn", t("No tariff", "Chưa có giá")); } },
          { label: t("Next version", "Phiên bản kế tiếp"), render: (l) => { const tr = tariffOf(l.location_id); const next = tr && historyOf(tr).find((h) => h.status === "SCHEDULED"); return next ? `${lb("SCHEDULED", "info")}<span class="sub">v${next.version} · ${fmt.date(next.effective_from)}</span>` : '<span class="muted">—</span>'; } },
        ],
      });
      let detail = "";
      if (loc && tariff) {
        const slot = slotAt(tariff, now);
        const low = cheapest(tariff);
        detail = `<div class="grid side mt">
          ${ui.card({
            title: t(`${loc.name} · version ${tariff.version}`, `${loc.name} · phiên bản ${tariff.version}`), sub: `${esc(t(tariff.name[0], tariff.name[1]))} · ${esc(t("in force since", "hiệu lực từ"))} ${fmt.date(tariff.effective_from)}`,
            actions: isOps() ? ui.btn(t("New version", "Phiên bản mới"), { icon: "plus", kind: "primary", sm: true, action: "new-version", attrs: `data-id="${loc.location_id}"` }) : "",
            body: `${bandHtml(tariff.slots, { height: 38 })}<div class="row between mt">${bandLegend()}</div>
              <div class="grid cols-3 mt">
                ${ui.kpi({ label: t("Price now", "Giá hiện tại"), value: fmt.num(slot.price_vnd_kwh), unit: "₫/kWh", foot: `${slot.from}–${slot.to} · ${esc(L(slot.source))}` })}
                ${ui.kpi({ label: t("Cheapest window", "Khung rẻ nhất"), value: `<span style="font-size:18px">${esc(low.windows.join(", "))}</span>`, foot: `${fmt.num(low.price)} ₫/kWh` })}
                ${ui.kpi({ label: t("Peak / off-peak gap", "Chênh cao điểm / thấp điểm"), value: fmt.pct(((Math.max(...tariff.slots.map((s) => s.price_vnd_kwh)) - low.price) / low.price) * 100), foot: t("open question: target gap", "câu hỏi mở: mức chênh mục tiêu") })}
              </div>
              <div class="mt">${ui.table({ rows: tariff.slots, selected: (s) => s === slot, columns: [
                { label: t("Time slot", "Khung giờ"), render: (s) => `<b class="mono">${s.from}–${s.to}</b>` },
                { label: t("Source", "Nguồn"), render: (s) => lb(s.source, s.source === "GRID" ? "info" : s.source === "SOLAR" ? "warn" : "violet") },
                { label: t("Band", "Khung"), render: (s) => lb(s.label, s.label === "PEAK" ? "danger" : s.label === "OFF_PEAK" ? "ok" : "muted") },
                { label: "₫/kWh", align: "right", render: (s) => `<b>${fmt.num(s.price_vnd_kwh)}</b>` },
                { label: t("Hours", "Số giờ"), align: "right", render: (s) => fmt.num((toMin(s.to) - toMin(s.from)) / 60, 1) },
              ] })}</div>`,
          })}
          ${ui.card({
            title: t("Version history", "Lịch sử phiên bản"), flush: true,
            body: `<ul class="list">${[{ version: tariff.version, status: "ACTIVE", effective_from: tariff.effective_from, effective_to: null, slots: tariff.slots, created_by: tariff.created_by || db.people.ops.user.user_id }, ...history].sort((a, b) => b.version - a.version).map((h) => `<li><div class="grow"><div class="row between"><span class="title">v${h.version}</span>${h.status === "ACTIVE" ? ui.badge("ACTIVE") : lb(h.status, h.status === "SCHEDULED" ? "info" : "muted")}</div>
              <div class="meta">${fmt.date(h.effective_from)} → ${h.effective_to ? fmt.date(h.effective_to) : h.status === "SCHEDULED" ? "…" : esc(t("now", "nay"))} · ${fmt.num(Math.min(...h.slots.map((s) => s.price_vnd_kwh)))}–${fmt.num(Math.max(...h.slots.map((s) => s.price_vnd_kwh)))} ₫/kWh · ${ui.userName(h.created_by)}</div>
              <div style="margin-top:6px">${bandHtml(h.slots, { height: 10, now: false, prices: false })}</div></div></li>`).join("")}</ul>`,
          })}
        </div>`;
      } else if (loc) {
        detail = `<div class="mt">${ui.card({ title: loc.name, actions: isOps() ? ui.btn(t("Create first version", "Tạo phiên bản đầu"), { icon: "plus", kind: "primary", sm: true, action: "new-version", attrs: `data-id="${loc.location_id}"` }) : "", body: ui.callout("warn", esc(t("No tariff yet: no charge can start at this station.", "Chưa có biểu giá: chưa thể sạc tại trạm này."))) })}</div>`;
      }
      return `<div data-cp>
        ${ui.pageHead({
          title: t("Tariffs", "Biểu giá"),
          sub: esc(t("Time-of-use prices per station, by power source, versioned with effective dates.", "Giá theo khung giờ cho từng trạm, theo nguồn điện, có phiên bản và ngày hiệu lực.")),
          features: ["PAY-09", "STN-12"],
        })}
        ${ui.callout("info", `<b>${esc(t("Price shown at start = price billed.", "Giá hiển thị khi bắt đầu = giá tính tiền."))}</b> ${esc(t("The app shows the current price before a charge; the session keeps that tariff version and unit price for good (PAY-10). Private stations have their own price (STN-12).", "Ứng dụng hiển thị giá hiện tại trước khi sạc; phiên sạc giữ nguyên phiên bản biểu giá và đơn giá đó (PAY-10). Trạm riêng có giá riêng (STN-12)."))}`)}
        ${!isOps() ? `<div class="mt small muted">${esc(t("Read-only: tariffs are published by G3 Network operations.", "Chỉ xem: biểu giá do bộ phận vận hành G3 Network ban hành."))}</div>` : ""}
        <section class="card mt">${overview}</section>
        ${detail}
      </div>`;
    },
    mount(root) {
      const host = root.querySelector("[data-cp]");
      bindCommon(host);
      delegate(host, { "new-version": (el) => tariffEditor(db.location(el.dataset.id)) });
    },
  });

  /* =====================================================================
     5. Energy (STN-14, STN-08, STN-13)
     ===================================================================== */
  // Planned arrivals from dispatched trips (next 4 hours) that the history alone does not show.
  const PLANNED_DEMAND = { "loc-longthanh": [5, 3, 1, 0], "loc-caimep": [1, 3, 5, 2], "loc-catlai": [1, 1, 0, 1] };

  function forecastFor(loc, locations) {
    const guns = gunsAt(loc.location_id).filter(({ c, s }) => s.is_online && !["Faulted", "Unavailable"].includes(c.status));
    const busy = guns.filter(({ c }) => BUSY.includes(c.status)).length;
    const free = guns.filter(({ c }) => c.status === "Available").length;
    const h0 = new Date().getHours();
    const history = db.charging_sessions.filter((s) => s.location_id === loc.location_id);
    const trucks = db.vehicles.filter((v) => v.live && v.live.is_online && v.live.state === "DRIVING" && v.live.soc_percent <= 40).filter((v) => {
      const near = locations.filter((l) => l.is_public || (l.allowed_organization_ids || []).includes(v.organization_id)).map((l) => ({ l, d: km(l, v.live) })).sort((a, b) => a.d - b.d)[0];
      return near && near.l.location_id === loc.location_id && near.d < 45;
    });
    const arrivals = [0, 1, 2, 3].map((k) => {
      const hour = (h0 + k) % 24;
      const base = history.filter((s) => new Date(s.started_at).getHours() === hour).length / 10;
      return Math.round(base + (PLANNED_DEMAND[loc.location_id] || [0, 0, 0, 0])[k] + (k === 0 ? trucks.length * 0.6 : 0) + r01(loc.location_id + hour) * 1.2);
    });
    // About 55% of the trucks charging now leave within the hour; arrivals stay about half an hour into the next one.
    const fill = arrivals.map((a, k) => (guns.length ? ((k === 0 ? busy * 0.45 : arrivals[k - 1] * 0.5) + a) / guns.length : 0));
    return { loc, guns: guns.length, busy, free, arrivals, fill, trucks };
  }

  G3.page({
    id: "energy",
    path: "/energy",
    title: ["Station energy", "Sản lượng trạm"],
    roles: ["OPERATIONS", "HEAD_ADMIN"],
    features: ["STN-14", "STN-08", "STN-13"],
    nav: { group: "charging", order: 4, icon: "chart" },
    render({ query }) {
      const days = Number(query.days) || 14;
      const locations = db.charging_locations;
      const data = locations.map((l, i) => {
        const e = db.stationEnergy(l.location_id, days);
        const kwh = e.perDay.reduce((a, d) => a + d.energy_kwh, 0);
        const sessions = e.perDay.reduce((a, d) => a + d.sessions, 0);
        const tariff = tariffOf(l.location_id);
        const from = Date.now() - days * db.DAY;
        const done = db.charging_sessions.filter((s) => s.location_id === l.location_id && s.status === "COMPLETED" && new Date(s.started_at).getTime() >= from);
        const green = tariff ? done.filter((s) => slotAt(tariff, new Date(s.started_at).getTime()).source !== "GRID").reduce((a, s) => a + s.energy_kwh, 0) : 0;
        return { l, e, kwh, sessions, color: EXTRA_COLORS[i % EXTRA_COLORS.length], chargers: chargersAt(l.location_id).length, greenShare: kwh ? (green / kwh) * 100 : 0, revenue: done.reduce((a, s) => a + s.cost_vnd, 0) };
      });
      const total = data.reduce((a, d) => a + d.kwh, 0);
      const sessions = data.reduce((a, d) => a + d.sessions, 0);
      const ranked = data.slice().sort((a, b) => b.kwh - a.kwh);
      const labels = Array.from({ length: days }, (_, i) => dayLabel(db.NOW - (days - 1 - i) * db.DAY));
      const pickLoc = data.find((d) => d.l.location_id === query.loc);
      const hourly = Array.from({ length: 24 }, (_, h) => (pickLoc ? [pickLoc] : data).reduce((a, d) => a + d.e.hourly[h].energy_kwh, 0) / days);
      const busiest = hourly.indexOf(Math.max(...hourly));
      const forecasts = locations.filter((l) => l.status === "ACTIVE").map((l) => forecastFor(l, locations.filter((x) => x.status === "ACTIVE")));
      const overloaded = forecasts.filter((f) => f.fill[0] >= 1);
      const alternativeFor = (f) => forecasts.filter((x) => x !== f && x.fill[0] < 0.8 && (x.loc.is_public || !f.loc.is_public)).map((x) => ({ x, d: km(x.loc, f.loc) })).sort((a, b) => a.d - b.d)[0];
      const heat = (v) => `<div title="${fmt.pct(v * 100)}" style="width:34px;height:22px;border-radius:4px;display:grid;place-items:center;font-size:11px;font-weight:600;color:${v >= 0.8 ? "#fff" : v >= 0.5 ? "#422006" : "var(--text)"};background:${v >= 1 ? "#dc2626" : v >= 0.8 ? "#d97706" : v >= 0.5 ? "#fde68a" : "var(--primary-soft)"}">${Math.round(v * 100)}</div>`;
      return `<div data-cp>
        ${ui.pageHead({
          title: t("Station energy", "Sản lượng trạm"),
          sub: esc(t("kWh sold per station and per hour (for time-of-use pricing), and the load expected in the next hours.", "kWh bán theo trạm và theo giờ (để định giá theo khung giờ), và tải dự kiến trong các giờ tới.")),
          features: ["STN-14", "STN-08", "STN-13"],
          actions: `${ui.segmented([["7", t("7 days", "7 ngày")], ["14", t("14 days", "14 ngày")], ["30", t("30 days", "30 ngày")]], String(days), "days")}${ui.btn(t("Export", "Xuất"), { icon: "download", action: "export-energy" })}`,
        })}
        <div class="grid cols-5">
          ${ui.kpi({ label: t("Energy sold", "Điện bán"), icon: "bolt", value: fmt.num(total / 1000, 1), unit: "MWh", foot: `${days} ${t("days", "ngày")}` })}
          ${ui.kpi({ label: t("Sessions", "Phiên sạc"), icon: "plug", value: fmt.num(sessions), foot: `${fmt.num(sessions ? total / sessions : 0, 1)} kWh / ${t("session", "phiên")}` })}
          ${ui.kpi({ label: t("Top station", "Trạm dẫn đầu"), icon: "station", value: `<span style="font-size:17px">${esc(ranked[0] ? ranked[0].l.name : "—")}</span>`, foot: ranked[0] ? `${fmt.pct(total ? (ranked[0].kwh / total) * 100 : 0)} ${t("of the network", "toàn mạng")}` : "" })}
          ${ui.kpi({ label: t("Busiest hour", "Giờ cao điểm"), icon: "clock", value: `${busiest}:00`, foot: `${fmt.num(hourly[busiest])} kWh ${t("on average", "trung bình")}` })}
          ${ui.kpi({ label: t("Overload in < 60 min", "Quá tải trong < 60 phút"), icon: "alert", value: overloaded.length, tone: overloaded.length ? "danger" : "ok", foot: overloaded.map((f) => f.loc.name).join(", ") || t("none", "không có") })}
        </div>
        <div class="grid side mt">
          ${ui.card({ title: t("kWh per day by station", "kWh theo ngày, theo trạm"), body: `${ui.barChart({ labels, stacked: true, height: 240, unit: " kWh", series: data.filter((d) => d.kwh > 0).map((d) => ({ name: d.l.name, values: d.e.perDay.map((x) => Math.round(x.energy_kwh)), color: d.color })) })}<div class="mt">${ui.legend(data.filter((d) => d.kwh > 0).map((d) => [d.l.name, d.color]))}</div>` })}
          ${ui.card({ title: t("Hourly profile", "Biểu đồ theo giờ"), sub: t("average kWh per hour", "kWh trung bình mỗi giờ"), body: `<div class="mb" style="margin-bottom:8px">${ui.select("loc", [["", t("All stations", "Mọi trạm")], ...data.map((d) => [d.l.location_id, d.l.name])], query.loc || "")}</div>` + ui.barChart({ labels: Array.from({ length: 24 }, (_, h) => `${h}h`), height: 240, unit: " kWh", series: [{ name: "kWh", values: hourly.map((v) => Math.round(v)), color: "#0e9f6e" }] }) })}
        </div>
        ${ui.card({ title: t("Ranking", "Xếp hạng trạm"), sub: `${days} ${t("days", "ngày")}`, cls: "mt", flush: true, body: ui.table({
          rows: ranked, href: (d) => `/stations/${d.l.location_id}`,
          columns: [
            { label: "#", render: (d) => `<b>${ranked.indexOf(d) + 1}</b>` },
            { label: t("Station", "Trạm"), render: (d) => `<b>${esc(d.l.name)}</b><span class="sub">${esc(d.l.region)}${d.l.status !== "ACTIVE" ? " · " + esc(G3.label(d.l.status)) : ""}</span>` },
            { label: "kWh", align: "right", render: (d) => fmt.num(d.kwh) },
            { label: t("Share", "Tỷ trọng"), render: (d) => `<div class="row" style="flex-wrap:nowrap"><div style="width:90px">${ui.meter(total ? (d.kwh / total) * 100 : 0)}</div><span class="small num">${fmt.pct(total ? (d.kwh / total) * 100 : 0)}</span></div>` },
            { label: t("Sessions", "Phiên"), align: "right", render: (d) => fmt.num(d.sessions) },
            { label: t("kWh / charger / day", "kWh / trụ / ngày"), align: "right", render: (d) => fmt.num(d.chargers ? d.kwh / d.chargers / days : 0) },
            { label: t("Revenue", "Doanh thu"), align: "right", render: (d) => fmt.vndShort(d.revenue) },
            { label: t("Green share", "Tỷ lệ điện xanh"), align: "right", render: (d) => (d.l.solar_kw || d.l.storage_kwh ? `<span style="color:var(--ok)">${fmt.pct(d.greenShare)}</span>` : '<span class="muted">—</span>') },
            { label: t("Trend", "Xu hướng"), render: (d) => ui.sparkline(d.e.perDay.map((x) => x.energy_kwh), d.color) },
          ],
        }) })}
        <h2 class="mt" style="margin-top:26px">${esc(t("Load forecast — next 4 hours", "Dự báo tải — 4 giờ tới"))}</h2>${ui.featureChips(["STN-08"])}
        ${overloaded.length ? `<div class="mt">${ui.callout("danger", overloaded.map((f) => { const alt = alternativeFor(f); return `<b>${esc(f.loc.name)}</b>: ${esc(t(`${f.arrivals[0]} trucks expected within 60 min for ${f.free} free gun(s).`, `dự kiến ${f.arrivals[0]} xe trong 60 phút tới cho ${f.free} súng trống.`))}${alt ? ` ${esc(t(`Suggest ${alt.x.loc.name} (${fmt.num(alt.d, 0)} km).`, `Gợi ý ${alt.x.loc.name} (${fmt.num(alt.d, 0)} km).`))}` : ""}`; }).join("<br>"))}</div>` : ""}
        <section class="card mt">${ui.table({
          rows: forecasts.sort((a, b) => b.fill[0] - a.fill[0]),
          columns: [
            { label: t("Station", "Trạm"), render: (f) => `<b class="nowrap">${esc(f.loc.name)}</b><span class="sub nowrap">${esc(f.loc.region)}</span>` },
            { label: t("Free / usable guns", "Súng trống / dùng được"), render: (f) => `${ui.badge(f.free ? "Available" : "Faulted", f.free ? "ok" : "danger", `${f.free} / ${f.guns}`)}` },
            { label: t("Arrivals < 60 min", "Xe đến < 60 phút"), align: "right", render: (f) => `<b>${f.arrivals[0]}</b>${f.trucks.length ? `<span class="sub nowrap">${f.trucks.length} ${esc(t("low-battery nearby", "xe pin yếu gần đây"))}</span>` : ""}` },
            { label: t("Fill rate next 4 h (%)", "Tỷ lệ lấp đầy 4 giờ tới (%)"), render: (f) => `<div class="row" style="gap:3px;flex-wrap:nowrap">${f.fill.map(heat).join("")}</div>` },
            { label: t("Warning", "Cảnh báo"), render: (f) => (f.fill[0] >= 1 ? ui.badge("CRITICAL", "danger", t("Overloaded < 60 min", "Quá tải < 60 phút")) : f.fill.slice(1).some((v) => v >= 1) ? ui.badge("WARNING", "warn", t("Overload later", "Quá tải sau đó")) : f.fill[0] >= 0.8 ? ui.badge("WARNING", "warn", t("Busy", "Đông")) : ui.badge("OK", "ok", t("OK", "Ổn"))) },
            { label: t("Suggested alternative", "Trạm thay thế"), render: (f) => { if (f.fill.every((v) => v < 0.8)) return '<span class="muted">—</span>'; const alt = alternativeFor(f); return alt ? `${esc(alt.x.loc.name)}<span class="sub">${fmt.num(alt.d, 0)} km · ${alt.x.free} ${esc(t("free", "trống"))}</span>` : `<span class="muted">${esc(t("none in range", "không có trong phạm vi"))}</span>`; } },
            { label: "", render: (f) => (f.fill[0] >= 0.8 ? ui.btn(t("Warn drivers", "Cảnh báo tài xế"), { icon: "send", sm: true, action: "warn-drivers", attrs: `data-id="${f.loc.location_id}"` }) : "") },
          ],
        })}</section>
        <div class="grid cols-2 mt">
          ${ui.card({ title: t("Recommendations to operations", "Đề xuất cho vận hành"), body: `<ul class="timeline">${[
            ...overloaded.map((f) => { const alt = alternativeFor(f); return `<li><b>${esc(f.loc.name)}</b><div class="small">${esc(alt ? t(`Send the next arrivals to ${alt.x.loc.name}; hold remote starts for trucks above 60%.`, `Điều các xe tới tiếp theo sang ${alt.x.loc.name}; tạm hoãn khởi động từ xa cho xe trên 60%.`) : t("Hold remote starts for trucks above 60%.", "Tạm hoãn khởi động từ xa cho xe trên 60%."))}</div></li>`; }),
            ...forecasts.filter((f) => gunsAt(f.loc.location_id).some(({ c }) => c.status === "Faulted")).map((f) => `<li><b>${esc(f.loc.name)}</b><div class="small">${esc(t("Repair the faulted gun: +1 gun of capacity before the evening peak.", "Sửa súng lỗi: thêm 1 súng trước cao điểm tối."))}</div></li>`),
            ...forecasts.filter((f) => f.fill.every((v) => v < 0.4) && f.guns > 2).slice(0, 2).map((f) => `<li><b>${esc(f.loc.name)}</b><div class="small">${esc(t("Under-used: promote the off-peak price to nearby fleets.", "Ít sử dụng: giới thiệu giá thấp điểm cho các đội xe gần đó."))}</div></li>`),
          ].join("") || `<li>${esc(t("No action needed", "Không cần xử lý"))}</li>`}</ul>` })}
          ${ui.card({ title: t("How the forecast works", "Cách dự báo"), body: `${ui.kv([[t("Arrivals", "Xe đến"), esc(t("history of the same hour + dispatched trips + low-battery trucks whose nearest station is this one", "lịch sử cùng giờ + chuyến đã điều + xe pin yếu có trạm gần nhất là trạm này"))], [t("Fill rate", "Tỷ lệ lấp đầy"), esc(t("(trucks charging + arriving) ÷ usable guns", "(xe đang sạc + xe đến) ÷ súng dùng được"))], [t("Driver warning", "Cảnh báo tài xế"), esc(t("when the destination is overloaded within 60 min, with at least one alternative in range", "khi trạm đích quá tải trong 60 phút, kèm ít nhất một trạm thay thế trong tầm"))]])}<p class="small muted" style="margin:10px 0 0">${esc(t("Prototype: mock forecast. The model waits for STN-07 (queue and waiting time) and RTE-01.", "Bản mẫu: dự báo giả lập. Mô hình chờ STN-07 (hàng đợi) và RTE-01."))}</p>` })}
        </div>
      </div>`;
    },
    mount(root, { query }) {
      const host = root.querySelector("[data-cp]");
      bindCommon(host);
      delegate(host, {
        "warn-drivers": (el) => {
          const loc = db.location(el.dataset.id);
          const active = db.charging_locations.filter((l) => l.status === "ACTIVE");
          const f = forecastFor(loc, active);
          const n = Math.max(1, f.trucks.length + (PLANNED_DEMAND[loc.location_id] || [0])[0]);
          ui.toast(t(`Warning sent to ${n} driver(s) heading to ${loc.name}, with an alternative station`, `Đã gửi cảnh báo tới ${n} tài xế đang tới ${loc.name}, kèm trạm thay thế`), "", "send");
        },
        "export-energy": () => {
          const days = Number(query.days) || 14;
          const rows = db.charging_locations.flatMap((l) => db.stationEnergy(l.location_id, days).perDay.map((d) => ({ station: l.name, date: d.date, energy_kwh: Math.round(d.energy_kwh * 10) / 10, sessions: d.sessions })));
          ui.downloadCsv("station-energy.csv", [{ label: "station", key: "station" }, { label: "date", key: "date" }, { label: "energy_kwh", key: "energy_kwh" }, { label: "sessions", key: "sessions" }], rows);
        },
      });
    },
  });

  /* =====================================================================
     6. Charging policy & warranty (POL-01, POL-03, POL-04, POL-05, POL-06)
     ===================================================================== */
  const inWindow = (time, win) => { const [from, to] = win.split("–"); if (from === "00:00" && to === "24:00") return true; return from < to ? time >= from && time < to : time >= from || time < to; };
  function policyFor(v) {
    const active = db.charging_policies.filter((p) => p.status === "ACTIVE");
    return active.find((p) => p.scope === "TRUCK" && p.scope_ids.includes(v.vehicle_id))
      || active.find((p) => p.scope === "FLEET" && p.scope_ids.some((f) => db.fleetAndDescendants(f).includes(v.fleet_id)))
      || active.find((p) => p.scope === "MODEL" && p.scope_ids.includes(v.vehicle_model_id));
  }
  const coveredTrucks = (p) => db.vehicles.filter((v) => policyFor(v) === p);
  function scopeNames(p) {
    if (p.scope === "MODEL") return p.scope_ids.map((id) => (db.model(id) || {}).code).join(", ");
    if (p.scope === "FLEET") return p.scope_ids.map((id) => { const f = db.fleet(id); return f ? `${f.name} (${db.org(f.organization_id).short_name})` : id; }).join(", ");
    return p.scope_ids.map((id) => (db.vehicle(id) || {}).license_plate).join(", ");
  }

  const policyHistory = {};
  function policyVersions(p) {
    if (!policyHistory[p.policy_id]) {
      policyHistory[p.policy_id] = [];
      for (let v = p.version - 1; v >= 1; v--) {
        const age = p.version - v;
        policyHistory[p.policy_id].push({ version: v, soc_min: Math.max(5, p.soc_min - 5 * age), soc_max: Math.min(100, p.soc_max + 5 * age), max_dc_sessions_per_day: p.max_dc_sessions_per_day + age, max_power_kw: p.max_power_kw + 60 * age, allowed_windows: ["00:00–24:00"], effective_from: new Date(new Date(p.effective_from).getTime() - age * 90 * db.DAY).toISOString(), changed_by: db.people.warranty.user.user_id, change_reason: v === 1 ? "Phiên bản ban đầu" : age === 1 ? "Siết ngưỡng SOC theo khuyến nghị CATL" : "Giảm công suất tối đa theo dữ liệu nhiệt độ pin" });
      }
    }
    return policyHistory[p.policy_id];
  }

  /* POL-02 run over the mock sessions: each finished session checked against the truck's policy. */
  let casesCache = null;
  const TYPE_WEIGHT = { POWER_ABOVE_MAX: 3, TOO_MANY_DC: 3, SOC_ABOVE_MAX: 2, OUTSIDE_WINDOW: 2, SOC_BELOW_MIN: 1 };
  function violationCases() {
    if (casesCache) return casesCache;
    casesCache = [];
    let no = 3100;
    db.compliance.forEach((comp) => {
      const v = db.vehicle(comp.vehicle_id);
      const p = policyFor(v);
      if (!p || !comp.violations_30d) return;
      const sessions = db.charging_sessions.filter((s) => s.vehicle_id === v.vehicle_id && s.status === "COMPLETED").sort((a, b) => (a.started_at < b.started_at ? -1 : 1));
      const found = [];
      sessions.forEach((s) => {
        const start = new Date(s.started_at);
        const sameDay = sessions.filter((x) => x.started_at.slice(0, 10) === s.started_at.slice(0, 10) && x.started_at <= s.started_at).length;
        if (s.soc_end_percent > p.soc_max) found.push({ s, type: "SOC_ABOVE_MAX", measured: s.soc_end_percent, limit: p.soc_max, unit: "%" });
        if (s.soc_start_percent < p.soc_min) found.push({ s, type: "SOC_BELOW_MIN", measured: s.soc_start_percent, limit: p.soc_min, unit: "%" });
        if (s.peak_power_kw > p.max_power_kw) found.push({ s, type: "POWER_ABOVE_MAX", measured: s.peak_power_kw, limit: p.max_power_kw, unit: " kW" });
        if (!p.allowed_windows.some((w) => inWindow(hm(start), w))) found.push({ s, type: "OUTSIDE_WINDOW", measured: hm(start), limit: p.allowed_windows.join(", "), unit: "" });
        if (sameDay > p.max_dc_sessions_per_day) found.push({ s, type: "TOO_MANY_DC", measured: sameDay, limit: p.max_dc_sessions_per_day, unit: "" });
      });
      // One case file per serious violation, at most the 30-day count of the compliance score.
      found.sort((a, b) => TYPE_WEIGHT[b.type] - TYPE_WEIGHT[a.type] || (a.s.started_at < b.s.started_at ? 1 : -1)).slice(0, Math.min(comp.violations_30d, 4)).forEach((f) => {
        const rnd = seeded(f.s.session_id + f.type);
        const detected = new Date(new Date(f.s.ended_at).getTime() + 2 * db.MIN).toISOString();
        const age = Date.now() - new Date(detected).getTime();
        const roll = rnd();
        const status = age < 2 * db.DAY ? "OPEN" : roll < 0.35 ? "UNDER_REVIEW" : roll < 0.7 ? "SENT_TO_WARRANTY" : "CLOSED";
        const history = [{ at: detected, by: null, text: ["Opened automatically from the session (POL-02)", "Tự động mở từ phiên sạc (POL-02)"] }];
        if (status !== "OPEN") history.push({ at: new Date(new Date(detected).getTime() + 0.6 * db.DAY).toISOString(), by: db.people.warranty.user.user_id, text: ["Review started", "Bắt đầu xem xét"] });
        if (status === "SENT_TO_WARRANTY" || status === "CLOSED") history.push({ at: new Date(new Date(detected).getTime() + 1.4 * db.DAY).toISOString(), by: null, text: ["Sent to the warranty team (Monday digest)", "Đã gửi nhóm bảo hành (bản tổng hợp thứ Hai)"] });
        if (status === "CLOSED") history.push({ at: new Date(new Date(detected).getTime() + 2.2 * db.DAY).toISOString(), by: db.people.warranty.user.user_id, text: ["Closed: warning sent to the customer, no penalty", "Đã đóng: gửi nhắc nhở khách hàng, không phạt"] });
        casesCache.push({
          case_id: `VC-${no++}`, vehicle_id: v.vehicle_id, organization_id: v.organization_id, session: f.s, policy: p, policy_version: p.version,
          type: f.type, measured: f.measured, limit: f.limit, unit: f.unit, detected_at: detected, status, history,
          telematics: { soc_end: f.s.soc_end_percent, pack_temp_max: Math.round(36 + rnd() * 9), pack_voltage: Math.round(590 + rnd() * 45), current_max: Math.round(250 + rnd() * 200), odometer_km: Math.round(v.odometer_km - rnd() * 2000), position: (db.location(f.s.location_id) || {}).name },
        });
      });
    });
    casesCache.sort((a, b) => (a.detected_at < b.detected_at ? 1 : -1));
    return casesCache;
  }

  function behaviourText(c) {
    const plate = db.vehicle(c.vehicle_id).license_plate;
    if (c.type === "SOC_ABOVE_MAX") return t(`${plate} charged to ${c.measured}% (limit ${c.limit}%)`, `${plate} sạc tới ${c.measured}% (giới hạn ${c.limit}%)`);
    if (c.type === "SOC_BELOW_MIN") return t(`${plate} started charging at ${c.measured}% (minimum ${c.limit}%)`, `${plate} bắt đầu sạc khi pin còn ${c.measured}% (tối thiểu ${c.limit}%)`);
    if (c.type === "POWER_ABOVE_MAX") return t(`${plate} charged at ${c.measured} kW (limit ${c.limit} kW)`, `${plate} sạc ở ${c.measured} kW (giới hạn ${c.limit} kW)`);
    if (c.type === "OUTSIDE_WINDOW") return t(`${plate} charged at ${c.measured}, outside ${c.limit}`, `${plate} sạc lúc ${c.measured}, ngoài khung ${c.limit}`);
    return t(`${plate}: ${c.measured} fast charges in one day (limit ${c.limit})`, `${plate}: ${c.measured} lần sạc nhanh trong một ngày (giới hạn ${c.limit})`);
  }
  function fixText(c) {
    const p = c.policy;
    if (c.type === "SOC_ABOVE_MAX") return t(`Stop at ${p.soc_max}%: set the target on the charger screen.`, `Dừng sạc ở ${p.soc_max}%: đặt mức mục tiêu trên màn hình trụ.`);
    if (c.type === "SOC_BELOW_MIN") return t(`Plan a charge before the battery drops below ${p.soc_min}%.`, `Lên kế hoạch sạc trước khi pin xuống dưới ${p.soc_min}%.`);
    if (c.type === "POWER_ABOVE_MAX") return t(`Use a gun limited to ${p.max_power_kw} kW, or ask operations to cap the power.`, `Dùng súng giới hạn ${p.max_power_kw} kW, hoặc đề nghị vận hành giới hạn công suất.`);
    if (c.type === "OUTSIDE_WINDOW") return t(`Charge within ${p.allowed_windows.join(", ")}.`, `Sạc trong khung ${p.allowed_windows.join(", ")}.`);
    return t(`Keep to ${p.max_dc_sessions_per_day} fast charge(s) a day; top up at the depot overnight.`, `Giữ tối đa ${p.max_dc_sessions_per_day} lần sạc nhanh mỗi ngày; sạc bổ sung tại bãi qua đêm.`);
  }
  const caseTone = (s) => (s === "OPEN" ? "warn" : s === "UNDER_REVIEW" ? "info" : s === "SENT_TO_WARRANTY" ? "violet" : "muted");

  function socRangeBar(min, max) {
    return `<div style="position:relative;height:10px;border-radius:5px;background:var(--danger-soft);width:100%;max-width:220px"><div style="position:absolute;left:${min}%;width:${max - min}%;top:0;bottom:0;background:var(--primary);border-radius:5px"></div></div><div class="small muted" style="max-width:220px;display:flex;justify-content:space-between"><span>0</span><span>${min}–${max}%</span><span>100</span></div>`;
  }

  function policyForm(p) {
    const isNew = !p;
    const models = db.vehicle_models.map((m) => [`MODEL:${m.vehicle_model_id}`, `${t("Model", "Dòng xe")} · ${m.code}`]);
    const fleets = db.fleets.map((f) => [`FLEET:${f.fleet_id}`, `${t("Fleet", "Đội")} · ${f.name} (${db.org(f.organization_id).short_name})`]);
    ui.formModal({
      title: isNew ? t("New charging policy", "Chính sách sạc mới") : t(`New version of "${p.name}" (v${p.version + 1})`, `Phiên bản mới của "${p.name}" (v${p.version + 1})`),
      intro: ui.callout("info", esc(t("A version takes effect at the chosen date; the previous one is kept, and every violation records the version it was checked against.", "Phiên bản có hiệu lực từ ngày đã chọn; phiên bản cũ vẫn được lưu và mỗi vi phạm ghi lại phiên bản đã dùng để kiểm tra."))),
      wide: true,
      fields: [
        { name: "name", label: t("Policy name", "Tên chính sách"), required: true, value: p ? p.name : "", full: true },
        { name: "scope", label: t("Applies to", "Áp dụng cho"), type: "select", options: [["MODEL", L("MODEL")], ["FLEET", L("FLEET")], ["TRUCK", L("TRUCK")]], value: p ? p.scope : "MODEL" },
        { name: "plates", label: t("Truck plates (scope truck)", "Biển số xe (phạm vi xe)"), value: p && p.scope === "TRUCK" ? scopeNames(p) : "", placeholder: "51C-123.45, 60C-678.90" },
        { name: "targets", label: t("Models or fleets", "Dòng xe hoặc đội xe"), type: "checkboxes", options: [...models, ...fleets], value: p && p.scope !== "TRUCK" ? p.scope_ids.map((id) => `${p.scope}:${id}`) : [], full: true },
        { name: "soc_min", label: t("Battery minimum (%)", "Pin tối thiểu (%)"), type: "number", required: true, value: p ? p.soc_min : 20 },
        { name: "soc_max", label: t("Battery maximum (%)", "Pin tối đa (%)"), type: "number", required: true, value: p ? p.soc_max : 90 },
        { name: "windows", label: t("Allowed hours", "Khung giờ cho phép"), required: true, value: p ? p.allowed_windows.join(", ") : "00:00–24:00", hint: t("e.g. 22:00–04:00, 10:00–14:00", "ví dụ 22:00–04:00, 10:00–14:00") },
        { name: "max_dc", label: t("Max fast charges per day", "Số lần sạc nhanh tối đa/ngày"), type: "number", required: true, value: p ? p.max_dc_sessions_per_day : 3 },
        { name: "max_power", label: t("Max power (kW)", "Công suất tối đa (kW)"), type: "number", required: true, value: p ? p.max_power_kw : 240 },
        { name: "effective", label: t("Effective from", "Hiệu lực từ"), type: "date", required: true, value: new Date().toISOString().slice(0, 10) },
        { name: "reason", label: t("Reason for the change", "Lý do thay đổi"), type: "textarea", required: true, full: true },
      ],
      submitLabel: isNew ? t("Create policy", "Tạo chính sách") : t("Publish version", "Ban hành phiên bản"),
      onSubmit(values) {
        const min = Number(values.soc_min), max = Number(values.soc_max);
        if (!(min >= 0 && max <= 100 && min < max)) return t("Battery range must be 0–100% with minimum below maximum.", "Khoảng pin phải trong 0–100% và mức tối thiểu nhỏ hơn tối đa.");
        const windows = values.windows.split(",").map((w) => w.trim().replace("-", "–")).filter(Boolean);
        if (!windows.length || !windows.every((w) => /^([01]\d|2[0-4]):[0-5]\d–([01]\d|2[0-4]):[0-5]\d$/.test(w))) return t("Write the hours as HH:MM–HH:MM, separated by commas.", "Ghi khung giờ dạng HH:MM–HH:MM, cách nhau bằng dấu phẩy.");
        if (!(Number(values.max_dc) >= 1)) return t("At least one fast charge per day.", "Tối thiểu một lần sạc nhanh mỗi ngày.");
        if (!(Number(values.max_power) >= 50 && Number(values.max_power) <= 480)) return t("Max power must be 50–480 kW.", "Công suất tối đa phải từ 50 đến 480 kW.");
        if (values.effective < new Date().toISOString().slice(0, 10)) return t("The effective date cannot be in the past.", "Ngày hiệu lực không được ở quá khứ.");
        let ids;
        if (values.scope === "TRUCK") {
          const plates = values.plates.split(",").map((x) => x.trim().toUpperCase()).filter(Boolean);
          const trucks = plates.map((pl) => db.vehicles.find((v) => v.license_plate.toUpperCase() === pl));
          if (!plates.length || trucks.some((x) => !x)) return t("Enter existing truck plates, separated by commas.", "Nhập biển số xe đã có, cách nhau bằng dấu phẩy.");
          ids = trucks.map((x) => x.vehicle_id);
        } else {
          ids = values.targets.filter((x) => x.startsWith(values.scope + ":")).map((x) => x.split(":")[1]);
          if (!ids.length) return t(`Tick at least one ${values.scope === "MODEL" ? "model" : "fleet"}.`, `Chọn ít nhất một ${values.scope === "MODEL" ? "dòng xe" : "đội xe"}.`);
        }
        const fields = { name: values.name, scope: values.scope, scope_ids: ids, soc_min: min, soc_max: max, allowed_windows: windows, max_dc_sessions_per_day: Number(values.max_dc), max_power_kw: Number(values.max_power), effective_from: new Date(`${values.effective}T00:00:00`).toISOString() };
        if (isNew) {
          db.charging_policies.push({ policy_id: db.uid("pol"), version: 1, status: "ACTIVE", ...fields });
          ui.toast(t("Policy created", "Đã tạo chính sách"));
        } else {
          policyVersions(p).unshift({ version: p.version, soc_min: p.soc_min, soc_max: p.soc_max, max_dc_sessions_per_day: p.max_dc_sessions_per_day, max_power_kw: p.max_power_kw, allowed_windows: p.allowed_windows, effective_from: p.effective_from, changed_by: me(), change_reason: values.reason.slice(0, 200) });
          Object.assign(p, fields, { version: p.version + 1 });
          ui.toast(t(`Version ${p.version} published`, `Đã ban hành phiên bản ${p.version}`));
        }
        casesCache = null;
        G3.render();
        return true;
      },
    });
  }

  function caseModal(c) {
    const s = c.session;
    const st = db.station(s.station_id);
    const series = measurementSeries(s);
    const editable = canPolicy();
    const foot = `${editable && c.status === "OPEN" ? ui.btn(t("Start review", "Bắt đầu xem xét"), { action: "c-review" }) : ""}${editable && !["SENT_TO_WARRANTY", "CLOSED"].includes(c.status) ? ui.btn(t("Send to warranty team", "Gửi nhóm bảo hành"), { icon: "send", kind: "primary", action: "c-send" }) : ""}${editable && c.status !== "CLOSED" ? ui.btn(t("Close case", "Đóng hồ sơ"), { action: "c-close" }) : ""}<button class="btn" data-close>${esc(t("Close", "Đóng"))}</button>`;
    ui.modal({
      title: `${c.case_id} · ${L(c.type)}`, wide: true, foot,
      body: `${ui.callout("danger", `<b>${esc(behaviourText(c))}</b><br>${esc(t("How to fix", "Cách khắc phục"))}: ${esc(fixText(c))}`)}
        <div class="grid cols-2 mt">
          <div><h4>${esc(t("Violation", "Vi phạm"))}</h4>${ui.kv([
            [t("Type", "Loại"), lb(c.type, "danger")],
            [t("Measured / limit", "Đo được / giới hạn"), `<b>${esc(String(c.measured))}${esc(c.unit)}</b> / ${esc(String(c.limit))}${esc(c.unit)}`],
            [t("Policy", "Chính sách"), `${esc(c.policy.name)} · v${c.policy_version}`],
            [t("Detected", "Phát hiện"), fmt.dateTime(c.detected_at)],
            [t("Status", "Trạng thái"), lb(c.status, caseTone(c.status))],
          ])}
          <h4 class="mt">${esc(t("Session snapshot (frozen at detection)", "Ảnh chụp phiên sạc (cố định khi phát hiện)"))}</h4>${ui.kv([
            [t("Session", "Phiên"), `<span class="mono">#${esc(s.ocpp_transaction_id)}</span>`],
            [t("Station / charger", "Trạm / trụ"), `${esc((db.location(s.location_id) || {}).name || "—")} · <span class="mono">${esc(st ? st.ocpp_identity : "—")}</span>`],
            [t("Time", "Thời gian"), `${fmt.dateTime(s.started_at)} → ${fmt.time(s.ended_at)}`],
            [t("Energy", "Điện năng"), fmt.kwh(s.energy_kwh)],
            [t("Battery", "Pin"), `${s.soc_start_percent}% → ${s.soc_end_percent}%`],
            [t("Peak power", "Công suất đỉnh"), `${fmt.num(s.peak_power_kw)} kW`],
          ])}</div>
          <div><h4>${esc(t("Matching telematics data", "Dữ liệu telematics tương ứng"))}</h4>${ui.kv([
            [t("Truck", "Xe"), esc(db.vehicle(c.vehicle_id).license_plate)],
            [t("Battery at end (T-Box)", "Pin khi kết thúc (T-Box)"), `${c.telematics.soc_end}%`],
            [t("Max pack temperature", "Nhiệt độ pin cao nhất"), `${c.telematics.pack_temp_max} °C`],
            [t("Pack voltage", "Điện áp pin"), `${c.telematics.pack_voltage} V`],
            [t("Max charge current", "Dòng sạc lớn nhất"), `${c.telematics.current_max} A`],
            [t("Odometer", "Số km"), fmt.km(c.telematics.odometer_km)],
            [t("Position", "Vị trí"), esc(c.telematics.position || "—")],
          ])}
          ${series.length ? `<div class="mt">${ui.lineChart({ labels: series.map((x) => fmt.time(x.at)), height: 130, yMin: 0, yMax: 100, unit: "%", series: [{ name: "SOC", values: series.map((x) => x.soc), color: "#0e9f6e", area: true }] })}</div>` : ""}</div>
        </div>
        <h4 class="mt">${esc(t("Handling history", "Lịch sử xử lý"))}</h4>
        <ul class="timeline mt">${c.history.map((h) => `<li><div class="when">${fmt.dateTime(h.at)} · ${ui.userName(h.by)}</div>${esc(t(h.text[0], h.text[1]))}</li>`).join("")}</ul>`,
      onMount(el, close) {
        const add = (text) => c.history.push({ at: new Date().toISOString(), by: me(), text });
        const on = (name, fn) => { const b = el.querySelector(`[data-action=${name}]`); if (b) b.addEventListener("click", fn); };
        on("c-review", () => { c.status = "UNDER_REVIEW"; add(["Review started", "Bắt đầu xem xét"]); close(); G3.render(); ui.toast(t(`${c.case_id} under review`, `${c.case_id} đang được xem xét`)); });
        on("c-send", () => { c.status = "SENT_TO_WARRANTY"; add(["Sent to the warranty team on demand", "Đã gửi nhóm bảo hành theo yêu cầu"]); close(); G3.render(); ui.toast(t(`${c.case_id} sent to the warranty team`, `Đã gửi ${c.case_id} tới nhóm bảo hành`), "", "send"); });
        on("c-close", () => { close(); ui.reasonModal({ title: t(`Close ${c.case_id}`, `Đóng ${c.case_id}`), submitLabel: t("Close case", "Đóng hồ sơ"), onSubmit(reason) { c.status = "CLOSED"; add([`Closed: ${reason}`, `Đã đóng: ${reason}`]); G3.render(); ui.toast(t(`${c.case_id} closed`, `Đã đóng ${c.case_id}`)); } }); });
      },
    });
  }

  function policiesTab(policies) {
    if (!policies.length) return ui.empty(t("No policy applies to your trucks", "Không có chính sách nào áp dụng cho xe của bạn"));
    return `${ui.featureChips(["POL-01"])}<div class="grid cols-2">${policies.map((p) => {
      const trucks = coveredTrucks(p).filter((v) => scope.isInternal() || scope.vehicleIds().has(v.vehicle_id));
      const versions = policyVersions(p);
      return ui.card({
        title: p.name, sub: `v${p.version} · ${esc(L(p.scope))}`,
        actions: canPolicy() ? ui.btn(t("New version", "Phiên bản mới"), { icon: "plus", sm: true, action: "policy-version", attrs: `data-id="${p.policy_id}"` }) : "",
        body: `<div class="grid cols-2" style="gap:12px 20px">
            <div><div class="small muted">${esc(t("Battery window", "Khoảng pin cho phép"))}</div>${socRangeBar(p.soc_min, p.soc_max)}</div>
            <div><div class="small muted">${esc(t("Allowed hours", "Khung giờ cho phép"))}</div><div class="row" style="gap:4px;margin-top:4px">${p.allowed_windows.map((w) => ui.tag(w)).join("")}</div></div>
          </div>
          <div class="divider"></div>
          ${ui.kv([
            [t("Max fast charges / day", "Sạc nhanh tối đa/ngày"), fmt.num(p.max_dc_sessions_per_day)],
            [t("Max power", "Công suất tối đa"), `${fmt.num(p.max_power_kw)} kW`],
            [t("Applies to", "Áp dụng cho"), `${lb(p.scope, "info")} ${esc(scopeNames(p))}`],
            [t("Trucks covered", "Số xe áp dụng"), `${trucks.length}${scope.isInternal() ? "" : ` ${esc(t("of yours", "của bạn"))}`}`],
            [t("In force since", "Hiệu lực từ"), fmt.date(p.effective_from)],
          ])}
          <div class="divider"></div>
          <div class="small muted" style="margin-bottom:6px">${esc(t("Version history", "Lịch sử phiên bản"))}</div>
          <ul class="timeline">${[{ version: p.version, soc_min: p.soc_min, soc_max: p.soc_max, max_dc_sessions_per_day: p.max_dc_sessions_per_day, max_power_kw: p.max_power_kw, effective_from: p.effective_from, current: true }, ...versions].map((h) => `<li><div class="when">v${h.version} · ${fmt.date(h.effective_from)}${h.current ? " · " + esc(t("current", "hiện hành")) : ""}</div><span class="small">SOC ${h.soc_min}–${h.soc_max}% · ${h.max_dc_sessions_per_day} DC/${esc(t("day", "ngày"))} · ${h.max_power_kw} kW${h.change_reason ? ` · <span class="muted">${esc(h.change_reason)}</span>` : ""}</span></li>`).join("")}</ul>`,
      });
    }).join("")}</div>`;
  }

  function complianceTab(rows, query, cases) {
    const order = { HIGH: 0, MEDIUM: 1, LOW: 2 };
    const list = rows.filter((c) => !query.risk || c.risk === query.risk).sort((a, b) => order[a.risk] - order[b.risk] || a.score - b.score);
    const avg = rows.reduce((a, c) => a + c.score, 0) / (rows.length || 1);
    const mainType = (vid) => { const own = cases.filter((c) => c.vehicle_id === vid); if (!own.length) return null; const counts = {}; own.forEach((c) => { counts[c.type] = (counts[c.type] || 0) + 1; }); return Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0]; };
    return `${ui.featureChips(["POL-04", "POL-05"])}<div class="grid cols-4">
        ${ui.kpi({ label: t("Average score", "Điểm trung bình"), icon: "shield", value: fmt.num(avg), unit: "/100" })}
        ${ui.kpi({ label: t("High-risk trucks", "Xe rủi ro cao"), icon: "alert", value: rows.filter((c) => c.risk === "HIGH").length, tone: "danger" })}
        ${ui.kpi({ label: t("Violations (30 d)", "Vi phạm (30 ngày)"), icon: "activity", value: rows.reduce((a, c) => a + c.violations_30d, 0) })}
        ${ui.kpi({ label: t("Compliant trucks", "Xe tuân thủ tốt"), icon: "check", value: fmt.pct((rows.filter((c) => c.score >= 85).length / (rows.length || 1)) * 100), tone: "ok", foot: t("score ≥ 85", "điểm ≥ 85") })}
      </div>
      <section class="card mt">
        <div class="toolbar">${ui.segmented([["", t("All", "Tất cả")], ["HIGH", G3.label("HIGH")], ["MEDIUM", G3.label("MEDIUM")], ["LOW", G3.label("LOW")]], query.risk || "", "risk")}<span class="small muted grow">${esc(t("Sorted by risk, then score. Click a truck for its case files.", "Sắp theo rủi ro, rồi theo điểm. Bấm vào xe để xem hồ sơ vi phạm."))}</span>${scope.isInternal() ? ui.btn(t("Export for warranty team", "Xuất cho nhóm bảo hành"), { icon: "download", sm: true, action: "export-compliance" }) : ""}</div>
        ${ui.table({
          rows: list, empty: t("No truck", "Không có xe"),
          rowAttrs: (c) => `data-action="truck-cases" data-id="${c.vehicle_id}" style="cursor:pointer"`,
          columns: [
            { label: t("Truck", "Xe"), render: (c) => ui.vehicleCell(db.vehicle(c.vehicle_id)) },
            ...(scope.isInternal() ? [{ label: t("Organization", "Tổ chức"), render: (c) => `<span class="nowrap">${ui.orgName(c.organization_id)}</span>` }] : []),
            { label: t("Fleet / model", "Đội / dòng xe"), render: (c) => { const v = db.vehicle(c.vehicle_id); const f = db.fleet(v.fleet_id); return `<span class="nowrap">${f ? esc(f.name) : "—"}</span><span class="sub">${esc(db.model(v.vehicle_model_id).code)}</span>`; } },
            { label: t("Score", "Điểm"), render: (c) => `<div class="row" style="flex-wrap:nowrap"><div style="width:80px">${ui.meter(c.score, c.score < 70 ? "danger" : c.score < 85 ? "warn" : "")}</div><b class="num">${c.score}</b></div>` },
            { label: t("Violations 30 d", "Vi phạm 30 ngày"), align: "right", render: (c) => c.violations_30d },
            { label: t("Main issue", "Lỗi chính"), render: (c) => { const ty = mainType(c.vehicle_id); return ty ? `<span class="nowrap">${esc(L(ty))}</span>` : '<span class="muted">—</span>'; } },
            { label: t("Trend (8 wk)", "Xu hướng (8 tuần)"), render: (c) => ui.sparkline(c.trend, c.trend[c.trend.length - 1] >= c.trend[0] ? "#0e9f6e" : "#dc2626") },
            { label: t("Battery warranty", "Bảo hành pin"), render: (c) => { const w = db.warranties.find((x) => x.object_kind === "BATTERY" && x.object_id === db.vehicle(c.vehicle_id).battery_id); return w ? `<span title="${esc(w.status_reason || "")}">${ui.badge(w.status)}</span>${w.status_reason ? `<span class="sub" style="display:block;max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(w.status_reason)}">${esc(w.status_reason)}</span>` : ""}` : "—"; } },
            { label: t("Risk", "Rủi ro"), render: (c) => ui.badge(c.risk) },
          ],
        })}
      </section>
      <p class="small muted mt">${esc(t("Scoring in the prototype: 100 minus weighted violations over 30 days. The real method waits for the policy values (open question).", "Cách tính trong bản mẫu: 100 trừ vi phạm có trọng số trong 30 ngày. Phương pháp thật chờ giá trị chính sách (câu hỏi mở)."))}</p>`;
  }

  function casesTab(cases, query) {
    const list = cases.filter((c) => (!query.status || c.status === query.status) && (!query.type || c.type === query.type) && (!query.truck || c.vehicle_id === query.truck));
    return `${ui.featureChips(["POL-06"])}${ui.callout("info", esc(t("Each case file freezes the session and the matching T-Box data at detection. Files go to the warranty team every Monday at 08:00, or on demand.", "Mỗi hồ sơ cố định dữ liệu phiên sạc và T-Box tại thời điểm phát hiện. Hồ sơ được gửi nhóm bảo hành lúc 08:00 thứ Hai hằng tuần, hoặc khi có yêu cầu.")))}
      <section class="card mt">
        <div class="toolbar">
          ${ui.select("status", [["", t("All statuses", "Mọi trạng thái")], ...["OPEN", "UNDER_REVIEW", "SENT_TO_WARRANTY", "CLOSED"].map((s) => [s, L(s)])], query.status || "")}
          ${ui.select("type", [["", t("All types", "Mọi loại")], ...Object.keys(TYPE_WEIGHT).map((s) => [s, L(s)])], query.type || "")}
          ${query.truck ? `<span class="tag">${esc(db.vehicle(query.truck).license_plate)}</span>${ui.btn("", { icon: "x", sm: true, kind: "ghost", action: "clear-truck" })}` : ""}
          <span class="grow"></span><span class="small muted">${list.length} ${esc(t("case files", "hồ sơ"))}</span>
        </div>
        ${ui.table({
          rows: list, empty: t("No case file", "Không có hồ sơ"),
          rowAttrs: (c) => `data-action="case" data-id="${c.case_id}" style="cursor:pointer"`,
          columns: [
            { label: t("Case", "Hồ sơ"), render: (c) => `<b class="mono">${esc(c.case_id)}</b><span class="sub">${fmt.dateTime(c.detected_at)}</span>` },
            { label: t("Truck", "Xe"), render: (c) => `<b>${esc(db.vehicle(c.vehicle_id).license_plate)}</b>${scope.isInternal() ? `<span class="sub">${ui.orgName(c.organization_id)}</span>` : ""}` },
            { label: t("Type", "Loại"), render: (c) => lb(c.type, "danger") },
            { label: t("Measured / limit", "Đo được / giới hạn"), render: (c) => `<b>${esc(String(c.measured))}${esc(c.unit)}</b> <span class="muted">/ ${esc(String(c.limit))}${esc(c.unit)}</span>` },
            { label: t("Session", "Phiên sạc"), render: (c) => `${esc((db.location(c.session.location_id) || {}).name || "—")}<span class="sub mono">#${esc(c.session.ocpp_transaction_id)}</span>` },
            { label: t("Policy", "Chính sách"), render: (c) => `${esc(c.policy.name)}<span class="sub">v${c.policy_version}</span>` },
            { label: t("Status", "Trạng thái"), render: (c) => lb(c.status, caseTone(c.status)) },
          ],
        })}
      </section>`;
  }

  function alertsTab(cases, notes) {
    const recent = cases.slice(0, 14);
    const byType = Object.keys(TYPE_WEIGHT).map((ty) => ({ ty, n: cases.filter((c) => c.type === ty).length })).filter((x) => x.n);
    return `${ui.featureChips(["POL-03"])}<div class="grid side">
      ${ui.card({ title: t("Warranty-risk alerts", "Cảnh báo nguy cơ mất bảo hành"), sub: t("sent in real time to the driver app and the fleet manager", "gửi ngay tới ứng dụng tài xế và quản lý đội xe"), flush: true, body: recent.length || notes.length ? `<ul class="list">${notes.map((n) => `<li><div class="sev-icon ${n.severity}">${ui.icon("shield", 16)}</div><div class="grow"><div class="title">${esc(t(n.title[0], n.title[1]))}</div><div class="meta">${esc(t("Fix: stop charging at 95% — full charges wear the LFP pack faster.", "Khắc phục: dừng sạc ở 95% — sạc đầy làm pin LFP xuống cấp nhanh hơn."))} · ${fmt.ago(n.created_at)}</div></div>${ui.badge(n.severity)}</li>`).join("")}${recent.map((c) => `<li class="clickable" data-action="case" data-id="${c.case_id}"><div class="sev-icon ${TYPE_WEIGHT[c.type] >= 3 ? "CRITICAL" : "WARNING"}">${ui.icon("shield", 16)}</div><div class="grow"><div class="title">${esc(behaviourText(c))}</div><div class="meta">${esc(t("Fix", "Khắc phục"))}: ${esc(fixText(c))}</div><div class="meta">${esc(t("Sent to driver app · fleet manager", "Đã gửi ứng dụng tài xế · quản lý đội xe"))} · ${fmt.ago(c.detected_at)}</div></div>${ui.badge(TYPE_WEIGHT[c.type] >= 3 ? "CRITICAL" : "WARNING")}</li>`).join("")}</ul>` : ui.empty(t("No warranty-risk alert", "Không có cảnh báo")) })}
      <div class="stack">
        ${ui.card({ title: t("Weekly summary", "Tóm tắt tuần"), sub: t("sent every Monday", "gửi mỗi thứ Hai"), body: byType.length ? `${ui.hBars({ labels: byType.map((x) => L(x.ty)), series: [{ values: byType.map((x) => x.n), color: "#dc2626" }] })}<div class="divider"></div><div class="small"><b>${esc(t("Advice", "Lời khuyên"))}</b><ul style="margin:6px 0 0;padding-left:18px">${byType.slice(0, 3).map((x) => `<li>${esc(fixText(cases.find((c) => c.type === x.ty)))}</li>`).join("")}</ul></div>` : ui.empty() })}
        ${ui.card({ title: t("Who is told", "Ai nhận cảnh báo"), body: ui.kv([[t("Driver", "Tài xế"), esc(t("App push, right after the session", "Thông báo ứng dụng ngay sau phiên sạc"))], [t("Fleet manager", "Quản lý đội xe"), esc(t("Portal inbox + e-mail if on file", "Hộp thư cổng web + e-mail nếu có"))], [t("Warranty team", "Nhóm bảo hành"), esc(t("Case file (POL-06)", "Hồ sơ vi phạm (POL-06)"))]]) })}
      </div></div>`;
  }

  G3.page({
    id: "policy",
    path: "/policy",
    title: ["Charging policy", "Chính sách sạc"],
    roles: ["WARRANTY", "FLEET_MANAGER", "ORG_ADMIN"],
    features: ["POL-01", "POL-03", "POL-04", "POL-05", "POL-06"],
    nav: { group: "charging", order: 5, icon: "shield" },
    render({ query }) {
      const ids = scope.vehicleIds();
      const policies = db.charging_policies.filter((p) => scope.isInternal() || coveredTrucks(p).some((v) => ids.has(v.vehicle_id)));
      const rows = scope.byVehicle(db.compliance);
      const cases = violationCases().filter((c) => ids.has(c.vehicle_id));
      const notes = scope.byVehicle(db.notifications).filter((n) => n.notification_type === "POLICY_VIOLATION");
      const tabs = [["policies", t("Policies", "Chính sách")], ["compliance", t("Compliance", "Tuân thủ")], ["cases", t("Violation case files", "Hồ sơ vi phạm")], ["alerts", t("Warranty-risk alerts", "Cảnh báo bảo hành")]];
      const tab = tabs.some(([k]) => k === query.tab) ? query.tab : "compliance";
      const body = tab === "policies" ? policiesTab(policies) : tab === "cases" ? casesTab(cases, query) : tab === "alerts" ? alertsTab(cases, notes) : complianceTab(rows, query, cases);
      return `<div data-cp>
        ${ui.pageHead({
          title: t("Charging policy & warranty", "Chính sách sạc & bảo hành"),
          sub: esc(scope.isInternal() ? t("Warranty-linked charging rules, how each truck complies, and the evidence behind every violation.", "Quy tắc sạc gắn với bảo hành, mức tuân thủ của từng xe và bằng chứng của mỗi vi phạm.") : t("The charging rules that keep your trucks' battery warranty, and how each truck complies. Read-only.", "Các quy tắc sạc giúp giữ bảo hành pin cho xe của bạn, và mức tuân thủ của từng xe. Chỉ xem.")),
          features: ["POL-01", "POL-03", "POL-04", "POL-05", "POL-06"],
          actions: canPolicy() ? ui.btn(t("New policy", "Chính sách mới"), { icon: "plus", kind: "primary", action: "policy-new" }) : "",
        })}
        ${ui.tabs(tabs, tab)}
        ${body}
      </div>`;
    },
    mount(root) {
      const host = root.querySelector("[data-cp]");
      bindCommon(host);
      delegate(host, {
        "policy-new": () => policyForm(null),
        "policy-version": (el) => policyForm(db.charging_policies.find((p) => p.policy_id === el.dataset.id)),
        "truck-cases": (el) => G3.setQuery({ tab: "cases", truck: el.dataset.id, risk: "" }),
        "clear-truck": () => G3.setQuery({ truck: "" }),
        case: (el) => caseModal(violationCases().find((c) => c.case_id === el.dataset.id)),
        "export-compliance": () => {
          const rows = scope.byVehicle(db.compliance);
          ui.downloadCsv("charging-compliance.csv", [
            { label: "plate", csv: (c) => db.vehicle(c.vehicle_id).license_plate }, { label: "vin", csv: (c) => db.vehicle(c.vehicle_id).vin },
            { label: "organization", csv: (c) => db.org(c.organization_id).name }, { label: "score", key: "score" }, { label: "violations_30d", key: "violations_30d" }, { label: "risk", key: "risk" },
          ], rows);
        },
      });
    },
  });
})();
