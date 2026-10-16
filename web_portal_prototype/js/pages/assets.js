/* Assets: the screens that keep the physical things the service runs on.
   - vehicles    /vehicles     registry, ownership transfer, activation, model catalog (VEH-01, VEH-02, VEH-03, VEH-05)
   - devices     /devices      T-Box registry, health, mounting, remote configuration, identity (DEV-01, 02, 04-08)
   - batteries   /batteries    battery models, packs, install history, state of health (BAT-01, MON-07)
   - warranties  /warranties   warranty register, usage against limits, warranty risk (WAR-01, POL-05)
   - maintenance /maintenance  reminders, repair cases, cost report, depreciation (MNT-01, MNT-03..07)
   Extra mock data (install histories, credentials, repair cases, costs) is derived
   here from record IDs, so it is the same on every load. */

(function () {
  const { ui, db, fmt, scope } = G3;
  const esc = G3.esc;
  const DAY = db.DAY;
  const NOW = db.NOW;

  /* ---------- Deterministic helpers ---------- */
  function hashOf(text) {
    let h = 2166136261;
    for (const ch of String(text)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  function rng(key) {
    let s = hashOf(key) || 1;
    return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; };
  }
  /** A mean-reverting walk of n points, clamped to [lo, hi]. */
  function walk(key, n, base, spread, lo, hi) {
    const r = rng(key);
    let v = base;
    return Array.from({ length: n }, () => { v = Math.min(hi, Math.max(lo, base + (v - base) * 0.6 + (r() - 0.5) * spread)); return Math.round(v * 10) / 10; });
  }
  const isoAgo = (days) => new Date(NOW - days * DAY).toISOString();
  const todayIso = () => new Date().toISOString().slice(0, 10);
  const daysUntil = (value) => Math.round((new Date(value).getTime() - Date.now()) / DAY);
  const monthLabel = (d) => `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getFullYear()).slice(2)}`;
  const me = () => (scope.user() || {}).user_id || null;

  /* ---------- Shared bits of UI ---------- */
  function statusBadge(status, reason) {
    const badge = ui.badge(status);
    return reason ? `<span data-tip="${esc(reason)}" style="cursor:help">${badge}</span>` : badge;
  }
  function plateLink(vehicle) {
    if (!vehicle) return '<span class="muted">—</span>';
    const label = `<b>${esc(vehicle.license_plate)}</b>`;
    return G3.canSee("vehicle-detail") ? `<a href="#/vehicles/${vehicle.vehicle_id}">${label}</a>` : label;
  }
  const fleetName = (id) => { const f = db.fleet(id); return f ? esc(f.name) : '<span class="muted">—</span>'; };
  const modelOf = (v) => db.model(v.vehicle_model_id) || {};
  const isLive = (row) => !row.deleted_at;
  /** Text stored as [en, vi] (seeded) or as typed by the user. */
  const txt = (value) => (Array.isArray(value) ? t(value[0], value[1]) : value);
  const retiredTag = () => ` <span class="tag">${esc(t("Retired", "Đã loại bỏ"))}</span>`;
  const sevTone = { CRITICAL: "danger", WARNING: "warn", INFO: "info" };

  /** Tabs, selects, search boxes and segmented controls write the URL query. */
  function bindCommon(root) {
    root.querySelectorAll("[data-tabs] [data-tab]").forEach((b) => b.addEventListener("click", () => G3.setQuery({ [b.closest("[data-tabs]").dataset.tabs]: b.dataset.tab })));
    root.querySelectorAll("select[data-filter], input[data-filter]").forEach((el) => el.addEventListener("change", () => G3.setQuery({ [el.dataset.filter]: el.value })));
    root.querySelectorAll("[data-seg] button").forEach((b) => b.addEventListener("click", () => G3.setQuery({ [b.closest("[data-seg]").dataset.seg]: b.dataset.value })));
    root.querySelectorAll("[data-open]").forEach((el) => el.addEventListener("click", (e) => {
      if (e.target.closest("button, a, input, select, label")) return;
      G3.setQuery({ [el.dataset.openKey || "d"]: el.dataset.open });
    }));
  }
  /** Every [data-action] element calls handlers[action](dataset). */
  function bindActions(root, handlers) {
    root.querySelectorAll("[data-action]").forEach((el) => el.addEventListener("click", (e) => {
      const handler = handlers[el.dataset.action];
      if (!handler) return;
      e.stopPropagation();
      handler(el.dataset, el);
    }));
  }
  /** A side drawer kept open by a query key (survives re-renders and live ticks). */
  function drawer(title, sub, body, foot) {
    return `<aside class="drawer" style="width:min(540px,100%);animation:none"><div class="modal-head"><div style="flex:1;min-width:0"><h2>${esc(title)}</h2>${sub ? `<div class="small muted">${sub}</div>` : ""}</div><button class="icon-btn" data-action="close-drawer" title="${esc(t("Close", "Đóng"))}">${ui.icon("x")}</button></div><div class="modal-body stack" style="flex:1">${body}</div>${foot ? `<div class="modal-foot">${foot}</div>` : ""}</aside>`;
  }
  function filterBar(items) { return `<div class="toolbar">${items.filter(Boolean).join("")}</div>`; }
  /** A small step indicator: done steps green, the current one highlighted. */
  function stepper(steps, currentIndex) {
    return `<div class="row" style="gap:4px;flex-wrap:nowrap;overflow-x:auto">${steps.map(([, label], i) => {
      const done = i < currentIndex, on = i === currentIndex;
      const bg = done ? "var(--primary-soft)" : on ? "var(--primary)" : "var(--surface-3)";
      const color = done ? "var(--primary-strong)" : on ? "#fff" : "var(--text-3)";
      return `<div style="flex:1;min-width:86px;padding:6px 8px;border-radius:6px;background:${bg};color:${color};font-size:11.5px;font-weight:600;text-align:center;white-space:nowrap">${done ? "✓ " : ""}${esc(label)}</div>`;
    }).join("")}</div>`;
  }
  const canOperate = () => scope.has("OPERATIONS", "HEAD_ADMIN");
  /** Fleets the persona may filter by (organization scope plus the FLT-03 fleet limit). */
  const scopedFleets = () => { const limit = scope.fleetLimit(); return scope.filter(db.fleets).filter((f) => !limit || limit.includes(f.fleet_id)); };

  /* ---------- Two trucks still on their way to activation (VEH-05) ----------
     The seed has only one truck waiting; these two show both waiting steps. */
  if (!db.vehicle("veh-029")) {
    const base = { fleet_id: null, status: "ACTIVE", status_reason: null, odometer_km: 3, live: null, soh_percent: 100, cycle_count: 1, driver_id: null };
    db.vehicles.push(Object.assign({}, base, { vehicle_id: "veh-029", vin: "LZT400K4R02610730", license_plate: "61C-482.17", vehicle_model_id: "vm-400", organization_id: "org-mn", handover_at: isoAgo(3), acquired_at: isoAgo(3), created_at: isoAgo(6), telematic_id: "tbx-029", battery_id: "bat-029", activation: "AWAITING_DATA" }));
    db.vehicles.push(Object.assign({}, base, { vehicle_id: "veh-030", vin: "LZT825B6R02611100", license_plate: "51C-605.38", vehicle_model_id: "vm-825", organization_id: "org-tcx", handover_at: null, acquired_at: isoAgo(1), created_at: isoAgo(1), telematic_id: null, battery_id: "bat-030", activation: "NO_DEVICE" }));
    db.telematics.push({ telematic_id: "tbx-029", telematic_serial: "TBX-2410-04480", imei: "861557203318842", iccid: "89840100003318842117", firmware_version: "v2.3.1", vehicle_id: "veh-029", organization_id: "org-mn", status: "ACTIVE", installed_at: isoAgo(2), send_interval_seconds: 15, reported_interval_seconds: null, signal_dbm: null, power_v: null, storage_used_percent: 0, gnss_fix: "NONE", sim_status: "NO_DATA", last_seen_at: null, health: "NO_DATA" });
    db.batteries.push({ battery_id: "bat-029", battery_serial: "CATL-350-2406119", battery_model_id: "bm-350", organization_id: "org-mn", vehicle_id: "veh-029", status: "ACTIVE", status_reason: null, manufactured_on: isoAgo(70).slice(0, 10), installed_at: isoAgo(9), soh_percent: 100, cycle_count: 1 });
    db.batteries.push({ battery_id: "bat-030", battery_serial: "CATL-423-2406330", battery_model_id: "bm-423", organization_id: "org-tcx", vehicle_id: "veh-030", status: "ACTIVE", status_reason: null, manufactured_on: isoAgo(64).slice(0, 10), installed_at: isoAgo(4), soh_percent: 100, cycle_count: 0 });
  }

  /* =====================================================================
     VEHICLES (VEH-01, VEH-02, VEH-03, VEH-05)
     ===================================================================== */
  const VIN_RE = /^[A-HJ-NPR-Z0-9]{17}$/;
  const PLATE_RE = /^\d{2}[A-Z]{1,2}-\d{3}\.\d{2}$|^\d{2}[A-Z]{1,2}-\d{4,5}$/;

  /* Model sheet extras (VEH-03): figures are illustrative, not Tri-Ring's datasheet. */
  const MODEL_SHEET = {
    "vm-262": { motor_kw: 250, top_speed_kmh: 90, payload_t: 10.5, confirmed: true, confirmed_on: isoAgo(48), source: ["Tri-Ring datasheet rev. C", "Bảng thông số Tri-Ring bản C"] },
    "vm-400": { motor_kw: 360, top_speed_kmh: 89, payload_t: 25, confirmed: true, confirmed_on: isoAgo(31), source: ["Tri-Ring datasheet rev. B", "Bảng thông số Tri-Ring bản B"] },
    "vm-825": { motor_kw: 480, top_speed_kmh: 85, payload_t: 31, confirmed: false, confirmed_on: null, source: ["G3 road tests, awaiting the manufacturer", "Chạy thử của G3, chờ hãng xác nhận"] },
  };
  /** kWh/km at a load share (0..1): a slightly convex curve between the empty and loaded figures. */
  const consumptionAt = (model, share) => model.consumption_empty_kwh_km + (model.consumption_loaded_kwh_km - model.consumption_empty_kwh_km) * Math.pow(share, 1.15);

  /* Ownership transfers already on record (VEH-02). */
  const transfers = [
    { transfer_id: "trf-1", vehicle_id: "veh-014", from: "org-dn", to: "org-tcx", effective_at: (db.vehicle("veh-014") || {}).acquired_at, recorded_at: (db.vehicle("veh-014") || {}).acquired_at, by: db.people.sales.user.user_id, reason: "Tân Cảng Xanh mua lại xe từ HTX Đồng Nai Xanh (HĐ 2026/MB-014)", ended: ["FLEET", "SESSION", "VIN"] },
    { transfer_id: "trf-2", vehicle_id: "veh-021", from: "org-tcx", to: "org-mn", effective_at: (db.vehicle("veh-021") || {}).acquired_at, recorded_at: (db.vehicle("veh-021") || {}).acquired_at, by: db.people.ops.user.user_id, reason: "Kết thúc hợp đồng thuê ngắn hạn, chuyển cho Miền Nam EV", ended: ["FLEET", "SUBSCRIPTION", "VIN"] },
    { transfer_id: "trf-3", vehicle_id: "veh-026", from: "org-g3", to: "org-ind", effective_at: (db.vehicle("veh-026") || {}).acquired_at, recorded_at: (db.vehicle("veh-026") || {}).acquired_at, by: db.people.sales2.user.user_id, reason: "Bàn giao xe mới cho chủ xe cá nhân (HĐ 2026/MB-031)", ended: [] },
  ];
  const ENDED_LABELS = {
    FLEET: ["Fleet membership", "Thành viên đội xe"],
    SESSION: ["Driving session", "Phiên lái"],
    RULES: ["Charging rules", "Quy tắc sạc"],
    SUBSCRIPTION: ["Subscription line", "Dòng thuê bao"],
    VIN: ["VIN credential", "Thẻ sạc theo VIN"],
    BATTERY: ["Battery moved", "Pin chuyển theo"],
  };

  function vehicleRows(query) {
    const q = (query.q || "").toLowerCase();
    return scope.vehicles().filter((v) => {
      if (query.status === "RETIRED") { if (isLive(v)) return false; }
      else if (query.status && (v.status !== query.status || !isLive(v))) return false;
      if (query.model && v.vehicle_model_id !== query.model) return false;
      if (query.owner && v.organization_id !== query.owner) return false;
      if (query.fleet === "none" && v.fleet_id) return false;
      if (query.fleet && query.fleet !== "none" && !db.fleetAndDescendants(query.fleet).includes(v.fleet_id)) return false;
      if (q && !(v.license_plate.toLowerCase().includes(q) || v.vin.toLowerCase().includes(q))) return false;
      return true;
    });
  }

  function ownerOptions() {
    return scope.filter(db.organizations).filter((o) => o.kind !== "PARTNER");
  }

  function registryTab(query) {
    const all = scope.vehicles();
    const live = all.filter(isLive);
    const rows = vehicleRows(query);
    const canWrite = scope.has("OPERATIONS", "FLEET_MANAGER", "ORG_ADMIN", "HEAD_ADMIN");
    const avgSoh = live.reduce((a, v) => a + v.soh_percent, 0) / (live.length || 1);
    const waiting = live.filter((v) => v.activation !== "ACTIVATED").length;
    const kpis = `<div class="grid cols-5 mb">
      ${ui.kpi({ label: t("Trucks", "Số xe"), icon: "truck", value: live.length, foot: `${all.length - live.length} ${t("retired", "đã loại bỏ")}` })}
      ${ui.kpi({ label: t("In service", "Đang hoạt động"), icon: "check", value: live.filter((v) => v.status === "ACTIVE").length, tone: "ok", foot: `${live.filter((v) => v.status === "INACTIVE").length} ${t("inactive", "ngừng hoạt động")}` })}
      ${ui.kpi({ label: t("Waiting for activation", "Chờ kích hoạt"), icon: "clock", value: waiting, tone: waiting ? "warn" : undefined, foot: "VEH-05" })}
      ${ui.kpi({ label: t("Average battery health", "SOH trung bình"), icon: "battery", value: fmt.num(avgSoh, 1), unit: "%", foot: `${live.filter((v) => v.soh_percent < 90).length} ${t("below 90%", "dưới 90%")}` })}
      ${ui.kpi({ label: t("Fleet odometer", "Tổng số km"), icon: "route", value: fmt.num(live.reduce((a, v) => a + v.odometer_km, 0) / 1000, 0), unit: t("k km", "nghìn km"), foot: `${fmt.num(live.reduce((a, v) => a + v.odometer_km, 0) / (live.length || 1))} km / ${t("truck", "xe")}` })}
    </div>`;
    const fleets = scopedFleets();
    const toolbar = filterBar([
      ui.search("q", query.q, t("Plate or VIN", "Biển số hoặc VIN")),
      ui.select("status", [["", t("Every status", "Mọi trạng thái")], ["ACTIVE", G3.label("ACTIVE")], ["INACTIVE", G3.label("INACTIVE")], ["RETIRED", t("Retired", "Đã loại bỏ")]], query.status || ""),
      ui.select("model", [["", t("Every model", "Mọi dòng xe")], ...db.vehicle_models.map((m) => [m.vehicle_model_id, m.code])], query.model || ""),
      scope.isInternal() ? ui.select("owner", [["", t("Every owner", "Mọi chủ xe")], ...ownerOptions().map((o) => [o.organization_id, o.short_name])], query.owner || "") : "",
      ui.select("fleet", [["", t("Every fleet", "Mọi đội xe")], ["none", t("Not in a fleet", "Chưa vào đội")], ...fleets.map((f) => [f.fleet_id, (f.parent_fleet_id ? "— " : "") + f.name])], query.fleet || ""),
      `<span class="grow"></span><span class="small muted">${rows.length} / ${all.length} ${t("trucks", "xe")}</span>`,
    ]);
    const table = ui.table({
      rows,
      href: G3.canSee("vehicle-detail") ? (v) => `/vehicles/${v.vehicle_id}` : null,
      empty: t("No truck matches these filters", "Không có xe phù hợp bộ lọc"),
      columns: [
        { label: t("Truck", "Xe"), render: (v) => ui.vehicleCell(v) + (isLive(v) ? "" : retiredTag()) },
        { label: t("Model", "Dòng xe"), render: (v) => `${esc(modelOf(v).code)}<span class="sub">${esc(modelOf(v).kind || "")}</span>` },
        { label: t("Owner", "Chủ xe"), render: (v) => ui.orgName(v.organization_id) },
        { label: t("Fleet", "Đội xe"), render: (v) => fleetName(v.fleet_id) },
        { label: t("Status", "Trạng thái"), render: (v) => statusBadge(v.status, v.status_reason) },
        { label: t("Activation", "Kích hoạt"), render: (v) => ui.badge(v.activation) },
        { label: t("Odometer", "Số km"), align: "right", render: (v) => fmt.num(v.odometer_km) },
        { label: "SOH", align: "right", render: (v) => `<span style="color:var(--${v.soh_percent < 90 ? "danger" : v.soh_percent < 95 ? "warn" : "text"})">${fmt.pct(v.soh_percent, 1)}</span>` },
        canWrite ? { label: "", align: "right", render: (v) => (isLive(v) ? ui.btn(t("Retire", "Loại bỏ"), { sm: true, kind: "ghost", action: "retire", attrs: `data-id="${v.vehicle_id}"` }) : ui.btn(t("Restore", "Khôi phục"), { sm: true, kind: "ghost", action: "restore", attrs: `data-id="${v.vehicle_id}"` })) } : null,
      ].filter(Boolean),
    });
    return `${kpis}${ui.card({ flush: true, body: toolbar + table })}`;
  }

  function addVehicleModal() {
    const internal = scope.isInternal();
    const fields = [
      { name: "vin", label: "VIN", required: true, placeholder: "LZT400K4R02610730", hint: t("17 characters, letters I, O and Q are not used.", "17 ký tự, không dùng chữ I, O và Q."), attrs: 'maxlength="17" style="text-transform:uppercase"' },
      { name: "plate", label: t("License plate", "Biển số"), required: true, placeholder: "51C-123.45" },
      { name: "model", label: t("Model", "Dòng xe"), type: "select", required: true, options: db.vehicle_models.map((m) => [m.vehicle_model_id, `${m.code} · ${m.kind}`]) },
      { name: "year", label: t("Manufacturing year", "Năm sản xuất"), type: "number", required: true, value: new Date().getFullYear() },
      internal ? { name: "owner", label: t("Owner", "Chủ xe"), type: "select", required: true, options: ownerOptions().map((o) => [o.organization_id, o.short_name + (o.is_internal ? t(" (stock)", " (kho)") : "")]), value: scope.orgFilter() || "org-g3" } : null,
      { name: "acquired", label: t("Owner since (handover)", "Thời điểm nhận xe"), type: "date", required: true, value: todayIso(), hint: t("When the owner really took the truck; it may be earlier than today.", "Thời điểm thực tế chủ xe nhận xe, có thể trước hôm nay.") },
    ].filter(Boolean);
    ui.formModal({
      title: t("Add a truck", "Thêm xe"),
      intro: ui.callout("info", esc(t("The truck starts ACTIVE and waits for a device (VEH-05). Its factory-fitted battery pack is registered with it (BAT-01).", "Xe bắt đầu ở trạng thái Hoạt động và chờ gắn thiết bị (VEH-05). Pin lắp sẵn từ nhà máy được đăng ký cùng xe (BAT-01)."))),
      wide: true,
      fields,
      submitLabel: t("Add truck", "Thêm xe"),
      onSubmit(values) {
        const vin = values.vin.toUpperCase();
        const plate = values.plate.toUpperCase().replace(/\s+/g, "");
        if (!VIN_RE.test(vin)) return t("The VIN must be exactly 17 letters or digits, without I, O or Q.", "VIN phải gồm đúng 17 chữ hoặc số, không có I, O, Q.");
        if (!PLATE_RE.test(plate)) return t("The plate looks wrong: use the form 51C-123.45.", "Biển số chưa đúng định dạng, ví dụ 51C-123.45.");
        const liveTrucks = db.vehicles.filter(isLive);
        if (liveTrucks.some((v) => v.vin === vin)) return t(`VIN ${vin} already belongs to a truck in service.`, `VIN ${vin} đã thuộc một xe đang hoạt động.`);
        if (liveTrucks.some((v) => v.license_plate === plate)) return t(`Plate ${plate} already belongs to a truck in service.`, `Biển số ${plate} đã thuộc một xe đang hoạt động.`);
        const year = Number(values.year);
        if (!(year >= 2015 && year <= new Date().getFullYear() + 1)) return t("Enter a manufacturing year between 2015 and next year.", "Năm sản xuất phải từ 2015 đến năm sau.");
        if (values.acquired > todayIso()) return t("The handover date cannot be in the future.", "Ngày nhận xe không được ở tương lai.");
        let n = db.vehicles.length + 1;
        while (db.vehicle(`veh-${String(n).padStart(3, "0")}`)) n++;
        const id = `veh-${String(n).padStart(3, "0")}`;
        const orgId = internal ? values.owner : scope.org().organization_id;
        const at = new Date(values.acquired + "T08:00:00").toISOString();
        const model = db.model(values.model);
        const batteryModelId = { "vm-262": "bm-211", "vm-400": "bm-350", "vm-825": "bm-423" }[model.vehicle_model_id];
        const batteryId = "bat-" + id.slice(4);
        db.batteries.push({ battery_id: batteryId, battery_serial: `CATL-${batteryModelId.slice(3)}-${2407000 + n * 7}`, battery_model_id: batteryModelId, organization_id: orgId, vehicle_id: id, status: "ACTIVE", status_reason: null, manufactured_on: todayIso(), installed_at: at, soh_percent: 100, cycle_count: 0 });
        db.vehicles.push({ vehicle_id: id, vin, license_plate: plate, vehicle_model_id: model.vehicle_model_id, year, organization_id: orgId, fleet_id: null, status: "ACTIVE", status_reason: null, handover_at: orgId === "org-g3" ? null : at, acquired_at: at, odometer_km: 0, created_at: new Date().toISOString(), live: null, soh_percent: 100, cycle_count: 0, driver_id: null, telematic_id: null, battery_id: batteryId, activation: "NO_DEVICE" });
        G3.render();
        ui.toast(t(`Truck ${plate} added — waiting for a device`, `Đã thêm xe ${plate} — chờ gắn thiết bị`));
      },
    });
  }

  function retireVehicle(id) {
    const v = db.vehicle(id);
    ui.reasonModal({
      title: t(`Retire ${v.license_plate}`, `Loại bỏ xe ${v.license_plate}`),
      intro: ui.callout("warn", esc(t("The truck leaves the system: every record is kept, its status becomes Inactive, its device counts as not mounted and its VIN and plate can be used again (soft delete).", "Xe rời khỏi hệ thống: mọi dữ liệu được giữ, trạng thái thành Ngừng hoạt động, thiết bị coi như chưa lắp, VIN và biển số được dùng lại (xóa mềm)."))),
      submitLabel: t("Retire truck", "Loại bỏ xe"),
      onSubmit(reason) {
        v.deleted_at = new Date().toISOString();
        v.status = "INACTIVE";
        v.status_reason = reason;
        G3.render();
        ui.toast(t(`${v.license_plate} retired`, `Đã loại bỏ xe ${v.license_plate}`));
      },
    });
  }

  function restoreVehicle(id) {
    const v = db.vehicle(id);
    ui.reasonModal({
      title: t(`Restore ${v.license_plate}`, `Khôi phục xe ${v.license_plate}`),
      intro: ui.callout("info", esc(t("A returning truck gets its own record back (no new row).", "Xe quay lại dùng lại hồ sơ cũ (không tạo hồ sơ mới)."))),
      danger: false,
      submitLabel: t("Restore", "Khôi phục"),
      onSubmit(reason) {
        const clash = db.vehicles.find((x) => x !== v && isLive(x) && (x.vin === v.vin || x.license_plate === v.license_plate));
        if (clash) return t(`${clash.license_plate} in service already uses this VIN or plate.`, `Xe ${clash.license_plate} đang dùng VIN hoặc biển số này.`);
        v.deleted_at = null;
        v.status = "ACTIVE";
        v.status_reason = null;
        G3.render();
        ui.toast(t(`${v.license_plate} restored: ${reason}`, `Đã khôi phục ${v.license_plate}: ${reason}`));
      },
    });
  }

  /** What a transfer ends at once (VH-12), for the truck as it is now. */
  function transferEffects(v) {
    const seller = db.org(v.organization_id);
    const fleet = db.fleet(v.fleet_id);
    const session = db.driving_sessions.find((s) => s.vehicle_id === v.vehicle_id && !s.ended_at);
    const rules = db.charging_policies.filter((p) => p.scope === "FLEET" && fleet && db.fleetAndDescendants(p.scope_ids[0]).includes(fleet.fleet_id));
    const sub = db.subscriptions.find((s) => s.organization_id === v.organization_id);
    const battery = db.battery(v.battery_id);
    const items = [
      ["FLEET", fleet ? t(`Leaves the seller's fleet ${fleet.name}`, `Rời đội xe ${fleet.name} của bên bán`) : t("Not in a fleet of the seller", "Không thuộc đội xe nào của bên bán"), !!fleet],
      ["SESSION", session ? t(`Open driving session of ${(db.driver(session.driver_id) || {}).full_name} ends (owner changed)`, `Phiên lái đang mở của ${(db.driver(session.driver_id) || {}).full_name} kết thúc (đổi chủ xe)`) : t("No open driving session", "Không có phiên lái đang mở"), !!session],
      ["RULES", rules.length ? t(`${rules.length} fleet charging rule(s) stop applying: ${rules.map((r) => r.name).join(", ")}`, `${rules.length} quy tắc sạc của đội ngừng áp dụng: ${rules.map((r) => r.name).join(", ")}`) : t("Model-wide charging rules keep applying", "Quy tắc sạc theo dòng xe vẫn áp dụng"), !!rules.length],
      ["SUBSCRIPTION", sub ? t(`Truck line removed from ${seller.short_name}'s plan ${db.find("plans", sub.plan_id).name}`, `Bỏ dòng xe khỏi gói ${db.find("plans", sub.plan_id).name} của ${seller.short_name}`) : t("Seller has no subscription", "Bên bán không có thuê bao"), !!sub],
      ["VIN", t(`VIN charging credential of ${seller.short_name} revoked`, `Thu hồi thẻ sạc theo VIN của ${seller.short_name}`), seller.organization_id !== "org-g3"],
      ["BATTERY", battery ? (battery.organization_id === v.organization_id ? t(`Battery ${battery.battery_serial} moves with the truck`, `Pin ${battery.battery_serial} chuyển theo xe`) : t(`Battery ${battery.battery_serial} stays with its owner ${db.org(battery.organization_id).short_name}`, `Pin ${battery.battery_serial} vẫn thuộc ${db.org(battery.organization_id).short_name}`)) : t("No battery fitted", "Chưa lắp pin"), !!battery && battery.organization_id === v.organization_id],
    ];
    return { items, session, battery };
  }

  function transferModal(id) {
    const v = db.vehicle(id);
    const { items, session, battery } = transferEffects(v);
    const seller = db.org(v.organization_id);
    const intro = `${ui.callout("warn", `<b>${esc(t("Ends at once on the effective date", "Kết thúc ngay từ ngày hiệu lực"))}</b><ul style="margin:6px 0 0;padding-left:18px">${items.map(([, text, applies]) => `<li style="${applies ? "" : "color:var(--text-3)"}">${esc(text)}</li>`).join("")}</ul>`)}
      <div style="height:8px"></div>${ui.callout("info", esc(t(`${seller.short_name} keeps reading its own period's history and reports; the new owner sees the truck from the effective date (VH-11). The T-Box stays on the truck.`, `${seller.short_name} vẫn xem được lịch sử và báo cáo trong thời gian mình sở hữu; chủ mới thấy xe từ ngày hiệu lực (VH-11). T-Box vẫn ở trên xe.`)))}`;
    ui.formModal({
      title: t(`Transfer ${v.license_plate}`, `Chuyển chủ xe ${v.license_plate}`),
      intro,
      fields: [
        { name: "to", label: t("New owner", "Chủ mới"), type: "select", required: true, options: db.organizations.filter((o) => o.kind !== "PARTNER" && o.organization_id !== v.organization_id).map((o) => [o.organization_id, o.short_name + (o.status !== "ACTIVE" ? ` (${G3.label(o.status)})` : "") + (o.is_internal ? t(" — back to stock", " — về kho") : "")]) },
        { name: "date", label: t("Effective date", "Ngày hiệu lực"), type: "date", required: true, value: todayIso(), hint: t("When the new owner really took the truck.", "Thời điểm chủ mới thực sự nhận xe.") },
        { name: "reason", label: t("Reason", "Lý do"), type: "textarea", required: true, placeholder: t("Sale contract number…", "Số hợp đồng mua bán…"), hint: t("Saved with the change history.", "Lưu vào lịch sử thay đổi.") },
      ],
      submitLabel: t("Transfer truck", "Chuyển chủ"),
      onSubmit(values) {
        if (values.date > todayIso()) return t("The effective date cannot be in the future.", "Ngày hiệu lực không được ở tương lai.");
        if (values.date < v.acquired_at.slice(0, 10)) return t(`The current owner took the truck on ${fmt.date(v.acquired_at)}; pick a later date.`, `Chủ hiện tại nhận xe ngày ${fmt.date(v.acquired_at)}; hãy chọn ngày sau đó.`);
        const now = new Date().toISOString();
        const ended = items.filter(([, , applies]) => applies).map(([key]) => key);
        if (session) { session.ended_at = now; session.end_cause = "OWNER_CHANGED"; }
        if (battery && battery.organization_id === v.organization_id) battery.organization_id = values.to;
        transfers.unshift({ transfer_id: "trf-" + (transfers.length + 1), vehicle_id: v.vehicle_id, from: v.organization_id, to: values.to, effective_at: new Date(values.date + "T08:00:00").toISOString(), recorded_at: now, by: me(), reason: values.reason.slice(0, 200), ended });
        v.organization_id = values.to;
        v.fleet_id = null;
        v.driver_id = null;
        v.acquired_at = new Date(values.date + "T08:00:00").toISOString();
        G3.render();
        ui.toast(t(`${v.license_plate} now belongs to ${db.org(values.to).short_name} · ${ended.length} item(s) ended`, `${v.license_plate} đã thuộc ${db.org(values.to).short_name} · kết thúc ${ended.length} mục`));
      },
    });
  }

  function ownershipTab(query) {
    const q = (query.q || "").toLowerCase();
    const rows = scope.vehicles().filter(isLive).filter((v) => (!query.owner || v.organization_id === query.owner) && (!q || v.license_plate.toLowerCase().includes(q) || v.vin.toLowerCase().includes(q)));
    const visibleIds = scope.vehicleIds();
    const past = transfers.filter((x) => visibleIds.has(x.vehicle_id));
    const leased = scope.vehicles().filter((v) => { const b = db.battery(v.battery_id); return b && b.organization_id !== v.organization_id; });
    return `${ui.featureChips(["VEH-02", "BAT-01"])}
      <div class="grid cols-4 mb mt">
        ${ui.kpi({ label: t("Owning organizations", "Tổ chức sở hữu"), icon: "building", value: new Set(rows.map((v) => v.organization_id)).size })}
        ${ui.kpi({ label: t("Trucks in G3 stock", "Xe trong kho G3"), icon: "truck", value: rows.filter((v) => v.organization_id === "org-g3").length })}
        ${ui.kpi({ label: t("Transfers on record", "Lần chuyển chủ"), icon: "handshake", value: past.length, foot: past[0] ? `${t("latest", "gần nhất")} ${fmt.date(past[0].effective_at)}` : "" })}
        ${ui.kpi({ label: t("Pack owner ≠ truck owner", "Chủ pin ≠ chủ xe"), icon: "battery", value: leased.length, foot: t("leased packs (VaaS)", "pin cho thuê (VaaS)") })}
      </div>
      ${ui.callout("info", esc(t("Each truck has exactly one owner at a time. A transfer records the owner from the date it really happened; the seller keeps its own period, every period is kept in the change history.", "Mỗi xe chỉ có một chủ tại một thời điểm. Chuyển chủ ghi nhận từ ngày thực tế; bên bán giữ dữ liệu giai đoạn của mình, mọi giai đoạn đều lưu trong lịch sử thay đổi.")))}
      <div class="mt">${ui.card({
        title: t("Past transfers", "Lịch sử chuyển chủ"), sub: `${past.length}`, flush: true,
        body: ui.table({
          rows: past,
          empty: t("No transfer yet", "Chưa có lần chuyển chủ nào"),
          columns: [
            { label: t("Effective", "Hiệu lực"), render: (x) => `${fmt.date(x.effective_at)}<span class="sub">${t("recorded", "ghi nhận")} ${fmt.ago(x.recorded_at)}</span>` },
            { label: t("Truck", "Xe"), render: (x) => plateLink(db.vehicle(x.vehicle_id)) },
            { label: t("From → to", "Từ → đến"), render: (x) => `${ui.orgName(x.from)} → <b>${ui.orgName(x.to)}</b>` },
            { label: t("Reason", "Lý do"), render: (x) => `<span class="small">${esc(x.reason)}</span>` },
            { label: t("Ended at once", "Kết thúc ngay"), render: (x) => x.ended.length ? x.ended.map((k) => ui.tag(t(ENDED_LABELS[k][0], ENDED_LABELS[k][1]))).join(" ") : '<span class="muted">—</span>' },
            { label: t("By", "Người thực hiện"), render: (x) => ui.userName(x.by) },
          ],
        }),
      })}</div>
      <div class="mt">${ui.card({
        title: t("Current owners", "Chủ xe hiện tại"), flush: true,
        body: filterBar([ui.search("q", query.q, t("Plate or VIN", "Biển số hoặc VIN")), ui.select("owner", [["", t("Every owner", "Mọi chủ xe")], ...ownerOptions().map((o) => [o.organization_id, o.short_name])], query.owner || "")]) + ui.table({
          rows,
          columns: [
            { label: t("Truck", "Xe"), render: (v) => ui.vehicleCell(v) },
            { label: t("Owner", "Chủ xe"), render: (v) => `<b>${ui.orgName(v.organization_id)}</b><span class="sub">${t("since", "từ")} ${fmt.date(v.acquired_at)}</span>` },
            { label: t("Fleet", "Đội xe"), render: (v) => fleetName(v.fleet_id) },
            { label: t("Battery owner", "Chủ pin"), render: (v) => { const b = db.battery(v.battery_id); if (!b) return "—"; return b.organization_id === v.organization_id ? `<span class="muted">${t("Same", "Cùng chủ")}</span>` : ui.badge("LEASED", "violet", `${t("Leased", "Thuê")} · ${db.org(b.organization_id).short_name}`); } },
            { label: t("Driver checked in", "Tài xế đang nhận"), render: (v) => ui.driverName(v.driver_id) },
            { label: "", align: "right", render: (v) => ui.btn(t("Transfer", "Chuyển chủ"), { sm: true, icon: "handshake", action: "transfer", attrs: `data-id="${v.vehicle_id}"` }) },
          ],
        }),
      })}</div>`;
  }

  function activationTab() {
    const trucks = scope.vehicles().filter(isLive);
    const registered = trucks.length;
    const withDevice = trucks.filter((v) => v.telematic_id || v.activation === "ACTIVATED").length;
    const activated = trucks.filter((v) => v.activation === "ACTIVATED").length;
    const waiting = trucks.filter((v) => v.activation !== "ACTIVATED").sort((a, b) => (a.created_at < b.created_at ? -1 : 1));
    // Days from registration to first data, derived per truck (the backend computes it from handover and the first telemetry).
    const lead = trucks.filter((v) => v.activation === "ACTIVATED").map((v) => 1 + (hashOf(v.vehicle_id) % 6)).sort((a, b) => a - b);
    const median = lead.length ? lead[Math.floor(lead.length / 2)] : 0;
    const months = Array.from({ length: 6 }, (_, i) => { const d = new Date(NOW); d.setDate(1); d.setMonth(d.getMonth() - (5 - i)); return d; });
    const cohort = months.map((d) => {
      const inMonth = trucks.filter((v) => { const c = new Date(v.created_at); return c.getMonth() === d.getMonth() && c.getFullYear() === d.getFullYear(); });
      return { label: monthLabel(d), registered: inMonth.length, activated: inMonth.filter((v) => v.activation === "ACTIVATED").length };
    });
    const funnel = [[t("Registered", "Đã đăng ký"), registered, "var(--accent)"], [t("Device assigned", "Đã gắn thiết bị"), withDevice, "var(--violet)"], [t("First data received", "Đã nhận dữ liệu đầu tiên"), activated, "var(--primary)"]];
    return `${ui.featureChips(["VEH-05", "DEV-02"])}
      <div class="grid cols-4 mb mt">
        ${ui.kpi({ label: t("Activation success rate", "Tỷ lệ kích hoạt thành công"), icon: "check", value: fmt.pct((activated / (registered || 1)) * 100, 1), tone: "ok", foot: `${activated} / ${registered} ${t("trucks", "xe")}` })}
        ${ui.kpi({ label: t("Waiting for a device", "Chờ gắn thiết bị"), icon: "chip", value: waiting.filter((v) => v.activation === "NO_DEVICE").length, tone: "warn" })}
        ${ui.kpi({ label: t("Waiting for first data", "Chờ dữ liệu đầu tiên"), icon: "signal", value: waiting.filter((v) => v.activation === "AWAITING_DATA").length, tone: "warn" })}
        ${ui.kpi({ label: t("Median time to activate", "Thời gian kích hoạt (trung vị)"), icon: "clock", value: median, unit: t("days", "ngày"), foot: t("registration → first data", "đăng ký → dữ liệu đầu") })}
      </div>
      <div class="grid side">
        ${ui.card({
          title: t("Activation funnel", "Phễu kích hoạt"),
          body: `<div class="stack" style="gap:14px">${funnel.map(([label, n, color], i) => `<div><div class="row between small"><span><b>${i + 1}.</b> ${esc(label)}</span><span class="num"><b>${n}</b> <span class="muted">${i ? `· ${fmt.pct((n / (funnel[i - 1][1] || 1)) * 100)} ${t("of previous", "so với bước trước")}` : ""}</span></span></div><div class="meter" style="height:22px;border-radius:6px;margin-top:4px"><span style="width:${(n / (registered || 1)) * 100}%;background:${color};border-radius:6px"></span></div></div>`).join("")}</div>
            <div class="divider"></div><div class="small muted">${esc(t("A truck is activated when the first data from its mounted T-Box arrives; there is no manual step (VEH-05).", "Xe được kích hoạt khi dữ liệu đầu tiên từ T-Box đã lắp được nhận; không có bước thủ công (VEH-05)."))}</div>`,
        })}
        ${ui.card({
          title: t("Registered and activated per month", "Đăng ký và kích hoạt theo tháng"),
          actions: ui.legend([[t("Registered", "Đăng ký"), ui.PALETTE[1]], [t("Activated", "Kích hoạt"), ui.PALETTE[0]]]),
          body: ui.barChart({ labels: cohort.map((c) => c.label), series: [{ name: t("Registered", "Đăng ký"), values: cohort.map((c) => c.registered), color: ui.PALETTE[1] }, { name: t("Activated", "Kích hoạt"), values: cohort.map((c) => c.activated), color: ui.PALETTE[0] }], height: 210 }),
        })}
      </div>
      <div class="mt">${ui.card({
        title: t("Trucks still waiting", "Xe đang chờ kích hoạt"), sub: `${waiting.length}`, flush: true,
        body: ui.table({
          rows: waiting,
          empty: t("Every truck is activated", "Mọi xe đã được kích hoạt"),
          columns: [
            { label: t("Truck", "Xe"), render: (v) => ui.vehicleCell(v) },
            { label: t("Model", "Dòng xe"), render: (v) => esc(modelOf(v).code) },
            { label: t("Owner", "Chủ xe"), render: (v) => ui.orgName(v.organization_id) },
            { label: t("Registered", "Đăng ký"), render: (v) => `${fmt.date(v.created_at)}<span class="sub">${fmt.ago(v.created_at)}</span>` },
            { label: t("Waiting for", "Đang chờ"), render: (v) => ui.badge(v.activation) },
            { label: t("Next step", "Bước tiếp theo"), render: (v) => { const d = db.telematic(v.telematic_id); return v.activation === "NO_DEVICE" ? esc(t("Mount a T-Box and assign it", "Lắp và gán T-Box cho xe")) : `${esc(t("Device", "Thiết bị"))} <span class="mono">${esc(d ? d.telematic_serial : "—")}</span> ${esc(t("mounted", "đã lắp"))} ${d ? fmt.ago(d.installed_at) : ""}; ${esc(t("check power and SIM", "kiểm tra nguồn và SIM"))}`; } },
            { label: "", align: "right", render: (v) => {
              if (!G3.canSee("devices")) return "";
              if (v.activation === "NO_DEVICE") return ui.btn(t("Assign device", "Gán thiết bị"), { sm: true, icon: "chip", href: `#/devices?assign=${v.vehicle_id}` });
              return `${ui.btn(t("Open device", "Mở thiết bị"), { sm: true, kind: "ghost", href: `#/devices?d=${v.telematic_id}` })}${canOperate() ? ui.btn(t("Simulate first data", "Giả lập dữ liệu đầu"), { sm: true, icon: "signal", action: "first-data", attrs: `data-id="${v.vehicle_id}"` }) : ""}`;
            } },
          ],
        }),
      })}</div>`;
  }

  /** First telemetry of a truck (demo of VEH-05): the truck appears parked at its depot. */
  function simulateFirstData(id) {
    const v = db.vehicle(id);
    const d = db.telematic(v.telematic_id);
    const place = v.organization_id === "org-mn" ? db.PLACES.vsip : db.PLACES.nhontrach;
    const now = new Date().toISOString();
    v.live = { soc_percent: 86, lat: place.lat + 0.002, lon: place.lon + 0.002, heading: 0, speed_kmh: 0, state: "PARKED", is_online: true, received_at: now, battery_temperature_celsius: 29, pack_voltage_v: 604, pack_current_a: 0, road: db.ROADS[5].name, road_index: 5, road_f: 0.5 };
    v.activation = "ACTIVATED";
    if (d) Object.assign(d, { health: "HEALTHY", last_seen_at: now, signal_dbm: -71, power_v: 25.6, storage_used_percent: 3, gnss_fix: "3D", sim_status: "OK", reported_interval_seconds: d.send_interval_seconds });
    G3.render();
    ui.toast(t(`First data from ${v.license_plate}: truck activated`, `Đã nhận dữ liệu đầu từ ${v.license_plate}: xe đã kích hoạt`));
  }

  function modelsTab() {
    const trucks = scope.vehicles().filter(isLive);
    const loads = Array.from({ length: 11 }, (_, i) => i * 10);
    const canEdit = canOperate();
    const trips = scope.byVehicle(db.trips).filter((tr) => tr.status === "COMPLETED" && tr.distance_km);
    const measured = (modelId, load) => {
      const rows = trips.filter((tr) => (db.vehicle(tr.vehicle_id) || {}).vehicle_model_id === modelId && tr.declared_load === load);
      const km = rows.reduce((a, r) => a + r.distance_km, 0);
      return { n: rows.length, value: km ? rows.reduce((a, r) => a + r.energy_kwh, 0) / km : null };
    };
    const models = db.vehicle_models;
    const sheetOf = (m) => MODEL_SHEET[m.vehicle_model_id];
    const specRows = [
      [t("Kind", "Loại xe"), (m) => esc(m.kind)],
      [t("Gross weight / payload", "Tổng trọng tải / tải hàng"), (m) => `${m.gross_weight_t} t / ${sheetOf(m).payload_t} t`],
      [t("Battery", "Pin"), (m) => { const b = db.battery_models.find((x) => x.capacity_kwh === m.battery_capacity_kwh); return `<b>${fmt.num(m.battery_capacity_kwh)} kWh</b>${b ? ` <span class="muted small">${esc(b.chemistry)} · ${esc(b.layout)} · ${b.nominal_voltage_v} V</span>` : ""}`; }],
      [t("Motor / top speed", "Động cơ / tốc độ tối đa"), (m) => `${sheetOf(m).motor_kw} kW / ${sheetOf(m).top_speed_kmh} km/h`],
      [t("Charging connector", "Chuẩn sạc"), (m) => esc(m.connector)],
      [t("Consumption empty → full", "Tiêu thụ rỗng → đầy tải"), (m) => `<b>${fmt.num(m.consumption_empty_kwh_km, 2)} → ${fmt.num(m.consumption_loaded_kwh_km, 2)}</b> kWh/km`],
      [t("Range on 90% of the pack", "Quãng đường với 90% pin"), (m) => `${fmt.num((m.battery_capacity_kwh * 0.9) / m.consumption_loaded_kwh_km)} – ${fmt.num((m.battery_capacity_kwh * 0.9) / m.consumption_empty_kwh_km)} km`],
      [t("Measured on trips (empty / loaded)", "Đo trên chuyến (rỗng / có hàng)"), (m) => { const e = measured(m.vehicle_model_id, "EMPTY"), l = measured(m.vehicle_model_id, "LOADED"); return e.n + l.n ? `${e.value ? fmt.num(e.value, 2) : "—"} / ${l.value ? fmt.num(l.value, 2) : "—"} kWh/km <span class="muted small">· ${e.n + l.n} ${t("trips", "chuyến")}</span>` : '<span class="muted">—</span>'; }],
      [t("Source of the figures", "Nguồn số liệu"), (m) => `<span class="small">${esc(t(sheetOf(m).source[0], sheetOf(m).source[1]))}${sheetOf(m).confirmed_on ? ` · ${fmt.date(sheetOf(m).confirmed_on)}` : ""}</span>`],
      [t("Trucks in service", "Số xe đang dùng"), (m) => `${trucks.filter((v) => v.vehicle_model_id === m.vehicle_model_id).length}`],
    ];
    const specTable = `<div class="table-wrap"><table class="tbl"><thead><tr><th></th>${models.map((m) => `<th><div class="row" style="gap:8px;flex-wrap:nowrap"><span style="font-size:14px;color:var(--text)">${esc(m.code)}</span>${sheetOf(m).confirmed ? ui.badge("CONFIRMED", "ok", t("Confirmed by the manufacturer", "Hãng đã xác nhận")) : ui.badge("UNCONFIRMED", "warn", t("Not confirmed yet", "Chưa được hãng xác nhận"))}${canEdit ? ui.btn("", { sm: true, kind: "ghost", icon: "edit", action: "edit-model", attrs: `data-id="${m.vehicle_model_id}" title="${esc(t("Edit", "Sửa"))}"` }) : ""}</div></th>`).join("")}</tr></thead><tbody>${specRows.map(([label, cell]) => `<tr><td class="muted" style="white-space:nowrap">${esc(label)}</td>${models.map((m) => `<td>${cell(m)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    return `${ui.featureChips(["VEH-03"])}
      <div class="mt">${ui.card({ title: t("Specifications", "Thông số kỹ thuật"), sub: t("electric figures only", "chỉ thông số điện"), flush: true, body: specTable })}</div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Consumption by load", "Tiêu thụ theo tải"), sub: t("kWh/km against % of rated payload", "kWh/km theo % tải định mức"),
          actions: ui.legend(models.map((m, i) => [m.code + (sheetOf(m).confirmed ? "" : " *"), ui.PALETTE[i + 1]])),
          body: `${ui.lineChart({ labels: loads.map((l) => String(l)), series: models.map((m, i) => ({ name: m.code, values: loads.map((l) => Math.round(consumptionAt(m, l / 100) * 100) / 100), color: ui.PALETTE[i + 1], dashed: !sheetOf(m).confirmed })), height: 240, yMin: 0.6, yMax: 1.8, yFormat: (v) => fmt.num(v, 2), unit: " kWh/km" })}
            <div class="small muted">${esc(t("* Dashed: G3's own road tests until the manufacturer confirms. The curve feeds range estimates and the trip load check (MON-11).", "* Nét đứt: số liệu chạy thử của G3 cho đến khi hãng xác nhận. Đường cong dùng để ước tính quãng đường và kiểm tra tải chuyến (MON-11)."))}</div>`,
        })}
        ${ui.card({
          title: t("Curve points", "Các điểm trên đường cong"), flush: true,
          body: ui.table({
            rows: [0, 25, 50, 75, 100],
            columns: [{ label: t("Load", "Tải"), render: (l) => `<b>${l}%</b>` }, ...models.map((m) => ({ label: m.code, align: "right", render: (l) => fmt.num(consumptionAt(m, l / 100), 2) }))],
          }),
        })}
      </div>`;
  }

  function editModel(id) {
    const m = db.model(id);
    const sheet = MODEL_SHEET[id];
    ui.formModal({
      title: t(`Edit ${m.code}`, `Sửa ${m.code}`),
      intro: ui.callout("info", esc(t("Electric figures only. Changing them changes every range estimate of this model.", "Chỉ thông số điện. Thay đổi sẽ áp dụng cho mọi ước tính quãng đường của dòng xe này."))),
      wide: true,
      fields: [
        { name: "capacity", label: t("Battery capacity (kWh)", "Dung lượng pin (kWh)"), type: "number", required: true, value: m.battery_capacity_kwh },
        { name: "empty", label: t("Consumption empty (kWh/km)", "Tiêu thụ khi rỗng (kWh/km)"), type: "number", required: true, value: m.consumption_empty_kwh_km, attrs: 'step="0.01"' },
        { name: "loaded", label: t("Consumption fully loaded (kWh/km)", "Tiêu thụ khi đầy tải (kWh/km)"), type: "number", required: true, value: m.consumption_loaded_kwh_km, attrs: 'step="0.01"' },
        { name: "confirmed", label: t("Manufacturer", "Nhà sản xuất"), type: "checkbox", value: sheet.confirmed, checkLabel: t("Figures confirmed by the manufacturer", "Thông số đã được hãng xác nhận") },
      ],
      onSubmit(values) {
        const capacity = Number(values.capacity), empty = Number(values.empty), loaded = Number(values.loaded);
        if (!(capacity > 50 && capacity < 1000)) return t("Capacity must be between 50 and 1,000 kWh.", "Dung lượng phải từ 50 đến 1.000 kWh.");
        if (!(empty > 0.3 && loaded > empty && loaded < 4)) return t("Loaded consumption must be above empty consumption (0.3–4 kWh/km).", "Tiêu thụ đầy tải phải lớn hơn khi rỗng (0,3–4 kWh/km).");
        Object.assign(m, { battery_capacity_kwh: capacity, consumption_empty_kwh_km: empty, consumption_loaded_kwh_km: loaded });
        if (values.confirmed && !sheet.confirmed) sheet.confirmed_on = new Date().toISOString();
        sheet.confirmed = values.confirmed;
        G3.render();
        ui.toast(t(`${m.code} updated`, `Đã cập nhật ${m.code}`));
      },
    });
  }

  const OWNERSHIP_ROLES = ["OPERATIONS", "SALES", "HEAD_ADMIN"];
  G3.page({
    id: "vehicles",
    path: "/vehicles",
    title: ["Vehicles", "Hồ sơ xe"],
    roles: ["OPERATIONS", "FLEET_MANAGER", "ORG_ADMIN", "SALES", "CUSTOMER_CARE", "WARRANTY", "MAINTENANCE"],
    features: ["VEH-01", "VEH-02", "VEH-03", "VEH-05"],
    nav: { group: "assets", order: 1, icon: "truck" },
    render({ query }) {
      const canOwn = scope.isInternal() && scope.has(...OWNERSHIP_ROLES);
      const tabs = [["registry", t("Registry", "Danh sách xe")], canOwn ? ["ownership", t("Ownership", "Chủ sở hữu")] : null, ["activation", t("Activation", "Kích hoạt")], ["models", t("Models", "Dòng xe")]].filter(Boolean);
      const tab = tabs.some(([k]) => k === query.tab) ? query.tab : "registry";
      const canWrite = scope.has("OPERATIONS", "FLEET_MANAGER", "ORG_ADMIN", "HEAD_ADMIN");
      const body = tab === "ownership" ? ownershipTab(query) : tab === "activation" ? activationTab() : tab === "models" ? modelsTab() : registryTab(query);
      return `${ui.pageHead({
        title: t("Vehicles", "Hồ sơ xe"),
        sub: esc(scope.isInternal() ? t("Every truck of every organization: profile, owner, activation and model.", "Mọi xe của mọi tổ chức: hồ sơ, chủ xe, kích hoạt và dòng xe.") : t(`Trucks of ${scope.org().name}`, `Xe của ${scope.org().name}`)),
        features: ["VEH-01", "VEH-02", "VEH-03", "VEH-05"],
        actions: tab === "registry" ? `${canWrite ? ui.btn(t("Add a truck", "Thêm xe"), { icon: "plus", kind: "primary", action: "add-vehicle" }) : ""}${ui.btn(t("Export", "Xuất"), { icon: "download", action: "export-vehicles" })}` : "",
      })}${ui.tabs(tabs, tab)}${body}`;
    },
    mount(root, { query }) {
      bindCommon(root);
      bindActions(root, {
        "add-vehicle": addVehicleModal,
        retire: (d) => retireVehicle(d.id),
        restore: (d) => restoreVehicle(d.id),
        transfer: (d) => transferModal(d.id),
        "first-data": (d) => simulateFirstData(d.id),
        "edit-model": (d) => editModel(d.id),
        "export-vehicles": () => ui.downloadCsv("vehicles.csv", [
          { label: "license_plate", key: "license_plate" }, { label: "vin", key: "vin" }, { label: "model", csv: (v) => modelOf(v).code },
          { label: "owner", csv: (v) => (db.org(v.organization_id) || {}).short_name }, { label: "fleet", csv: (v) => (db.fleet(v.fleet_id) || {}).name || "" },
          { label: "status", key: "status" }, { label: "status_reason", key: "status_reason" }, { label: "activation", key: "activation" },
          { label: "odometer_km", csv: (v) => Math.round(v.odometer_km) }, { label: "soh_percent", key: "soh_percent" },
        ], vehicleRows(query)),
      });
    },
  });

  /* =====================================================================
     DEVICES (DEV-01, DEV-02, DEV-04, DEV-05, DEV-06, DEV-07, DEV-08)
     ===================================================================== */
  /* Mount history: one row per period a device served a truck (DEV-02). */
  const mounts = db.telematics.filter((d) => d.vehicle_id).map((d) => ({ telematic_id: d.telematic_id, serial: d.telematic_serial, vehicle_id: d.vehicle_id, installed_at: d.installed_at, removed_at: null, reason: null }));
  [["veh-003", "TBX-2403-01877", 128, 41, "GNSS antenna fault, swapped under warranty", "Lỗi ăng-ten GNSS, thay theo bảo hành"], ["veh-012", "TBX-2403-02210", 150, 63, "Water in the connector after a flooded road", "Nước vào giắc cắm sau khi đi đường ngập"]].forEach(([vehicleId, serial, from, to, en, vi]) => {
    if (db.vehicle(vehicleId)) mounts.push({ telematic_id: null, serial, vehicle_id: vehicleId, installed_at: isoAgo(from), removed_at: isoAgo(to), reason: [en, vi] });
  });
  /* The interval each organization's devices should use (DEV-07: one per organization). */
  const orgInterval = { "org-tcx": 10, "org-mn": 15, "org-dn": 15, "org-ind": 15, "org-g3": 15 };
  const configCommands = [
    { command_id: "cfg-3", organization_id: "org-tcx", from: 15, to: 10, at: isoAgo(21), by: db.people.ops.user.user_id, published: 13, skipped: 1, failed: 0, rolled_back: false },
    { command_id: "cfg-2", organization_id: "org-mn", from: 30, to: 15, at: isoAgo(44), by: db.people.ops.user.user_id, published: 7, skipped: 0, failed: 0, rolled_back: false },
    { command_id: "cfg-1", organization_id: "org-tcx", from: 30, to: 15, at: isoAgo(60), by: db.people.head.user.user_id, published: 12, skipped: 0, failed: 1, rolled_back: false },
  ];
  /* Device identity (DEV-08): X.509 for firmware v2.3 and later, a token for older firmware. */
  const credentials = {};
  function credentialOf(d) {
    if (!credentials[d.telematic_id]) {
      const h = hashOf(d.telematic_id + "cred");
      const issued = d.installed_at ? new Date(d.installed_at).getTime() - 3 * DAY : NOW - 20 * DAY;
      let expires = issued + 365 * DAY;
      if (d.telematic_id === "tbx-003" || d.telematic_id === "tbx-015") expires = NOW + (9 + (h % 12)) * DAY;
      credentials[d.telematic_id] = {
        kind: d.firmware_version === "v2.2.8" ? "TOKEN" : "X509",
        fingerprint: h.toString(16).padStart(8, "0").toUpperCase().replace(/(..)(?=.)/g, "$1:") + ":" + (hashOf(d.telematic_serial) % 65536).toString(16).padStart(4, "0").toUpperCase().replace(/(..)(?=.)/g, "$1:"),
        issued_at: new Date(issued).toISOString(),
        expires_at: new Date(expires).toISOString(),
        revoked_at: d.telematic_id === "tbx-027" ? isoAgo(1) : null,
        revoke_reason: d.telematic_id === "tbx-027" ? "Nghi tháo thiết bị: mất nguồn đột ngột (DEV-06)" : null,
        rejected_24h: d.telematic_id === "tbx-027" ? 3 : 0,
      };
    }
    return credentials[d.telematic_id];
  }
  function credentialStatus(c) {
    if (c.revoked_at) return "REVOKED";
    const days = daysUntil(c.expires_at);
    return days < 0 ? "EXPIRED" : days <= 30 ? "EXPIRING" : "VALID";
  }
  const CRED_BADGE = { VALID: ["ok", ["Valid", "Hợp lệ"]], EXPIRING: ["warn", ["Expires soon", "Sắp hết hạn"]], EXPIRED: ["danger", ["Expired", "Hết hạn"]], REVOKED: ["danger", ["Revoked", "Đã thu hồi"]] };
  const credBadge = (s) => ui.badge(s, CRED_BADGE[s][0], t(CRED_BADGE[s][1][0], CRED_BADGE[s][1][1]));

  /* Why a silent device went quiet (DEV-06), from the last readings it sent. */
  const SILENCE = {
    POWER_CUT: ["danger", ["Power cut — possible removal", "Mất nguồn đột ngột — nghi tháo thiết bị"]],
    NO_SIGNAL: ["warn", ["No signal — coverage or antenna", "Mất sóng — vùng phủ hoặc ăng-ten"]],
    IGNITION_OFF: ["muted", ["Ignition off — truck parked", "Tắt máy — xe đang đỗ"]],
  };
  function silenceCause(d) {
    if (d.health !== "SILENT") return null;
    if (d.power_v !== null && d.power_v < 5) return "POWER_CUT";
    return hashOf(d.telematic_id) % 2 ? "NO_SIGNAL" : "IGNITION_OFF";
  }
  const causeBadge = (c) => ui.badge(c, SILENCE[c][0], t(SILENCE[c][1][0], SILENCE[c][1][1]));
  function causeEvidence(d, cause) {
    const v = db.vehicle(d.vehicle_id);
    const power = fmt.num(d.power_v, 1);
    if (cause === "POWER_CUT") return t(`Supply fell from 24 V to ${power} V in under 2 s with no ignition-off message before it. The device may have been removed or its wire cut: check the truck, the credential can be revoked (Security tab).`, `Nguồn tụt từ 24 V xuống ${power} V trong chưa đầy 2 giây, không có tin tắt máy trước đó. Có thể thiết bị bị tháo hoặc đứt dây: cần kiểm tra xe, có thể thu hồi chứng thư (thẻ Bảo mật).`);
    if (cause === "NO_SIGNAL") return t(`Still powered (${power} V on backup) when it went quiet, last signal −108 dBm and GNSS lost: most likely no coverage or a damaged antenna. Data is stored on the device and sent when it reconnects.`, `Vẫn còn nguồn (${power} V dự phòng) khi mất tín hiệu, sóng cuối −108 dBm và mất GNSS: nhiều khả năng mất vùng phủ hoặc hỏng ăng-ten. Dữ liệu được lưu trên thiết bị và gửi khi có kết nối lại.`);
    return t(`Ignition-off message received ${v && v.live ? fmt.ago(v.live.received_at) : ""}, then standby at ${power} V. Normal for a parked truck; no alert beyond the silence alert.`, `Đã nhận tin tắt máy ${v && v.live ? fmt.ago(v.live.received_at) : ""}, sau đó ở chế độ chờ ${power} V. Bình thường với xe đang đỗ; chỉ có cảnh báo mất tín hiệu.`);
  }

  function visibleDevices() {
    const ids = scope.vehicleIds();
    const orgIds = scope.orgIds();
    return db.telematics.filter((d) => (d.vehicle_id ? ids.has(d.vehicle_id) : orgIds.includes(d.organization_id)));
  }
  const healthOf = (d) => (d.deleted_at ? "RETIRED" : d.health);
  const lastSeen = (d) => { const v = db.vehicle(d.vehicle_id); return v && v.live && v.live.is_online ? v.live.received_at : d.last_seen_at; };
  const signalTone = (dbm) => (dbm === null ? "muted" : dbm < -100 ? "danger" : dbm < -90 ? "warn" : "ok");

  function deviceRows(query) {
    const q = (query.q || "").toLowerCase();
    return visibleDevices().filter((d) => {
      if (query.health && healthOf(d) !== query.health) return false;
      if (!query.health && d.deleted_at) return false;
      if (query.firmware && d.firmware_version !== query.firmware) return false;
      const v = db.vehicle(d.vehicle_id);
      if (q && !(d.telematic_serial.toLowerCase().includes(q) || d.imei.includes(q) || (v && v.license_plate.toLowerCase().includes(q)))) return false;
      return true;
    });
  }

  function registryDevicesTab(query) {
    const all = visibleDevices().filter(isLive);
    const mounted = all.filter((d) => d.vehicle_id);
    const healthy = mounted.filter((d) => d.health === "HEALTHY").length;
    const silent = mounted.filter((d) => d.health === "SILENT");
    const rows = deviceRows(query);
    const counts = {};
    visibleDevices().forEach((d) => { counts[healthOf(d)] = (counts[healthOf(d)] || 0) + 1; });
    const firmwares = [...new Set(all.map((d) => d.firmware_version))].sort();
    return `<div class="grid cols-5 mb">
        ${ui.kpi({ label: t("Healthy share", "Tỷ lệ ổn định"), icon: "check", value: fmt.pct((healthy / (mounted.length || 1)) * 100), tone: "ok", foot: `${healthy} / ${mounted.length} ${t("mounted", "đang lắp")}` })}
        ${ui.kpi({ label: t("Silent", "Im lặng"), icon: "signal", value: silent.length, tone: silent.length ? "danger" : undefined, foot: silent.filter((d) => silenceCause(d) === "POWER_CUT").length ? `<span style="color:var(--danger)">${silent.filter((d) => silenceCause(d) === "POWER_CUT").length} ${t("possible removal", "nghi bị tháo")}</span>` : "DEV-05" })}
        ${ui.kpi({ label: t("Needs attention", "Cần chú ý"), icon: "alert", value: mounted.filter((d) => d.health === "ATTENTION").length, tone: "warn", foot: t("weak signal, storage, old firmware", "sóng yếu, bộ nhớ, firmware cũ") })}
        ${ui.kpi({ label: t("No data yet", "Chưa có dữ liệu"), icon: "clock", value: mounted.filter((d) => d.health === "NO_DATA").length, foot: t("mounted, waiting for first data", "đã lắp, chờ dữ liệu đầu") })}
        ${ui.kpi({ label: t("Not mounted", "Chưa lắp"), icon: "chip", value: all.filter((d) => !d.vehicle_id).length, foot: t("in stock", "trong kho") })}
      </div>
      ${silent.length ? `<div class="grid cols-${Math.min(3, silent.length)} mb">${silent.map((d) => { const cause = silenceCause(d); const v = db.vehicle(d.vehicle_id); return `<div class="card clickable" data-open="${d.telematic_id}" style="cursor:pointer"><div class="card-body stack" style="gap:6px"><div class="row between"><b>${esc(v ? v.license_plate : d.telematic_serial)}</b><span class="small muted">${esc(t("silent", "im lặng"))} ${fmt.ago(lastSeen(d))}</span></div><div>${causeBadge(cause)}</div><div class="small muted">${esc(t("Last supply", "Nguồn cuối"))} ${fmt.num(d.power_v, 1)} V · <span class="mono">${esc(d.telematic_serial)}</span> · DEV-06</div></div></div>`; }).join("")}</div>` : ""}
      ${ui.card({
        flush: true,
        body: filterBar([
          ui.search("q", query.q, t("Serial, IMEI or plate", "Số serial, IMEI hoặc biển số")),
          ui.select("health", [["", t("Every health state", "Mọi tình trạng")], ...["HEALTHY", "ATTENTION", "SILENT", "NO_DATA", "NOT_MOUNTED", "RETIRED"].map((h) => [h, `${h === "RETIRED" ? t("Retired", "Đã loại bỏ") : G3.label(h)} (${counts[h] || 0})`])], query.health || ""),
          ui.select("firmware", [["", t("Every firmware", "Mọi firmware")], ...firmwares.map((f) => [f, f])], query.firmware || ""),
          `<span class="grow"></span><span class="small muted">${rows.length} ${t("devices", "thiết bị")}</span>`,
        ]) + ui.table({
          rows,
          rowAttrs: (d) => `data-open="${d.telematic_id}" style="cursor:pointer"`,
          selected: (d) => d.telematic_id === query.d,
          empty: t("No device matches", "Không có thiết bị phù hợp"),
          columns: [
            { label: t("Serial · IMEI · ICCID", "Serial · IMEI · ICCID"), render: (d) => `<b class="mono">${esc(d.telematic_serial)}</b><span class="sub mono">IMEI ${esc(d.imei)}</span><span class="sub mono">${esc(d.iccid)} · SIM ${esc(G3.label(d.sim_status))}</span>` },
            { label: "Firmware", cls: "nowrap", render: (d) => `<span class="mono">${esc(d.firmware_version)}</span>${d.firmware_version === "v2.2.8" ? ` <span class="badge warn plain" data-tip="${esc(t("Below the minimum v2.3", "Thấp hơn bản tối thiểu v2.3"))}">!</span>` : ""}` },
            { label: t("Owner", "Chủ sở hữu"), cls: "nowrap", render: (d) => ui.orgName(d.organization_id) },
            { label: t("Truck", "Xe"), cls: "nowrap", render: (d) => plateLink(db.vehicle(d.vehicle_id)) },
            { label: t("Health", "Tình trạng"), render: (d) => { const cause = silenceCause(d); return (d.deleted_at ? ui.badge("RETIRED", "muted", t("Retired", "Đã loại bỏ")) : ui.badge(d.health)) + (cause ? `<span class="sub">${esc(t(SILENCE[cause][1][0], SILENCE[cause][1][1]))}</span>` : ""); } },
            { label: t("Last seen", "Lần cuối"), cls: "nowrap", render: (d) => (lastSeen(d) ? fmt.ago(lastSeen(d)) : '<span class="muted">—</span>') },
            { label: t("Signal · power", "Sóng · nguồn"), align: "right", cls: "nowrap", render: (d) => `${d.signal_dbm === null ? '<span class="muted">—</span>' : `<span style="color:var(--${signalTone(d.signal_dbm)})">${d.signal_dbm} dBm</span>`}<span class="sub" style="${d.power_v !== null && d.power_v < 20 ? "color:var(--danger)" : ""}">${d.power_v === null ? "—" : fmt.num(d.power_v, 1) + " V"}</span>` },
            { label: t("Storage", "Bộ nhớ"), render: (d) => `<div class="row" style="flex-wrap:nowrap;gap:6px">${ui.meter(d.storage_used_percent, d.storage_used_percent > 85 ? "danger" : d.storage_used_percent > 70 ? "warn" : "")}<span class="small num">${d.storage_used_percent}%</span></div>` },
            { label: "GNSS", render: (d) => (d.gnss_fix === "NONE" ? '<span class="muted">—</span>' : ui.tag(d.gnss_fix)) },
          ],
        }),
      })}`;
  }

  function deviceDrawer(d) {
    const v = db.vehicle(d.vehicle_id);
    const cause = silenceCause(d);
    const silent = d.health === "SILENT";
    const n = 28;
    const signal = walk(d.telematic_id + "sig", n, d.signal_dbm || -88, 12, -115, -55);
    const power = walk(d.telematic_id + "pwr", n, 25.2, 1.6, 22.5, 28.2);
    const storageBase = Math.max(2, d.storage_used_percent - 12);
    const storage = Array.from({ length: n }, (_, i) => Math.round(storageBase + ((d.storage_used_percent - storageBase) * i) / (n - 1)));
    if (silent) { for (let i = n - 4; i < n; i++) { signal[i] = -115; power[i] = d.power_v; } }
    const days = Array.from({ length: 7 }, (_, i) => { const r = hashOf(d.telematic_id + "h" + i) % 10; if (!d.vehicle_id || d.health === "NO_DATA") return "NO_DATA"; if (i === 6) return d.health; if (silent && i >= 5) return "SILENT"; return r === 0 ? "ATTENTION" : d.health === "ATTENTION" && r < 5 ? "ATTENTION" : "HEALTHY"; });
    const dayColor = { HEALTHY: "var(--ok)", ATTENTION: "var(--warn)", SILENT: "var(--danger)", NO_DATA: "var(--border-strong)" };
    const history = mounts.filter((m) => m.telematic_id === d.telematic_id || (v && m.vehicle_id === v.vehicle_id)).sort((a, b) => (a.installed_at < b.installed_at ? 1 : -1));
    const spark = (label, values, unit, color, digits) => `<div><div class="row between small"><span class="muted">${esc(label)}</span><b class="num">${values[values.length - 1] === null ? "—" : fmt.num(values[values.length - 1], digits)} ${unit}</b></div>${ui.sparkline(values.map((x) => (x === null ? 0 : x)), color, 460, 34)}</div>`;
    const silenceNote = db.notifications.find((x) => x.notification_type === "DEVICE_OFFLINE_ALERT" && x.vehicle_id === d.vehicle_id);
    const body = `
      ${cause ? ui.callout(cause === "POWER_CUT" ? "danger" : cause === "NO_SIGNAL" ? "warn" : "info", `<b>${esc(t(SILENCE[cause][1][0], SILENCE[cause][1][1]))}</b> · DEV-06<br>${esc(causeEvidence(d, cause))}`) : ""}
      ${ui.kv([
        [t("Health", "Tình trạng"), d.deleted_at ? ui.badge("RETIRED", "muted", t("Retired", "Đã loại bỏ")) : ui.badge(d.health)],
        [t("Truck", "Xe"), v ? `${plateLink(v)} <span class="muted small">${t("since", "từ")} ${fmt.date(d.installed_at)}</span>` : `<span class="muted">${esc(t("Not mounted", "Chưa lắp"))}</span>`],
        [t("Owner", "Chủ sở hữu"), ui.orgName(d.organization_id)],
        ["IMEI", `<span class="mono">${esc(d.imei)}</span>`],
        ["ICCID", `<span class="mono">${esc(d.iccid)}</span> · SIM ${esc(G3.label(d.sim_status))}`],
        ["Firmware", `<span class="mono">${esc(d.firmware_version)}</span>`],
        [t("Send interval", "Chu kỳ gửi"), `${d.send_interval_seconds} s${d.reported_interval_seconds && d.reported_interval_seconds !== d.send_interval_seconds ? ` · <span style="color:var(--warn)">${t("device reports", "thiết bị báo")} ${d.reported_interval_seconds} s</span>` : ""}`],
        [t("Last seen", "Lần cuối"), lastSeen(d) ? `${fmt.dateTime(lastSeen(d))} · ${fmt.ago(lastSeen(d))}` : null],
        silenceNote ? [t("Silence alert", "Cảnh báo im lặng"), `${fmt.ago(silenceNote.created_at)} · ${esc(t("once per silence", "một lần mỗi đợt im lặng"))} (DEV-05)`] : null,
      ])}
      <div><h4 class="mb" style="margin-bottom:8px">${esc(t("Health, last 7 days", "Tình trạng 7 ngày qua"))}</h4><div style="display:grid;grid-template-columns:repeat(7,1fr);gap:4px">${days.map((h, i) => `<div data-tip="${esc(`${fmt.date(new Date(Date.now() - (6 - i) * DAY))}: ${G3.label(h)}`)}" style="height:18px;border-radius:4px;background:${dayColor[h]}"></div>`).join("")}</div></div>
      ${d.vehicle_id && d.health !== "NO_DATA" ? `<div class="stack" style="gap:10px">${spark(t("Signal (dBm)", "Sóng (dBm)"), signal, "dBm", "var(--accent)", 0)}${spark(t("Power supply", "Nguồn cấp"), power, "V", "var(--primary)", 1)}${spark(t("Storage used", "Bộ nhớ đã dùng"), storage, "%", "var(--violet)", 0)}</div>` : ""}
      <div><h4 style="margin-bottom:8px">${esc(t("Mount history", "Lịch sử lắp đặt"))}</h4>${history.length ? `<ul class="timeline">${history.map((m) => `<li><div><b>${esc(m.serial)}</b> → ${plateLink(db.vehicle(m.vehicle_id))}</div><div class="when">${fmt.date(m.installed_at)} – ${m.removed_at ? fmt.date(m.removed_at) : t("now", "nay")}${m.reason ? ` · ${esc(txt(m.reason))}` : ""}</div></li>`).join("")}</ul>` : `<div class="small muted">${esc(t("Never mounted", "Chưa từng lắp"))}</div>`}</div>`;
    const actions = d.deleted_at ? "" : `${d.vehicle_id ? `${ui.btn(t("Replace", "Thay thế"), { icon: "refresh", action: "replace-device", attrs: `data-id="${d.telematic_id}"` })}${ui.btn(t("Unassign", "Gỡ khỏi xe"), { action: "unassign-device", attrs: `data-id="${d.telematic_id}"` })}` : ui.btn(t("Assign to a truck", "Gán cho xe"), { icon: "truck", kind: "primary", action: "assign-device", attrs: `data-id="${d.telematic_id}"` })}${ui.btn(t("Retire", "Loại bỏ"), { kind: "danger", action: "retire-device", attrs: `data-id="${d.telematic_id}"` })}`;
    return drawer(d.telematic_serial, "T-Box · DEV-01 · DEV-02 · DEV-04 · DEV-06", body, actions);
  }

  /** Trucks that may take a device: in scope, in service and without a live device. */
  const trucksWithoutDevice = () => scope.vehicles().filter((v) => isLive(v) && !v.telematic_id);
  const stockDevices = () => visibleDevices().filter((d) => isLive(d) && !d.vehicle_id && d.status === "ACTIVE");

  function mountDevice(d, v, at, reason) {
    d.vehicle_id = v.vehicle_id;
    d.installed_at = at;
    d.organization_id = d.organization_id || "org-g3";
    v.telematic_id = d.telematic_id;
    d.health = v.live ? (v.live.is_online ? "HEALTHY" : "SILENT") : "NO_DATA";
    d.send_interval_seconds = orgInterval[v.organization_id] || 15;
    if (v.activation === "NO_DEVICE") v.activation = v.live ? "ACTIVATED" : "AWAITING_DATA";
    mounts.push({ telematic_id: d.telematic_id, serial: d.telematic_serial, vehicle_id: v.vehicle_id, installed_at: at, removed_at: null, reason: reason || null });
  }
  function unmountDevice(d, reason) {
    const v = db.vehicle(d.vehicle_id);
    const open = mounts.find((m) => m.telematic_id === d.telematic_id && !m.removed_at);
    if (open) { open.removed_at = new Date().toISOString(); open.reason = reason; }
    if (v) v.telematic_id = null;
    d.vehicle_id = null;
    d.health = "NOT_MOUNTED";
    d.signal_dbm = null; d.power_v = null; d.gnss_fix = "NONE";
  }

  function assignDeviceModal(deviceId, vehicleId) {
    const devices = deviceId ? [db.telematic(deviceId)] : stockDevices();
    const trucks = trucksWithoutDevice();
    if (!devices.length) { ui.toast(t("No device in stock: add one first", "Không còn thiết bị trong kho: hãy thêm thiết bị"), "alert"); return; }
    if (!trucks.length) { ui.toast(t("Every truck already has a device", "Mọi xe đều đã có thiết bị"), "alert"); return; }
    ui.formModal({
      title: t("Assign a device to a truck", "Gán thiết bị cho xe"),
      intro: ui.callout("info", esc(t("One live device per truck. The truck is activated when the first data from this device arrives (VEH-05).", "Mỗi xe chỉ có một thiết bị đang hoạt động. Xe được kích hoạt khi nhận dữ liệu đầu tiên từ thiết bị này (VEH-05)."))),
      fields: [
        { name: "device", label: t("Device", "Thiết bị"), type: "select", required: true, options: devices.map((d) => [d.telematic_id, `${d.telematic_serial} · ${d.firmware_version}`]), value: deviceId },
        { name: "vehicle", label: t("Truck", "Xe"), type: "select", required: true, options: trucks.map((v) => [v.vehicle_id, `${v.license_plate} · ${modelOf(v).code} · ${db.org(v.organization_id).short_name}`]), value: vehicleId },
        { name: "date", label: t("Mounted on", "Ngày lắp"), type: "date", required: true, value: todayIso() },
      ],
      submitLabel: t("Assign", "Gán"),
      onSubmit(values) {
        const d = db.telematic(values.device), v = db.vehicle(values.vehicle);
        if (v.telematic_id) return t(`${v.license_plate} already has a live device.`, `Xe ${v.license_plate} đã có thiết bị đang hoạt động.`);
        if (values.date > todayIso()) return t("The mount date cannot be in the future.", "Ngày lắp không được ở tương lai.");
        mountDevice(d, v, new Date(values.date + "T09:00:00").toISOString());
        G3.setQuery({ d: d.telematic_id, assign: null });
        ui.toast(t(`${d.telematic_serial} mounted on ${v.license_plate} — waiting for first data`, `Đã lắp ${d.telematic_serial} lên xe ${v.license_plate} — chờ dữ liệu đầu`));
      },
    });
  }

  function replaceDeviceModal(id) {
    const old = db.telematic(id);
    const v = db.vehicle(old.vehicle_id);
    const stock = stockDevices();
    if (!stock.length) { ui.toast(t("No device in stock to replace it with", "Không còn thiết bị trong kho để thay"), "alert"); return; }
    ui.formModal({
      title: t(`Replace the device on ${v.license_plate}`, `Thay thiết bị trên xe ${v.license_plate}`),
      intro: ui.callout("info", esc(t("The old device is unmounted and kept in the history; the truck's data continues from the new one.", "Thiết bị cũ được gỡ và lưu trong lịch sử; dữ liệu của xe tiếp tục từ thiết bị mới."))),
      fields: [
        { name: "device", label: t("New device", "Thiết bị mới"), type: "select", required: true, options: stock.map((d) => [d.telematic_id, `${d.telematic_serial} · ${d.firmware_version}`]) },
        { name: "reason", label: t("Reason", "Lý do"), type: "textarea", required: true, placeholder: t("Faulty GNSS, water damage…", "Lỗi GNSS, vào nước…") },
      ],
      submitLabel: t("Replace", "Thay thế"),
      onSubmit(values) {
        const fresh = db.telematic(values.device);
        unmountDevice(old, values.reason.slice(0, 200));
        mountDevice(fresh, v, new Date().toISOString(), null);
        G3.setQuery({ d: fresh.telematic_id });
        ui.toast(t(`${fresh.telematic_serial} replaces ${old.telematic_serial} on ${v.license_plate}`, `${fresh.telematic_serial} thay ${old.telematic_serial} trên xe ${v.license_plate}`));
      },
    });
  }

  function addDeviceModal() {
    ui.formModal({
      title: t("Add a device", "Thêm thiết bị"),
      wide: true,
      fields: [
        { name: "serial", label: t("Serial", "Số serial"), required: true, placeholder: "TBX-2410-05123" },
        { name: "imei", label: "IMEI", required: true, placeholder: t("15 digits", "15 chữ số"), attrs: 'inputmode="numeric" maxlength="15"' },
        { name: "iccid", label: "ICCID", required: true, placeholder: t("19 or 20 digits", "19 hoặc 20 chữ số"), attrs: 'inputmode="numeric" maxlength="20"' },
        { name: "firmware", label: "Firmware", type: "select", required: true, options: [["v2.3.1", "v2.3.1"], ["v2.4.0-rc1", "v2.4.0-rc1"]] },
      ],
      submitLabel: t("Add device", "Thêm thiết bị"),
      onSubmit(values) {
        const serial = values.serial.toUpperCase();
        if (!/^TBX-\d{4}-\d{5}$/.test(serial)) return t("Serial must look like TBX-2410-05123.", "Số serial phải có dạng TBX-2410-05123.");
        if (!/^\d{15}$/.test(values.imei)) return t("IMEI must be exactly 15 digits.", "IMEI phải gồm đúng 15 chữ số.");
        if (!/^\d{19,20}$/.test(values.iccid)) return t("ICCID must be 19 or 20 digits.", "ICCID phải gồm 19 hoặc 20 chữ số.");
        const live = db.telematics.filter(isLive);
        if (live.some((d) => d.telematic_serial === serial)) return t("This serial is already registered.", "Số serial này đã được đăng ký.");
        if (live.some((d) => d.imei === values.imei)) return t("This IMEI is already registered.", "IMEI này đã được đăng ký.");
        const id = "tbx-n" + (db.telematics.length + 1);
        db.telematics.push({ telematic_id: id, telematic_serial: serial, imei: values.imei, iccid: values.iccid, firmware_version: values.firmware, vehicle_id: null, organization_id: "org-g3", status: "ACTIVE", installed_at: null, send_interval_seconds: 15, reported_interval_seconds: null, signal_dbm: null, power_v: null, storage_used_percent: 0, gnss_fix: "NONE", sim_status: "OK", last_seen_at: null, health: "NOT_MOUNTED" });
        G3.setQuery({ d: id });
        ui.toast(t(`${serial} added to stock`, `Đã thêm ${serial} vào kho`));
      },
    });
  }

  /** Devices of the organization's trucks and what a push would do to each (DEV-07). */
  function pushTargets(orgId) {
    return db.vehicles.filter((v) => v.organization_id === orgId).map((v) => {
      const d = db.telematic(v.telematic_id);
      let outcome = "PUBLISHED", why = null;
      if (!isLive(v)) { outcome = "SKIPPED"; why = t("Truck retired", "Xe đã loại bỏ"); }
      else if (!d) { outcome = "SKIPPED"; why = t("No live device", "Chưa có thiết bị"); }
      else if (d.status !== "ACTIVE" || d.deleted_at) { outcome = "SKIPPED"; why = t("Device not active", "Thiết bị không hoạt động"); }
      else if (d.telematic_id === "tbx-019") { outcome = "FAILED"; why = t("Broker refused: session expired", "Broker từ chối: phiên đã hết hạn"); }
      return { v, d, outcome, why };
    });
  }
  const OUTCOME_BADGE = { PUBLISHED: ["ok", ["Published", "Đã gửi"]], SKIPPED: ["muted", ["Skipped", "Bỏ qua"]], FAILED: ["danger", ["Failed", "Thất bại"]] };
  const outcomeBadge = (o) => ui.badge(o, OUTCOME_BADGE[o][0], t(OUTCOME_BADGE[o][1][0], OUTCOME_BADGE[o][1][1]));

  function configTab(query) {
    const orgs = scope.filter(db.organizations).filter((o) => db.vehicles.some((v) => v.organization_id === o.organization_id && v.telematic_id)).sort((a, b) => a.is_internal - b.is_internal);
    const orgId = orgs.some((o) => o.organization_id === query.org) ? query.org : (orgs[0] || {}).organization_id;
    if (!orgId) return ui.empty(t("No organization with devices", "Không có tổ chức nào có thiết bị"));
    const targets = pushTargets(orgId);
    const interval = orgInterval[orgId] || 15;
    const applied = targets.filter((x) => x.d && x.outcome !== "SKIPPED");
    const confirmState = (x) => {
      if (!x.d || x.outcome === "SKIPPED") return ["muted", t("—", "—")];
      if (x.d.health === "SILENT" || x.d.health === "NO_DATA") return ["warn", t("Waiting for a status report", "Chờ báo cáo trạng thái")];
      return x.d.reported_interval_seconds === x.d.send_interval_seconds ? ["ok", t("Applied", "Đã áp dụng")] : ["danger", t("Not applied", "Chưa áp dụng")];
    };
    const notApplied = applied.filter((x) => confirmState(x)[0] === "danger").length;
    const history = configCommands.filter((c) => c.organization_id === orgId);
    return `${ui.featureChips(["DEV-07"])}
      <div class="grid side mt">
        ${ui.card({
          title: t("Send interval of the organization", "Chu kỳ gửi dữ liệu của tổ chức"),
          actions: ui.select("org", orgs.map((o) => [o.organization_id, o.short_name]), orgId),
          body: `<div class="row" style="gap:18px;align-items:flex-end">
              <div><div class="small muted">${esc(t("Current", "Hiện tại"))}</div><div style="font-size:30px;font-weight:650" class="num">${interval}<small class="muted" style="font-size:14px"> ${esc(t("seconds", "giây"))}</small></div></div>
              <div class="field"><label>${esc(t("New interval", "Chu kỳ mới"))}</label>${ui.select("interval", [5, 10, 15, 30, 60].map((s) => [s, `${s} s`]), query.interval || interval)}</div>
              ${ui.btn(t(`Push to ${applied.length} devices`, `Gửi tới ${applied.length} thiết bị`), { icon: "send", kind: "primary", action: "push-config", attrs: `data-org="${orgId}"` })}
            </div>
            <div class="divider"></div>
            <div class="small muted">${esc(t("One interval for every truck of the organization. Each device confirms the value it uses in its next status report; a device reporting another value has not applied it.", "Một chu kỳ cho mọi xe của tổ chức. Mỗi thiết bị xác nhận giá trị đang dùng trong báo cáo trạng thái kế tiếp; thiết bị báo giá trị khác là chưa áp dụng."))}</div>
            ${notApplied ? `<div class="mt">${ui.callout("warn", esc(t(`${notApplied} device(s) report another interval than the one pushed.`, `${notApplied} thiết bị đang báo chu kỳ khác với giá trị đã gửi.`)))}</div>` : ""}`,
        })}
        ${ui.card({
          title: t("Command history", "Lịch sử lệnh"), flush: true,
          body: history.length ? `<ul class="list">${history.map((c, i) => `<li><div class="sev-icon INFO">${ui.icon("send", 15)}</div><div class="grow"><div class="title">${c.from} s → ${c.to} s ${c.rolled_back ? ui.tag(t("rolled back", "đã hoàn tác")) : ""}</div><div class="meta">${fmt.dateTime(c.at)} · ${ui.userName(c.by)}</div><div class="meta">${c.published} ${t("published", "đã gửi")} · ${c.skipped} ${t("skipped", "bỏ qua")} · ${c.failed} ${t("failed", "thất bại")}</div></div>${i === 0 && !c.rolled_back ? ui.btn(t("Roll back", "Hoàn tác"), { sm: true, icon: "refresh", action: "rollback-config", attrs: `data-id="${c.command_id}"` }) : ""}</li>`).join("")}</ul>` : ui.empty(t("No command sent yet", "Chưa gửi lệnh nào")),
        })}
      </div>
      <div class="mt">${ui.card({
        title: t("Result per device", "Kết quả từng thiết bị"), sub: `${targets.length}`, flush: true,
        body: ui.table({
          rows: targets,
          columns: [
            { label: t("Truck", "Xe"), render: (x) => plateLink(x.v) },
            { label: t("Device", "Thiết bị"), render: (x) => (x.d ? `<span class="mono">${esc(x.d.telematic_serial)}</span>` : '<span class="muted">—</span>') },
            { label: t("Last push", "Lần gửi cuối"), render: (x) => `${outcomeBadge(x.outcome)}${x.why ? `<span class="sub">${esc(x.why)}</span>` : ""}` },
            { label: t("Pushed", "Đã gửi"), align: "right", render: (x) => (x.d ? `${x.d.send_interval_seconds} s` : "—") },
            { label: t("Reported", "Thiết bị báo"), align: "right", render: (x) => (x.d && x.d.reported_interval_seconds ? `${x.d.reported_interval_seconds} s` : "—") },
            { label: t("Confirmation", "Xác nhận"), render: (x) => { const [tone, label] = confirmState(x); return tone === "muted" ? '<span class="muted">—</span>' : ui.badge(tone, tone, label); } },
          ],
        }),
      })}</div>`;
  }

  /** Push an interval to every device of the organization; online devices confirm it on their next report. */
  function pushConfig(orgId, interval, isRollback) {
    const targets = pushTargets(orgId);
    const from = orgInterval[orgId] || 15;
    targets.forEach((x) => {
      if (x.outcome !== "PUBLISHED") return;
      x.d.send_interval_seconds = interval;
      // A stuck device (veh-005's old firmware) keeps reporting its old value: the "not applied" case.
      if (x.d.health !== "SILENT" && x.d.health !== "NO_DATA" && x.d.telematic_id !== "tbx-005") x.d.reported_interval_seconds = interval;
    });
    orgInterval[orgId] = interval;
    configCommands.unshift({ command_id: "cfg-" + (configCommands.length + 1), organization_id: orgId, from, to: interval, at: new Date().toISOString(), by: me(), published: targets.filter((x) => x.outcome === "PUBLISHED").length, skipped: targets.filter((x) => x.outcome === "SKIPPED").length, failed: targets.filter((x) => x.outcome === "FAILED").length, rolled_back: false, is_rollback: !!isRollback });
    G3.render();
    ui.toast(t(`Interval ${interval} s pushed: ${configCommands[0].published} published, ${configCommands[0].failed} failed`, `Đã gửi chu kỳ ${interval} s: ${configCommands[0].published} thành công, ${configCommands[0].failed} thất bại`), "", "send");
  }

  function securityTab(query) {
    const devices = visibleDevices().filter(isLive);
    const rows = devices.filter((d) => !query.cred || credentialStatus(credentialOf(d)) === query.cred);
    const counts = { VALID: 0, EXPIRING: 0, EXPIRED: 0, REVOKED: 0 };
    devices.forEach((d) => counts[credentialStatus(credentialOf(d))]++);
    const tokens = devices.filter((d) => credentialOf(d).kind === "TOKEN").length;
    return `${ui.featureChips(["DEV-08"])}
      <div class="grid cols-4 mb mt">
        ${ui.kpi({ label: t("Valid credentials", "Chứng thư hợp lệ"), icon: "shield", value: counts.VALID, tone: "ok", foot: `${devices.length - tokens} X.509 · ${tokens} token` })}
        ${ui.kpi({ label: t("Expire within 30 days", "Hết hạn trong 30 ngày"), icon: "clock", value: counts.EXPIRING, tone: counts.EXPIRING ? "warn" : undefined })}
        ${ui.kpi({ label: t("Revoked", "Đã thu hồi"), icon: "lock", value: counts.REVOKED, tone: counts.REVOKED ? "danger" : undefined })}
        ${ui.kpi({ label: t("Rejected connections (24 h)", "Kết nối bị từ chối (24 giờ)"), icon: "alert", value: devices.reduce((a, d) => a + credentialOf(d).rejected_24h, 0), foot: t("checked on every connection", "kiểm tra mỗi lần kết nối") })}
      </div>
      ${tokens ? ui.callout("warn", esc(t(`${tokens} device(s) on firmware v2.2.8 still use a token: update to v2.3 to move them to a certificate.`, `${tokens} thiết bị dùng firmware v2.2.8 vẫn dùng token: nâng lên v2.3 để chuyển sang chứng thư.`))) : ""}
      <div class="mt">${ui.card({
        flush: true,
        actions: "",
        body: filterBar([ui.segmented([["", t("All", "Tất cả")], ...Object.keys(counts).map((k) => [k, `${t(CRED_BADGE[k][1][0], CRED_BADGE[k][1][1])} (${counts[k]})`])], query.cred || "", "cred"), '<span class="grow"></span>', counts.EXPIRING ? ui.btn(t("Renew every expiring", "Gia hạn tất cả sắp hết hạn"), { sm: true, icon: "refresh", action: "renew-expiring" }) : ""]) + ui.table({
          rows,
          columns: [
            { label: t("Device", "Thiết bị"), render: (d) => `<b class="mono">${esc(d.telematic_serial)}</b><span class="sub">${d.vehicle_id ? esc(db.vehicle(d.vehicle_id).license_plate) : t("in stock", "trong kho")}</span>` },
            { label: t("Kind", "Loại"), render: (d) => ui.tag(credentialOf(d).kind === "X509" ? t("X.509 certificate", "Chứng thư X.509") : t("Device token", "Token thiết bị")) },
            { label: t("Fingerprint", "Dấu vân tay"), render: (d) => `<span class="mono small">${esc(credentialOf(d).fingerprint)}</span>` },
            { label: t("Issued", "Cấp ngày"), render: (d) => fmt.date(credentialOf(d).issued_at) },
            { label: t("Expires", "Hết hạn"), render: (d) => { const c = credentialOf(d); const days = daysUntil(c.expires_at); return `${fmt.date(c.expires_at)}<span class="sub" style="${days <= 30 ? "color:var(--warn)" : ""}">${fmt.ago(c.expires_at)}</span>`; } },
            { label: t("Status", "Trạng thái"), render: (d) => { const c = credentialOf(d); const s = credentialStatus(c); return c.revoke_reason ? `<span data-tip="${esc(c.revoke_reason)}" style="cursor:help">${credBadge(s)}</span>` : credBadge(s); } },
            { label: t("Last verified", "Xác thực cuối"), render: (d) => { const c = credentialOf(d); return c.revoked_at ? `<span style="color:var(--danger)">${c.rejected_24h} ${t("rejected", "bị từ chối")}</span>` : lastSeen(d) ? fmt.ago(lastSeen(d)) : '<span class="muted">—</span>'; } },
            { label: "", align: "right", render: (d) => (credentialOf(d).revoked_at ? ui.btn(t("Issue new", "Cấp mới"), { sm: true, action: "issue-cred", attrs: `data-id="${d.telematic_id}"` }) : ui.btn(t("Revoke", "Thu hồi"), { sm: true, kind: "ghost", action: "revoke-cred", attrs: `data-id="${d.telematic_id}"` })) },
          ],
        }),
      })}</div>`;
  }

  function issueCredential(d) {
    const c = credentialOf(d);
    const now = Date.now();
    Object.assign(c, { kind: d.firmware_version === "v2.2.8" ? "TOKEN" : "X509", fingerprint: (hashOf(d.telematic_id + now) >>> 0).toString(16).padStart(8, "0").toUpperCase().replace(/(..)(?=.)/g, "$1:") + ":5E:A1", issued_at: new Date(now).toISOString(), expires_at: new Date(now + 365 * DAY).toISOString(), revoked_at: null, revoke_reason: null, rejected_24h: 0 });
  }

  G3.page({
    id: "devices",
    path: "/devices",
    title: ["Devices", "Thiết bị"],
    roles: ["OPERATIONS"],
    features: ["DEV-01", "DEV-02", "DEV-04", "DEV-05", "DEV-06", "DEV-07", "DEV-08"],
    nav: { group: "assets", order: 2, icon: "chip", badge: () => visibleDevices().filter((d) => d.health === "SILENT" && isLive(d)).length },
    render({ query }) {
      const tabs = [["registry", t("Registry & health", "Danh sách & tình trạng")], ["config", t("Remote configuration", "Cấu hình từ xa")], ["security", t("Security", "Bảo mật")]];
      const tab = tabs.some(([k]) => k === query.tab) ? query.tab : "registry";
      const selected = query.d && tab === "registry" ? db.telematic(query.d) : null;
      const body = tab === "config" ? configTab(query) : tab === "security" ? securityTab(query) : registryDevicesTab(query);
      return `${ui.pageHead({
        title: t("Telematics devices", "Thiết bị telematics"),
        sub: esc(t("Every T-Box: registry, health, which truck it serves, its configuration and identity.", "Mọi T-Box: danh sách, tình trạng, gắn với xe nào, cấu hình và định danh.")),
        features: ["DEV-01", "DEV-02", "DEV-04", "DEV-05", "DEV-06", "DEV-07", "DEV-08"],
        actions: tab === "registry" ? `${ui.btn(t("Assign to a truck", "Gán cho xe"), { icon: "truck", action: "assign-any" })}${ui.btn(t("Add a device", "Thêm thiết bị"), { icon: "plus", kind: "primary", action: "add-device" })}` : "",
      })}${ui.tabs(tabs, tab)}${body}${selected && (!selected.vehicle_id || scope.vehicleIds().has(selected.vehicle_id)) ? deviceDrawer(selected) : ""}`;
    },
    mount(root, { query }) {
      bindCommon(root);
      bindActions(root, {
        "close-drawer": () => G3.setQuery({ d: null }),
        "add-device": addDeviceModal,
        "assign-any": () => assignDeviceModal(null, null),
        "assign-device": (d) => assignDeviceModal(d.id, null),
        "replace-device": (d) => replaceDeviceModal(d.id),
        "unassign-device": (data) => {
          const d = db.telematic(data.id);
          ui.reasonModal({
            title: t(`Unassign ${d.telematic_serial}`, `Gỡ ${d.telematic_serial} khỏi xe`),
            intro: ui.callout("warn", esc(t("The truck stops sending data until another device is mounted.", "Xe sẽ ngừng gửi dữ liệu cho đến khi lắp thiết bị khác."))),
            submitLabel: t("Unassign", "Gỡ khỏi xe"),
            onSubmit(reason) { const plate = db.vehicle(d.vehicle_id).license_plate; unmountDevice(d, reason); G3.render(); ui.toast(t(`${d.telematic_serial} removed from ${plate}`, `Đã gỡ ${d.telematic_serial} khỏi xe ${plate}`)); },
          });
        },
        "retire-device": (data) => {
          const d = db.telematic(data.id);
          ui.reasonModal({
            title: t(`Retire ${d.telematic_serial}`, `Loại bỏ ${d.telematic_serial}`),
            intro: ui.callout("warn", esc(t("The device leaves the system; its history is kept and its credential is revoked.", "Thiết bị rời khỏi hệ thống; lịch sử được giữ và chứng thư bị thu hồi."))),
            submitLabel: t("Retire device", "Loại bỏ thiết bị"),
            onSubmit(reason) {
              if (d.vehicle_id) unmountDevice(d, reason);
              d.status = "INACTIVE"; d.status_reason = reason; d.deleted_at = new Date().toISOString();
              const c = credentialOf(d); c.revoked_at = d.deleted_at; c.revoke_reason = reason;
              G3.setQuery({ d: null });
              ui.toast(t(`${d.telematic_serial} retired`, `Đã loại bỏ ${d.telematic_serial}`));
            },
          });
        },
        "push-config": (data) => {
          const value = Number(root.querySelector("[data-filter=interval]").value);
          const from = orgInterval[data.org] || 15;
          if (value === from) { ui.toast(t("The devices already use this interval", "Thiết bị đã dùng chu kỳ này"), "", "info"); return; }
          ui.modal({
            title: t("Push configuration", "Gửi cấu hình"),
            body: `${ui.callout("info", esc(t(`Send interval ${from} s → ${value} s for every truck of ${db.org(data.org).short_name}.`, `Chu kỳ gửi ${from} s → ${value} s cho mọi xe của ${db.org(data.org).short_name}.`)))}<p class="small muted">${esc(t("A shorter interval means fresher positions but more mobile data and storage on the device.", "Chu kỳ ngắn hơn cho vị trí mới hơn nhưng tốn dữ liệu di động và bộ nhớ thiết bị hơn."))}</p>`,
            foot: `<button class="btn" data-close>${esc(t("Cancel", "Hủy"))}</button><button class="btn primary" data-confirm>${esc(t("Push", "Gửi"))}</button>`,
            onMount(el, close) { el.querySelector("[data-confirm]").addEventListener("click", () => { close(); pushConfig(data.org, value, false); }); },
          });
        },
        "rollback-config": (data) => {
          const c = configCommands.find((x) => x.command_id === data.id);
          c.rolled_back = true;
          pushConfig(c.organization_id, c.from, true);
        },
        "revoke-cred": (data) => {
          const d = db.telematic(data.id);
          ui.reasonModal({
            title: t(`Revoke the credential of ${d.telematic_serial}`, `Thu hồi chứng thư của ${d.telematic_serial}`),
            intro: ui.callout("warn", esc(t("Every new connection from this device is refused until a new credential is issued.", "Mọi kết nối mới từ thiết bị này bị từ chối cho đến khi cấp chứng thư mới."))),
            submitLabel: t("Revoke", "Thu hồi"),
            onSubmit(reason) { const c = credentialOf(d); c.revoked_at = new Date().toISOString(); c.revoke_reason = reason; G3.render(); ui.toast(t(`Credential of ${d.telematic_serial} revoked`, `Đã thu hồi chứng thư của ${d.telematic_serial}`)); },
          });
        },
        "issue-cred": (data) => { const d = db.telematic(data.id); issueCredential(d); G3.render(); ui.toast(t(`New credential issued to ${d.telematic_serial}`, `Đã cấp chứng thư mới cho ${d.telematic_serial}`)); },
        "renew-expiring": () => {
          const list = visibleDevices().filter((d) => isLive(d) && credentialStatus(credentialOf(d)) === "EXPIRING");
          list.forEach(issueCredential);
          G3.render();
          ui.toast(t(`${list.length} credential(s) renewed`, `Đã gia hạn ${list.length} chứng thư`));
        },
      });
      // Opened from the activation list (/devices?assign=<vehicle>): open the form once, then drop the parameter.
      if (query.assign) {
        const vehicleId = query.assign;
        setTimeout(() => { G3.setQuery({ assign: null }); assignDeviceModal(null, vehicleId); }, 0);
      }
    },
  });

  /* =====================================================================
     BATTERIES (BAT-01, MON-07)
     ===================================================================== */
  const installs = db.batteries.filter((b) => b.vehicle_id).map((b) => ({ battery_id: b.battery_id, vehicle_id: b.vehicle_id, installed_at: b.installed_at, removed_at: null, reason: null }));
  // bat-022 left its truck for a cell replacement (repair case RC-0398) and came back.
  installs.filter((x) => x.battery_id === "bat-022").forEach((x) => {
    installs.push({ battery_id: x.battery_id, vehicle_id: x.vehicle_id, installed_at: x.installed_at, removed_at: isoAgo(21), reason: ["Removed for a cell module replacement (RC-0398)", "Tháo ra để thay mô-đun cell (RC-0398)"] });
    x.installed_at = isoAgo(14);
  });
  if (db.vehicle("veh-027")) installs.push({ battery_id: "bat-spare", vehicle_id: "veh-027", installed_at: isoAgo(40), removed_at: isoAgo(38), reason: ["Acceptance test on the stock truck done", "Đã chạy thử nghiệm thu trên xe kho"] });
  if (db.vehicle("veh-022")) installs.push({ battery_id: "bat-spare", vehicle_id: "veh-022", installed_at: isoAgo(21), removed_at: isoAgo(14), reason: ["Loan pack while cells of bat-022 were replaced", "Pin cho mượn trong lúc thay cell của bat-022"] });

  const visibleBatteries = () => { const ids = scope.vehicleIds(); const orgIds = scope.orgIds(); return db.batteries.filter((b) => (b.vehicle_id ? ids.has(b.vehicle_id) : orgIds.includes(b.organization_id))); };
  const batteryModel = (b) => db.battery_models.find((m) => m.battery_model_id === b.battery_model_id) || {};
  const sohTone = (soh) => (soh < 90 ? "danger" : soh < 95 ? "warn" : "");

  function batteriesRender(query) {
    const all = visibleBatteries().filter(isLive);
    const q = (query.q || "").toLowerCase();
    const rows = visibleBatteries().filter((b) => {
      if (query.status === "RETIRED") return !isLive(b);
      if (!isLive(b)) return false;
      if (query.status === "FITTED" && !b.vehicle_id) return false;
      if (query.status === "SPARE" && b.vehicle_id) return false;
      if (query.model && b.battery_model_id !== query.model) return false;
      if (query.owner === "differs") { const v = db.vehicle(b.vehicle_id); if (!v || v.organization_id === b.organization_id) return false; }
      if (q && !(b.battery_serial.toLowerCase().includes(q) || (db.vehicle(b.vehicle_id) || { license_plate: "" }).license_plate.toLowerCase().includes(q))) return false;
      return true;
    }).sort((a, b) => a.soh_percent - b.soh_percent);
    const fitted = all.filter((b) => b.vehicle_id);
    const spare = all.filter((b) => !b.vehicle_id);
    const leased = fitted.filter((b) => db.vehicle(b.vehicle_id).organization_id !== b.organization_id);
    const avg = fitted.reduce((a, b) => a + b.soh_percent, 0) / (fitted.length || 1);
    const lowest = fitted.slice().sort((a, b) => a.soh_percent - b.soh_percent).slice(0, 8);
    const selected = query.b ? db.battery(query.b) : null;
    const canEdit = scope.has("OPERATIONS", "MAINTENANCE", "HEAD_ADMIN");
    // SOH vs cycles: a simple dot plot (MON-07).
    const dots = (() => {
      const W = 640, H = 220, L = 40, B = 24;
      const maxC = Math.max(1400, ...fitted.map((b) => b.cycle_count));
      const x = (c) => L + (c / maxC) * (W - L - 12);
      const y = (s) => 10 + ((100 - s) / 14) * (H - B - 10);
      let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px">`;
      [100, 96, 92, 88].forEach((s) => { svg += `<line class="grid-line" x1="${L}" x2="${W - 12}" y1="${y(s)}" y2="${y(s)}"/><text x="${L - 6}" y="${y(s) + 4}" text-anchor="end">${s}%</text>`; });
      svg += `<line x1="${L}" x2="${W - 12}" y1="${y(90)}" y2="${y(90)}" stroke="var(--danger)" stroke-dasharray="4 4"/><text x="${W - 14}" y="${y(90) - 4}" text-anchor="end" style="fill:var(--danger)">${esc(t("alert 90%", "ngưỡng 90%"))}</text>`;
      [0, 400, 800, 1200].forEach((c) => { svg += `<text x="${x(c)}" y="${H - 6}" text-anchor="middle">${c}</text>`; });
      fitted.forEach((b) => { const v = db.vehicle(b.vehicle_id); svg += `<circle cx="${x(b.cycle_count).toFixed(1)}" cy="${y(Math.max(86, b.soh_percent)).toFixed(1)}" r="5.5" fill="${b.soh_percent < 90 ? "#dc2626" : ui.PALETTE[["bm-211", "bm-350", "bm-423"].indexOf(b.battery_model_id) + 1]}" fill-opacity="0.8" data-tip="${esc(`${b.battery_serial} · ${v.license_plate} · SOH ${fmt.num(b.soh_percent, 1)}% · ${b.cycle_count} ${t("cycles", "chu kỳ")}`)}"/>`; });
      return svg + "</svg>";
    })();
    return `${ui.pageHead({
      title: t("Batteries", "Pin"),
      sub: esc(t("Every pack as an asset: its model, owner (which may differ from the truck's), the trucks it was fitted to and its health.", "Mỗi bộ pin là một tài sản: dòng pin, chủ sở hữu (có thể khác chủ xe), các xe đã lắp và sức khỏe pin.")),
      features: ["BAT-01", "MON-07"],
      actions: canEdit ? `${ui.btn(t("Add a pack", "Thêm bộ pin"), { icon: "plus", kind: "primary", action: "add-pack" })}` : "",
    })}
      <div class="grid cols-5 mb">
        ${ui.kpi({ label: t("Packs", "Bộ pin"), icon: "battery", value: all.length, foot: `${fitted.length} ${t("fitted", "đang lắp")} · ${spare.length} ${t("spare", "dự phòng")}` })}
        ${ui.kpi({ label: t("Average SOH", "SOH trung bình"), icon: "activity", value: fmt.num(avg, 1), unit: "%", tone: avg < 95 ? "warn" : "ok" })}
        ${ui.kpi({ label: t("Below 90% SOH", "SOH dưới 90%"), icon: "alert", value: fitted.filter((b) => b.soh_percent < 90).length, tone: fitted.some((b) => b.soh_percent < 90) ? "danger" : undefined, foot: "MON-07" })}
        ${ui.kpi({ label: t("Average cycles", "Số chu kỳ trung bình"), icon: "refresh", value: fmt.num(fitted.reduce((a, b) => a + b.cycle_count, 0) / (fitted.length || 1)), foot: t("warranty limit 4,000", "giới hạn bảo hành 4.000") })}
        ${ui.kpi({ label: t("Leased packs", "Pin cho thuê"), icon: "handshake", value: leased.length, foot: t("owner ≠ truck owner", "chủ pin ≠ chủ xe") })}
      </div>
      <div class="grid side mb">
        ${ui.card({ title: t("Health against cycles", "Sức khỏe theo số chu kỳ"), sub: t("each dot is a fitted pack", "mỗi chấm là một bộ pin đang lắp"), actions: ui.legend(db.battery_models.map((m, i) => [`${m.capacity_kwh} kWh`, ui.PALETTE[i + 1]])), body: dots })}
        ${ui.card({ title: t("Lowest health", "Sức khỏe thấp nhất"), sub: t("scale 80–100%", "thang 80–100%"), body: `<div class="stack" style="gap:10px">${lowest.map((b) => `<div class="clickable" data-open="${b.battery_id}" data-open-key="b" style="cursor:pointer"><div class="row between small"><span><b>${esc(db.vehicle(b.vehicle_id).license_plate)}</b> <span class="muted mono">${esc(b.battery_serial)}</span></span><b class="num" style="color:var(--${sohTone(b.soh_percent) || "text"})">${fmt.num(b.soh_percent, 1)}%</b></div>${ui.meter((b.soh_percent - 80) * 5, sohTone(b.soh_percent))}</div>`).join("")}</div>` })}
      </div>
      ${ui.card({
        title: t("Battery models", "Dòng pin"), flush: true, cls: "mb",
        body: ui.table({
          rows: db.battery_models,
          columns: [
            { label: t("Model", "Dòng pin"), render: (m) => `<b>${esc(m.name)}</b>` },
            { label: t("Chemistry", "Hóa học"), render: (m) => ui.tag(m.chemistry) },
            { label: t("Capacity", "Dung lượng"), align: "right", render: (m) => `${fmt.num(m.capacity_kwh)} kWh` },
            { label: t("Nominal voltage", "Điện áp danh định"), align: "right", render: (m) => `${m.nominal_voltage_v} V` },
            { label: t("Layout", "Cấu hình"), render: (m) => `<span class="mono">${esc(m.layout)}</span> · ${m.cells} cell` },
            { label: t("For", "Dùng cho"), render: (m) => esc(db.vehicle_models.filter((vm) => vm.battery_capacity_kwh === m.capacity_kwh).map((vm) => vm.code).join(", ")) },
            { label: t("Packs", "Số bộ"), align: "right", render: (m) => all.filter((b) => b.battery_model_id === m.battery_model_id).length },
          ],
        }),
      })}
      <div class="mt"></div>
      ${spare.length ? `<div class="mb">${ui.callout("info", `<b>${esc(t("Spare packs", "Pin dự phòng"))}</b>: ${spare.map((b) => `<a href="#/batteries?b=${b.battery_id}">${esc(b.battery_serial)}</a> (${esc(batteryModel(b).name)}, SOH ${fmt.num(b.soh_percent, 1)}%, ${ui.orgName(b.organization_id)})`).join(" · ")}. ${esc(t("Lent to a truck while its own pack is repaired.", "Cho xe mượn trong lúc sửa pin của xe."))}`, "battery")}</div>` : ""}
      ${ui.card({
        title: t("Packs", "Bộ pin"), flush: true,
        body: filterBar([
          ui.search("q", query.q, t("Serial or plate", "Số serial hoặc biển số")),
          ui.select("status", [["", t("In service", "Đang sử dụng")], ["FITTED", t("Fitted to a truck", "Đang lắp trên xe")], ["SPARE", t("Spare / in stock", "Dự phòng / trong kho")], ["RETIRED", t("Retired", "Đã loại bỏ")]], query.status || ""),
          ui.select("model", [["", t("Every model", "Mọi dòng pin")], ...db.battery_models.map((m) => [m.battery_model_id, m.name])], query.model || ""),
          ui.select("owner", [["", t("Any owner", "Mọi chủ sở hữu")], ["differs", t("Owner differs from the truck's", "Chủ pin khác chủ xe")]], query.owner || ""),
          `<span class="grow"></span><span class="small muted">${rows.length} ${t("packs", "bộ")}</span>`,
        ]) + ui.table({
          rows,
          rowAttrs: (b) => `data-open="${b.battery_id}" data-open-key="b" style="cursor:pointer"`,
          selected: (b) => b.battery_id === query.b,
          columns: [
            { label: t("Serial", "Serial"), render: (b) => `<b class="mono">${esc(b.battery_serial)}</b><span class="sub">${t("made", "sản xuất")} ${fmt.date(b.manufactured_on)}</span>` },
            { label: t("Model", "Dòng pin"), render: (b) => esc(batteryModel(b).name) },
            { label: t("Owner", "Chủ sở hữu"), render: (b) => { const v = db.vehicle(b.vehicle_id); return ui.orgName(b.organization_id) + (v && v.organization_id !== b.organization_id ? ` ${ui.badge("LEASED", "violet", t("leased", "cho thuê"))}` : ""); } },
            { label: t("Truck", "Xe"), render: (b) => (b.vehicle_id ? `${plateLink(db.vehicle(b.vehicle_id))}<span class="sub">${ui.orgName(db.vehicle(b.vehicle_id).organization_id)} · ${t("since", "từ")} ${fmt.date(b.installed_at)}</span>` : `<span class="muted">${esc(t("Not fitted", "Chưa lắp"))}</span>`) },
            { label: "SOH", render: (b) => `<div class="row" style="flex-wrap:nowrap;gap:6px;min-width:110px">${ui.meter((b.soh_percent - 80) * 5, sohTone(b.soh_percent))}<b class="small num">${fmt.num(b.soh_percent, 1)}%</b></div>` },
            { label: t("Cycles", "Chu kỳ"), align: "right", render: (b) => fmt.num(b.cycle_count) },
            { label: t("Status", "Trạng thái"), render: (b) => statusBadge(b.status, b.status_reason) + (isLive(b) ? "" : retiredTag()) },
          ],
        }),
      })}
      ${selected && (selected.vehicle_id ? scope.vehicleIds().has(selected.vehicle_id) : scope.orgIds().includes(selected.organization_id)) ? batteryDrawer(selected, canEdit) : ""}`;
  }

  function batteryDrawer(b, canEdit) {
    const v = db.vehicle(b.vehicle_id);
    const model = batteryModel(b);
    const history = installs.filter((x) => x.battery_id === b.battery_id).sort((a, c) => (a.installed_at < c.installed_at ? 1 : -1));
    const soh = v ? db.sohSeries(v.vehicle_id, 180) : [];
    const warranty = db.warranties.find((w) => w.object_kind === "BATTERY" && w.object_id === b.battery_id);
    const body = `${ui.kv([
        [t("Model", "Dòng pin"), `${esc(model.name)} · ${esc(model.chemistry)} · ${model.nominal_voltage_v} V · <span class="mono">${esc(model.layout)}</span>`],
        [t("Owner", "Chủ sở hữu"), ui.orgName(b.organization_id) + (v && v.organization_id !== b.organization_id ? ` · ${esc(t("leased to", "cho thuê"))} ${ui.orgName(v.organization_id)}` : "")],
        [t("Fitted to", "Đang lắp"), v ? `${plateLink(v)} · ${esc(modelOf(v).code)}` : `<span class="muted">${esc(t("Not fitted", "Chưa lắp"))}</span>`],
        [t("Status", "Trạng thái"), statusBadge(b.status, b.status_reason) + (b.status_reason ? ` <span class="small muted">${esc(b.status_reason)}</span>` : "")],
        ["SOH", `<b style="color:var(--${sohTone(b.soh_percent) || "text"})">${fmt.num(b.soh_percent, 1)}%</b> · ${fmt.num((b.soh_percent / 100) * model.capacity_kwh, 0)} / ${model.capacity_kwh} kWh`],
        [t("Cycles", "Chu kỳ"), fmt.num(b.cycle_count)],
        [t("Warranty", "Bảo hành"), warranty ? `${statusBadge(warranty.status, warranty.status_reason)} ${t("until", "đến")} ${fmt.date(warranty.ends_on)}${G3.canSee("warranties") ? ` · <a href="#/warranties?q=${encodeURIComponent(b.battery_serial)}">${esc(t("open", "mở"))}</a>` : ""}` : null],
      ])}
      ${soh.length ? `<div><div class="row between small"><b>${esc(t("Capacity fade, 180 days", "Suy giảm dung lượng 180 ngày"))}</b><span class="muted">MON-07</span></div>${ui.lineChart({ labels: soh.filter((_, i) => i % 6 === 0).map((p) => p.date.slice(8, 10) + "/" + p.date.slice(5, 7)), series: [{ name: "SOH", values: soh.filter((_, i) => i % 6 === 0).map((p) => p.soh_percent), area: true, color: ui.PALETTE[0] }], height: 150, yMin: Math.floor(Math.min(...soh.map((p) => p.soh_percent)) - 1), yMax: 100, unit: "%" })}</div>` : ""}
      <div><h4 style="margin-bottom:8px">${esc(t("Install history", "Lịch sử lắp đặt"))}</h4>${history.length ? `<ul class="timeline">${history.map((x) => `<li><div>${plateLink(db.vehicle(x.vehicle_id))} <span class="small muted">${ui.orgName((db.vehicle(x.vehicle_id) || {}).organization_id)}</span></div><div class="when">${fmt.date(x.installed_at)} – ${x.removed_at ? fmt.date(x.removed_at) : t("now", "nay")}${x.reason ? ` · ${esc(txt(x.reason))}` : ""}</div></li>`).join("")}</ul>` : `<div class="small muted">${esc(t("Never fitted", "Chưa từng lắp"))}</div>`}</div>
      ${ui.callout("info", esc(t("A pack's warranty follows the pack, not the truck: moving it keeps its warranty and counters.", "Bảo hành pin đi theo bộ pin, không theo xe: chuyển pin vẫn giữ bảo hành và bộ đếm.")))}`;
    const actions = !canEdit || !isLive(b) ? "" : `${b.vehicle_id ? ui.btn(t("Remove from truck", "Tháo khỏi xe"), { action: "remove-pack", attrs: `data-id="${b.battery_id}"` }) : ui.btn(t("Fit to a truck", "Lắp lên xe"), { icon: "truck", kind: "primary", action: "fit-pack", attrs: `data-id="${b.battery_id}"` })}${ui.btn(t("Retire", "Loại bỏ"), { kind: "danger", action: "retire-pack", attrs: `data-id="${b.battery_id}"` })}`;
    return drawer(b.battery_serial, "BAT-01 · MON-07", body, actions);
  }

  G3.page({
    id: "batteries",
    path: "/batteries",
    title: ["Batteries", "Pin"],
    roles: ["OPERATIONS", "MAINTENANCE", "WARRANTY"],
    features: ["BAT-01", "MON-07"],
    nav: { group: "assets", order: 3, icon: "battery" },
    render: ({ query }) => batteriesRender(query),
    mount(root) {
      bindCommon(root);
      bindActions(root, {
        "close-drawer": () => G3.setQuery({ b: null }),
        "add-pack": () => ui.formModal({
          title: t("Add a battery pack", "Thêm bộ pin"),
          wide: true,
          fields: [
            { name: "serial", label: t("Serial", "Số serial"), required: true, placeholder: "CATL-350-2409123" },
            { name: "model", label: t("Model", "Dòng pin"), type: "select", required: true, options: db.battery_models.map((m) => [m.battery_model_id, m.name]) },
            { name: "owner", label: t("Owner", "Chủ sở hữu"), type: "select", required: true, options: ownerOptions().map((o) => [o.organization_id, o.short_name]), value: "org-g3" },
            { name: "made", label: t("Manufactured on", "Ngày sản xuất"), type: "date", required: true, value: todayIso() },
          ],
          submitLabel: t("Add pack", "Thêm bộ pin"),
          onSubmit(values) {
            const serial = values.serial.toUpperCase();
            if (!/^CATL-\d{3}-\d{7}$/.test(serial)) return t("Serial must look like CATL-350-2409123.", "Số serial phải có dạng CATL-350-2409123.");
            if (db.batteries.some((b) => isLive(b) && b.battery_serial === serial)) return t("This serial is already registered.", "Số serial này đã được đăng ký.");
            if (values.made > todayIso()) return t("The manufacturing date cannot be in the future.", "Ngày sản xuất không được ở tương lai.");
            const id = "bat-n" + (db.batteries.length + 1);
            db.batteries.push({ battery_id: id, battery_serial: serial, battery_model_id: values.model, organization_id: values.owner, vehicle_id: null, status: "INACTIVE", status_reason: t("New pack in stock", "Pin mới trong kho"), manufactured_on: values.made, installed_at: null, soh_percent: 100, cycle_count: 0 });
            G3.setQuery({ b: id, status: "SPARE" });
            ui.toast(t(`${serial} added to stock`, `Đã thêm ${serial} vào kho`));
          },
        }),
        "fit-pack": (data) => {
          const b = db.battery(data.id);
          const model = batteryModel(b);
          const trucks = scope.vehicles().filter((v) => isLive(v) && !v.battery_id && modelOf(v).battery_capacity_kwh === model.capacity_kwh);
          if (!trucks.length) { ui.toast(t(`No ${model.capacity_kwh} kWh truck is without a pack: remove one first`, `Không có xe ${model.capacity_kwh} kWh nào trống pin: hãy tháo pin trước`), "alert"); return; }
          ui.formModal({
            title: t(`Fit ${b.battery_serial}`, `Lắp ${b.battery_serial}`),
            intro: ui.callout("info", esc(t("One pack per truck; only trucks of a matching model without a pack are listed.", "Mỗi xe một bộ pin; chỉ liệt kê xe cùng dòng đang trống pin."))),
            fields: [{ name: "vehicle", label: t("Truck", "Xe"), type: "select", required: true, options: trucks.map((v) => [v.vehicle_id, `${v.license_plate} · ${modelOf(v).code} · ${db.org(v.organization_id).short_name}`]) }, { name: "date", label: t("Fitted on", "Ngày lắp"), type: "date", required: true, value: todayIso() }],
            submitLabel: t("Fit pack", "Lắp pin"),
            onSubmit(values) {
              const v = db.vehicle(values.vehicle);
              if (v.battery_id) return t(`${v.license_plate} already has a pack.`, `Xe ${v.license_plate} đã có pin.`);
              const at = new Date(values.date + "T09:00:00").toISOString();
              Object.assign(b, { vehicle_id: v.vehicle_id, installed_at: at, status: "ACTIVE", status_reason: null });
              v.battery_id = b.battery_id;
              installs.push({ battery_id: b.battery_id, vehicle_id: v.vehicle_id, installed_at: at, removed_at: null, reason: null });
              G3.render();
              ui.toast(t(`${b.battery_serial} fitted to ${v.license_plate}`, `Đã lắp ${b.battery_serial} lên xe ${v.license_plate}`));
            },
          });
        },
        "remove-pack": (data) => {
          const b = db.battery(data.id);
          const v = db.vehicle(b.vehicle_id);
          ui.reasonModal({
            title: t(`Remove ${b.battery_serial} from ${v.license_plate}`, `Tháo ${b.battery_serial} khỏi xe ${v.license_plate}`),
            intro: ui.callout("warn", esc(t("The truck cannot drive until another pack is fitted; the pack goes back to stock (Inactive).", "Xe không thể chạy cho đến khi lắp pin khác; pin về kho (Ngừng hoạt động)."))),
            submitLabel: t("Remove", "Tháo pin"),
            onSubmit(reason) {
              const open = installs.find((x) => x.battery_id === b.battery_id && !x.removed_at);
              if (open) { open.removed_at = new Date().toISOString(); open.reason = reason; }
              v.battery_id = null;
              Object.assign(b, { vehicle_id: null, status: "INACTIVE", status_reason: reason });
              G3.render();
              ui.toast(t(`${b.battery_serial} removed from ${v.license_plate}`, `Đã tháo ${b.battery_serial} khỏi xe ${v.license_plate}`));
            },
          });
        },
        "retire-pack": (data) => {
          const b = db.battery(data.id);
          ui.reasonModal({
            title: t(`Retire ${b.battery_serial}`, `Loại bỏ ${b.battery_serial}`),
            intro: ui.callout("warn", esc(t("For a pack at the end of its life or scrapped; its history and warranty file are kept.", "Dùng cho pin hết vòng đời hoặc thanh lý; lịch sử và hồ sơ bảo hành được giữ."))),
            submitLabel: t("Retire pack", "Loại bỏ pin"),
            onSubmit(reason) {
              const v = db.vehicle(b.vehicle_id);
              if (v) { v.battery_id = null; const open = installs.find((x) => x.battery_id === b.battery_id && !x.removed_at); if (open) { open.removed_at = new Date().toISOString(); open.reason = reason; } }
              Object.assign(b, { vehicle_id: null, status: "INACTIVE", status_reason: reason, deleted_at: new Date().toISOString() });
              G3.setQuery({ b: null });
              ui.toast(t(`${b.battery_serial} retired`, `Đã loại bỏ ${b.battery_serial}`));
            },
          });
        },
      });
    },
  });

  /* =====================================================================
     WARRANTIES (WAR-01, POL-05)
     ===================================================================== */
  const warrantyHistory = {};
  (function seedHistory() {
    const voided = db.warranties.find((w) => w.status === "VOIDED");
    if (voided) warrantyHistory[voided.warranty_id] = [{ at: isoAgo(9), by: db.people.warranty.user.user_id, from: "ACTIVE", to: "VOIDED", reason: voided.status_reason }];
  })();
  const KIND_ICON = { VEHICLE: "truck", BATTERY: "battery", TELEMATIC: "chip", STATION: "station" };
  /* The Long Thành chargers were commissioned first: their 2-year warranty ends soon. */
  db.charging_stations.filter((st) => st.location_id === "loc-longthanh").forEach((st, i) => {
    if (db.warranties.some((w) => w.object_id === st.station_id)) return;
    db.warranties.push({ warranty_id: `war-lt-${i + 1}`, object_kind: "STATION", object_id: st.station_id, organization_id: "org-g3", warranty_type: "STANDARD", starts_on: isoAgo(640 + i * 9).slice(0, 10), ends_on: isoAgo(640 + i * 9 - 730).slice(0, 10), distance_limit_km: null, cycle_limit: null, energy_limit_kwh: 250000, session_limit: 8000, status: "ACTIVE", status_reason: null });
  });

  /** Average km per day of a truck over the last 30 days (MON-14 daily report), cached per truck. */
  const kmPerDayCache = {};
  function kmPerDay(v) {
    if (!(v.vehicle_id in kmPerDayCache)) { const rows = db.dailyReport(v.vehicle_id, 30); kmPerDayCache[v.vehicle_id] = rows.reduce((a, r) => a + r.distance_km, 0) / rows.length; }
    return kmPerDayCache[v.vehicle_id];
  }

  /** The covered object, where it is now, and its counters against each limit: [label, used, limit, unit, used per day]. */
  function warrantyView(w) {
    const view = { label: w.object_id, sub: "", where: null, usages: [], visible: true };
    const ids = scope.vehicleIds();
    if (w.object_kind === "VEHICLE") {
      const v = db.vehicle(w.object_id);
      view.label = v ? v.license_plate : w.object_id; view.sub = v ? v.vin : ""; view.where = v; view.visible = !!v && ids.has(v.vehicle_id);
      if (w.distance_limit_km && v) view.usages.push([t("Distance", "Quãng đường"), v.odometer_km, w.distance_limit_km, "km", kmPerDay(v)]);
    } else if (w.object_kind === "BATTERY") {
      const b = db.battery(w.object_id);
      const v = b && db.vehicle(b.vehicle_id);
      view.label = b ? b.battery_serial : w.object_id; view.sub = v ? `${t("on", "trên xe")} ${v.license_plate}` : t("not fitted", "chưa lắp"); view.where = v; view.visible = b ? (v ? ids.has(v.vehicle_id) : scope.orgIds().includes(b.organization_id)) : false;
      if (w.cycle_limit && b) {
        // One full cycle ≈ the distance 90% of the pack covers at the model's average consumption.
        const m = v && modelOf(v);
        const kmPerCycle = m && m.battery_capacity_kwh ? (0.9 * m.battery_capacity_kwh) / ((m.consumption_empty_kwh_km + m.consumption_loaded_kwh_km) / 2) : null;
        view.usages.push([t("Cycles", "Chu kỳ"), b.cycle_count, w.cycle_limit, "", kmPerCycle ? kmPerDay(v) / kmPerCycle : 0]);
      }
      if (b) view.soh = b.soh_percent;
    } else if (w.object_kind === "TELEMATIC") {
      const d = db.telematic(w.object_id);
      const v = d && db.vehicle(d.vehicle_id);
      view.label = d ? d.telematic_serial : w.object_id; view.sub = v ? `${t("on", "trên xe")} ${v.license_plate}` : t("not mounted", "chưa lắp"); view.where = v; view.visible = d ? (v ? ids.has(v.vehicle_id) : scope.orgIds().includes(d.organization_id)) : false;
    } else if (w.object_kind === "STATION") {
      const s = db.station(w.object_id);
      view.label = s ? s.ocpp_identity : w.object_id; view.sub = s ? (db.location(s.location_id) || {}).name : ""; view.visible = scope.orgIds().includes(w.organization_id);
      const days = Math.max(30, (Date.now() - new Date(w.starts_on).getTime()) / DAY);
      if (s && w.energy_limit_kwh) view.usages.push([t("Energy delivered", "Điện đã cấp"), s.energy_delivered_kwh_total, w.energy_limit_kwh, "kWh", s.energy_delivered_kwh_total / days]);
      if (s && w.session_limit) view.usages.push([t("Sessions", "Phiên sạc"), s.sessions_total, w.session_limit, "", s.sessions_total / days]);
    }
    const start = new Date(w.starts_on).getTime(), end = new Date(w.ends_on).getTime();
    const elapsedShare = Math.max(0, Math.min(1, (Date.now() - start) / (end - start)));
    view.timeShare = elapsedShare;
    // The date a limit is reached at the current pace, when it comes before the end date.
    let projected = end;
    view.usages.forEach(([, used, limit, , perDay]) => { if (perDay > 0) { const at = Date.now() + Math.max(0, (limit - used) / perDay) * DAY; if (at < projected) projected = at; } });
    view.effectiveEnd = projected;
    view.byLimit = projected < end;
    return view;
  }

  function warrantyRows(query) {
    const q = (query.q || "").toLowerCase();
    return scope.filter(db.warranties).map((w) => ({ w, view: warrantyView(w) })).filter(({ w, view }) => {
      if (!view.visible) return false;
      if (query.kind && w.object_kind !== query.kind) return false;
      if (query.status === "EXPIRING") { if (w.status !== "ACTIVE" || daysUntil(view.effectiveEnd) > 180) return false; }
      else if (query.status && w.status !== query.status) return false;
      if (q && !(view.label.toLowerCase().includes(q) || String(view.sub).toLowerCase().includes(q))) return false;
      return true;
    }).sort((a, b) => (a.w.status !== b.w.status ? (a.w.status === "ACTIVE" ? -1 : 1) : a.view.effectiveEnd - b.view.effectiveEnd));
  }

  function usageCell(view) {
    const parts = view.usages.map(([label, used, limit, unit]) => {
      const share = (used / limit) * 100;
      return `<div style="min-width:210px"><div class="row between small" style="flex-wrap:nowrap"><span class="muted">${esc(label)}</span><span class="num nowrap">${fmt.num(used)} / ${fmt.num(limit)}${unit ? " " + unit : ""}</span></div>${ui.meter(share, share > 85 ? "danger" : share > 60 ? "warn" : "")}</div>`;
    });
    parts.push(`<div style="min-width:210px"><div class="row between small" style="flex-wrap:nowrap"><span class="muted">${esc(t("Time", "Thời gian"))}</span><span class="num">${fmt.pct(view.timeShare * 100)}</span></div>${ui.meter(view.timeShare * 100, "")}</div>`);
    return `<div class="stack" style="gap:6px">${parts.join("")}</div>`;
  }

  function registerTab(query) {
    const rows = warrantyRows(query);
    const all = scope.filter(db.warranties).filter((w) => warrantyView(w).visible);
    const active = all.filter((w) => w.status === "ACTIVE");
    const soon = active.filter((w) => daysUntil(warrantyView(w).effectiveEnd) <= 180);
    const canEdit = scope.has("WARRANTY", "OPERATIONS", "HEAD_ADMIN") && scope.isInternal();
    return `<div class="grid cols-4 mb">
        ${ui.kpi({ label: t("Active warranties", "Bảo hành hiệu lực"), icon: "shield", value: active.length, tone: "ok", foot: Object.keys(KIND_ICON).map((k) => `${active.filter((w) => w.object_kind === k).length} ${G3.label(k).toLowerCase()}`).join(" · ") })}
        ${ui.kpi({ label: t("Ending within 6 months", "Hết hạn trong 6 tháng"), icon: "clock", value: soon.length, tone: soon.length ? "warn" : undefined, foot: t("by date or by limit", "theo ngày hoặc theo giới hạn") })}
        ${ui.kpi({ label: t("Voided", "Đã hủy"), icon: "x", value: all.filter((w) => w.status === "VOIDED").length, tone: all.some((w) => w.status === "VOIDED") ? "danger" : undefined })}
        ${ui.kpi({ label: t("Closest to a limit", "Gần giới hạn nhất"), icon: "gauge", value: (() => { const max = Math.max(0, ...active.flatMap((w) => warrantyView(w).usages.map(([, u, l]) => (u / l) * 100))); return fmt.pct(max); })(), foot: t("of a distance, cycle or energy limit", "của giới hạn km, chu kỳ hoặc điện năng") })}
      </div>
      ${ui.card({
        flush: true,
        body: filterBar([
          ui.search("q", query.q, t("Plate, serial or charger", "Biển số, serial hoặc trụ")),
          ui.segmented([["", t("Every object", "Mọi đối tượng")], ...Object.keys(KIND_ICON).filter((k) => k !== "STATION" || scope.isInternal()).map((k) => [k, G3.label(k)])], query.kind || "", "kind"),
          ui.select("status", [["", t("Every status", "Mọi trạng thái")], ["ACTIVE", G3.label("ACTIVE")], ["EXPIRING", t("Ending within 6 months", "Hết hạn trong 6 tháng")], ["VOIDED", G3.label("VOIDED")], ["EXPIRED", G3.label("EXPIRED")]], query.status || ""),
          `<span class="grow"></span><span class="small muted">${rows.length} ${t("warranties", "bảo hành")} · ${t("soonest end first", "hết hạn sớm nhất trước")}</span>`,
        ]) + ui.table({
          rows,
          empty: t("No warranty matches", "Không có bảo hành phù hợp"),
          columns: [
            { label: t("Covered object", "Đối tượng"), render: ({ w, view }) => `<div class="row" style="flex-wrap:nowrap;gap:10px"><span class="sev-icon INFO" style="background:var(--surface-3);color:var(--text-2)">${ui.icon(KIND_ICON[w.object_kind], 15)}</span><div><b>${esc(view.label)}</b><span class="sub">${esc(G3.label(w.object_kind))} · ${esc(view.sub || "")}</span></div></div>` },
            { label: t("Owner", "Chủ sở hữu"), cls: "nowrap", render: ({ w }) => ui.orgName(w.organization_id) },
            { label: t("Type", "Loại"), render: ({ w }) => ui.tag(G3.label(w.warranty_type)) },
            { label: t("Period", "Thời hạn"), cls: "nowrap", render: ({ w }) => `${fmt.date(w.starts_on)}<span class="sub">→ ${fmt.date(w.ends_on)}</span>` },
            { label: t("Usage against limits", "Sử dụng so với giới hạn"), render: ({ view }) => usageCell(view) },
            { label: t("Ends", "Kết thúc"), render: ({ w, view }) => (w.status !== "ACTIVE" ? '<span class="muted">—</span>' : `<b>${fmt.date(view.effectiveEnd)}</b><span class="sub" style="${daysUntil(view.effectiveEnd) <= 180 ? "color:var(--warn)" : ""}">${fmt.ago(new Date(view.effectiveEnd).toISOString())}${view.byLimit ? ` · ${t("by limit at this pace", "theo giới hạn ở nhịp hiện tại")}` : ""}</span>`) },
            { label: t("Status", "Trạng thái"), render: ({ w }) => statusBadge(w.status, w.status_reason) + (view_soh(w)) },
            canEdit ? { label: "", align: "right", render: ({ w }) => `${warrantyHistory[w.warranty_id] ? ui.btn("", { sm: true, kind: "ghost", icon: "clock", action: "warranty-history", attrs: `data-id="${w.warranty_id}" title="${esc(t("History", "Lịch sử"))}"` }) : ""}${w.status === "ACTIVE" ? ui.btn(t("Void", "Hủy"), { sm: true, kind: "ghost", action: "void-warranty", attrs: `data-id="${w.warranty_id}"` }) : ""}` } : null,
          ].filter(Boolean),
        }),
      })}
      <div class="mt small muted">${esc(t("Usage comes from the object's own counters: odometer for a truck, charge cycles for a pack, energy and sessions for a charger. A pack's warranty follows the pack when it moves to another truck.", "Mức sử dụng lấy từ bộ đếm của đối tượng: số km của xe, số chu kỳ sạc của pin, điện năng và số phiên của trụ. Bảo hành pin đi theo bộ pin khi chuyển sang xe khác."))}</div>`;
  }
  /** A pack's warranty also asks for SOH ≥ 70% at the end; show it under the status. */
  function view_soh(w) {
    if (w.object_kind !== "BATTERY") return "";
    const b = db.battery(w.object_id);
    return b ? `<span class="sub">SOH ${fmt.num(b.soh_percent, 1)}% · ${t("guaranteed ≥ 70%", "cam kết ≥ 70%")}</span>` : "";
  }

  function riskTab(query) {
    const rows = scope.byVehicle(db.compliance).filter((c) => !query.risk || c.risk === query.risk).sort((a, b) => a.score - b.score);
    const all = scope.byVehicle(db.compliance);
    const avg = all.reduce((a, c) => a + c.score, 0) / (all.length || 1);
    const batteryWarranty = (vehicleId) => { const v = db.vehicle(vehicleId); return db.warranties.find((w) => w.object_kind === "BATTERY" && w.object_id === (v || {}).battery_id); };
    return `${ui.featureChips(["POL-05", "POL-04"])}
      <div class="grid cols-4 mb mt">
        ${ui.kpi({ label: t("Average compliance", "Tuân thủ trung bình"), icon: "shield", value: fmt.num(avg, 0), unit: "/100" })}
        ${ui.kpi({ label: t("High-risk trucks", "Xe rủi ro cao"), icon: "alert", value: all.filter((c) => c.risk === "HIGH").length, tone: "danger" })}
        ${ui.kpi({ label: t("Violations (30 d)", "Vi phạm (30 ngày)"), icon: "list", value: all.reduce((a, c) => a + c.violations_30d, 0) })}
        ${ui.kpi({ label: t("Battery warranties voided", "Bảo hành pin bị hủy"), icon: "x", value: all.filter((c) => (batteryWarranty(c.vehicle_id) || {}).status === "VOIDED").length })}
      </div>
      ${ui.card({
        flush: true,
        body: filterBar([ui.segmented([["", t("All", "Tất cả")], ["HIGH", G3.label("HIGH")], ["MEDIUM", G3.label("MEDIUM")], ["LOW", G3.label("LOW")]], query.risk || "", "risk"), '<span class="grow"></span>', G3.canSee("policy") ? ui.btn(t("Charging policies", "Chính sách sạc"), { sm: true, kind: "ghost", href: "#/policy" }) : "", ui.btn(t("Export for the warranty team", "Xuất cho bộ phận bảo hành"), { sm: true, icon: "download", action: "export-risk" })]) + ui.table({
          rows,
          href: G3.canSee("vehicle-detail") ? (c) => `/vehicles/${c.vehicle_id}` : null,
          columns: [
            { label: t("Truck", "Xe"), render: (c) => ui.vehicleCell(db.vehicle(c.vehicle_id)) },
            { label: t("Owner", "Chủ xe"), render: (c) => ui.orgName(c.organization_id) },
            { label: t("Compliance", "Tuân thủ"), render: (c) => `<div class="row" style="flex-wrap:nowrap;gap:6px;min-width:120px">${ui.meter(c.score, c.score < 70 ? "danger" : c.score < 85 ? "warn" : "")}<b class="small num">${c.score}</b></div>` },
            { label: t("Trend (8 weeks)", "Xu hướng (8 tuần)"), render: (c) => ui.sparkline(c.trend, c.risk === "HIGH" ? "var(--danger)" : "var(--primary)") },
            { label: t("Violations 30 d", "Vi phạm 30 ngày"), align: "right", render: (c) => c.violations_30d },
            { label: t("Risk", "Rủi ro"), render: (c) => ui.badge(c.risk) },
            { label: t("Battery warranty", "Bảo hành pin"), render: (c) => { const w = batteryWarranty(c.vehicle_id); return w ? statusBadge(w.status, w.status_reason) : "—"; } },
          ],
        }),
      })}`;
  }

  function registerWarranty(kind) {
    const ids = scope.vehicleIds();
    const objects = kind === "VEHICLE" ? scope.vehicles().filter(isLive).map((v) => [v.vehicle_id, `${v.license_plate} · ${v.vin}`, v.organization_id])
      : kind === "BATTERY" ? visibleBatteries().filter(isLive).map((b) => [b.battery_id, `${b.battery_serial}${b.vehicle_id ? " · " + db.vehicle(b.vehicle_id).license_plate : ""}`, b.vehicle_id ? db.vehicle(b.vehicle_id).organization_id : b.organization_id])
      : kind === "TELEMATIC" ? visibleDevices().filter(isLive).map((d) => [d.telematic_id, `${d.telematic_serial}${d.vehicle_id && ids.has(d.vehicle_id) ? " · " + db.vehicle(d.vehicle_id).license_plate : ""}`, d.vehicle_id ? db.vehicle(d.vehicle_id).organization_id : d.organization_id])
      : db.charging_stations.map((s) => [s.station_id, `${s.ocpp_identity} · ${(db.location(s.location_id) || {}).name}`, "org-g3"]);
    const limitFields = { VEHICLE: [{ name: "km", label: t("Distance limit (km)", "Giới hạn quãng đường (km)"), type: "number", value: 300000 }], BATTERY: [{ name: "cycles", label: t("Cycle limit", "Giới hạn chu kỳ"), type: "number", value: 4000 }], TELEMATIC: [], STATION: [{ name: "kwh", label: t("Energy limit (kWh)", "Giới hạn điện năng (kWh)"), type: "number", value: 400000 }, { name: "sessions", label: t("Session limit", "Giới hạn số phiên"), type: "number", value: 12000 }] }[kind];
    const years = { VEHICLE: 5, BATTERY: 8, TELEMATIC: 2, STATION: 3 }[kind];
    const end = new Date(); end.setFullYear(end.getFullYear() + years);
    ui.formModal({
      title: t(`Register a ${G3.label(kind).toLowerCase()} warranty`, `Đăng ký bảo hành ${G3.label(kind).toLowerCase()}`),
      wide: true,
      fields: [
        { name: "object", label: G3.label(kind), type: "select", required: true, options: objects.map(([id, label]) => [id, label]), full: true },
        { name: "type", label: t("Type", "Loại"), type: "select", required: true, options: [["STANDARD", G3.label("STANDARD")], ["EXTENDED", G3.label("EXTENDED")]] },
        { name: "starts", label: t("Starts on", "Bắt đầu"), type: "date", required: true, value: todayIso() },
        { name: "ends", label: t("Ends on", "Kết thúc"), type: "date", required: true, value: end.toISOString().slice(0, 10) },
        ...limitFields,
      ],
      submitLabel: t("Register", "Đăng ký"),
      onSubmit(values) {
        if (values.ends <= values.starts) return t("The end date must be after the start date.", "Ngày kết thúc phải sau ngày bắt đầu.");
        const overlap = db.warranties.find((w) => w.object_id === values.object && w.warranty_type === values.type && w.status === "ACTIVE" && w.starts_on <= values.ends && w.ends_on >= values.starts);
        if (overlap) return t(`An active ${G3.label(values.type).toLowerCase()} warranty already covers this object from ${fmt.date(overlap.starts_on)} to ${fmt.date(overlap.ends_on)}.`, `Đối tượng đã có bảo hành ${G3.label(values.type).toLowerCase()} hiệu lực từ ${fmt.date(overlap.starts_on)} đến ${fmt.date(overlap.ends_on)}.`);
        for (const f of limitFields) if (values[f.name] !== "" && !(Number(values[f.name]) > 0)) return t(`"${f.label}" must be a positive number.`, `"${f.label}" phải là số dương.`);
        const owner = objects.find(([id]) => id === values.object)[2];
        db.warranties.push({ warranty_id: db.uid("war"), object_kind: kind, object_id: values.object, organization_id: owner, warranty_type: values.type, starts_on: values.starts, ends_on: values.ends, distance_limit_km: values.km ? Number(values.km) : null, cycle_limit: values.cycles ? Number(values.cycles) : null, energy_limit_kwh: values.kwh ? Number(values.kwh) : null, session_limit: values.sessions ? Number(values.sessions) : null, status: "ACTIVE", status_reason: null });
        G3.render();
        ui.toast(t("Warranty registered", "Đã đăng ký bảo hành"));
      },
    });
  }

  G3.page({
    id: "warranties",
    path: "/warranties",
    title: ["Warranties", "Bảo hành"],
    roles: ["WARRANTY", "OPERATIONS", "FLEET_MANAGER", "ORG_ADMIN"],
    features: ["WAR-01", "POL-05"],
    nav: { group: "assets", order: 4, icon: "shield" },
    render({ query }) {
      const tabs = [["register", t("Register", "Sổ bảo hành")], ["risk", t("Warranty risk", "Rủi ro bảo hành")]];
      const tab = query.tab === "risk" ? "risk" : "register";
      const canEdit = scope.has("WARRANTY", "OPERATIONS", "HEAD_ADMIN") && scope.isInternal();
      return `${ui.pageHead({
        title: t("Warranties", "Bảo hành"),
        sub: esc(scope.isInternal() ? t("Warranties of trucks, packs, devices and chargers: period, limits, usage and status.", "Bảo hành của xe, pin, thiết bị và trụ sạc: thời hạn, giới hạn, mức sử dụng và trạng thái.") : t(`Warranties of ${scope.org().name}'s trucks, packs and devices.`, `Bảo hành xe, pin và thiết bị của ${scope.org().name}.`)),
        features: ["WAR-01", "POL-05"],
        actions: tab === "register" && canEdit ? ui.btn(t("Register a warranty", "Đăng ký bảo hành"), { icon: "plus", kind: "primary", action: "register-warranty" }) : "",
      })}${ui.tabs(tabs, tab)}${tab === "risk" ? riskTab(query) : registerTab(query)}`;
    },
    mount(root, { query }) {
      bindCommon(root);
      bindActions(root, {
        "register-warranty": (_, el) => ui.popover(el, [{ title: t("Covered object", "Đối tượng bảo hành") }, ...Object.keys(KIND_ICON).map((k) => ({ label: G3.label(k), icon: KIND_ICON[k], onClick: () => registerWarranty(k) }))], { align: "right" }),
        "void-warranty": (data) => {
          const w = db.find("warranties", data.id);
          const view = warrantyView(w);
          ui.reasonModal({
            title: t(`Void the warranty of ${view.label}`, `Hủy bảo hành của ${view.label}`),
            intro: ui.callout("warn", esc(t("A voided warranty no longer covers claims. The reason is shown to the owner and kept in the change history.", "Bảo hành bị hủy không còn chi trả yêu cầu. Lý do hiển thị cho chủ sở hữu và lưu trong lịch sử thay đổi."))),
            submitLabel: t("Void warranty", "Hủy bảo hành"),
            onSubmit(reason) {
              (warrantyHistory[w.warranty_id] = warrantyHistory[w.warranty_id] || []).unshift({ at: new Date().toISOString(), by: me(), from: w.status, to: "VOIDED", reason });
              w.status = "VOIDED"; w.status_reason = reason;
              G3.render();
              ui.toast(t(`Warranty of ${view.label} voided`, `Đã hủy bảo hành của ${view.label}`));
            },
          });
        },
        "warranty-history": (data) => {
          const w = db.find("warranties", data.id);
          ui.modal({
            title: t(`History · ${warrantyView(w).label}`, `Lịch sử · ${warrantyView(w).label}`),
            body: `<ul class="timeline">${warrantyHistory[w.warranty_id].map((h) => `<li><div>${ui.badge(h.from)} → ${ui.badge(h.to)}</div><div class="small">${esc(h.reason)}</div><div class="when">${fmt.dateTime(h.at)} · ${ui.userName(h.by)}</div></li>`).join("")}<li><div>${ui.badge("ACTIVE")} ${esc(t("registered", "đăng ký"))}</div><div class="when">${fmt.date(w.starts_on)}</div></li></ul>`,
          });
        },
        "export-risk": () => ui.downloadCsv("warranty-risk.csv", [
          { label: "license_plate", csv: (c) => db.vehicle(c.vehicle_id).license_plate }, { label: "vin", csv: (c) => db.vehicle(c.vehicle_id).vin },
          { label: "owner", csv: (c) => db.org(c.organization_id).short_name }, { label: "score", key: "score" }, { label: "violations_30d", key: "violations_30d" }, { label: "risk", key: "risk" },
        ], scope.byVehicle(db.compliance).filter((c) => !query.risk || c.risk === query.risk)),
      });
    },
  });

  /* =====================================================================
     MAINTENANCE (MNT-01, MNT-03, MNT-04, MNT-05, MNT-06, MNT-07)
     ===================================================================== */
  const PLAN_ITEMS = [["Brake system check", "Kiểm tra hệ thống phanh"], ["HV insulation test", "Đo cách điện cao áp"], ["Battery & motor coolant", "Nước làm mát pin & động cơ"], ["Tyres & alignment", "Lốp & cân chỉnh"], ["Cabin air filter", "Lọc gió cabin"], ["Gear oil (every 2nd service)", "Dầu hộp số (mỗi 2 lần)"]];
  const plans = {
    "vm-262": { interval_km: 20000, interval_months: 6, labour_hours: 4, cost_vnd: 4800000 },
    "vm-400": { interval_km: 25000, interval_months: 6, labour_hours: 5, cost_vnd: 6200000 },
    "vm-825": { interval_km: 30000, interval_months: 6, labour_hours: 6, cost_vnd: 7900000 },
  };
  const WORKSHOPS = { "org-p2": "Xưởng dịch vụ xe điện Cái Mép", g3: "G3 Service Nhơn Trạch", "org-p1": "Garage Cứu hộ 24/7 Biên Hòa" };
  /* Last periodic service per truck (seeded), plus the ones the user records. */
  const services = [];
  db.vehicles.forEach((v) => {
    const plan = plans[v.vehicle_model_id];
    const r = rng(v.vehicle_id + "svc");
    const kmSince = Math.round(Math.min(r() * plan.interval_km * 1.1, v.odometer_km * 0.7));
    const daysSince = Math.round(10 + r() * 185);
    if (v.odometer_km < 1000) return;
    services.push({ service_id: "svc-" + v.vehicle_id, vehicle_id: v.vehicle_id, kind: "PERIODIC", at: isoAgo(daysSince), odometer_km: Math.max(0, Math.round(v.odometer_km - kmSince)), workshop: r() < 0.5 ? "g3" : "org-p2", cost_vnd: plan.cost_vnd, note: null });
  });

  const STAGES = [["FAULT", "Fault", "Báo lỗi"], ["DIAGNOSIS", "Diagnosis", "Chẩn đoán"], ["DRIVABLE", "Drivable?", "Chạy được?"], ["WORKSHOP", "Workshop", "Vào xưởng"], ["REPAIR", "Repair", "Sửa chữa"], ["TEST", "Test", "Chạy thử"], ["HANDOVER", "Handover", "Bàn giao"], ["CLOSED", "Closed", "Đóng"]];
  const stageIndex = (s) => STAGES.findIndex(([k]) => k === s);
  const stageLabel = (i) => t(STAGES[i][1], STAGES[i][2]);
  const CLAIM_BADGE = { DRAFT: ["muted", ["Draft", "Nháp"]], SUBMITTED: ["info", ["Submitted", "Đã gửi"]], APPROVED: ["ok", ["Approved", "Đã duyệt"]], REJECTED: ["danger", ["Rejected", "Từ chối"]] };
  const claimBadge = (s) => ui.badge(s, CLAIM_BADGE[s][0], t(CLAIM_BADGE[s][1][0], CLAIM_BADGE[s][1][1]));
  const KIND_BADGE = { UNPLANNED: ["warn", ["Unplanned", "Đột xuất"]], OVERHAUL: ["violet", ["Overhaul", "Đại tu"]], PERIODIC: ["info", ["Periodic", "Định kỳ"]] };
  const kindBadge = (k) => ui.badge(k, KIND_BADGE[k][0], t(KIND_BADGE[k][1][0], KIND_BADGE[k][1][1]));

  /* Repair cases (MNT-03), tied to the trucks the seed alerts and tickets describe. */
  const repairCases = [
    { case_id: "RC-0412", vehicle_id: "veh-010", kind: "UNPLANNED", opened_at: isoAgo(2), source: ["Component fault alert C1A2F", "Cảnh báo lỗi linh kiện C1A2F"], fault: ["Low tyre pressure, rear left, keeps coming back", "Áp suất lốp sau trái thấp, lặp lại nhiều lần"], stage: "DIAGNOSIS", drivable: true, workshop: "org-p2", parts: [], labour_hours: 0.5, component: "TYRE", claim: null, log: [] },
    { case_id: "RC-0409", vehicle_id: "veh-013", kind: "UNPLANNED", opened_at: isoAgo(1), source: ["Battery safety alert 58 °C", "Cảnh báo an toàn pin 58 °C"], fault: ["High battery temperature while parked", "Nhiệt độ pin cao khi đang đỗ"], stage: "DRIVABLE", drivable: null, workshop: "g3", parts: [], labour_hours: 1.5, component: "COOLING", claim: { claim_no: "WC-2026-118", status: "DRAFT", files: ["bms-log-0409.csv", "thermal-photo-1.jpg"] }, log: [] },
    { case_id: "RC-0405", vehicle_id: "veh-011", kind: "OVERHAUL", opened_at: isoAgo(12), source: ["Planned overhaul", "Đại tu theo kế hoạch"], fault: ["60,000 km overhaul and drive motor bearing noise", "Đại tu 60.000 km và tiếng ồn ổ bi động cơ"], stage: "REPAIR", drivable: false, workshop: "org-p2", parts: [["Drive motor bearing set", "Bộ ổ bi động cơ", 1, 18500000], ["Coolant (20 L)", "Nước làm mát (20 L)", 2, 1450000], ["Brake pads, axle set", "Má phanh, bộ cầu", 3, 4200000], ["Cabin air filter", "Lọc gió cabin", 1, 650000]], labour_hours: 26, covers_periodic: true, component: "MOTOR_BEARING", claim: null, log: [] },
    { case_id: "RC-0401", vehicle_id: "veh-024", kind: "UNPLANNED", opened_at: isoAgo(3), source: ["Support ticket (hotline)", "Yêu cầu hỗ trợ (tổng đài)"], fault: ["Will not power on after charging, U0100", "Không lên nguồn sau khi sạc, mã U0100"], stage: "WORKSHOP", drivable: false, workshop: "org-p1", parts: [["DC-DC converter 24 V", "Bộ chuyển đổi DC-DC 24 V", 1, 46800000], ["Towing 38 km", "Kéo xe 38 km", 1, 5600000]], labour_hours: 6, component: "DCDC", claim: { claim_no: "WC-2026-115", status: "SUBMITTED", files: ["dtc-freeze-frame.json", "tow-receipt.pdf", "photo-connector.jpg"] }, log: [] },
    { case_id: "RC-0398", vehicle_id: "veh-022", kind: "UNPLANNED", opened_at: isoAgo(21), source: ["Battery health alert 87.6%", "Cảnh báo sức khỏe pin 87,6%"], fault: ["Weak cells 17, 18 and 143", "Cell yếu số 17, 18 và 143"], stage: "TEST", drivable: true, workshop: "g3", parts: [["Cell module 2P12S", "Mô-đun cell 2P12S", 3, 31500000]], labour_hours: 14, component: "BATTERY", claim: { claim_no: "WC-2026-102", status: "REJECTED", files: ["cell-voltage-report.pdf", "charging-history.csv"], note: ["Battery warranty voided: repeated DC charging outside allowed stations (clause 7.2). Cost borne by the owner.", "Bảo hành pin đã bị hủy: sạc DC ngoài trạm được phép nhiều lần (điều 7.2). Chủ xe chịu chi phí."] }, log: [] },
    { case_id: "RC-0388", vehicle_id: "veh-020", kind: "UNPLANNED", opened_at: isoAgo(2.1), source: ["SOS (accident)", "SOS (tai nạn)"], fault: ["Minor collision at Dầu Giây junction, front bumper", "Va chạm nhẹ tại nút giao Dầu Giây, cản trước"], stage: "HANDOVER", drivable: true, workshop: "org-p1", parts: [["Front bumper", "Cản trước", 1, 12400000], ["Headlamp right", "Đèn pha phải", 1, 6900000]], labour_hours: 8, component: "BODY", claim: null, log: [] },
    { case_id: "RC-0371", vehicle_id: "veh-001", kind: "UNPLANNED", opened_at: isoAgo(5), source: ["Support ticket (app)", "Yêu cầu hỗ trợ (ứng dụng)"], fault: ["Air conditioning not cooling", "Điều hòa không mát"], stage: "CLOSED", drivable: true, workshop: "g3", parts: [["Electric A/C compressor", "Máy nén điều hòa điện", 1, 22800000]], labour_hours: 3, component: "AC_COMPRESSOR", claim: { claim_no: "WC-2026-097", status: "APPROVED", files: ["ac-pressure-test.pdf"] }, log: [] },
  ].filter((c) => db.vehicle(c.vehicle_id));
  const LABOUR_RATE = 450000;
  repairCases.forEach((c) => {
    // Stage log from the opening time to the current stage.
    const idx = stageIndex(c.stage);
    const span = Math.max(0.2, (NOW - new Date(c.opened_at).getTime()) / DAY);
    c.log = STAGES.slice(0, idx + 1).map(([key], i) => ({ stage: key, at: new Date(new Date(c.opened_at).getTime() + (span * DAY * i) / (idx + 1)).toISOString(), by: i % 2 ? db.people.maintenance.user.user_id : null }));
  });
  const caseCost = (c) => c.parts.reduce((a, p) => a + p[2] * p[3], 0) + c.labour_hours * LABOUR_RATE;
  const caseVisible = (c) => scope.vehicleIds().has(c.vehicle_id);

  /** Reminder state of one truck against its model's plan (MNT-01, MNT-04). */
  function reminderOf(v) {
    const plan = plans[v.vehicle_model_id];
    const last = services.filter((s) => s.vehicle_id === v.vehicle_id).sort((a, b) => (a.at < b.at ? 1 : -1))[0];
    const covering = repairCases.find((c) => c.vehicle_id === v.vehicle_id && c.covers_periodic);
    if (!last) return { v, plan, status: "NEW", last: null, kmLeft: plan.interval_km - v.odometer_km, daysLeft: null };
    const due = new Date(last.at); due.setMonth(due.getMonth() + plan.interval_months);
    const kmLeft = Math.round(last.odometer_km + plan.interval_km - v.odometer_km);
    const daysLeft = daysUntil(due);
    let status = "OK";
    if (Date.now() - new Date(last.at).getTime() < 14 * DAY) status = "DONE";
    else if (kmLeft < 0 || daysLeft < 0) status = "OVERDUE";
    else if (kmLeft <= 1000 || daysLeft <= 7) status = "DUE";
    else if (kmLeft <= 3000 || daysLeft <= 30) status = "DUE_SOON";
    // A recorded overhaul that included the periodic items covers the reminder: no double service.
    if (covering && status !== "DONE" && status !== "OK") return { v, plan, status: "COVERED", last, kmLeft, daysLeft, due, covering };
    return { v, plan, status, last, kmLeft, daysLeft, due };
  }
  const REMINDER_BADGE = { OVERDUE: ["danger", ["Overdue", "Quá hạn"]], DUE: ["warn", ["Due", "Đến hạn"]], DUE_SOON: ["info", ["Due soon", "Sắp đến hạn"]], DONE: ["ok", ["Done", "Đã làm"]], COVERED: ["ok", ["Covered by a repair", "Đã làm trong lần sửa"]], OK: ["muted", ["Not due", "Chưa đến hạn"]], NEW: ["muted", ["First service ahead", "Chờ lần đầu"]] };
  const reminderBadge = (s) => ui.badge(s, REMINDER_BADGE[s][0], t(REMINDER_BADGE[s][1][0], REMINDER_BADGE[s][1][1]));

  function maintenanceTrucks(query) {
    return scope.vehicles().filter(isLive).filter((v) => !query.fleet || db.fleetAndDescendants(query.fleet).includes(v.fleet_id));
  }
  function fleetFilter(query) {
    return ui.select("fleet", [["", t("Every fleet", "Mọi đội xe")], ...scopedFleets().map((f) => [f.fleet_id, (f.parent_fleet_id ? "— " : "") + f.name])], query.fleet || "");
  }

  function remindersTab(query) {
    const all = maintenanceTrucks(query).map(reminderOf);
    const order = ["OVERDUE", "DUE", "DUE_SOON", "COVERED", "DONE", "NEW", "OK"];
    const rows = all.filter((r) => !query.due || r.status === query.due).sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status) || (a.kmLeft - b.kmLeft));
    const count = (s) => all.filter((r) => r.status === s).length;
    const canPlan = scope.has("MAINTENANCE", "HEAD_ADMIN") && scope.isInternal();
    return `${ui.featureChips(["MNT-01", "MNT-04"])}
      <div class="grid cols-4 mb mt">
        ${ui.kpi({ label: t("Overdue", "Quá hạn"), icon: "alert", value: count("OVERDUE"), tone: count("OVERDUE") ? "danger" : undefined })}
        ${ui.kpi({ label: t("Due now", "Đến hạn"), icon: "wrench", value: count("DUE"), tone: count("DUE") ? "warn" : undefined, foot: t("≤ 1,000 km or 7 days", "≤ 1.000 km hoặc 7 ngày") })}
        ${ui.kpi({ label: t("Due soon", "Sắp đến hạn"), icon: "clock", value: count("DUE_SOON"), foot: t("≤ 3,000 km or 30 days", "≤ 3.000 km hoặc 30 ngày") })}
        ${ui.kpi({ label: t("Done (14 d)", "Đã làm (14 ngày)"), icon: "check", value: count("DONE") + count("COVERED"), tone: "ok", foot: `${count("COVERED")} ${t("covered by a repair", "làm trong lần sửa")}` })}
      </div>
      <div class="grid cols-4 mb">
        ${db.vehicle_models.map((m) => { const p = plans[m.vehicle_model_id]; return `<div class="card"><div class="card-body"><div class="row between"><b>${esc(m.code)}</b>${canPlan ? ui.btn("", { sm: true, kind: "ghost", icon: "edit", action: "edit-plan", attrs: `data-id="${m.vehicle_model_id}" title="${esc(t("Edit plan", "Sửa kế hoạch"))}"` }) : ""}</div><div style="font-size:18px;font-weight:650" class="num">${fmt.num(p.interval_km)} km <span class="muted" style="font-size:13px;font-weight:500">${t("or", "hoặc")} ${p.interval_months} ${t("months", "tháng")}</span></div><div class="small muted">${p.labour_hours} ${t("h labour", "giờ công")} · ${fmt.vndShort(p.cost_vnd)} ${t("per service", "mỗi lần")}</div></div></div>`; }).join("")}
        <div class="card"><div class="card-body"><b>${esc(t("Items each service", "Hạng mục mỗi lần"))}</b><div class="small muted" style="margin-top:4px">${PLAN_ITEMS.map(([en, vi]) => esc(t(en, vi))).join(" · ")}</div></div></div>
      </div>
        ${ui.card({
          flush: true,
          body: filterBar([ui.segmented([["", t("All", "Tất cả")], ["OVERDUE", t("Overdue", "Quá hạn")], ["DUE", t("Due", "Đến hạn")], ["DUE_SOON", t("Due soon", "Sắp đến")], ["DONE", t("Done", "Đã làm")]], query.due || "", "due"), fleetFilter(query), '<span class="grow"></span>', ui.btn(t("Export", "Xuất"), { sm: true, icon: "download", action: "export-reminders" })]) + ui.table({
            rows,
            columns: [
              { label: t("Truck", "Xe"), render: (r) => `${plateLink(r.v)}<span class="sub">${esc(modelOf(r.v).code)} · ${fleetName(r.v.fleet_id)}</span>` },
              { label: t("Last service", "Lần bảo dưỡng cuối"), render: (r) => (r.last ? `${fmt.date(r.last.at)}<span class="sub">${fmt.num(r.last.odometer_km)} km · ${esc(WORKSHOPS[r.last.workshop] || "")}</span>` : '<span class="muted">—</span>') },
              { label: t("Odometer", "Số km"), align: "right", render: (r) => fmt.num(r.v.odometer_km) },
              { label: t("Next due", "Lần tới"), render: (r) => (r.due ? `${fmt.num(r.last.odometer_km + r.plan.interval_km)} km ${t("or", "hoặc")} ${fmt.date(r.due)}` : `${fmt.num(r.plan.interval_km)} km`) },
              { label: t("Left", "Còn lại"), align: "right", render: (r) => `<span style="color:var(--${r.kmLeft < 0 ? "danger" : r.kmLeft <= 3000 ? "warn" : "text"})">${fmt.num(r.kmLeft)} km</span>${r.daysLeft !== null ? `<span class="sub" style="${r.daysLeft < 0 ? "color:var(--danger)" : ""}">${r.daysLeft} ${t("days", "ngày")}</span>` : ""}` },
              { label: t("Status", "Trạng thái"), render: (r) => reminderBadge(r.status) + (r.covering ? `<span class="sub">${esc(r.covering.case_id)}</span>` : "") },
              { label: "", align: "right", cls: "nowrap", render: (r) => (scope.has("MAINTENANCE", "FLEET_MANAGER", "HEAD_ADMIN") ? ui.btn(t("Record service", "Ghi nhận bảo dưỡng"), { sm: true, kind: "ghost", action: "record-service", attrs: `data-id="${r.v.vehicle_id}"` }) : "") },
            ],
          }),
        })}
      <div class="small muted mt">${esc(t("Whichever comes first. Drivers and fleet managers get the reminder; a service already recorded (or done inside a repair) clears it.", "Đến mốc nào trước thì nhắc. Tài xế và quản lý đội xe nhận nhắc nhở; một lần bảo dưỡng đã ghi nhận (hoặc làm trong lần sửa chữa) sẽ xóa nhắc nhở."))}</div>`;
  }

  function repairsTab(query) {
    const cases = repairCases.filter(caseVisible).filter((c) => (query.open === "closed" ? c.stage === "CLOSED" : query.open === "all" ? true : c.stage !== "CLOSED"));
    const selected = repairCases.find((c) => c.case_id === query.case && caseVisible(c)) || cases[0];
    const open = repairCases.filter(caseVisible).filter((c) => c.stage !== "CLOSED");
    const canEdit = scope.has("MAINTENANCE", "HEAD_ADMIN") || (scope.isInternal() && scope.has("WARRANTY"));
    const detail = (c) => {
      if (!c) return ui.card({ body: ui.empty(t("No repair case", "Không có ca sửa chữa")) });
      const v = db.vehicle(c.vehicle_id);
      const idx = stageIndex(c.stage);
      const partsCost = c.parts.reduce((a, p) => a + p[2] * p[3], 0);
      const warranty = db.warranties.find((w) => (c.component === "BATTERY" ? w.object_kind === "BATTERY" && w.object_id === v.battery_id : w.object_kind === "VEHICLE" && w.object_id === v.vehicle_id));
      return ui.card({
        title: `${c.case_id} · ${v.license_plate}`, sub: esc(t(c.fault[0], c.fault[1])),
        actions: kindBadge(c.kind),
        body: `${stepper(STAGES.map((st, i) => [st[0], stageLabel(i)]), idx)}
          <div class="grid cols-2 mt">
            <div>${ui.kv([
              [t("Truck", "Xe"), `${plateLink(v)} · ${esc(modelOf(v).code)} · ${ui.orgName(v.organization_id)}`],
              [t("Source", "Nguồn"), esc(t(c.source[0], c.source[1]))],
              [t("Opened", "Mở lúc"), `${fmt.dateTime(c.opened_at)} · ${fmt.ago(c.opened_at)}`],
              [t("Drivable?", "Chạy được?"), c.drivable === null ? `${ui.badge("PENDING")}${canEdit && c.stage === "DRIVABLE" ? ` ${ui.btn(t("Yes", "Có"), { sm: true, action: "drivable", attrs: `data-id="${c.case_id}" data-value="1"` })} ${ui.btn(t("No, tow it", "Không, kéo xe"), { sm: true, kind: "danger", action: "drivable", attrs: `data-id="${c.case_id}" data-value="0"` })}` : ""}` : c.drivable ? ui.badge("OK", "ok", t("Yes, may keep driving", "Có, được tiếp tục chạy")) : ui.badge("NO", "danger", t("No, off the road", "Không, dừng khai thác"))],
              [t("Workshop", "Xưởng"), esc(WORKSHOPS[c.workshop] || "—")],
            ])}</div>
            <div><h4 style="margin-bottom:8px">${esc(t("Stage log", "Nhật ký các bước"))}</h4><ul class="timeline">${c.log.slice().reverse().map((l) => `<li><div><b>${esc(stageLabel(stageIndex(l.stage)))}</b>${l.note ? ` · <span class="small">${esc(l.note)}</span>` : ""}</div><div class="when">${fmt.dateTime(l.at)} · ${ui.userName(l.by)}</div></li>`).join("")}</ul></div>
          </div>
          <div class="divider"></div>
          <h4 style="margin-bottom:8px">${esc(t("Parts and labour", "Phụ tùng và nhân công"))}</h4>
          ${c.parts.length ? ui.table({ rows: c.parts, columns: [{ label: t("Part", "Phụ tùng"), render: (p) => esc(t(p[0], p[1])) }, { label: t("Qty", "SL"), align: "right", render: (p) => p[2] }, { label: t("Unit cost", "Đơn giá"), align: "right", render: (p) => fmt.vnd(p[3]) }, { label: t("Amount", "Thành tiền"), align: "right", render: (p) => fmt.vnd(p[2] * p[3]) }] }) : `<div class="small muted">${esc(t("No part yet", "Chưa có phụ tùng"))}</div>`}
          <div class="row between mt small"><span>${esc(t("Labour", "Nhân công"))}: ${fmt.num(c.labour_hours, 1)} ${t("h", "giờ")} × ${fmt.vnd(LABOUR_RATE)}</span><span>${esc(t("Parts", "Phụ tùng"))}: ${fmt.vnd(partsCost)}</span><b>${esc(t("Total", "Tổng"))}: ${fmt.vnd(caseCost(c))}</b></div>
          <div class="divider"></div>
          <div class="row between"><h4>${esc(t("Warranty claim file", "Hồ sơ yêu cầu bảo hành"))}</h4>${c.claim ? claimBadge(c.claim.status) : ""}</div>
          ${c.claim ? `<div class="small mt" style="margin-top:6px">${ui.kv([[t("Claim", "Mã hồ sơ"), `<span class="mono">${esc(c.claim.claim_no)}</span>`], [t("Warranty", "Bảo hành"), warranty ? `${esc(G3.label(warranty.object_kind))} · ${statusBadge(warranty.status, warranty.status_reason)} ${t("until", "đến")} ${fmt.date(warranty.ends_on)}` : "—"], [t("Files", "Tệp"), c.claim.files.map((f) => `<span class="tag">${ui.icon("file", 12)} ${esc(f)}</span>`).join(" ")]])}</div>${c.claim.note ? `<div class="mt">${ui.callout("danger", esc(t(c.claim.note[0], c.claim.note[1])))}</div>` : ""}` : `<div class="small muted" style="margin-top:6px">${esc(t("No claim: the owner pays, or open a claim if a warranty covers it.", "Chưa có hồ sơ: chủ xe chi trả, hoặc lập hồ sơ nếu còn bảo hành."))}</div>`}
          ${canEdit ? `<div class="row mt">${c.stage !== "CLOSED" && c.stage !== "DRIVABLE" ? ui.btn(t(`Move to ${stageLabel(idx + 1)}`, `Chuyển sang ${stageLabel(idx + 1)}`), { kind: "primary", icon: "chevron", action: "advance-case", attrs: `data-id="${c.case_id}"` }) : ""}${ui.btn(t("Add a part", "Thêm phụ tùng"), { icon: "plus", action: "add-part", attrs: `data-id="${c.case_id}"` })}${!c.claim ? ui.btn(t("Open a warranty claim", "Lập hồ sơ bảo hành"), { icon: "shield", action: "open-claim", attrs: `data-id="${c.case_id}"` }) : c.claim.status === "DRAFT" ? ui.btn(t("Submit the claim", "Gửi hồ sơ"), { icon: "send", action: "submit-claim", attrs: `data-id="${c.case_id}"` }) : ""}</div>` : ""}`,
      });
    };
    return `${ui.featureChips(["MNT-03"])}
      <div class="grid cols-4 mb mt">
        ${ui.kpi({ label: t("Open cases", "Ca đang mở"), icon: "wrench", value: open.length })}
        ${ui.kpi({ label: t("Trucks off the road", "Xe dừng khai thác"), icon: "truck", value: open.filter((c) => c.drivable === false).length, tone: open.some((c) => c.drivable === false) ? "danger" : undefined })}
        ${ui.kpi({ label: t("Claims pending", "Hồ sơ BH chờ duyệt"), icon: "shield", value: open.filter((c) => c.claim && ["DRAFT", "SUBMITTED"].includes(c.claim.status)).length })}
        ${ui.kpi({ label: t("Cost of open cases", "Chi phí ca đang mở"), icon: "receipt", value: fmt.vndShort(open.reduce((a, c) => a + caseCost(c), 0)) })}
      </div>
      <div class="grid side-l">
        ${ui.card({
          flush: true,
          body: filterBar([ui.segmented([["", t("Open", "Đang mở")], ["closed", t("Closed", "Đã đóng")], ["all", t("All", "Tất cả")]], query.open || "", "open")]) + (cases.length ? `<ul class="list">${cases.map((c) => { const v = db.vehicle(c.vehicle_id); return `<li class="clickable ${selected && selected.case_id === c.case_id ? "unread" : ""}" data-open="${c.case_id}" data-open-key="case"><div class="sev-icon ${c.drivable === false ? "CRITICAL" : c.drivable === null ? "WARNING" : "INFO"}">${ui.icon("wrench", 15)}</div><div class="grow"><div class="row between"><span class="title">${esc(c.case_id)} · ${esc(v.license_plate)}</span>${kindBadge(c.kind)}</div><div class="meta">${esc(t(c.fault[0], c.fault[1]))}</div><div class="meta">${esc(stageLabel(stageIndex(c.stage)))} · ${fmt.ago(c.opened_at)} · ${fmt.vndShort(caseCost(c))}</div></div></li>`; }).join("")}</ul>` : ui.empty(t("No case", "Không có ca nào"))),
        })}
        ${detail(selected)}
      </div>`;
  }

  /* Monthly maintenance cost per truck over 12 months (MNT-05), split by kind. */
  function monthlyCosts(v) {
    const plan = plans[v.vehicle_model_id];
    const r = rng(v.vehicle_id + "cost");
    const owned = Math.max(1, Math.round((Date.now() - new Date(v.acquired_at).getTime()) / (30 * DAY)));
    const phase = Math.floor(r() * 6);
    const out = Array.from({ length: 12 }, (_, i) => {
      const monthsAgo = 11 - i;
      if (monthsAgo >= owned) return { periodic: 0, unplanned: 0, overhaul: 0 };
      const periodic = (monthsAgo + phase) % 6 === 0 ? plan.cost_vnd : 0;
      const unplanned = r() < 0.16 ? Math.round((0.8 + r() * 4.5) * 1e6 / 1e4) * 1e4 : 0;
      return { periodic, unplanned, overhaul: 0 };
    });
    // Recorded repair cases land in the month they were opened.
    repairCases.filter((c) => c.vehicle_id === v.vehicle_id).forEach((c) => {
      const d = new Date(c.opened_at), n = new Date();
      const idx = 11 - ((n.getFullYear() - d.getFullYear()) * 12 + n.getMonth() - d.getMonth());
      if (idx >= 0 && idx < 12) out[idx][c.kind === "OVERHAUL" ? "overhaul" : "unplanned"] += caseCost(c);
    });
    services.filter((s) => s.vehicle_id === v.vehicle_id && s.recorded).forEach((s) => { out[11].periodic += s.cost_vnd; });
    return out;
  }

  function costsTab(query) {
    const trucks = maintenanceTrucks(query);
    const months = Array.from({ length: 12 }, (_, i) => { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - (11 - i)); return monthLabel(d); });
    const perTruck = trucks.map((v) => { const m = monthlyCosts(v); const sum = (k) => m.reduce((a, x) => a + x[k], 0); return { v, months: m, periodic: sum("periodic"), unplanned: sum("unplanned"), overhaul: sum("overhaul") }; }).map((r) => Object.assign(r, { total: r.periodic + r.unplanned + r.overhaul })).sort((a, b) => b.total - a.total);
    const fleetMonths = months.map((_, i) => perTruck.reduce((a, r) => ({ periodic: a.periodic + r.months[i].periodic, unplanned: a.unplanned + r.months[i].unplanned, overhaul: a.overhaul + r.months[i].overhaul }), { periodic: 0, unplanned: 0, overhaul: 0 }));
    const tot = (k) => perTruck.reduce((a, r) => a + r[k], 0);
    const total = tot("total");
    const km = trucks.reduce((a, v) => a + v.odometer_km, 0);
    const colors = { periodic: ui.PALETTE[1], unplanned: ui.PALETTE[2], overhaul: ui.PALETTE[3] };
    const groups = scope.isInternal() ? [...new Set(trucks.map((v) => v.organization_id))].map((id) => ({ label: db.org(id).short_name + (db.org(id).legal_form === "INDIVIDUAL" ? t(" (individual)", " (cá nhân)") : ""), value: perTruck.filter((r) => r.v.organization_id === id).reduce((a, r) => a + r.total, 0) }))
      : [...new Set(trucks.map((v) => v.fleet_id))].map((id) => ({ label: id ? db.fleet(id).name : t("Not in a fleet", "Chưa vào đội"), value: perTruck.filter((r) => r.v.fleet_id === id).reduce((a, r) => a + r.total, 0) }));
    groups.sort((a, b) => b.value - a.value);
    return `${ui.featureChips(["MNT-05"])}
      <div class="grid cols-4 mb mt">
        ${ui.kpi({ label: t("Maintenance cost (12 m)", "Chi phí bảo trì (12 tháng)"), icon: "receipt", value: fmt.vndShort(total), foot: `${trucks.length} ${t("trucks", "xe")}` })}
        ${ui.kpi({ label: t("Periodic", "Định kỳ"), icon: "clock", value: fmt.vndShort(tot("periodic")), foot: fmt.pct((tot("periodic") / (total || 1)) * 100) })}
        ${ui.kpi({ label: t("Unplanned repairs", "Sửa chữa đột xuất"), icon: "alert", value: fmt.vndShort(tot("unplanned")), tone: "warn", foot: fmt.pct((tot("unplanned") / (total || 1)) * 100) })}
        ${ui.kpi({ label: t("Cost per km", "Chi phí/km"), icon: "route", value: fmt.num(total / (km || 1)), unit: "₫/km", foot: `${t("overhaul", "đại tu")} ${fmt.vndShort(tot("overhaul"))}` })}
      </div>
      <div class="grid side">
        ${ui.card({
          title: t("Cost per month", "Chi phí theo tháng"), sub: t("whole fleet", "toàn đội"),
          actions: `${ui.legend([[t("Periodic", "Định kỳ"), colors.periodic], [t("Unplanned", "Đột xuất"), colors.unplanned], [t("Overhaul", "Đại tu"), colors.overhaul]])}${fleetFilter(query)}`,
          body: ui.barChart({ labels: months, stacked: true, height: 240, series: [{ name: t("Periodic", "Định kỳ"), values: fleetMonths.map((m) => m.periodic), color: colors.periodic }, { name: t("Unplanned", "Đột xuất"), values: fleetMonths.map((m) => m.unplanned), color: colors.unplanned }, { name: t("Overhaul", "Đại tu"), values: fleetMonths.map((m) => m.overhaul), color: colors.overhaul }], yFormat: (v) => (v >= 1e6 ? fmt.num(v / 1e6, 0) + t("M", "tr") : fmt.num(v)), unit: " ₫" }),
        })}
        ${ui.card({ title: scope.isInternal() ? t("By customer", "Theo khách hàng") : t("By fleet", "Theo đội xe"), sub: "12 m", body: ui.hBars({ labels: groups.map((g) => g.label), series: [{ values: groups.map((g) => g.value), color: ui.PALETTE[2] }], yFormat: (v) => fmt.vndShort(v) }) })}
      </div>
      <div class="mt">${ui.card({
        title: t("Per truck", "Theo từng xe"), flush: true, actions: ui.btn(t("Export", "Xuất"), { sm: true, icon: "download", action: "export-costs" }),
        body: ui.table({
          rows: perTruck,
          columns: [
            { label: t("Truck", "Xe"), render: (r) => `${plateLink(r.v)}<span class="sub">${esc(modelOf(r.v).code)} · ${ui.orgName(r.v.organization_id)}</span>` },
            { label: t("Periodic", "Định kỳ"), align: "right", render: (r) => fmt.vnd(r.periodic) },
            { label: t("Unplanned", "Đột xuất"), align: "right", render: (r) => fmt.vnd(r.unplanned) },
            { label: t("Overhaul", "Đại tu"), align: "right", render: (r) => (r.overhaul ? fmt.vnd(r.overhaul) : '<span class="muted">—</span>') },
            { label: t("Total 12 m", "Tổng 12 tháng"), align: "right", render: (r) => `<b>${fmt.vnd(r.total)}</b>` },
            { label: "₫/km", align: "right", render: (r) => fmt.num(r.total / (r.v.odometer_km || 1)) },
            { label: t("Trend", "Xu hướng"), render: (r) => ui.sparkline(r.months.map((m) => m.periodic + m.unplanned + m.overhaul), "var(--warn)", 80, 22) },
          ],
        }),
      })}</div>`;
  }

  /* Asset cost and depreciation (MNT-06). Đồng Nai Xanh bought its trucks second-hand: they entered service earlier. */
  const PURCHASE = { "vm-262": 2150000000, "vm-400": 3050000000, "vm-825": 3850000000 };
  const BATTERY_VND_PER_KWH = 2900000;
  const NORMS = [
    ["TYRE", ["Tyres", "Lốp xe"], 120000, "km"], ["BRAKE", ["Brake pads", "Má phanh"], 90000, "km"], ["MOTOR_BEARING", ["Drive motor bearings", "Ổ bi động cơ"], 300000, "km"],
    ["BATTERY", ["Battery pack", "Bộ pin"], 4000, ["cycles", "chu kỳ"]], ["COOLING", ["Coolant pump", "Bơm nước làm mát"], 15000, ["h", "giờ"]],
    ["DCDC", ["DC-DC converter", "Bộ DC-DC"], 20000, ["h", "giờ"]], ["AC_COMPRESSOR", ["A/C compressor", "Máy nén điều hòa"], 6000, ["h", "giờ"]], ["BODY", ["Body panels", "Thân vỏ"], null, ""],
  ];
  function assetOf(v) {
    const used = v.organization_id === "org-dn";
    const inService = used ? new Date(new Date(v.acquired_at).getTime() - (4.4 * 365 + (hashOf(v.vehicle_id) % 90)) * DAY) : new Date(v.acquired_at);
    const years = 6;
    const cost = PURCHASE[v.vehicle_model_id] || 3e9;
    const residual = cost * 0.1;
    const elapsed = Math.max(0, (Date.now() - inService.getTime()) / (365 * DAY));
    const share = Math.min(1, elapsed / years);
    const accumulated = (cost - residual) * share;
    const battery = db.battery(v.battery_id);
    const packCost = battery ? (db.battery_models.find((m) => m.battery_model_id === battery.battery_model_id) || { capacity_kwh: 300 }).capacity_kwh * BATTERY_VND_PER_KWH : 0;
    const packShare = battery ? Math.min(1, battery.cycle_count / 4000) : 0;
    return { v, used, inService, years, cost, residual, accumulated, book: cost - accumulated, share, monthsLeft: Math.max(0, Math.round((years - elapsed) * 12)), battery, packCost, packBook: packCost - packCost * 0.95 * packShare, packShare };
  }

  function depreciationTab(query) {
    const assets = maintenanceTrucks(query).map(assetOf);
    const totalCost = assets.reduce((a, x) => a + x.cost + x.packCost, 0);
    const totalBook = assets.reduce((a, x) => a + x.book + x.packBook, 0);
    const repairs12 = (v) => monthlyCosts(v).reduce((a, m) => a + m.unplanned + m.overhaul, 0);
    // Lifecycle alerts (MNT-07), computed from the cases, the norms and the book values.
    const alerts = [];
    repairCases.filter(caseVisible).forEach((c) => {
      const norm = NORMS.find((n) => n[0] === c.component);
      const v = db.vehicle(c.vehicle_id);
      if (!norm || !norm[2]) return;
      const unit = txt(norm[3]);
      const life = norm[3] === "km" ? v.odometer_km : c.component === "BATTERY" ? (db.battery(v.battery_id) || {}).cycle_count : Math.round(v.odometer_km / 38);
      if (life < norm[2] * 0.6) alerts.push({ sev: "WARNING", v, kind: t("Failed early", "Hỏng sớm"), text: t(`${norm[1][0]} failed at ${fmt.num(life)} ${unit}, norm ${fmt.num(norm[2])} ${unit} (${fmt.pct((life / norm[2]) * 100)}) · ${c.case_id}`, `${norm[1][1]} hỏng ở ${fmt.num(life)} ${unit}, định mức ${fmt.num(norm[2])} ${unit} (${fmt.pct((life / norm[2]) * 100)}) · ${c.case_id}`) });
    });
    assets.forEach((a) => {
      const rep = repairs12(a.v);
      if (rep > a.book * 0.1) alerts.push({ sev: "CRITICAL", v: a.v, kind: t("Repairs vs value", "Sửa chữa so với giá trị"), text: t(`Repairs in 12 months ${fmt.vndShort(rep)} = ${fmt.pct((rep / a.book) * 100)} of the book value ${fmt.vndShort(a.book)} (threshold 10%)`, `Sửa chữa 12 tháng ${fmt.vndShort(rep)} = ${fmt.pct((rep / a.book) * 100)} giá trị còn lại ${fmt.vndShort(a.book)} (ngưỡng 10%)`) });
      if (a.monthsLeft <= 18) alerts.push({ sev: a.monthsLeft <= 12 ? "WARNING" : "INFO", v: a.v, kind: t("End of depreciation", "Sắp hết khấu hao"), text: t(`${a.monthsLeft} months of depreciation left (in service since ${fmt.date(a.inService)})`, `Còn ${a.monthsLeft} tháng khấu hao (đưa vào sử dụng từ ${fmt.date(a.inService)})`) });
    });
    alerts.sort((a, b) => ["CRITICAL", "WARNING", "INFO"].indexOf(a.sev) - ["CRITICAL", "WARNING", "INFO"].indexOf(b.sev));
    const early = (key) => repairCases.filter(caseVisible).filter((c) => c.component === key).length;
    return `${ui.featureChips(["MNT-06", "MNT-07"])}
      <div class="grid cols-4 mb mt">
        ${ui.kpi({ label: t("Purchase cost", "Nguyên giá"), icon: "receipt", value: fmt.vndShort(totalCost), foot: t("trucks and packs", "xe và pin") })}
        ${ui.kpi({ label: t("Book value", "Giá trị còn lại"), icon: "chart", value: fmt.vndShort(totalBook), foot: fmt.pct((totalBook / (totalCost || 1)) * 100) + t(" of cost", " nguyên giá") })}
        ${ui.kpi({ label: t("Depreciation this year", "Khấu hao năm nay"), icon: "activity", value: fmt.vndShort(assets.reduce((a, x) => a + (x.share < 1 ? (x.cost - x.residual) / x.years : 0), 0)), foot: t("straight line, 6 years", "đường thẳng, 6 năm") })}
        ${ui.kpi({ label: t("Lifecycle alerts", "Cảnh báo vòng đời"), icon: "alert", value: alerts.length, tone: alerts.some((a) => a.sev === "CRITICAL") ? "danger" : alerts.length ? "warn" : undefined, foot: "MNT-07" })}
      </div>
      <div class="grid side">
        ${ui.card({
          title: t("Assets", "Tài sản"), sub: t("straight line 6 years, 10% residual · packs by cycles (4,000)", "đường thẳng 6 năm, thu hồi 10% · pin theo chu kỳ (4.000)"), flush: true, actions: fleetFilter(query),
          body: ui.table({
            rows: assets.sort((a, b) => b.share - a.share),
            columns: [
              { label: t("Truck", "Xe"), cls: "nowrap", render: (a) => `${plateLink(a.v)}<span class="sub">${esc(modelOf(a.v).code)} · ${t("since", "từ")} ${fmt.date(a.inService)}${a.used ? ` · ${t("used", "mua lại")}` : ""}</span>` },
              { label: t("Cost", "Nguyên giá"), align: "right", cls: "nowrap", render: (a) => fmt.vndShort(a.cost) },
              { label: t("Life used", "Đã khấu hao"), render: (a) => `<div class="row" style="flex-wrap:nowrap;gap:6px;min-width:120px">${ui.meter(a.share * 100, a.share > 0.85 ? "danger" : a.share > 0.6 ? "warn" : "")}<span class="small num">${fmt.pct(a.share * 100)}</span></div><span class="sub">${a.monthsLeft} ${t("months left", "tháng còn lại")}</span>` },
              { label: t("Book value", "Còn lại"), align: "right", cls: "nowrap", render: (a) => `<b>${fmt.vndShort(a.book)}</b><span class="sub">${t("residual", "thu hồi")} ${fmt.vndShort(a.residual)}</span>` },
              { label: t("Pack value", "Giá trị pin"), align: "right", cls: "nowrap", render: (a) => (a.battery ? `${fmt.vndShort(a.packBook)}<span class="sub">${fmt.pct(a.packShare * 100, 1)} ${t("used", "đã dùng")}</span>` : "—") },
            ],
          }),
        })}
        <div class="stack">
          ${ui.card({
            title: t("Lifecycle alerts", "Cảnh báo vòng đời"), sub: `${alerts.length}`, flush: true,
            body: alerts.length ? `<ul class="list">${alerts.slice(0, 8).map((a) => `<li><div class="sev-icon ${a.sev}">${ui.icon("alert", 15)}</div><div class="grow"><div class="title">${esc(a.v.license_plate)} · ${esc(a.kind)}</div><div class="meta">${esc(a.text)}</div></div>${ui.badge(a.sev)}</li>`).join("")}</ul>` : ui.empty(t("No lifecycle alert", "Không có cảnh báo vòng đời")),
          })}
          ${ui.card({
            title: t("Component lifetime norms", "Định mức tuổi thọ linh kiện"), flush: true,
            body: ui.table({
              rows: NORMS.filter((n) => n[2]),
              columns: [
                { label: t("Component", "Linh kiện"), render: (n) => esc(t(n[1][0], n[1][1])) },
                { label: t("Norm", "Định mức"), align: "right", render: (n) => `${fmt.num(n[2])} ${esc(txt(n[3]))}` },
                { label: t("Failures (open + closed)", "Số lần hỏng"), align: "right", render: (n) => (early(n[0]) ? `<b style="color:var(--warn)">${early(n[0])}</b>` : '<span class="muted">0</span>') },
              ],
            }),
          })}
        </div>
      </div>
      <div class="mt small muted">${esc(t("Illustrative figures. Rules: alert when a component fails before 60% of its norm, when 12-month repairs exceed 10% of the book value, and 18 months before the end of depreciation.", "Số liệu minh họa. Quy tắc: cảnh báo khi linh kiện hỏng trước 60% định mức, khi chi phí sửa chữa 12 tháng vượt 10% giá trị còn lại, và 18 tháng trước khi hết khấu hao."))}</div>`;
  }

  function recordService(id) {
    const v = db.vehicle(id);
    ui.formModal({
      title: t(`Record a service on ${v.license_plate}`, `Ghi nhận bảo dưỡng xe ${v.license_plate}`),
      wide: true,
      fields: [
        { name: "date", label: t("Done on", "Ngày thực hiện"), type: "date", required: true, value: todayIso() },
        { name: "km", label: t("Odometer (km)", "Số km"), type: "number", required: true, value: Math.round(v.odometer_km) },
        { name: "workshop", label: t("Workshop", "Xưởng"), type: "select", required: true, options: Object.entries(WORKSHOPS) },
        { name: "cost", label: t("Cost (₫)", "Chi phí (₫)"), type: "number", required: true, value: plans[v.vehicle_model_id].cost_vnd },
        { name: "items", label: t("Items done", "Hạng mục đã làm"), type: "checkboxes", full: true, options: PLAN_ITEMS.map(([en, vi], i) => [String(i), t(en, vi)]), value: ["0", "1", "2", "3", "4"] },
      ],
      submitLabel: t("Record", "Ghi nhận"),
      onSubmit(values) {
        const km = Number(values.km);
        if (values.date > todayIso()) return t("The date cannot be in the future.", "Ngày không được ở tương lai.");
        if (!(km > 0) || km > v.odometer_km + 50) return t(`The odometer must be at most ${fmt.num(v.odometer_km)} km.`, `Số km không được vượt ${fmt.num(v.odometer_km)} km.`);
        if (!values.items.length) return t("Tick at least one item.", "Chọn ít nhất một hạng mục.");
        services.push({ service_id: "svc-n" + services.length, vehicle_id: v.vehicle_id, kind: "PERIODIC", at: new Date(values.date + "T10:00:00").toISOString(), odometer_km: km, workshop: values.workshop, cost_vnd: Number(values.cost) || 0, recorded: true, note: null });
        G3.render();
        ui.toast(t(`Service recorded on ${v.license_plate}: next reminder reset`, `Đã ghi nhận bảo dưỡng xe ${v.license_plate}: đặt lại nhắc nhở`));
      },
    });
  }

  G3.page({
    id: "maintenance",
    path: "/maintenance",
    title: ["Maintenance", "Bảo trì & sửa chữa"],
    roles: ["FLEET_MANAGER", "MAINTENANCE", "ACCOUNTANT", "WARRANTY"],
    features: ["MNT-01", "MNT-03", "MNT-04", "MNT-05", "MNT-06", "MNT-07"],
    nav: { group: "assets", order: 5, icon: "wrench" },
    render({ query }) {
      const tabs = [["reminders", t("Reminders", "Nhắc bảo dưỡng")], ["repairs", t("Repair cases", "Ca sửa chữa")], ["costs", t("Cost report", "Báo cáo chi phí")], ["depreciation", t("Depreciation", "Khấu hao")]];
      const tab = tabs.some(([k]) => k === query.tab) ? query.tab : scope.has("ACCOUNTANT") && !scope.has("MAINTENANCE", "FLEET_MANAGER") ? "costs" : "reminders";
      const body = tab === "repairs" ? repairsTab(query) : tab === "costs" ? costsTab(query) : tab === "depreciation" ? depreciationTab(query) : remindersTab(query);
      const canOpen = scope.has("MAINTENANCE", "FLEET_MANAGER", "HEAD_ADMIN");
      return `${ui.pageHead({
        title: t("Maintenance & repairs", "Bảo trì & sửa chữa"),
        sub: esc(t("Periodic maintenance, repair cases from fault to handover, what it costs and what the trucks are still worth.", "Bảo dưỡng định kỳ, ca sửa chữa từ báo lỗi đến bàn giao, chi phí và giá trị còn lại của xe.")),
        features: ["MNT-01", "MNT-03", "MNT-04", "MNT-05", "MNT-06", "MNT-07"],
        actions: tab === "repairs" && canOpen ? ui.btn(t("Open a case", "Mở ca sửa chữa"), { icon: "plus", kind: "primary", action: "open-case" }) : "",
      })}${ui.tabs(tabs, tab)}${body}`;
    },
    mount(root, { query }) {
      bindCommon(root);
      const findCase = (id) => repairCases.find((c) => c.case_id === id);
      bindActions(root, {
        "record-service": (d) => recordService(d.id),
        "edit-plan": (d) => {
          const p = plans[d.id];
          ui.formModal({
            title: t(`Maintenance plan · ${db.model(d.id).code}`, `Kế hoạch bảo dưỡng · ${db.model(d.id).code}`),
            fields: [{ name: "km", label: t("Every (km)", "Mỗi (km)"), type: "number", required: true, value: p.interval_km }, { name: "months", label: t("Or every (months)", "Hoặc mỗi (tháng)"), type: "number", required: true, value: p.interval_months }, { name: "cost", label: t("Standard cost (₫)", "Chi phí chuẩn (₫)"), type: "number", required: true, value: p.cost_vnd }],
            onSubmit(values) {
              if (!(Number(values.km) >= 5000 && Number(values.months) >= 1)) return t("Use at least 5,000 km and 1 month.", "Tối thiểu 5.000 km và 1 tháng.");
              Object.assign(p, { interval_km: Number(values.km), interval_months: Number(values.months), cost_vnd: Number(values.cost) || p.cost_vnd });
              G3.render();
              ui.toast(t("Plan updated: reminders recomputed", "Đã cập nhật kế hoạch: tính lại nhắc nhở"));
            },
          });
        },
        "export-reminders": () => ui.downloadCsv("maintenance-reminders.csv", [
          { label: "license_plate", csv: (r) => r.v.license_plate }, { label: "model", csv: (r) => modelOf(r.v).code }, { label: "odometer_km", csv: (r) => Math.round(r.v.odometer_km) },
          { label: "last_service", csv: (r) => (r.last ? r.last.at.slice(0, 10) : "") }, { label: "km_left", key: "kmLeft" }, { label: "days_left", key: "daysLeft" }, { label: "status", key: "status" },
        ], maintenanceTrucks(query).map(reminderOf)),
        "export-costs": () => ui.downloadCsv("maintenance-costs.csv", [
          { label: "license_plate", csv: (v) => v.license_plate }, { label: "owner", csv: (v) => db.org(v.organization_id).short_name },
          { label: "periodic_vnd", csv: (v) => monthlyCosts(v).reduce((a, m) => a + m.periodic, 0) }, { label: "unplanned_vnd", csv: (v) => monthlyCosts(v).reduce((a, m) => a + m.unplanned, 0) }, { label: "overhaul_vnd", csv: (v) => monthlyCosts(v).reduce((a, m) => a + m.overhaul, 0) },
        ], maintenanceTrucks(query)),
        "open-case": () => {
          const trucks = scope.vehicles().filter(isLive);
          ui.formModal({
            title: t("Open a repair case", "Mở ca sửa chữa"),
            wide: true,
            fields: [
              { name: "vehicle", label: t("Truck", "Xe"), type: "select", required: true, options: trucks.map((v) => [v.vehicle_id, `${v.license_plate} · ${modelOf(v).code}`]) },
              { name: "kind", label: t("Kind", "Loại"), type: "select", required: true, options: Object.keys(KIND_BADGE).filter((k) => k !== "PERIODIC").map((k) => [k, t(KIND_BADGE[k][1][0], KIND_BADGE[k][1][1])]) },
              { name: "source", label: t("Reported by", "Nguồn báo"), type: "select", required: true, options: [["DRIVER", t("Driver report", "Tài xế báo")], ["ALERT", t("System alert", "Cảnh báo hệ thống")], ["INSPECTION", t("Inspection", "Kiểm tra định kỳ")]] },
              { name: "component", label: t("Component", "Linh kiện"), type: "select", required: true, options: NORMS.map((n) => [n[0], t(n[1][0], n[1][1])]) },
              { name: "fault", label: t("Fault", "Mô tả lỗi"), type: "textarea", required: true, full: true },
            ],
            submitLabel: t("Open case", "Mở ca"),
            onSubmit(values) {
              const id = `RC-0${413 + repairCases.length}`;
              const sources = { DRIVER: ["Driver report", "Tài xế báo"], ALERT: ["System alert", "Cảnh báo hệ thống"], INSPECTION: ["Inspection", "Kiểm tra định kỳ"] };
              const now = new Date().toISOString();
              repairCases.unshift({ case_id: id, vehicle_id: values.vehicle, kind: values.kind, opened_at: now, source: sources[values.source], fault: [values.fault, values.fault], stage: "FAULT", drivable: null, workshop: "g3", parts: [], labour_hours: 0, component: values.component, claim: null, log: [{ stage: "FAULT", at: now, by: me() }] });
              G3.setQuery({ case: id, open: null });
              ui.toast(t(`Case ${id} opened`, `Đã mở ca ${id}`));
            },
          });
        },
        "advance-case": (d) => {
          const c = findCase(d.id);
          const next = STAGES[stageIndex(c.stage) + 1];
          const nextLabel = stageLabel(stageIndex(c.stage) + 1);
          ui.formModal({
            title: t(`${c.case_id}: move to ${nextLabel}`, `${c.case_id}: chuyển sang ${nextLabel}`),
            fields: [{ name: "note", label: t("Note", "Ghi chú"), type: "textarea", required: next[0] === "TEST" || next[0] === "HANDOVER", placeholder: next[0] === "HANDOVER" ? t("Test result, who received the truck…", "Kết quả chạy thử, người nhận xe…") : "" }],
            submitLabel: t("Move", "Chuyển"),
            onSubmit(values) {
              c.stage = next[0];
              c.log.push({ stage: next[0], at: new Date().toISOString(), by: me(), note: values.note || null });
              G3.render();
              ui.toast(t(`${c.case_id} is now at ${nextLabel}`, `${c.case_id} đã chuyển sang ${nextLabel}`));
            },
          });
        },
        drivable: (d) => {
          const c = findCase(d.id);
          c.drivable = d.value === "1";
          c.stage = "WORKSHOP";
          c.log.push({ stage: "WORKSHOP", at: new Date().toISOString(), by: me(), note: c.drivable ? t("May keep driving to the workshop", "Được tự chạy đến xưởng") : t("Off the road, tow requested", "Dừng khai thác, đã gọi xe kéo") });
          G3.render();
          ui.toast(c.drivable ? t("Decision: the truck may keep driving", "Quyết định: xe được tiếp tục chạy") : t("Decision: off the road, towing", "Quyết định: dừng khai thác, kéo xe"), c.drivable ? "" : "alert");
        },
        "add-part": (d) => {
          const c = findCase(d.id);
          ui.formModal({
            title: t(`Add a part to ${c.case_id}`, `Thêm phụ tùng cho ${c.case_id}`),
            fields: [{ name: "name", label: t("Part", "Phụ tùng"), required: true }, { name: "qty", label: t("Quantity", "Số lượng"), type: "number", required: true, value: 1 }, { name: "cost", label: t("Unit cost (₫)", "Đơn giá (₫)"), type: "number", required: true }, { name: "hours", label: t("Extra labour (h)", "Thêm giờ công"), type: "number", value: 0 }],
            onSubmit(values) {
              if (!(Number(values.qty) > 0) || !(Number(values.cost) >= 0)) return t("Quantity and cost must be positive.", "Số lượng và đơn giá phải dương.");
              c.parts.push([values.name, values.name, Number(values.qty), Number(values.cost)]);
              c.labour_hours += Number(values.hours) || 0;
              G3.render();
              ui.toast(t("Part added", "Đã thêm phụ tùng"));
            },
          });
        },
        "open-claim": (d) => { const c = findCase(d.id); c.claim = { claim_no: `WC-2026-${120 + repairCases.indexOf(c)}`, status: "DRAFT", files: [`${c.case_id.toLowerCase()}-diagnosis.pdf`, "photos.zip"] }; G3.render(); ui.toast(t(`Claim ${c.claim.claim_no} drafted with the case file`, `Đã lập hồ sơ ${c.claim.claim_no} kèm hồ sơ ca sửa`)); },
        "submit-claim": (d) => { const c = findCase(d.id); c.claim.status = "SUBMITTED"; G3.render(); ui.toast(t(`Claim ${c.claim.claim_no} sent to the warranty team`, `Đã gửi hồ sơ ${c.claim.claim_no} cho bộ phận bảo hành`), "", "send"); },
      });
    },
  });
})();
