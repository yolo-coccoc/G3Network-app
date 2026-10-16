/* Monitoring screens.
   - vehicle-detail  /vehicles/:id   live status, route replay, battery and cells,
     trips, charging, profile, ownership, device, battery and warranties
     (MON-02/04/05/06/07/08/09/10/11/12/13, CHG-04, DRV-02, VEH-01/02/05,
     BAT-01, DEV-02, WAR-01)
   - notifications   /notifications  notification center with acknowledgement
     and escalation (NTF-01, NTF-08, MON-04/05/06, DEV-05, FLT-05, DRV-02)
   - trips           /trips          trips, trip planning, shift plan
     (MON-11, MON-12, MON-13, DRV-03)
   - battery-health  /battery-health fleet SOH and cells (MON-07, MON-08, MON-05)
   - faults          /faults         component faults and the DTC catalog
     (MON-06, VEH-06, MON-05)
   - reports         /reports        operating, cost, charging & warranty,
     driving-time and charging-efficiency reports
     (MON-14, MON-16, FLT-07, FLT-08, DRV-06, DRV-07) */

(function () {
  const { ui, db, fmt, scope } = G3;
  const esc = G3.esc;
  const { MIN, HOUR, DAY } = db;

  /* ---------- Small helpers ---------- */
  function hashOf(text) {
    let h = 2166136261;
    for (const ch of String(text)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  /** Deterministic generator per key, so mock values survive re-renders. */
  function seeded(key) {
    let s = hashOf(key) || 1;
    return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; };
  }
  const pad = (n) => String(n).padStart(2, "0");
  const dm = (ms) => { const d = new Date(ms); return `${d.getDate()}/${d.getMonth() + 1}`; };
  const localDate = (value) => { const d = new Date(value); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
  const toInput = (ms) => { const d = new Date(ms); return `${localDate(ms)}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };
  const hm = (value) => { const d = new Date(value); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
  const distKm = (a, b) => Math.hypot((a.lat - b.lat) * 111, (a.lon - b.lon) * 109.3);
  const sum = (rows, f) => rows.reduce((a, r) => a + (f(r) || 0), 0);
  const round1 = (x) => Math.round(x * 10) / 10;
  const stateOf = (v) => (!v.live ? "NO_DATA" : v.live.is_online ? v.live.state : "OFFLINE");

  function vehicleLink(v) {
    if (!v) return '<span class="muted">—</span>';
    return G3.canSee("vehicle-detail") ? `<a href="#/vehicles/${v.vehicle_id}"><b>${esc(v.license_plate)}</b></a>` : `<b>${esc(v.license_plate)}</b>`;
  }
  function driverLink(driverId) {
    const d = db.driver(driverId);
    if (!d) return ui.driverName(null);
    return G3.canSee("driver-detail") ? `<a href="#/drivers/${d.driver_id}">${esc(d.full_name)}</a>` : esc(d.full_name);
  }
  function reachableLocations() {
    return db.charging_locations.filter((l) => l.is_public || scope.isInternal() || scope.orgIds().some((id) => (l.allowed_organization_ids || []).includes(id)));
  }
  /** Nearest active station the truck may use, preferring one with a free connector (MON-04). */
  function nearestStation(v, position) {
    const pos = position || v.live;
    if (!pos) return null;
    const rows = db.charging_locations
      .filter((l) => l.status === "ACTIVE" && (l.is_public || (l.allowed_organization_ids || []).includes(v.organization_id)))
      .map((l) => {
        const free = db.charging_stations.filter((s) => s.location_id === l.location_id && s.is_online).flatMap((s) => s.connectors).filter((c) => c.status === "Available").length;
        return { location: l, distance_km: distKm(l, pos), free };
      })
      .sort((a, b) => a.distance_km - b.distance_km);
    return rows.find((r) => r.free > 0) || rows[0] || null;
  }
  /** Tariff slot (label + price) in force at a time at a location. */
  function slotAt(locationId, value) {
    const tariff = db.tariffs.find((x) => x.location_id === locationId);
    if (!tariff) return { label: "NORMAL", price_vnd_kwh: 4180 };
    const d = new Date(value);
    const key = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return tariff.slots.find((s) => key >= s.from && key < s.to) || tariff.slots[0];
  }
  const SLOT_LABEL = { OFF_PEAK: ["Off-peak", "Thấp điểm"], NORMAL: ["Normal", "Bình thường"], PEAK: ["Peak", "Cao điểm"], SOLAR: ["Solar", "Điện mặt trời"] };
  const slotLabel = (label) => t(...(SLOT_LABEL[label] || [label, label]));

  /** Tabs, segmented controls, select/input filters and checkboxes write the URL query. */
  function bindCommon(root) {
    root.querySelectorAll("[data-tabs] [data-tab]").forEach((b) => b.addEventListener("click", () => G3.setQuery({ [b.closest("[data-tabs]").dataset.tabs]: b.dataset.tab })));
    root.querySelectorAll("[data-seg] button").forEach((b) => b.addEventListener("click", () => G3.setQuery({ [b.closest("[data-seg]").dataset.seg]: b.dataset.value })));
    root.querySelectorAll("select[data-filter], input[data-filter]").forEach((el) => el.addEventListener("change", () => G3.setQuery({ [el.dataset.filter]: el.value })));
    root.querySelectorAll("input[data-check]").forEach((el) => el.addEventListener("change", () => G3.setQuery({ [el.dataset.check]: el.checked ? "1" : null })));
  }
  function notifIcon(n) {
    const type = n.notification_type;
    if (type === "SOS_ALERT") return "sos";
    if (type.includes("BATTERY") || type === "SOH_ALERT" || type === "ANOMALY_ALERT") return "battery";
    if (type === "GEOFENCE_ALERT") return "shape";
    if (type === "DEVICE_OFFLINE_ALERT") return "chip";
    if (type === "CHARGER_FAULT" || type === "CHARGING_RECEIPT") return "plug";
    if (type.includes("TRIP")) return "route";
    if (type.includes("DRIVER")) return "id";
    if (type === "FAULT_ALERT") return "wrench";
    if (type.includes("WALLET") || type.includes("TOP_UP")) return "wallet";
    return n.severity === "INFO" ? "info" : "alert";
  }
  const canManageVehicle = () => scope.has("HEAD_ADMIN", "OPERATIONS", "FLEET_MANAGER", "ORG_ADMIN");
  const canDispatch = () => scope.has("HEAD_ADMIN", "FLEET_MANAGER", "DISPATCHER", "ORG_ADMIN");
  const isOps = () => scope.isInternal() && scope.has("HEAD_ADMIN", "OPERATIONS");

  /* ---------- Fault-code catalog (VEH-06, mock manufacturer table) ---------- */
  const COMPONENTS = {
    MOTOR: ["Drive motor", "Động cơ điện"],
    CONTROLLER: ["Controller board", "Bộ điều khiển"],
    BATTERY: ["HV battery", "Pin cao áp"],
    AUX_BATTERY: ["Auxiliary battery", "Ắc quy phụ 24 V"],
    BRAKES: ["Brakes", "Phanh"],
    TPMS: ["Tyres (TPMS)", "Lốp (TPMS)"],
    LIGHTS: ["Lights", "Đèn"],
    DOORS: ["Doors", "Cửa"],
    AC: ["Air-conditioning", "Điều hòa"],
  };
  const compLabel = (c) => t(...(COMPONENTS[c] || [c, c]));
  const DRIVABLE = {
    YES: [["Can keep driving", "Vẫn chạy được"], "ok"],
    LIMITED: [["Drive to a workshop", "Chạy về xưởng"], "warn"],
    NO: [["Stop — do not drive", "Dừng xe — không chạy tiếp"], "danger"],
  };
  const drivableBadge = (d) => ui.badge(d, DRIVABLE[d][1], t(...DRIVABLE[d][0]));
  const DTC = [
    ["P0A3F", "MOTOR", "CRITICAL", "NO", ["Motor position sensor A circuit", "Mạch cảm biến vị trí động cơ A"], ["Stop safely, switch off HV, call rescue.", "Dừng an toàn, ngắt cao áp, gọi cứu hộ."], "3.0"],
    ["P0A78", "MOTOR", "CRITICAL", "NO", ["Drive motor inverter performance", "Hiệu suất biến tần động cơ"], ["Stop; tow to an authorised workshop.", "Dừng xe; kéo về xưởng ủy quyền."], "3.0"],
    ["P1E00", "MOTOR", "WARNING", "LIMITED", ["Motor over-temperature, power reduced", "Động cơ quá nhiệt, giảm công suất"], ["Reduce load, check coolant at the next stop.", "Giảm tải, kiểm tra nước làm mát ở điểm dừng tới."], "3.0"],
    ["U0100", "CONTROLLER", "CRITICAL", "NO", ["Lost communication with the VCU", "Mất liên lạc với VCU"], ["Do not drive; restart once, then call support.", "Không chạy; khởi động lại một lần rồi gọi hỗ trợ."], "3.0"],
    ["U0293", "CONTROLLER", "WARNING", "LIMITED", ["Lost communication with the powertrain module", "Mất liên lạc mô-đun truyền động"], ["Drive slowly to the nearest workshop.", "Chạy chậm về xưởng gần nhất."], "3.1"],
    ["B1C42", "BATTERY", "CRITICAL", "LIMITED", ["Battery pack temperature high", "Nhiệt độ khối pin cao"], ["Stop charging, park in the shade, watch the temperature.", "Ngừng sạc, đỗ nơi râm mát, theo dõi nhiệt độ."], "3.0"],
    ["P0AA6", "BATTERY", "CRITICAL", "NO", ["HV insulation fault", "Lỗi cách điện cao áp"], ["Evacuate the cab, do not touch HV parts, call rescue.", "Rời cabin, không chạm phần cao áp, gọi cứu hộ."], "3.0"],
    ["P1B10", "AUX_BATTERY", "WARNING", "YES", ["24 V auxiliary battery low", "Ắc quy phụ 24 V yếu"], ["Check the DC/DC converter at the next service.", "Kiểm tra bộ DC/DC ở lần bảo dưỡng tới."], "3.0"],
    ["P0562", "AUX_BATTERY", "WARNING", "YES", ["System voltage low", "Điện áp hệ thống thấp"], ["Avoid long idle with loads on.", "Tránh để tải phụ chạy lâu khi đỗ."], "3.0"],
    ["C0040", "BRAKES", "CRITICAL", "NO", ["Brake pressure sensor circuit", "Mạch cảm biến áp suất phanh"], ["Stop; brakes may be degraded.", "Dừng xe; phanh có thể suy giảm."], "3.0"],
    ["C1095", "BRAKES", "WARNING", "LIMITED", ["Brake pad wear limit reached", "Má phanh mòn tới giới hạn"], ["Book a workshop within 500 km.", "Đặt lịch xưởng trong vòng 500 km."], "3.0"],
    ["C1A2F", "TPMS", "WARNING", "YES", ["Tyre pressure low", "Áp suất lốp thấp"], ["Inflate or check the tyre within 50 km.", "Bơm hoặc kiểm tra lốp trong 50 km."], "3.0"],
    ["C1A30", "TPMS", "INFO", "YES", ["TPMS sensor battery low", "Pin cảm biến áp suất lốp yếu"], ["Replace the sensor at the next service.", "Thay cảm biến ở lần bảo dưỡng tới."], "3.2"],
    ["B1A01", "LIGHTS", "WARNING", "LIMITED", ["Headlamp circuit open", "Hở mạch đèn pha"], ["Drive in daylight only until repaired.", "Chỉ chạy ban ngày cho đến khi sửa."], "3.0"],
    ["B1B05", "DOORS", "WARNING", "YES", ["Door ajar while moving", "Cửa mở khi xe chạy"], ["Stop and close the door.", "Dừng và đóng cửa."], "3.1"],
    ["B1D20", "AC", "INFO", "YES", ["A/C compressor fault", "Lỗi máy nén điều hòa"], ["Book a service; driving is not affected.", "Đặt lịch sửa; không ảnh hưởng vận hành."], "3.0"],
    ["B1D21", "AC", "INFO", "YES", ["A/C refrigerant pressure low", "Áp suất gas điều hòa thấp"], ["Recharge the refrigerant.", "Nạp lại gas điều hòa."], "3.2"],
  ].map(([code, component, severity, drivable, description, advice, since]) => ({ code, component, severity, drivable, description, advice, since }));
  const DTC_VERSIONS = [
    { version: "3.2", released_on: "2026-08-20", note: ["Adds C1A30, B1D21; brake pad wear raised to Warning", "Thêm C1A30, B1D21; mòn má phanh nâng lên Cảnh báo"] },
    { version: "3.1", released_on: "2026-03-02", note: ["Adds U0293, B1B05", "Thêm U0293, B1B05"] },
    { version: "3.0", released_on: "2025-11-15", note: ["First table for EVT-262/400/825", "Bảng đầu tiên cho EVT-262/400/825"] },
  ];
  /** Catalog rows as they were in a version (older severity kept for C1095). */
  function dtcFor(version) {
    return DTC.filter((d) => Number(d.since) <= Number(version)).map((d) => (d.code === "C1095" && version !== "3.2" ? { ...d, severity: "INFO" } : d));
  }
  const dtc = (code) => DTC.find((d) => d.code === code);

  /* Component faults currently reported by the trucks (MON-06), built once. */
  const FAULTS = [];
  (function buildFaults() {
    const fixed = { "veh-010": ["C1A2F"], "veh-018": ["P0A3F"], "veh-013": ["B1C42"], "veh-024": ["U0100"], "veh-002": ["C1095"], "veh-020": ["P1B10"] };
    const pool = ["P1E00", "P1B10", "P0562", "C1095", "C1A2F", "C1A30", "B1A01", "B1B05", "B1D20", "B1D21", "U0293"];
    db.vehicles.filter((v) => v.live && v.handover_at).forEach((v) => {
      const r = seeded(v.vehicle_id + "faults");
      const codes = fixed[v.vehicle_id] ? fixed[v.vehicle_id].slice() : [];
      if (!codes.length && r() < 0.4) codes.push(pool[Math.floor(r() * pool.length)]);
      if (r() < 0.25) codes.push(pool[Math.floor(r() * pool.length)]);
      [...new Set(codes)].forEach((code, i) => {
        const cleared = !fixed[v.vehicle_id] && r() < 0.3;
        const detected = db.NOW - Math.floor(r() * 6 * DAY) - (fixed[v.vehicle_id] && i === 0 ? 0 : HOUR);
        FAULTS.push({ fault_id: `flt-${v.vehicle_id}-${code}`, vehicle_id: v.vehicle_id, organization_id: v.organization_id, dtc: code, detected_at: new Date(fixed[v.vehicle_id] && i === 0 ? db.NOW - (10 + r() * 120) * MIN : detected).toISOString(), status: cleared ? "CLEARED" : "ACTIVE", cleared_at: cleared ? new Date(detected + (2 + r() * 20) * HOUR).toISOString() : null, cleared_reason: cleared ? "Đã xóa lỗi sau khi sửa" : null });
      });
    });
  })();
  const activeFaults = (vehicleId) => FAULTS.filter((f) => f.vehicle_id === vehicleId && f.status === "ACTIVE");

  /* Data frozen at alert time (MON-04 nearest station, MON-05 snapshot), keyed by notification. */
  const FROZEN = new Map();
  function frozenContext(n) {
    if (FROZEN.has(n.notification_id)) return FROZEN.get(n.notification_id);
    const v = db.vehicle(n.vehicle_id);
    const snapshot = {};
    if (v && v.live) {
      const p = n.payload || {};
      snapshot.soc_percent = p.soc !== undefined ? p.soc : Math.round(v.live.soc_percent);
      snapshot.lat = Number(v.live.lat.toFixed(4));
      snapshot.lon = Number(v.live.lon.toFixed(4));
      snapshot.road = v.live.road;
      snapshot.speed_kmh = Math.round(v.live.speed_kmh);
      snapshot.battery_temperature_celsius = v.live.battery_temperature_celsius;
      snapshot.pack_voltage_v = v.live.pack_voltage_v;
      snapshot.pack_current_a = v.live.pack_current_a;
      // The station named in the alert text wins over a fresh lookup, so the payload matches what was sent.
      const named = /(G3 [^,—]+?),? (\d+(?:\.\d+)?) km/.exec(n.title[0]);
      const near = nearestStation(v);
      if (p.station || named || near) {
        snapshot.nearest_station = p.station || (named ? named[1] : near.location.name);
        snapshot.nearest_station_km = p.distance_km !== undefined ? p.distance_km : named ? Number(named[2]) : round1(near.distance_km);
      }
    }
    FROZEN.set(n.notification_id, snapshot);
    return snapshot;
  }
  db.notifications.forEach(frozenContext);

  /* ---------- Trip helpers (MON-11/12/13) ---------- */
  const STOP_KINDS = { LOADING: ["Loading / unloading", "Bốc / dỡ hàng"], REST: ["Driver rest", "Tài xế nghỉ"], TRAFFIC: ["Traffic stop", "Dừng do kẹt xe"], PARKING: ["Parking", "Đỗ xe"] };
  function tripStops(trip) {
    const r = seeded(trip.trip_id + "stops");
    const names = Object.values(db.PLACES).map((p) => p.name).filter((n) => n !== trip.origin && n !== trip.destination);
    const start = trip.started_at ? new Date(trip.started_at).getTime() : 0;
    const end = trip.ended_at ? new Date(trip.ended_at).getTime() : start + HOUR;
    let left = trip.stop_minutes || 0;
    return Array.from({ length: trip.stops || 0 }, (_, i) => {
      const minutes = i === trip.stops - 1 ? left : Math.round(left * (0.3 + r() * 0.4));
      left -= minutes;
      return { place: names[Math.floor(r() * names.length)], kind: Object.keys(STOP_KINDS)[Math.floor(r() * 4)], minutes: Math.max(2, minutes), at: new Date(start + ((i + 1) / (trip.stops + 1)) * (end - start)).toISOString() };
    });
  }
  const isMismatch = (trip) => trip.declared_load && trip.inferred_load && trip.declared_load !== trip.inferred_load;
  function loadCell(trip) {
    if (!trip.declared_load) return '<span class="muted">—</span>';
    const inferred = trip.inferred_load ? ui.badge(trip.inferred_load, trip.inferred_load === "LOADED" ? "ok" : "muted") : '<span class="muted small">' + esc(t("pending", "đang tính")) + "</span>";
    return `<span class="small">${esc(G3.label(trip.declared_load))} → </span>${inferred}${isMismatch(trip) ? ` <span class="badge danger" title="${esc(t("Declared load differs from what consumption shows (MON-13)", "Tải khai báo khác với tải suy ra từ điện năng (MON-13)"))}">${esc(t("Mismatch", "Lệch"))}</span>` : ""}`;
  }
  function tripColumns(withVehicle) {
    return [
      { label: t("Trip", "Chuyến"), cls: "nowrap", render: (r) => `<b class="mono">${esc(r.trip_id)}</b><span class="sub">${ui.badge(r.status)}</span>` },
      { label: withVehicle ? t("Truck · driver", "Xe · tài xế") : t("Driver", "Tài xế"), cls: "nowrap", render: (r) => (withVehicle ? `${vehicleLink(db.vehicle(r.vehicle_id))}<span class="sub">${driverLink(r.driver_id)}</span>` : driverLink(r.driver_id)) },
      { label: t("Route", "Tuyến"), render: (r) => `<span class="nowrap">${esc(r.origin)}</span><span class="sub nowrap">→ ${esc(r.destination)}</span>` },
      { label: t("Departure", "Khởi hành"), cls: "nowrap", render: (r) => `${dm(new Date(r.planned_departure_at).getTime())} ${hm(r.planned_departure_at)}<span class="sub">${r.started_at ? `${esc(t("started", "bắt đầu"))} ${hm(r.started_at)}` : esc(t("planned", "dự kiến"))}</span>` },
      { label: t("Distance", "Quãng đường"), align: "right", cls: "nowrap", render: (r) => (r.distance_km ? `${fmt.num(r.distance_km, 1)} km<span class="sub">${fmt.num(r.energy_kwh, 1)} kWh</span>` : "—") },
      { label: "kWh/km", align: "right", render: (r) => (r.distance_km ? fmt.num(r.energy_kwh / r.distance_km, 2) : "—") },
      { label: t("Cost", "Chi phí"), align: "right", cls: "nowrap", render: (r) => (r.cost_vnd ? fmt.vnd(r.cost_vnd) : "—") },
      { label: t("Load", "Tải hàng"), render: loadCell },
      { label: t("Stops", "Điểm dừng"), align: "right", cls: "nowrap", render: (r) => (r.stops ? `${r.stops}<span class="sub">${fmt.duration(r.stop_minutes)}</span>` : r.status === "COMPLETED" ? "0" : "—") },
    ];
  }
  function openTrip(tripId) {
    const trip = db.trips.find((x) => x.trip_id === tripId);
    if (!trip) return;
    const v = db.vehicle(trip.vehicle_id);
    const model = db.model(v.vehicle_model_id);
    const expected = trip.declared_load === "LOADED" ? model.consumption_loaded_kwh_km : model.consumption_empty_kwh_km;
    const rate = trip.distance_km ? trip.energy_kwh / trip.distance_km : null;
    const delay = trip.started_at ? Math.round((new Date(trip.started_at) - new Date(trip.planned_departure_at)) / MIN) : null;
    const arrDelay = trip.ended_at ? Math.round((new Date(trip.ended_at) - new Date(trip.planned_arrival_at)) / MIN) : null;
    const delayText = (m) => (m === null ? "" : `<span class="sub" style="color:var(--${m > 15 ? "danger" : m > 0 ? "warn" : "ok"})">${m > 0 ? "+" : ""}${m} ${esc(t("min", "phút"))}</span>`);
    const stops = tripStops(trip);
    const body = `
      ${isMismatch(trip) ? ui.callout("warn", esc(t(`Declared ${G3.label(trip.declared_load)}, but consumption (${fmt.num(rate, 2)} kWh/km vs ${fmt.num(expected, 2)} expected) shows ${G3.label(trip.inferred_load)}. Check the delivery note (MON-13).`, `Khai báo ${G3.label(trip.declared_load)}, nhưng điện năng (${fmt.num(rate, 2)} kWh/km so với ${fmt.num(expected, 2)} dự kiến) cho thấy ${G3.label(trip.inferred_load)}. Kiểm tra phiếu giao hàng (MON-13).`))) + '<div style="height:12px"></div>' : ""}
      <div class="grid cols-2">
        <div>${ui.kv([
          [t("Status", "Trạng thái"), ui.badge(trip.status) + (trip.status_reason ? ` <span class="small muted">${esc(trip.status_reason)}</span>` : "")],
          [t("Truck", "Xe"), `${vehicleLink(v)} <span class="small muted">${esc(model.code)}</span>`],
          [t("Driver", "Tài xế"), driverLink(trip.driver_id)],
          [t("Route", "Tuyến"), `${esc(trip.origin)} → ${esc(trip.destination)}`],
          [t("Load", "Tải"), loadCell(trip)],
        ])}</div>
        <div>${ui.kv([
          [t("Planned", "Kế hoạch"), `${fmt.dateTime(trip.planned_departure_at)} → ${hm(trip.planned_arrival_at)}`],
          [t("Started", "Bắt đầu"), trip.started_at ? `${fmt.dateTime(trip.started_at)} ${delayText(delay)}` : null],
          [t("Finished", "Kết thúc"), trip.ended_at ? `${fmt.dateTime(trip.ended_at)} ${delayText(arrDelay)}` : null],
          [t("Distance · energy", "Quãng đường · điện"), trip.distance_km ? `${fmt.km(trip.distance_km, 1)} · ${fmt.kwh(trip.energy_kwh)}` : null],
          [t("Consumption", "Tiêu thụ"), rate ? `${fmt.num(rate, 2)} kWh/km <span class="small muted">(${esc(t("model", "mẫu xe"))} ${fmt.num(expected, 2)})</span>` : null],
          [t("Electricity cost", "Tiền điện"), trip.cost_vnd ? fmt.vnd(trip.cost_vnd) : null],
        ])}</div>
      </div>
      <h4 class="mt">${esc(t("Stops and parking", "Điểm dừng và đỗ"))} ${ui.featureChips(["MON-12"])}</h4>
      ${stops.length ? ui.table({ rows: stops, columns: [
        { label: t("Time", "Thời gian"), render: (s) => hm(s.at) },
        { label: t("Place", "Địa điểm"), render: (s) => esc(s.place) },
        { label: t("Kind", "Loại"), render: (s) => esc(t(...STOP_KINDS[s.kind])) },
        { label: t("Duration", "Thời lượng"), align: "right", render: (s) => fmt.duration(s.minutes) },
      ] }) : `<p class="small muted">${esc(trip.status === "COMPLETED" ? t("No stop longer than 3 minutes.", "Không có điểm dừng quá 3 phút.") : t("Stops are recorded while the trip runs.", "Điểm dừng được ghi khi chuyến đang chạy."))}</p>`}`;
    const canCancel = trip.status === "PLANNED" && canDispatch();
    ui.modal({
      title: `${t("Trip", "Chuyến")} ${trip.trip_id}`, wide: true, body,
      foot: `${canCancel ? `<button class="btn danger" data-cancel-trip>${esc(t("Cancel trip", "Hủy chuyến"))}</button>` : ""}<button class="btn" data-close>${esc(t("Close", "Đóng"))}</button>`,
      onMount(el, close) {
        const b = el.querySelector("[data-cancel-trip]");
        if (b) b.addEventListener("click", () => {
          close();
          ui.reasonModal({
            title: t(`Cancel trip ${trip.trip_id}`, `Hủy chuyến ${trip.trip_id}`), submitLabel: t("Cancel trip", "Hủy chuyến"),
            onSubmit(reason) { trip.status = "CANCELLED"; trip.status_reason = reason; G3.render(); ui.toast(t("Trip cancelled; the driver is notified", "Đã hủy chuyến; tài xế đã được báo")); },
          });
        });
      },
    });
  }
  function bindTripRows(root) {
    root.querySelectorAll("[data-trip]").forEach((tr) => tr.addEventListener("click", (e) => { if (!e.target.closest("a, button")) openTrip(tr.dataset.trip); }));
  }

  /* ---------- Warranty helpers (WAR-01) ---------- */
  /** Usage and the expiry computed from the date and the counter, whichever comes first. */
  function warrantyUsage(w) {
    const start = new Date(w.starts_on).getTime();
    const end = new Date(w.ends_on).getTime();
    const timePct = Math.max(0, Math.min(100, ((db.NOW - start) / (end - start)) * 100));
    let used = null, limit = null, unit = "";
    if (w.object_kind === "VEHICLE" && w.distance_limit_km) { const v = db.vehicle(w.object_id); used = v ? v.odometer_km : 0; limit = w.distance_limit_km; unit = "km"; }
    if (w.object_kind === "BATTERY" && w.cycle_limit) { const b = db.battery(w.object_id); used = b ? b.cycle_count : 0; limit = w.cycle_limit; unit = t(" cycles", " chu kỳ"); }
    if (w.object_kind === "STATION" && w.energy_limit_kwh) { const s = db.station(w.object_id); used = s ? s.energy_delivered_kwh_total : 0; limit = w.energy_limit_kwh; unit = " kWh"; }
    let expires = end, by = "DATE";
    if (used !== null && used > 0) {
      const perDay = used / Math.max(1, (db.NOW - start) / DAY);
      const projected = db.NOW + ((limit - used) / perDay) * DAY;
      if (projected < end) { expires = projected; by = "COUNTER"; }
    }
    return { timePct, used, limit, unit, counterPct: limit ? Math.min(100, (used / limit) * 100) : null, expires, by };
  }
  function warrantyRows(rows) {
    return ui.table({
      rows, empty: t("No warranty on file", "Chưa có bảo hành"),
      columns: [
        { label: t("Object", "Đối tượng"), render: (w) => `<b>${esc(G3.label(w.object_kind))}</b><span class="sub">${esc(G3.label(w.warranty_type))}</span>` },
        { label: t("Period", "Thời hạn"), render: (w) => `${fmt.date(w.starts_on)} → ${fmt.date(w.ends_on)}<div style="margin-top:4px">${ui.meter(warrantyUsage(w).timePct)}</div>` },
        { label: t("Limit used", "Giới hạn đã dùng"), render: (w) => { const u = warrantyUsage(w); return u.limit ? `${fmt.num(u.used)} / ${fmt.num(u.limit)}${esc(u.unit)}<div style="margin-top:4px">${ui.meter(u.counterPct, u.counterPct > 85 ? "danger" : u.counterPct > 65 ? "warn" : "")}</div>` : '<span class="muted small">' + esc(t("Time only", "Chỉ theo thời gian")) + "</span>"; } },
        { label: t("Expires", "Hết hạn"), render: (w) => { const u = warrantyUsage(w); return `<b>${fmt.date(u.expires)}</b><span class="sub">${esc(u.by === "COUNTER" ? t("limit reached first", "chạm giới hạn trước") : t("by date", "theo ngày"))}</span>`; } },
        { label: t("Status", "Trạng thái"), render: (w) => `${ui.badge(w.status)}${w.status_reason ? `<span class="sub">${esc(w.status_reason)}</span>` : ""}` },
      ],
    });
  }

  /* ==========================================================
     Vehicle detail
     ========================================================== */
  function liveHtml(v, key) {
    const live = v.live;
    if (key === "odo") return fmt.km(v.odometer_km);
    if (!live) return '<span class="muted">—</span>';
    if (key === "soc") return ui.soc(live.soc_percent);
    if (key === "socnum") return fmt.num(live.soc_percent);
    if (key === "speed") return fmt.num(live.speed_kmh);
    if (key === "temp") return `<span style="${live.battery_temperature_celsius >= 45 ? "color:var(--danger)" : ""}">${fmt.num(live.battery_temperature_celsius)} °C</span>`;
    if (key === "ago") return esc(fmt.ago(live.received_at));
    if (key === "pos") return `<span class="mono small">${live.lat.toFixed(4)}, ${live.lon.toFixed(4)}</span> · ${esc(live.road)}`;
    if (key === "state") return ui.vehicleState(v);
    if (key === "power") return `${fmt.num(live.pack_voltage_v)} V · ${fmt.num(live.pack_current_a)} A`;
    return "";
  }
  const live = (v, key) => `<span data-live="${key}">${liveHtml(v, key)}</span>`;

  function vehicleNotFound(id) {
    return `${ui.pageHead({ title: t("Vehicle not found", "Không tìm thấy xe"), crumbs: G3.canSee("vehicles") ? [{ label: t("Vehicles", "Hồ sơ xe"), href: "#/vehicles" }, { label: id }] : null })}
      ${ui.callout("warn", esc(t("This truck does not exist or belongs to an organization (or a fleet) you cannot see. The backend answers 404 for another organization's truck, so its existence is not revealed (ID-50, FLT-03).", "Xe không tồn tại hoặc thuộc tổ chức (hay đội xe) mà bạn không được xem. Backend trả 404 cho xe của tổ chức khác nên không lộ sự tồn tại của xe (ID-50, FLT-03).")), "lock")}
      <div class="row mt">${G3.canSee("vehicles") ? ui.btn(t("Back to vehicles", "Về danh sách xe"), { href: "#/vehicles", icon: "truck" }) : ""}${G3.canSee("live-map") ? ui.btn(t("Live map", "Bản đồ"), { href: "#/map", icon: "map" }) : ""}</div>`;
  }

  function overviewTab(v) {
    const series = v.live ? db.socSeries(v.vehicle_id, 24) : [];
    const driver = db.driver(v.driver_id);
    const session = db.driving_sessions.find((s) => s.vehicle_id === v.vehicle_id && !s.ended_at);
    const alerts = scope.byVehicle(db.notifications).filter((n) => n.vehicle_id === v.vehicle_id).slice(0, 6);
    const near = nearestStation(v);
    const driving = v.live && v.live.is_online && v.live.state === "DRIVING";
    const faults = activeFaults(v.vehicle_id);
    const slot = near ? slotAt(near.location.location_id, Date.now()) : null;

    const driverBody = driver
      ? `${ui.kv([
          [t("Driver", "Tài xế"), driverLink(driver.driver_id)],
          [t("Phone", "Điện thoại"), `<span class="mono">${esc(driver.phone_number)}</span>`],
          [t("Checked in", "Nhận xe"), session ? `${fmt.ago(session.checked_in_at)} · ${esc(G3.label(session.check_in_method))}` : "—"],
          [t("Licence", "Bằng lái"), `${esc(driver.license_class)} · ${esc(t("expires", "hết hạn"))} ${fmt.date(driver.license_expires_on)}`],
        ])}${canDispatch() ? `<div class="row mt">${ui.btn(t("End check-in", "Kết thúc nhận xe"), { action: "end-checkin", sm: true, icon: "logout" })}</div>` : ""}`
      : `${driving ? ui.callout("danger", esc(t("Driving with nobody checked in. The fleet manager was alerted (DRV-02).", "Xe đang chạy nhưng chưa có tài xế nhận xe. Quản lý đội đã được cảnh báo (DRV-02)."))) : `<p class="muted small">${esc(t("Nobody is checked in to this truck.", "Chưa có tài xế nhận xe này."))}</p>`}
         ${canDispatch() && v.status === "ACTIVE" && v.live ? `<div class="row mt">${ui.btn(t("Check in a driver", "Gán tài xế nhận xe"), { action: "checkin", sm: true, kind: "primary", icon: "id" })}</div>` : ""}`;

    return `
      <div class="grid cols-5">
        ${ui.kpi({ label: t("Battery", "Pin"), icon: "battery", value: live(v, "socnum"), unit: "%", foot: live(v, "soc") })}
        ${ui.kpi({ label: t("Speed", "Tốc độ"), icon: "gauge", value: live(v, "speed"), unit: "km/h", foot: live(v, "state") })}
        ${ui.kpi({ label: t("Battery temperature", "Nhiệt độ pin"), icon: "thermometer", value: live(v, "temp"), foot: live(v, "power") })}
        ${ui.kpi({ label: t("Odometer", "Số km"), icon: "route", value: live(v, "odo"), foot: `SOH ${fmt.pct(v.soh_percent, 1)} · ${fmt.num(v.cycle_count)} ${t("cycles", "chu kỳ")}` })}
        ${ui.kpi({ label: t("Last data", "Dữ liệu cuối"), icon: "signal", value: live(v, "ago"), foot: v.live ? (v.live.is_online ? t("T-Box online", "T-Box trực tuyến") : t("T-Box silent — values are the last received", "T-Box im lặng — giá trị là lần nhận cuối")) : t("No data yet", "Chưa có dữ liệu") })}
      </div>
      <div class="grid side mt">
        <div class="stack">
          ${ui.card({
            title: t("Battery over the last 24 h", "Pin trong 24 giờ qua"), sub: "MON-02",
            body: series.length ? ui.lineChart({ labels: series.map((p) => hm(p.at)), series: [{ name: t("Battery", "Pin"), values: series.map((p) => p.soc_percent), area: true }], yMin: 0, yMax: 100, unit: "%", height: 200 }) : ui.empty(t("No telemetry received yet", "Chưa nhận dữ liệu")),
          })}
          ${ui.card({
            title: t("Position", "Vị trí"), sub: live(v, "pos"), flush: true,
            actions: G3.canSee("live-map") ? ui.btn(t("Live map", "Bản đồ"), { href: `#/map?v=${v.vehicle_id}`, sm: true, kind: "ghost", icon: "map" }) : "",
            body: v.live ? ui.map({ vehicles: [v], locations: reachableLocations(), geofences: scope.filter(db.geofences), selectedVehicleId: v.vehicle_id, height: 300, legend: false, id: "veh-mini" }) : ui.empty(),
          })}
        </div>
        <div class="stack">
          ${ui.card({ title: t("Driver checked in", "Tài xế đang nhận xe"), sub: "DRV-02", body: driverBody })}
          ${ui.card({
            title: t("Nearest station", "Trạm sạc gần nhất"), sub: "MON-04",
            body: near ? `${ui.kv([
              [t("Station", "Trạm"), G3.canSee("station-detail") ? `<a href="#/stations/${near.location.location_id}">${esc(near.location.name)}</a>` : esc(near.location.name)],
              [t("Distance", "Khoảng cách"), `${fmt.num(near.distance_km, 1)} km`],
              [t("Free connectors", "Súng còn trống"), near.free ? `<b style="color:var(--ok)">${near.free}</b>` : `<span style="color:var(--danger)">0</span>`],
              [t("Price now", "Giá hiện tại"), `${fmt.vnd(slot.price_vnd_kwh)}/kWh · ${esc(slotLabel(slot.label))}`],
            ])}` : ui.empty(),
          })}
          ${ui.card({
            title: t("Recent alerts", "Cảnh báo gần đây"), sub: "MON-04 · MON-05 · MON-06", flush: true,
            actions: ui.btn(t("All", "Tất cả"), { href: `#/notifications?vehicle=${v.vehicle_id}`, sm: true, kind: "ghost" }),
            body: alerts.length ? `<ul class="list">${alerts.map((n) => `<li class="clickable ${n.read_at ? "" : "unread"}" data-href="/notifications?n=${n.notification_id}"><div class="sev-icon ${n.severity}">${ui.icon(notifIcon(n), 16)}</div><div class="grow"><div class="title">${esc(t(n.title[0], n.title[1]))}</div><div class="meta">${esc(G3.label(n.notification_type))} · ${fmt.ago(n.created_at)}</div></div></li>`).join("")}</ul>` : ui.empty(t("No alerts for this truck", "Xe chưa có cảnh báo")),
          })}
          ${faults.length ? ui.card({
            title: t("Active component faults", "Lỗi linh kiện đang có"), sub: "MON-06", flush: true,
            body: `<ul class="list">${faults.map((f) => { const d = dtc(f.dtc); return `<li><div class="sev-icon ${d.severity}">${ui.icon("wrench", 16)}</div><div class="grow"><div class="title"><span class="mono">${esc(d.code)}</span> ${esc(t(...d.description))}</div><div class="meta">${esc(compLabel(d.component))} · ${fmt.ago(f.detected_at)}</div></div>${drivableBadge(d.drivable)}</li>`; }).join("")}</ul>`,
          }) : ""}
        </div>
      </div>`;
  }

  function routeStats(route) {
    let km = 0, moving = 0, stops = 0, maxSpeed = 0, stopped = false;
    route.forEach((p, i) => {
      if (i) km += distKm(route[i - 1], p);
      if (p.speed_kmh > 0) { moving += 5; stopped = false; } else if (!stopped) { stops++; stopped = true; }
      maxSpeed = Math.max(maxSpeed, p.speed_kmh);
    });
    return { km, moving, stops, maxSpeed };
  }

  /**
   * Route of the last hours from db.routeHistory. When the truck sits near the
   * start of its road the core history covers almost no distance; the points
   * are then laid back and forth along the same road so the replay stays
   * readable (times, speeds and battery are kept).
   */
  function replayRoute(v, hours) {
    const route = db.routeHistory(v.vehicle_id, hours);
    if (route.length < 2 || routeStats(route).km > 5) return route;
    const road = db.ROADS[v.live.road_index];
    const span = Math.min(1.6, 0.12 * hours);
    const end = v.live.road_f;
    return route.map((p, i) => {
      const x = end - span * (1 - i / (route.length - 1));
      const g = ((x % 2) + 2) % 2;
      const at = db.pointOnRoad(road, g > 1 ? 2 - g : g);
      return { ...p, lat: at.lat, lon: at.lon };
    });
  }

  function routeTab(v, query) {
    const hours = ["2", "8", "24"].includes(query.hours) ? Number(query.hours) : 8;
    const route = replayRoute(v, hours);
    const segment = ui.segmented([["2", t("Last 2 h", "2 giờ qua")], ["8", t("Last 8 h", "8 giờ qua")], ["24", t("Last 24 h", "24 giờ qua")]], String(hours), "hours");
    if (route.length < 2) return `<div class="toolbar card mb">${segment}</div>${ui.empty(t("No position history for this truck yet", "Chưa có lịch sử vị trí của xe"))}`;
    const s = routeStats(route);
    const last = route[route.length - 1];
    return `
      ${ui.callout("info", esc(t("Location history is kept at least 6 months (MON-10). Opening the replay and every export are written to the access audit log (ACC-18).", "Lịch sử vị trí được lưu ít nhất 6 tháng (MON-10). Mở xem lại lộ trình và mỗi lần xuất dữ liệu đều được ghi vào nhật ký truy cập (ACC-18).")))}
      <div class="grid cols-4 mt">
        ${ui.kpi({ label: t("Distance", "Quãng đường"), icon: "route", value: fmt.num(s.km, 1), unit: "km", foot: `${hours} ${t("hours", "giờ")} · ${route.length} ${t("points", "điểm")}` })}
        ${ui.kpi({ label: t("Moving time", "Thời gian di chuyển"), icon: "clock", value: fmt.duration(s.moving), foot: `${s.stops} ${t("stops", "lần dừng")}` })}
        ${ui.kpi({ label: t("Max speed", "Tốc độ tối đa"), icon: "gauge", value: fmt.num(s.maxSpeed), unit: "km/h" })}
        ${ui.kpi({ label: t("Battery used", "Pin đã dùng"), icon: "battery", value: fmt.num(route[0].soc_percent - last.soc_percent), unit: "%", foot: `${route[0].soc_percent}% → ${last.soc_percent}%` })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Route replay", "Xem lại lộ trình"), sub: `${fmt.dateTime(route[0].at)} → ${hm(last.at)}`, flush: true,
          actions: `<div class="row">${segment}${ui.btn(t("Export", "Xuất"), { icon: "download", action: "export-route", sm: true })}</div>`,
          body: ui.map({ route, locations: reachableLocations(), height: 548, legend: false, id: "replay" }),
        })}
        <div class="stack">
          ${ui.card({
            title: t("Playback", "Phát lại"),
            body: `<div class="row mb">${ui.btn(t("Play", "Phát"), { icon: "chevron", action: "play", kind: "primary", sm: true })}<span class="small muted" data-cursor-time>${fmt.dateTime(route[0].at)}</span></div>
              <input type="range" min="0" max="${route.length - 1}" value="0" step="1" data-route-slider style="width:100%">
              <div class="divider"></div>
              <div data-cursor-info></div>`,
          })}
          ${ui.card({ title: t("Speed and battery", "Tốc độ và pin"), sub: "km/h · %", body: ui.lineChart({ labels: route.map((p) => hm(p.at)), series: [{ name: t("Speed", "Tốc độ"), values: route.map((p) => p.speed_kmh), color: ui.PALETTE[1] }, { name: t("Battery", "Pin"), values: route.map((p) => p.soc_percent), color: ui.PALETTE[0] }], yMin: 0, yMax: 100, height: 180 }) + ui.legend([[t("Speed (km/h)", "Tốc độ (km/h)"), ui.PALETTE[1]], [t("Battery (%)", "Pin (%)"), ui.PALETTE[0]]]) })}
        </div>
      </div>`;
  }

  function cellColor(devMv) {
    if (devMv <= -60) return "#ef4444";
    if (devMv <= -30) return "#fca5a5";
    if (devMv <= -15) return "#fde68a";
    if (devMv >= 30) return "#93c5fd";
    if (devMv >= 15) return "#bfdbfe";
    return "#a7f3d0";
  }
  function cellAnalysis(v) {
    const cells = db.cellSnapshot(v.vehicle_id);
    const mean = sum(cells, (c) => c.voltage_v) / cells.length;
    const withDev = cells.map((c) => ({ ...c, dev_mv: Math.round((c.voltage_v - mean) * 1000) }));
    const weak = withDev.filter((c) => c.dev_mv <= -60 || c.soh_percent < v.soh_percent - 5);
    const volts = cells.map((c) => c.voltage_v);
    return { cells: withDev, mean, weak, imbalanceMv: Math.round((Math.max(...volts) - Math.min(...volts)) * 1000) };
  }
  const CELL_PRICE_VND = 2600000;
  const MODULE_LABOUR_VND = 3500000;
  const BALANCE_TEST_VND = 2000000;
  const PACK_PRICE_VND_KWH = 2900000;

  function batteryTab(v) {
    const battery = db.battery(v.battery_id);
    if (!battery) return ui.card({ title: t("Battery", "Pin"), body: ui.empty(t("No battery pack is fitted to this truck.", "Xe chưa được lắp pin.")) });
    const bm = db.find("battery_models", battery.battery_model_id);
    const soh = db.sohSeries(v.vehicle_id, 180);
    const lo = Math.floor(Math.min(90, ...soh.map((p) => p.soh_percent)) - 1);
    const a = cellAnalysis(v);
    const usable = bm ? bm.capacity_kwh * (v.soh_percent / 100) : null;
    const modules = [...new Set(a.weak.map((c) => Math.ceil(c.cell / 16)))];
    const repairCost = a.weak.length * CELL_PRICE_VND + modules.length * MODULE_LABOUR_VND + BALANCE_TEST_VND;
    const packCost = bm ? bm.capacity_kwh * PACK_PRICE_VND_KWH : 0;
    const safety = scope.byVehicle(db.notifications).filter((n) => n.vehicle_id === v.vehicle_id && ["ANOMALY_ALERT", "SOH_ALERT"].includes(n.notification_type));
    const months = battery && battery.installed_at ? Math.max(1, (db.NOW - new Date(battery.installed_at)) / (30 * DAY)) : 1;
    const shown = a.cells.length;

    return `
      <div class="grid cols-5">
        ${ui.kpi({ label: t("State of health", "Sức khỏe pin (SOH)"), icon: "battery", value: fmt.num(v.soh_percent, 1), unit: "%", tone: v.soh_percent < 90 ? "danger" : undefined, foot: v.soh_percent < 90 ? t("Below the 90% threshold", "Dưới ngưỡng 90%") : `${t("Threshold", "Ngưỡng")} 90%` })}
        ${ui.kpi({ label: t("Charge cycles", "Chu kỳ sạc"), icon: "refresh", value: fmt.num(v.cycle_count), foot: `≈ ${fmt.num(v.cycle_count / months, 0)} ${t("per month", "mỗi tháng")}` })}
        ${ui.kpi({ label: t("Usable capacity", "Dung lượng khả dụng"), icon: "bolt", value: usable ? fmt.num(usable, 0) : "—", unit: "kWh", foot: bm ? `${t("of", "trên")} ${bm.capacity_kwh} kWh · −${fmt.num(bm.capacity_kwh - usable, 0)} kWh` : "" })}
        ${ui.kpi({ label: t("Cell imbalance", "Lệch áp cell"), icon: "activity", value: a.imbalanceMv, unit: "mV", tone: a.imbalanceMv > 80 ? "danger" : a.imbalanceMv > 40 ? "warn" : undefined, foot: `${t("mean", "trung bình")} ${fmt.num(a.mean, 3)} V` })}
        ${ui.kpi({ label: t("Weak cells", "Cell yếu"), icon: "alert", value: a.weak.length, tone: a.weak.length ? "danger" : "ok", foot: modules.length ? `${t("modules", "mô-đun")} ${modules.map((m) => "M" + m).join(", ")}` : t("None", "Không có") })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("State of health over 180 days", "SOH trong 180 ngày"), sub: "MON-07",
          actions: ui.legend([["SOH", ui.PALETTE[0]], [t("Alert threshold 90%", "Ngưỡng cảnh báo 90%"), "#dc2626"]]),
          body: ui.lineChart({ labels: soh.map((p) => dm(new Date(p.date).getTime())), series: [{ name: "SOH", values: soh.map((p) => p.soh_percent), area: true }, { name: t("Threshold", "Ngưỡng"), values: soh.map(() => 90), color: "#dc2626", dashed: true }], yMin: lo, yMax: 100, unit: "%", yFormat: (x) => fmt.num(x, 0), height: 230 }),
        })}
        ${ui.card({
          title: t("Battery fitted", "Pin đang lắp"), sub: "BAT-01",
          actions: G3.canSee("batteries") ? ui.btn(t("Battery registry", "Hồ sơ pin"), { href: "#/batteries", sm: true, kind: "ghost" }) : "",
          body: battery ? ui.kv([
            [t("Serial", "Số serial"), `<span class="mono">${esc(battery.battery_serial)}</span>`],
            [t("Model", "Mẫu pin"), `${esc(bm.name)} · ${esc(bm.layout)}`],
            [t("Chemistry", "Hóa học"), `${esc(bm.chemistry)} · ${bm.nominal_voltage_v} V`],
            [t("Manufactured", "Sản xuất"), fmt.date(battery.manufactured_on)],
            [t("Fitted", "Lắp đặt"), fmt.date(battery.installed_at)],
            [t("Owner", "Chủ sở hữu"), battery.organization_id !== v.organization_id ? `${ui.orgName(battery.organization_id)} <span class="tag">${esc(t("leased (VaaS)", "thuê pin (VaaS)"))}</span>` : ui.orgName(battery.organization_id)],
          ]) : ui.empty(),
        })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Cell voltages", "Điện áp từng cell"), sub: `MON-08 · ${shown} ${t("cells", "cell")}${bm && bm.cells > shown ? ` ${t("of", "trên")} ${bm.cells}` : ""} · ${t("one row = one 16-cell module", "mỗi hàng = một mô-đun 16 cell")}`,
          actions: ui.legend([[t("Weak", "Yếu"), "#ef4444"], [t("Low", "Thấp"), "#fca5a5"], [t("Slightly low", "Hơi thấp"), "#fde68a"], [t("Balanced", "Cân bằng"), "#a7f3d0"], [t("High", "Cao"), "#93c5fd"]]),
          body: `<div class="cell-grid">${a.cells.map((c) => `<div style="background:${cellColor(c.dev_mv)};${c.dev_mv <= -60 ? "color:#fff;font-weight:700;outline:2px solid #991b1b" : ""}" data-tip="${esc(`${t("Cell", "Cell")} ${c.cell} (M${Math.ceil(c.cell / 16)}): ${fmt.num(c.voltage_v, 3)} V (${c.dev_mv > 0 ? "+" : ""}${c.dev_mv} mV) · SOH ${fmt.num(c.soh_percent, 1)}% · ${c.temperature_celsius} °C`)}">${c.cell}</div>`).join("")}</div>`,
        })}
        ${ui.card({
          title: t("Repair recommendation", "Khuyến nghị sửa chữa"), sub: "MON-08",
          body: a.weak.length ? `${ui.callout("warn", esc(t(`Replace ${a.weak.length} weak cell(s) in ${modules.length} module(s) instead of the whole pack.`, `Thay ${a.weak.length} cell yếu trong ${modules.length} mô-đun thay vì thay cả khối pin.`)), "wrench")}
            ${ui.table({ rows: a.weak, columns: [
              { label: "Cell", render: (c) => `<b>${c.cell}</b> <span class="small muted">M${Math.ceil(c.cell / 16)}</span>` },
              { label: "V", align: "right", render: (c) => fmt.num(c.voltage_v, 3) },
              { label: "mV", align: "right", render: (c) => `<span style="color:var(--danger)">${c.dev_mv}</span>` },
              { label: "SOH", align: "right", render: (c) => fmt.pct(c.soh_percent, 1) },
            ] })}
            <div class="mt">${ui.kv([
              [t("Cells", "Cell"), `${a.weak.length} × ${fmt.vnd(CELL_PRICE_VND)}`],
              [t("Module labour", "Công tháo mô-đun"), `${modules.length} × ${fmt.vnd(MODULE_LABOUR_VND)}`],
              [t("Balancing & test", "Cân bằng & kiểm tra"), fmt.vnd(BALANCE_TEST_VND)],
              [t("Estimate", "Dự toán"), `<b>${fmt.vnd(repairCost)}</b>`],
              [t("New pack instead", "Nếu thay cả pin"), `${fmt.vndShort(packCost)} · ${t("saves", "tiết kiệm")} <b style="color:var(--ok)">${fmt.pct((1 - repairCost / packCost) * 100, 1)}</b>`],
            ])}</div>
            ${G3.canSee("maintenance") ? `<div class="row mt">${ui.btn(t("Open a repair order", "Tạo lệnh sửa chữa"), { href: "#/maintenance", sm: true, icon: "wrench" })}</div>` : ""}`
            : ui.callout("ok", esc(t(`No weak cell. Imbalance ${a.imbalanceMv} mV is within the 80 mV limit; no repair needed.`, `Không có cell yếu. Lệch áp ${a.imbalanceMv} mV trong giới hạn 80 mV; không cần sửa.`))),
        })}
      </div>
      ${safety.length ? `<div class="mt">${ui.card({ title: t("Battery safety and health alerts", "Cảnh báo an toàn và sức khỏe pin"), sub: "MON-05 · MON-07", flush: true, body: `<ul class="list">${safety.map((n) => `<li class="clickable" data-href="/notifications?n=${n.notification_id}"><div class="sev-icon ${n.severity}">${ui.icon("battery", 16)}</div><div class="grow"><div class="title">${esc(t(n.title[0], n.title[1]))}</div><div class="meta">${fmt.dateTime(n.created_at)}</div></div>${ui.badge(n.severity)}</li>`).join("")}</ul>` })}</div>` : ""}`;
  }

  function tripsTab(v) {
    const trips = scope.byVehicle(db.trips, false).filter((x) => x.vehicle_id === v.vehicle_id).sort((a, b) => (a.planned_departure_at < b.planned_departure_at ? 1 : -1));
    const done = trips.filter((x) => x.status === "COMPLETED");
    const km = sum(done, (x) => x.distance_km);
    const kwh = sum(done, (x) => x.energy_kwh);
    const emptyKm = sum(done.filter((x) => x.inferred_load === "EMPTY"), (x) => x.distance_km);
    return `
      <div class="grid cols-4">
        ${ui.kpi({ label: t("Completed trips (14 d)", "Chuyến hoàn tất (14 ngày)"), icon: "route", value: done.length, foot: `${trips.filter((x) => x.status === "PLANNED").length} ${t("planned", "đã lên lịch")}` })}
        ${ui.kpi({ label: t("Distance", "Quãng đường"), icon: "truck", value: fmt.num(km), unit: "km", foot: fmt.vnd(sum(done, (x) => x.cost_vnd)) })}
        ${ui.kpi({ label: t("Average consumption", "Tiêu thụ trung bình"), icon: "bolt", value: fmt.num(km ? kwh / km : 0, 2), unit: "kWh/km", foot: fmt.kwh(kwh, 0) })}
        ${ui.kpi({ label: t("Empty running", "Chạy rỗng"), icon: "activity", value: fmt.pct(km ? (emptyKm / km) * 100 : 0), foot: `${done.filter(isMismatch).length} ${t("load mismatches", "lệch tải")} · MON-13` })}
      </div>
      <div class="mt">${ui.card({
        title: t("Trips of this truck", "Chuyến của xe"), sub: "MON-11 · MON-12 · MON-13", flush: true,
        actions: G3.canSee("trips") ? ui.btn(t("All trips", "Mọi chuyến"), { href: `#/trips?vehicle=${v.vehicle_id}`, sm: true, kind: "ghost" }) : "",
        body: ui.table({ rows: trips, columns: tripColumns(false), rowAttrs: (r) => `data-trip="${r.trip_id}" style="cursor:pointer"`, empty: t("No trip recorded", "Chưa có chuyến") }),
      })}</div>`;
  }

  function chargingTab(v) {
    const sessions = scope.byVehicle(db.charging_sessions, false).filter((s) => s.vehicle_id === v.vehicle_id);
    const done = sessions.filter((s) => s.status === "COMPLETED");
    const kwh = sum(done, (s) => s.energy_kwh);
    const cost = sum(done, (s) => s.cost_vnd);
    const offPeak = done.filter((s) => ["OFF_PEAK", "SOLAR"].includes(slotAt(s.location_id, s.started_at).label));
    return `
      <div class="grid cols-4">
        ${ui.kpi({ label: t("Sessions (30 d)", "Phiên sạc (30 ngày)"), icon: "plug", value: sessions.length, foot: `${sessions.filter((s) => s.status === "ACTIVE").length} ${t("charging now", "đang sạc")}` })}
        ${ui.kpi({ label: t("Energy", "Điện năng"), icon: "bolt", value: fmt.num(kwh, 0), unit: "kWh" })}
        ${ui.kpi({ label: t("Cost", "Chi phí"), icon: "receipt", value: fmt.vndShort(cost), foot: `${fmt.num(kwh ? cost / kwh : 0)} ₫/kWh ${t("average", "trung bình")}` })}
        ${ui.kpi({ label: t("Off-peak or solar", "Thấp điểm hoặc điện mặt trời"), icon: "sun", value: fmt.pct(done.length ? (offPeak.length / done.length) * 100 : 0), foot: "DRV-07" })}
      </div>
      <div class="mt">${ui.card({
        title: t("Charging history of this truck", "Lịch sử sạc của xe"), sub: "CHG-04", flush: true,
        actions: G3.canSee("charging-sessions") ? ui.btn(t("Open in charging sessions", "Mở trong phiên sạc"), { href: `#/charging-sessions?vehicle=${v.vehicle_id}`, sm: true, kind: "ghost", icon: "plug" }) : "",
        body: ui.table({
          rows: sessions, empty: t("No charging session", "Chưa có phiên sạc"),
          columns: [
            { label: t("Started", "Bắt đầu"), render: (s) => `${fmt.dateTime(s.started_at)}<span class="sub mono">#${esc(s.ocpp_transaction_id)}</span>` },
            { label: t("Station", "Trạm"), render: (s) => { const l = db.location(s.location_id); const st = db.station(s.station_id); return `${esc(l ? l.name : "—")}<span class="sub mono">${esc(st ? st.ocpp_identity : "")}</span>`; } },
            { label: t("Battery", "Pin"), render: (s) => `${s.soc_start_percent}% → ${s.soc_end_percent}%` },
            { label: "kWh", align: "right", render: (s) => fmt.num(s.energy_kwh, 1) },
            { label: t("Duration", "Thời lượng"), align: "right", render: (s) => fmt.duration(s.duration_minutes) },
            { label: t("Price", "Đơn giá"), align: "right", render: (s) => `${fmt.num(s.price_vnd_kwh)}<span class="sub">${esc(slotLabel(slotAt(s.location_id, s.started_at).label))}</span>` },
            { label: t("Cost", "Chi phí"), align: "right", render: (s) => (s.cost_vnd === null ? "—" : fmt.vnd(s.cost_vnd)) },
            { label: t("Bill", "Hóa đơn"), render: (s) => `${ui.badge(s.status)} ${ui.badge(s.bill_status)}` },
          ],
        }),
      })}</div>`;
  }

  function changeLog(v) {
    if (!v.change_log) {
      v.change_log = [{ at: v.created_at, by: null, text: ["Registered", "Đăng ký xe"], reason: ["Imported from the delivery list", "Nhập từ danh sách giao xe"] }];
      if (v.handover_at) v.change_log.unshift({ at: v.handover_at, by: db.people.sales.user.user_id, text: ["Owner set to " + db.org(v.organization_id).short_name, "Chủ xe: " + db.org(v.organization_id).short_name], reason: ["Handover to the customer", "Bàn giao cho khách hàng"] });
      if (v.status === "INACTIVE") v.change_log.unshift({ at: new Date(db.NOW - 3 * DAY).toISOString(), by: db.people.ops.user.user_id, text: ["Status ACTIVE → INACTIVE", "Trạng thái Hoạt động → Ngừng hoạt động"], reason: v.status_reason });
    }
    return v.change_log;
  }

  function profileTab(v) {
    const model = db.model(v.vehicle_model_id);
    const fleet = db.fleet(v.fleet_id);
    const device = v.telematic_id ? db.telematic(v.telematic_id) : null;
    const battery = db.battery(v.battery_id);
    const warranties = scope.filter(db.warranties).filter((w) => (w.object_kind === "VEHICLE" && w.object_id === v.vehicle_id) || (w.object_kind === "BATTERY" && w.object_id === v.battery_id) || (w.object_kind === "TELEMATIC" && v.telematic_id && w.object_id === v.telematic_id));
    const firstData = v.live ? (v.handover_at ? new Date(new Date(v.handover_at).getTime() + 2 * HOUR).toISOString() : v.live.received_at) : null;
    const steps = [
      [t("Registered", "Đăng ký"), v.created_at, true],
      [t("T-Box mounted", "Lắp T-Box"), device ? device.installed_at : null, !!device],
      [t("First data received", "Nhận dữ liệu đầu tiên"), firstData, !!v.live],
    ];
    const owners = [{ org: "org-g3", from: v.created_at, to: v.handover_at, reason: t("Stock at G3 Network", "Xe tồn kho G3 Network") }];
    if (v.handover_at && v.organization_id !== "org-g3") owners.push({ org: v.organization_id, from: v.handover_at, to: null, reason: t("Sale, contract HĐ-" + String(2600 + (hashOf(v.vehicle_id) % 300)), "Bán xe, hợp đồng HĐ-" + String(2600 + (hashOf(v.vehicle_id) % 300))) });
    // A customer sees the dealer period only as "G3 Network"; earlier owners' names stay internal (VH-11).
    const log = changeLog(v);

    return `
      <div class="grid cols-2">
        ${ui.card({
          title: t("Vehicle profile", "Hồ sơ xe"), sub: "VEH-01",
          actions: canManageVehicle() ? `<div class="row">${ui.btn(t("Edit", "Sửa"), { icon: "edit", action: "edit-vehicle", sm: true })}${ui.btn(v.status === "ACTIVE" ? t("Deactivate", "Ngừng hoạt động") : t("Reactivate", "Kích hoạt lại"), { icon: "power", action: "status-vehicle", sm: true, kind: v.status === "ACTIVE" ? "danger" : "primary" })}</div>` : "",
          body: ui.kv([
            ["VIN", `<span class="mono">${esc(v.vin)}</span>`],
            [t("Plate", "Biển số"), `<b>${esc(v.license_plate)}</b>`],
            [t("Model", "Mẫu xe"), `${esc(model.name)} · ${esc(model.kind)} · ${model.gross_weight_t} t`],
            [t("Battery capacity", "Dung lượng pin"), `${model.battery_capacity_kwh} kWh · ${esc(model.connector)}`],
            [t("Owner", "Chủ xe"), ui.orgName(v.organization_id)],
            [t("Fleet", "Đội xe"), fleet ? (G3.canSee("fleets") ? `<a href="#/fleets?fleet=${fleet.fleet_id}">${esc(fleet.name)}</a>` : esc(fleet.name)) : `<span class="muted">${esc(t("Not in a fleet", "Chưa thuộc đội"))}</span>`],
            [t("Acquired", "Ngày nhận xe"), fmt.date(v.acquired_at)],
            [t("Status", "Trạng thái"), `${ui.badge(v.status)}${v.status_reason ? ` <span class="small muted">${esc(v.status_reason)}</span>` : ""}`],
          ]),
        })}
        ${ui.card({
          title: t("Activation", "Kích hoạt"), sub: "VEH-05",
          actions: ui.badge(v.activation),
          body: `<ul class="timeline">${steps.map(([label, at, ok]) => `<li style="${ok ? "" : "opacity:.5"}"><b>${esc(label)}</b> ${ok ? ui.icon("check", 14, 'style="color:var(--ok);vertical-align:-2px"') : ""}<div class="when">${at ? fmt.dateTime(at) : esc(t("waiting", "đang chờ"))}</div></li>`).join("")}</ul>
            <div class="divider"></div>
            <h4>${esc(t("Ownership history", "Lịch sử chủ sở hữu"))} ${ui.featureChips(["VEH-02"])}</h4>
            <ul class="timeline mt">${owners.slice().reverse().map((o) => `<li><b>${ui.orgName(o.org)}</b> ${o.to ? "" : ui.badge("ACTIVE", "ok", t("current", "hiện tại"))}<div class="when">${fmt.date(o.from)} → ${o.to ? fmt.date(o.to) : t("now", "nay")} · ${esc(o.reason)}</div></li>`).join("")}</ul>`,
        })}
      </div>
      <div class="grid cols-2 mt">
        ${ui.card({
          title: t("Device mounted", "Thiết bị đang lắp"), sub: "DEV-02",
          actions: `<div class="row">${G3.canSee("devices") ? ui.btn(t("Devices", "Thiết bị"), { href: "#/devices", sm: true, kind: "ghost" }) : ""}${isOps() ? ui.btn(device ? t("Replace", "Thay thiết bị") : t("Mount a device", "Lắp thiết bị"), { icon: "chip", action: "replace-device", sm: true }) : ""}</div>`,
          body: device ? ui.kv([
            [t("Serial", "Số serial"), `<span class="mono">${esc(device.telematic_serial)}</span>`],
            ["IMEI", `<span class="mono">${esc(device.imei)}</span>`],
            [t("Firmware", "Firmware"), esc(device.firmware_version)],
            [t("Health", "Tình trạng"), ui.badge(device.health)],
            [t("Signal · power", "Sóng · nguồn"), device.signal_dbm ? `${device.signal_dbm} dBm · ${fmt.num(device.power_v, 1)} V` : "—"],
            [t("Send interval", "Chu kỳ gửi"), `${device.send_interval_seconds} s${device.reported_interval_seconds && device.reported_interval_seconds !== device.send_interval_seconds ? ` <span class="badge warn">${esc(t("device reports", "thiết bị báo"))} ${device.reported_interval_seconds} s</span>` : ""}`],
            [t("Mounted", "Lắp từ"), fmt.date(device.installed_at)],
          ]) : ui.callout("warn", esc(t("No T-Box mounted: the truck cannot send data (NO_DEVICE).", "Chưa lắp T-Box: xe không gửi được dữ liệu (NO_DEVICE)."))),
        })}
        ${ui.card({
          title: t("Battery fitted", "Pin đang lắp"), sub: "BAT-01",
          actions: G3.canSee("batteries") ? ui.btn(t("Batteries", "Pin"), { href: "#/batteries", sm: true, kind: "ghost" }) : "",
          body: battery ? ui.kv([
            [t("Serial", "Số serial"), `<span class="mono">${esc(battery.battery_serial)}</span>`],
            [t("Model", "Mẫu pin"), esc(db.find("battery_models", battery.battery_model_id).name)],
            ["SOH", `${fmt.pct(battery.soh_percent, 1)} · ${fmt.num(battery.cycle_count)} ${esc(t("cycles", "chu kỳ"))}`],
            [t("Owner", "Chủ sở hữu"), ui.orgName(battery.organization_id) + (battery.organization_id !== v.organization_id ? ` <span class="tag">VaaS</span>` : "")],
            [t("Fitted", "Lắp từ"), fmt.date(battery.installed_at)],
          ]) : ui.empty(),
        })}
      </div>
      <div class="mt">${ui.card({ title: t("Warranties of the truck, battery and device", "Bảo hành của xe, pin và thiết bị"), sub: "WAR-01", flush: true, actions: G3.canSee("warranties") ? ui.btn(t("Warranty register", "Sổ bảo hành"), { href: "#/warranties", sm: true, kind: "ghost" }) : "", body: warrantyRows(warranties) })}</div>
      <div class="mt">${ui.card({
        title: t("Change history", "Lịch sử thay đổi"), sub: t("who, when and why", "ai, khi nào, vì sao"),
        body: `<ul class="timeline">${log.map((e) => `<li><b>${esc(t(e.text[0], e.text[1]))}</b><div class="when">${fmt.dateTime(e.at)} · ${ui.userName(e.by)}${e.reason ? ` · ${esc(Array.isArray(e.reason) ? t(...e.reason) : e.reason)}` : ""}</div></li>`).join("")}</ul>`,
      })}</div>`;
  }

  // Location-history views are audited once per opening, not on each re-render.
  let auditedRouteKey = null;

  function vehicleDetail(ctx) {
    const v = scope.vehicles().find((x) => x.vehicle_id === ctx.params.id);
    if (!v) return vehicleNotFound(ctx.params.id);
    const tab = ["overview", "route", "battery", "trips", "charging", "profile"].includes(ctx.query.tab) ? ctx.query.tab : "overview";
    const model = db.model(v.vehicle_model_id);
    const fleet = db.fleet(v.fleet_id);
    const tabs = ui.tabs([["overview", t("Overview", "Tổng quan")], ["route", t("Route replay", "Lộ trình")], ["battery", t("Battery & cells", "Pin & cell")], ["trips", t("Trips", "Chuyến đi")], ["charging", t("Charging", "Sạc")], ["profile", t("Profile & warranty", "Hồ sơ & bảo hành")]], tab);
    const body = { overview: overviewTab, route: routeTab, battery: batteryTab, trips: tripsTab, charging: chargingTab, profile: profileTab }[tab](v, ctx.query);
    const tabFeatures = { overview: ["MON-02", "MON-09", "DRV-02", "MON-04", "MON-05", "MON-06"], route: ["MON-10"], battery: ["MON-07", "MON-08", "BAT-01"], trips: ["MON-11", "MON-12", "MON-13"], charging: ["CHG-04"], profile: ["VEH-01", "VEH-02", "VEH-05", "DEV-02", "BAT-01", "WAR-01"] }[tab];
    return `${ui.pageHead({
      title: v.license_plate,
      crumbs: G3.canSee("vehicles") ? [{ label: t("Vehicles", "Hồ sơ xe"), href: "#/vehicles" }, { label: v.license_plate }] : null,
      sub: `<span class="mono">${esc(v.vin)}</span> · ${esc(model.name)} · ${ui.orgName(v.organization_id)}${fleet ? ` · ${esc(fleet.name)}` : ""}`,
      features: tabFeatures,
      actions: `<span data-live="state">${ui.vehicleState(v)}</span>${ui.badge(v.activation)}${v.status !== "ACTIVE" ? ui.badge(v.status) : ""}${G3.canSee("live-map") && v.live ? ui.btn(t("Live map", "Bản đồ"), { href: `#/map?v=${v.vehicle_id}`, icon: "map", sm: true }) : ""}`,
    })}${tabs}${body}`;
  }

  function mountVehicleDetail(root, ctx) {
    const v = scope.vehicles().find((x) => x.vehicle_id === ctx.params.id);
    bindCommon(root);
    bindTripRows(root);
    if (!v) return;
    const tab = ctx.query.tab || "overview";

    G3.onTick(() => {
      root.querySelectorAll("[data-live]").forEach((el) => { el.innerHTML = liveHtml(v, el.dataset.live); });
      ui.refreshMarkers(root);
    });

    if (tab === "overview" && v.live) {
      setTimeout(() => { const wrap = root.querySelector("[data-map-wrap=veh-mini]"); if (wrap && wrap.zoomTo) wrap.zoomTo(v.live.lat, v.live.lon, 320); }, 0);
    }

    if (tab === "route") {
      const hours = ["2", "8", "24"].includes(ctx.query.hours) ? Number(ctx.query.hours) : 8;
      const key = `${v.vehicle_id}|${hours}`;
      if (auditedRouteKey !== key) { G3.audit("VIEW", "LOCATION_HISTORY", v.license_plate, null); auditedRouteKey = key; }
      // Leaving the replay (another tab or page) makes the next opening count again.
      G3.onLeave(() => setTimeout(() => { if (!location.hash.includes(`/vehicles/${v.vehicle_id}`) || !location.hash.includes("tab=route")) auditedRouteKey = null; }, 0));
      const route = replayRoute(v, hours);
      if (route.length > 1) bindReplay(root, v, route, hours);
    } else {
      auditedRouteKey = null;
    }

    const on = (action, fn) => { const el = root.querySelector(`[data-action=${action}]`); if (el) el.addEventListener("click", fn); };
    on("edit-vehicle", () => editVehicle(v));
    on("status-vehicle", () => changeVehicleStatus(v));
    on("replace-device", () => replaceDevice(v));
    on("checkin", () => checkInDriver(v));
    on("end-checkin", () => {
      const s = db.driving_sessions.find((x) => x.vehicle_id === v.vehicle_id && !x.ended_at);
      ui.reasonModal({
        title: t("End the driver's check-in", "Kết thúc phiên nhận xe của tài xế"), submitLabel: t("End check-in", "Kết thúc"), danger: false,
        onSubmit(reason) {
          if (s) { s.ended_at = new Date().toISOString(); s.end_cause = "CHECKED_OUT"; s.end_reason = reason; }
          v.driver_id = null;
          G3.render();
          ui.toast(t("Check-in ended from the portal", "Đã kết thúc nhận xe từ cổng web"));
        },
      });
    });
  }

  function bindReplay(root, v, route, hours) {
    const slider = root.querySelector("[data-route-slider]");
    const cursor = root.querySelector("[data-route-cursor]");
    const info = root.querySelector("[data-cursor-info]");
    const timeEl = root.querySelector("[data-cursor-time]");
    const playBtn = root.querySelector("[data-action=play]");
    let timer = null;
    const show = (i) => {
      const p = route[i];
      const [x, y] = ui.project(p.lat, p.lon);
      if (cursor) cursor.setAttribute("style", `transform:translate(${x.toFixed(1)}px,${y.toFixed(1)}px) scale(var(--k,1))`);
      timeEl.textContent = fmt.dateTime(p.at);
      info.innerHTML = ui.kv([
        [t("Time", "Thời gian"), hm(p.at)],
        [t("Speed", "Tốc độ"), p.speed_kmh ? `${p.speed_kmh} km/h` : ui.badge("PARKED", "accent", t("Stopped", "Đang dừng"))],
        [t("Battery", "Pin"), ui.soc(p.soc_percent)],
        [t("Position", "Vị trí"), `<span class="mono small">${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}</span>`],
      ]);
    };
    const stop = () => { clearInterval(timer); timer = null; playBtn.innerHTML = `${ui.icon("chevron", 14)}${esc(t("Play", "Phát"))}`; };
    slider.addEventListener("input", () => show(Number(slider.value)));
    playBtn.addEventListener("click", () => {
      if (timer) return stop();
      if (Number(slider.value) >= route.length - 1) slider.value = 0;
      playBtn.innerHTML = `${ui.icon("x", 14)}${esc(t("Pause", "Tạm dừng"))}`;
      timer = setInterval(() => {
        const i = Number(slider.value) + 1;
        if (i >= route.length) return stop();
        slider.value = i;
        show(i);
      }, 140);
    });
    G3.onLeave(() => clearInterval(timer));
    show(0);
    // Frame the whole route once the map's pan/zoom is mounted.
    setTimeout(() => {
      const wrap = root.querySelector("[data-map-wrap=replay]");
      if (!wrap || !wrap.zoomTo) return;
      const pts = route.map((p) => ui.project(p.lat, p.lon));
      const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
      const w = Math.max(Math.max(...xs) - Math.min(...xs), (Math.max(...ys) - Math.min(...ys)) * (ui.MAP_SIZE.width / ui.MAP_SIZE.height), 120) * 1.35;
      const [lat, lon] = ui.unproject((Math.max(...xs) + Math.min(...xs)) / 2, (Math.max(...ys) + Math.min(...ys)) / 2);
      wrap.zoomTo(lat, lon, w);
    }, 0);
    root.querySelector("[data-action=export-route]").addEventListener("click", () => ui.auditedExport({
      dataType: "LOCATION_HISTORY", subject: v.license_plate, filename: `route-${v.license_plate}-${hours}h.csv`, rows: route,
      columns: [{ label: "recorded_at", key: "at" }, { label: "lat", key: "lat" }, { label: "lon", key: "lon" }, { label: "speed_kmh", key: "speed_kmh" }, { label: "soc_percent", key: "soc_percent" }],
    }));
  }

  const VIN_RE = /^[A-HJ-NPR-Z0-9]{17}$/;
  const PLATE_RE = /^\d{2}[A-Z]{1,2}-\d{3}\.\d{2}$/;
  function editVehicle(v) {
    const internal = isOps();
    const fleets = db.fleets.filter((f) => f.organization_id === v.organization_id);
    ui.formModal({
      title: t(`Edit ${v.license_plate}`, `Sửa hồ sơ ${v.license_plate}`), wide: true,
      intro: internal ? "" : ui.callout("info", esc(t("The VIN and model are corrected by G3 operations; you can change the plate and the fleet.", "VIN và mẫu xe do vận hành G3 hiệu chỉnh; bạn có thể sửa biển số và đội xe."))),
      fields: [
        { name: "vin", label: "VIN", value: v.vin, required: true, hint: t("17 characters, no I, O or Q", "17 ký tự, không có I, O, Q"), attrs: internal ? 'maxlength="17" style="text-transform:uppercase"' : "readonly" },
        { name: "license_plate", label: t("Plate", "Biển số"), value: v.license_plate, required: true, hint: t("Format 51C-123.45", "Định dạng 51C-123.45"), attrs: 'style="text-transform:uppercase"' },
        internal ? { name: "vehicle_model_id", label: t("Model", "Mẫu xe"), type: "select", value: v.vehicle_model_id, options: db.vehicle_models.map((m) => [m.vehicle_model_id, m.name]) } : null,
        { name: "fleet_id", label: t("Fleet", "Đội xe"), type: "select", value: v.fleet_id || "", options: [["", t("Not in a fleet", "Chưa thuộc đội")], ...fleets.map((f) => [f.fleet_id, (f.parent_fleet_id ? "— " : "") + f.name])] },
        { name: "reason", label: t("Reason for the change", "Lý do thay đổi"), type: "textarea", required: true, full: true, hint: t("Saved in the change history (change_reason).", "Lưu vào lịch sử thay đổi (change_reason).") },
      ].filter(Boolean),
      onSubmit(values) {
        const vin = values.vin.toUpperCase();
        const plate = values.license_plate.toUpperCase();
        if (!VIN_RE.test(vin)) return t("The VIN must have 17 letters or digits, without I, O or Q.", "VIN phải gồm 17 chữ hoặc số, không có I, O, Q.");
        if (!PLATE_RE.test(plate)) return t("The plate must look like 51C-123.45.", "Biển số phải có dạng 51C-123.45.");
        if (db.vehicles.some((x) => x.vehicle_id !== v.vehicle_id && x.vin === vin)) return t("Another truck already has this VIN.", "Đã có xe khác dùng VIN này.");
        if (db.vehicles.some((x) => x.vehicle_id !== v.vehicle_id && x.license_plate === plate)) return t("Another truck already has this plate.", "Đã có xe khác dùng biển số này.");
        const changes = [];
        if (vin !== v.vin) changes.push(`VIN ${v.vin} → ${vin}`);
        if (plate !== v.license_plate) changes.push(`${t("plate", "biển số")} ${v.license_plate} → ${plate}`);
        if (values.vehicle_model_id && values.vehicle_model_id !== v.vehicle_model_id) changes.push(`${t("model", "mẫu xe")} → ${db.model(values.vehicle_model_id).code}`);
        if ((values.fleet_id || null) !== (v.fleet_id || null)) changes.push(`${t("fleet", "đội xe")} → ${values.fleet_id ? db.fleet(values.fleet_id).name : "—"}`);
        if (!changes.length) return t("Nothing changed.", "Không có thay đổi.");
        v.vin = vin;
        v.license_plate = plate;
        if (values.vehicle_model_id) v.vehicle_model_id = values.vehicle_model_id;
        v.fleet_id = values.fleet_id || null;
        changeLog(v).unshift({ at: new Date().toISOString(), by: scope.user().user_id, text: [changes.join(", "), changes.join(", ")], reason: values.reason.slice(0, 200) });
        G3.render();
        ui.toast(t("Vehicle profile saved", "Đã lưu hồ sơ xe"));
      },
    });
  }
  function changeVehicleStatus(v) {
    const next = v.status === "ACTIVE" ? "INACTIVE" : "ACTIVE";
    ui.reasonModal({
      title: next === "INACTIVE" ? t(`Deactivate ${v.license_plate}`, `Ngừng hoạt động xe ${v.license_plate}`) : t(`Reactivate ${v.license_plate}`, `Kích hoạt lại xe ${v.license_plate}`),
      intro: `<p class="small muted">${esc(next === "INACTIVE" ? t("An inactive truck stays in reports and history but leaves the live views and the alert routing. Maintenance is a reason, not a status.", "Xe ngừng hoạt động vẫn có trong báo cáo và lịch sử nhưng rời khỏi màn hình trực tuyến và luồng cảnh báo. Bảo dưỡng là lý do, không phải trạng thái.") : t("The truck returns to the live views and alerts.", "Xe trở lại màn hình trực tuyến và cảnh báo."))}</p>`,
      submitLabel: next === "INACTIVE" ? t("Deactivate", "Ngừng hoạt động") : t("Reactivate", "Kích hoạt lại"),
      danger: next === "INACTIVE",
      onSubmit(reason) {
        changeLog(v).unshift({ at: new Date().toISOString(), by: scope.user().user_id, text: [`Status ${v.status} → ${next}`, `Trạng thái ${G3.label(v.status)} → ${G3.label(next)}`], reason });
        v.status = next;
        v.status_reason = next === "INACTIVE" ? reason : null;
        G3.render();
        ui.toast(next === "INACTIVE" ? t("Truck deactivated", "Đã ngừng hoạt động xe") : t("Truck reactivated", "Đã kích hoạt lại xe"));
      },
    });
  }
  function replaceDevice(v) {
    const stock = db.telematics.filter((d) => !d.vehicle_id && d.status === "ACTIVE");
    if (!stock.length) return ui.toast(t("No device in stock to mount", "Không còn thiết bị trong kho để lắp"), "alert");
    const old = v.telematic_id ? db.telematic(v.telematic_id) : null;
    ui.formModal({
      title: old ? t(`Replace the T-Box of ${v.license_plate}`, `Thay T-Box xe ${v.license_plate}`) : t(`Mount a T-Box on ${v.license_plate}`, `Lắp T-Box cho xe ${v.license_plate}`),
      intro: old ? ui.callout("info", esc(t(`${old.telematic_serial} is unmounted and goes back to stock; telemetry keeps the device that sent each reading.`, `${old.telematic_serial} được tháo và trả về kho; dữ liệu cũ vẫn gắn với thiết bị đã gửi.`))) : "",
      fields: [
        { name: "telematic_id", label: t("Device from stock", "Thiết bị trong kho"), type: "select", options: stock.map((d) => [d.telematic_id, `${d.telematic_serial} · ${d.firmware_version}`]), required: true },
        { name: "installed_at", label: t("Mounted at", "Thời điểm lắp"), type: "datetime-local", value: toInput(Date.now()), required: true, hint: t("When it really happened (installed_at), may be entered late.", "Thời điểm thực tế (installed_at), có thể nhập muộn.") },
        { name: "reason", label: t("Reason", "Lý do"), type: "textarea", required: true },
      ],
      submitLabel: old ? t("Replace", "Thay") : t("Mount", "Lắp"),
      onSubmit(values) {
        const fresh = db.telematic(values.telematic_id);
        const at = new Date(values.installed_at).toISOString();
        if (old) { old.vehicle_id = null; old.installed_at = null; old.health = "NOT_MOUNTED"; }
        fresh.vehicle_id = v.vehicle_id;
        fresh.installed_at = at;
        fresh.health = "NO_DATA";
        v.telematic_id = fresh.telematic_id;
        if (v.activation === "NO_DEVICE") v.activation = "AWAITING_DATA";
        changeLog(v).unshift({ at, by: scope.user().user_id, text: [`T-Box ${old ? old.telematic_serial + " → " : ""}${fresh.telematic_serial}`, `T-Box ${old ? old.telematic_serial + " → " : ""}${fresh.telematic_serial}`], reason: values.reason.slice(0, 200) });
        G3.render();
        ui.toast(t(`${fresh.telematic_serial} mounted on ${v.license_plate}`, `Đã lắp ${fresh.telematic_serial} cho xe ${v.license_plate}`));
      },
    });
  }
  function checkInDriver(v) {
    const busy = new Set(db.driving_sessions.filter((s) => !s.ended_at).map((s) => s.driver_id));
    const pool = db.drivers.filter((d) => d.organization_id === v.organization_id && d.status === "ACTIVE" && !busy.has(d.driver_id));
    if (!pool.length) return ui.toast(t("Every active driver is already checked in to a truck", "Mọi tài xế đang hoạt động đều đã nhận xe"), "alert");
    ui.formModal({
      title: t(`Check in a driver to ${v.license_plate}`, `Gán tài xế nhận xe ${v.license_plate}`),
      intro: `<p class="small muted">${esc(t("Used when the driver cannot scan the QR code. The session is recorded with the method PORTAL (DRV-02).", "Dùng khi tài xế không quét được mã QR. Phiên được ghi với phương thức Cổng web (DRV-02)."))}</p>`,
      fields: [{ name: "driver_id", label: t("Driver", "Tài xế"), type: "select", options: pool.map((d) => [d.driver_id, `${d.full_name} · ${d.phone_number}`]), required: true }],
      submitLabel: t("Check in", "Nhận xe"),
      onSubmit(values) {
        db.driving_sessions.unshift({ driving_session_id: db.uid("ds"), driver_id: values.driver_id, vehicle_id: v.vehicle_id, organization_id: v.organization_id, check_in_method: "PORTAL", checked_in_at: new Date().toISOString(), ended_at: null, end_cause: null });
        v.driver_id = values.driver_id;
        G3.render();
        ui.toast(t(`${db.driver(values.driver_id).full_name} checked in`, `${db.driver(values.driver_id).full_name} đã nhận xe`));
      },
    });
  }

  G3.page({
    id: "vehicle-detail",
    path: "/vehicles/:id",
    title: ["Vehicle detail", "Chi tiết xe"],
    roles: ["FLEET_MANAGER", "DISPATCHER", "ORG_ADMIN", "CUSTOMER_CARE", "OPERATIONS", "WARRANTY", "MAINTENANCE", "ACCOUNTANT", "SALES", "CO_ADMIN"],
    features: ["MON-02", "MON-09", "MON-04", "MON-05", "MON-06", "MON-07", "MON-08", "MON-10", "MON-11", "MON-12", "MON-13", "CHG-04", "DRV-02", "VEH-01", "VEH-02", "VEH-05", "BAT-01", "DEV-02", "WAR-01"],
    navParent: "vehicles",
    render: vehicleDetail,
    mount: mountVehicleDetail,
  });

  /* ==========================================================
     Notification center
     ========================================================== */
  const ESCALATION = [
    { after: 0, who: ["Fleet manager on duty", "Quản lý đội trực"], channel: ["App + portal", "Ứng dụng + cổng web"] },
    { after: 10, who: ["Organization admin", "Quản trị tổ chức"], channel: ["App + portal + SMS", "Ứng dụng + cổng web + SMS"] },
    { after: 20, who: ["G3 operations 24/7", "Vận hành G3 24/7"], channel: ["Call from the operations desk", "Gọi điện từ bàn vận hành"] },
  ];
  const isAcked = (n) => !!n.acknowledged_at;
  function escalationLevel(n) {
    if (n.severity !== "CRITICAL") return 0;
    const end = n.acknowledged_at ? new Date(n.acknowledged_at).getTime() : Date.now();
    const minutes = (end - new Date(n.created_at).getTime()) / MIN;
    return ESCALATION.filter((s) => minutes >= s.after).length;
  }

  function payloadRows(n, snap) {
    const p = n.payload || {};
    const v = db.vehicle(n.vehicle_id);
    const rows = [];
    switch (n.notification_type) {
      case "BATTERY_ALERT": {
        const LEVEL = { EARLY: ["Early (30%)", "Sớm (30%)"], MAIN: ["Main (20%)", "Chính (20%)"], CRITICAL: ["Critical (10%)", "Nghiêm trọng (10%)"] };
        rows.push([t("Level", "Mức"), p.level ? esc(t(...LEVEL[p.level])) : "—"], [t("Battery at alert", "Pin lúc cảnh báo"), ui.soc(snap.soc_percent)], [t("Nearest station then", "Trạm gần nhất lúc đó"), snap.nearest_station ? `${esc(snap.nearest_station)} · ${fmt.num(snap.nearest_station_km, 1)} km` : null]);
        break;
      }
      case "ANOMALY_ALERT": {
        const KIND = { HIGH_BATTERY_TEMPERATURE: ["High battery temperature", "Nhiệt độ pin cao"], SUDDEN_VOLTAGE_DROP: ["Sudden voltage drop", "Sụt áp đột ngột"] };
        rows.push([t("Anomaly", "Bất thường"), esc(p.anomaly ? t(...(KIND[p.anomaly] || [p.anomaly, p.anomaly])) : "—")], [t("Value", "Giá trị"), p.value !== undefined ? `${p.value} °C` : null], [t("Pack at alert", "Khối pin lúc cảnh báo"), snap.pack_voltage_v ? `${snap.pack_voltage_v} V · ${snap.pack_current_a} A · ${snap.battery_temperature_celsius} °C` : null]);
        break;
      }
      case "GEOFENCE_ALERT":
        rows.push([t("Transition", "Chuyển vùng"), p.transition ? ui.badge(p.transition, p.transition === "ENTER" ? "info" : "warn", p.transition === "ENTER" ? t("Entered", "Đi vào") : t("Left", "Đi ra")) : null], [t("Zone", "Vùng"), esc(p.geofence || "—")]);
        break;
      case "FAULT_ALERT": {
        const d = p.dtc ? dtc(p.dtc) : null;
        rows.push([t("Fault code", "Mã lỗi"), d ? `<span class="mono">${esc(d.code)}</span> ${esc(t(...d.description))}` : null], [t("Drivability", "Khả năng vận hành"), d ? drivableBadge(d.drivable) : null], [t("Advice", "Hướng xử lý"), d ? esc(t(...d.advice)) : null]);
        break;
      }
      case "DEVICE_OFFLINE_ALERT": {
        const dev = v && v.telematic_id ? db.telematic(v.telematic_id) : null;
        rows.push([t("Device", "Thiết bị"), dev ? `<span class="mono">${esc(dev.telematic_serial)}</span>` : null], [t("Last seen", "Lần cuối"), dev ? fmt.dateTime(dev.last_seen_at) : null], [t("Power at last report", "Nguồn lần báo cuối"), dev && dev.power_v !== null ? `${fmt.num(dev.power_v, 1)} V` : null]);
        break;
      }
      case "SOH_ALERT":
        rows.push(["SOH", v ? fmt.pct(v.soh_percent, 1) : null], [t("Threshold", "Ngưỡng"), "90%"]);
        break;
      case "SOS_ALERT": {
        const c = db.support_cases.find((x) => x.case_type === "SOS" && x.vehicle_id === n.vehicle_id);
        rows.push([t("Support case", "Yêu cầu hỗ trợ"), c ? (G3.canSee("support-detail") ? `<a href="#/support/${c.support_case_id}">${esc(c.support_case_id)}</a>` : esc(c.support_case_id)) + " " + ui.badge(c.status) : null]);
        break;
      }
      case "CHARGER_FAULT": {
        const st = db.station(n.station_id);
        const c = st && st.connectors.find((x) => x.status === "Faulted");
        rows.push([t("Charger", "Trụ sạc"), st ? `<span class="mono">${esc(st.ocpp_identity)}</span>` : null], [t("Error", "Lỗi"), c ? `${esc(c.error_code)} · ${esc(c.vendor_error_code)}` : null]);
        break;
      }
      default:
        break;
    }
    if (v && snap.lat) rows.push([t("Position then", "Vị trí lúc đó"), `<span class="mono small">${snap.lat}, ${snap.lon}</span> · ${esc(snap.road)} · ${snap.speed_kmh} km/h`]);
    return rows;
  }

  function notificationDetail(n) {
    if (!n) return ui.card({ title: t("Details", "Chi tiết"), body: `<p class="muted small">${esc(t("Select a notification to see its payload, recipients and escalation.", "Chọn một thông báo để xem dữ liệu, người nhận và mức leo thang."))}</p>` });
    const v = db.vehicle(n.vehicle_id);
    const snap = frozenContext(n);
    const level = escalationLevel(n);
    const escalation = n.severity === "CRITICAL" ? `<div class="divider"></div><h4>${esc(t("Escalation", "Leo thang"))} ${ui.featureChips(["NTF-08"])}</h4>
      <ul class="timeline mt">${ESCALATION.map((s, i) => {
        const due = new Date(n.created_at).getTime() + s.after * MIN;
        const reached = i < level;
        const skipped = isAcked(n) && !reached;
        return `<li style="${reached ? "" : "opacity:.55"}"><b>${esc(t(...s.who))}</b> <span class="small muted">${esc(t(...s.channel))}</span><div class="when">${reached ? `${esc(t("sent", "đã gửi"))} ${hm(due)}` : skipped ? esc(t("not needed — acknowledged", "không cần — đã xác nhận")) : `${esc(t("due", "dự kiến"))} ${hm(due)} (${fmt.ago(new Date(due).toISOString())})`}</div></li>`;
      }).join("")}</ul>` : "";
    const ack = n.severity === "CRITICAL"
      ? (isAcked(n) ? ui.callout("ok", `${esc(t("Acknowledged by", "Đã xác nhận bởi"))} <b>${ui.userName(n.acknowledged_by_user_id)}</b> · ${fmt.dateTime(n.acknowledged_at)}${n.acknowledge_note ? `<br><span class="small">${esc(n.acknowledge_note)}</span>` : ""}`) : ui.callout("danger", esc(t("Critical alert not acknowledged yet: it escalates to the next person every 10 minutes.", "Cảnh báo nghiêm trọng chưa được xác nhận: cứ 10 phút sẽ chuyển lên người tiếp theo."))))
      : "";
    const payload = { ...(n.payload || {}), snapshot: snap };
    return ui.card({
      title: G3.label(n.notification_type), sub: `#${n.notification_id}`,
      actions: ui.badge(n.severity),
      body: `<p class="strong" style="margin:0 0 10px">${esc(t(n.title[0], n.title[1]))}</p>
        ${ack}
        <div class="mt">${ui.kv([
          [t("Truck", "Xe"), v ? vehicleLink(v) : null],
          scope.isInternal() ? [t("Organization", "Tổ chức"), ui.orgName(n.organization_id)] : null,
          [t("Created", "Thời điểm"), `${fmt.dateTime(n.created_at)} · ${fmt.ago(n.created_at)}`],
          [t("Read", "Đã đọc"), n.read_at ? fmt.dateTime(n.read_at) : `<span class="muted">${esc(t("unread", "chưa đọc"))}</span>`],
          ...payloadRows(n, snap),
        ])}</div>
        ${escalation}
        <div class="divider"></div>
        <details><summary class="small muted" style="cursor:pointer">${esc(t("Payload (JSONB, frozen at alert time)", "Dữ liệu (JSONB, cố định lúc cảnh báo)"))}</summary><pre class="mono small" style="white-space:pre-wrap;background:var(--surface-2);padding:10px;border-radius:8px;margin:8px 0 0">${esc(JSON.stringify(payload, null, 2))}</pre></details>
        <div class="row mt">
          ${n.severity === "CRITICAL" && !isAcked(n) ? ui.btn(t("Acknowledge", "Xác nhận đã xử lý"), { action: "ack", kind: "primary", sm: true, icon: "check" }) : ""}
          ${ui.btn(n.read_at ? t("Mark unread", "Đánh dấu chưa đọc") : t("Mark read", "Đánh dấu đã đọc"), { action: "toggle-read", sm: true })}
          ${v && G3.canSee("vehicle-detail") ? ui.btn(t("Open truck", "Mở xe"), { href: `#/vehicles/${v.vehicle_id}`, sm: true, kind: "ghost", icon: "truck" }) : ""}
          ${n.notification_type === "GEOFENCE_ALERT" && G3.canSee("geofences") ? ui.btn(t("Geofences", "Vùng địa lý"), { href: "#/geofences", sm: true, kind: "ghost" }) : ""}
        </div>`,
    });
  }

  function notificationsPage(ctx) {
    const q = ctx.query;
    const all = scope.byVehicle(db.notifications);
    const rows = all.filter((n) => (!q.type || n.notification_type === q.type) && (!q.severity || n.severity === q.severity) && (!q.vehicle || n.vehicle_id === q.vehicle) && (!q.unread || !n.read_at));
    const selected = all.find((n) => String(n.notification_id) === q.n) || null;
    const types = [...new Set(all.map((n) => n.notification_type))];
    const trucks = scope.vehicles().filter((v) => all.some((n) => n.vehicle_id === v.vehicle_id));
    const unread = all.filter((n) => !n.read_at).length;
    const criticalOpen = all.filter((n) => n.severity === "CRITICAL" && !isAcked(n));
    const escalated = criticalOpen.filter((n) => escalationLevel(n) > 1).length;
    const last24 = all.filter((n) => Date.now() - new Date(n.created_at).getTime() < DAY).length;
    return `${ui.pageHead({
      title: t("Notification center", "Trung tâm thông báo"),
      sub: esc(t("Alerts and messages for your roles, newest first. The portal checks for new ones about every 10 seconds (NTF-01).", "Cảnh báo và tin nhắn theo vai trò của bạn, mới nhất trước. Cổng web kiểm tra tin mới khoảng mỗi 10 giây (NTF-01).")),
      features: ["NTF-01", "NTF-08", "MON-04", "MON-05", "MON-06", "DEV-05", "FLT-05", "DRV-02"],
      actions: ui.btn(t("Mark all read", "Đánh dấu đã đọc tất cả"), { icon: "check", action: "read-all", disabled: !rows.some((n) => !n.read_at) }),
    })}
      <div class="grid cols-4">
        ${ui.kpi({ label: t("Unread", "Chưa đọc"), icon: "bell", value: unread, foot: `${all.length} ${t("in total", "tổng cộng")}` })}
        ${ui.kpi({ label: t("Critical, not acknowledged", "Nghiêm trọng chưa xác nhận"), icon: "alert", value: criticalOpen.length, tone: criticalOpen.length ? "danger" : "ok", foot: "NTF-08" })}
        ${ui.kpi({ label: t("Escalated", "Đã leo thang"), icon: "users", value: escalated, tone: escalated ? "warn" : undefined, foot: t("past the first recipient", "vượt người nhận đầu tiên") })}
        ${ui.kpi({ label: t("Last 24 hours", "24 giờ qua"), icon: "clock", value: last24 })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          flush: true,
          body: `<div class="toolbar">
              ${ui.select("type", [["", t("All types", "Mọi loại")], ...types.map((x) => [x, G3.label(x)])], q.type || "")}
              ${ui.select("severity", [["", t("All severities", "Mọi mức độ")], ...["CRITICAL", "WARNING", "INFO"].map((x) => [x, G3.label(x)])], q.severity || "")}
              ${ui.select("vehicle", [["", t("All trucks", "Mọi xe")], ...trucks.map((v) => [v.vehicle_id, v.license_plate])], q.vehicle || "")}
              <label class="check small"><input type="checkbox" data-check="unread" ${q.unread ? "checked" : ""}> ${esc(t("Unread only", "Chỉ chưa đọc"))}</label>
              <span class="grow"></span><span class="small muted">${rows.length} ${esc(t("shown", "hiển thị"))}</span>
            </div>
            ${rows.length ? `<ul class="list">${rows.map((n) => {
              const v = db.vehicle(n.vehicle_id);
              return `<li class="clickable ${n.read_at ? "" : "unread"}" data-notif="${n.notification_id}" style="${selected && selected.notification_id === n.notification_id ? "box-shadow:inset 3px 0 0 var(--primary)" : ""}">
                <div class="sev-icon ${n.severity}">${ui.icon(notifIcon(n), 16)}</div>
                <div class="grow"><div class="title">${esc(t(n.title[0], n.title[1]))}</div>
                <div class="meta">${esc(G3.label(n.notification_type))}${v ? ` · ${esc(v.license_plate)}` : ""}${scope.isInternal() ? ` · ${ui.orgName(n.organization_id)}` : ""} · ${fmt.ago(n.created_at)}</div></div>
                <div class="stack" style="gap:4px;align-items:flex-end">${ui.badge(n.severity)}${n.severity === "CRITICAL" ? (isAcked(n) ? ui.badge("ACKNOWLEDGED") : `<span class="tag">${esc(t("level", "cấp"))} ${escalationLevel(n)}/3</span>`) : ""}</div>
              </li>`;
            }).join("")}</ul>` : ui.empty(t("No notification matches the filters", "Không có thông báo phù hợp bộ lọc"))}`,
        })}
        <div data-detail>${notificationDetail(selected)}</div>
      </div>`;
  }

  function mountNotifications(root, ctx) {
    bindCommon(root);
    root.querySelectorAll("[data-notif]").forEach((li) => li.addEventListener("click", () => {
      const n = db.notifications.find((x) => String(x.notification_id) === li.dataset.notif);
      if (n && !n.read_at) n.read_at = new Date().toISOString();
      G3.setQuery({ n: li.dataset.notif });
    }));
    const readAll = root.querySelector("[data-action=read-all]");
    if (readAll) readAll.addEventListener("click", () => {
      const q = ctx.query;
      const rows = scope.byVehicle(db.notifications).filter((n) => !n.read_at && (!q.type || n.notification_type === q.type) && (!q.severity || n.severity === q.severity) && (!q.vehicle || n.vehicle_id === q.vehicle));
      const now = new Date().toISOString();
      rows.forEach((n) => { n.read_at = now; });
      G3.render();
      ui.toast(t(`${rows.length} notifications marked read`, `Đã đánh dấu đọc ${rows.length} thông báo`));
    });
    const selected = scope.byVehicle(db.notifications).find((n) => String(n.notification_id) === ctx.query.n);
    const ack = root.querySelector("[data-action=ack]");
    if (ack && selected) ack.addEventListener("click", () => ui.formModal({
      title: t("Acknowledge the alert", "Xác nhận cảnh báo"),
      intro: `<p class="small muted">${esc(t("Acknowledging stops the escalation. Your name and the time are recorded.", "Xác nhận sẽ dừng leo thang. Tên bạn và thời điểm được ghi lại."))}</p>`,
      fields: [{ name: "note", label: t("What was done", "Đã xử lý thế nào"), type: "textarea", placeholder: t("Called the driver, truck parked safely…", "Đã gọi tài xế, xe đã đỗ an toàn…") }],
      submitLabel: t("Acknowledge", "Xác nhận"),
      onSubmit(values) {
        const now = new Date().toISOString();
        selected.acknowledged_at = now;
        selected.acknowledged_by_user_id = scope.user().user_id;
        selected.acknowledge_note = values.note.slice(0, 200) || null;
        selected.read_at = selected.read_at || now;
        G3.render();
        ui.toast(t("Alert acknowledged; escalation stopped", "Đã xác nhận; ngừng leo thang"));
      },
    }));
    const toggle = root.querySelector("[data-action=toggle-read]");
    if (toggle && selected) toggle.addEventListener("click", () => { selected.read_at = selected.read_at ? null : new Date().toISOString(); G3.render(); });
    // New alerts arrive with the notification poll; refresh the list when one lands in scope.
    let known = scope.byVehicle(db.notifications).length;
    G3.onTick(() => { const now = scope.byVehicle(db.notifications).length; if (now !== known) { known = now; G3.render(); } });
  }

  G3.page({
    id: "notifications",
    path: "/notifications",
    title: ["Notifications", "Thông báo"],
    roles: "*",
    features: ["NTF-01", "NTF-08", "MON-04", "MON-05", "MON-06", "DEV-05", "FLT-05", "DRV-02"],
    nav: { group: "monitoring", order: 2, icon: "bell", badge: () => scope.byVehicle(db.notifications).filter((n) => !n.read_at).length },
    render: notificationsPage,
    mount: mountNotifications,
  });

  /* ==========================================================
     Trips and shift plan
     ========================================================== */
  const SHIFTS = {
    S1: { label: ["Early 05–13", "Ca sáng 05–13"], hours: 8, color: "var(--info-soft)" },
    S2: { label: ["Late 13–21", "Ca chiều 13–21"], hours: 8, color: "var(--primary-soft)" },
    N: { label: ["Night 21–05", "Ca đêm 21–05"], hours: 8, color: "var(--surface-3)" },
    OFF: { label: ["Off", "Nghỉ"], hours: 0, color: "transparent" },
    LEAVE: { label: ["Leave", "Nghỉ phép"], hours: 0, color: "var(--warn-soft)" },
  };
  const shiftOverrides = {};
  function shiftOf(driver, dayIndex) {
    const key = `${driver.driver_id}|${dayIndex}`;
    if (shiftOverrides[key]) return shiftOverrides[key];
    if (driver.status !== "ACTIVE") return "LEAVE";
    const r = seeded(key)();
    const base = hashOf(driver.driver_id) % 3;
    if (dayIndex === 6 || r < 0.12) return "OFF";
    return ["S1", "S2", "N"][(base + (r < 0.2 ? 1 : 0)) % 3];
  }

  function tripsList(q) {
    const all = scope.byVehicle(db.trips, false);
    const rows = all.filter((x) => {
      if (q.status && x.status !== q.status) return false;
      if (q.driver && x.driver_id !== q.driver) return false;
      if (q.vehicle && x.vehicle_id !== q.vehicle) return false;
      if (q.day === "upcoming") return new Date(x.planned_departure_at).getTime() > Date.now();
      if (q.day && localDate(x.planned_departure_at) !== q.day) return false;
      return true;
    }).sort((a, b) => (a.planned_departure_at < b.planned_departure_at ? 1 : -1));
    const done = all.filter((x) => x.status === "COMPLETED");
    const km = sum(done, (x) => x.distance_km);
    const emptyKm = sum(done.filter((x) => x.inferred_load === "EMPTY"), (x) => x.distance_km);
    const late = done.filter((x) => new Date(x.ended_at) - new Date(x.planned_arrival_at) > 15 * MIN).length;
    const days = Array.from({ length: 14 }, (_, i) => db.NOW - i * DAY);
    const drivers = scope.filter(db.drivers);
    const trucks = scope.vehicles().filter((v) => all.some((x) => x.vehicle_id === v.vehicle_id));
    return `
      <div class="grid cols-5">
        ${ui.kpi({ label: t("In progress", "Đang chạy"), icon: "route", value: all.filter((x) => x.status === "IN_PROGRESS").length, foot: `${all.filter((x) => x.status === "PLANNED").length} ${t("planned", "đã lên lịch")}` })}
        ${ui.kpi({ label: t("Completed (14 d)", "Hoàn tất (14 ngày)"), icon: "check", value: done.length, foot: `${fmt.num(km)} km` })}
        ${ui.kpi({ label: t("On-time arrival", "Đến đúng giờ"), icon: "clock", value: fmt.pct(done.length ? ((done.length - late) / done.length) * 100 : 0), foot: `${late} ${t("late > 15 min", "trễ > 15 phút")}` })}
        ${ui.kpi({ label: t("Empty running", "Chạy rỗng"), icon: "truck", value: fmt.pct(km ? (emptyKm / km) * 100 : 0), foot: `${fmt.num(emptyKm)} km · MON-13` })}
        ${ui.kpi({ label: t("Load mismatches", "Lệch tải"), icon: "alert", value: done.filter(isMismatch).length, tone: done.some(isMismatch) ? "warn" : undefined, foot: t("declared vs inferred", "khai báo vs suy ra") })}
      </div>
      <div class="mt">${ui.card({
        flush: true,
        body: `<div class="toolbar">
            ${ui.select("status", [["", t("All statuses", "Mọi trạng thái")], ...["PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED"].map((s) => [s, G3.label(s)])], q.status || "")}
            ${ui.select("day", [["", t("Any day", "Mọi ngày")], ["upcoming", t("Upcoming", "Sắp tới")], ...days.map((ms) => [localDate(ms), fmt.date(ms)])], q.day || "")}
            ${ui.select("driver", [["", t("All drivers", "Mọi tài xế")], ...drivers.map((d) => [d.driver_id, d.full_name])], q.driver || "")}
            ${ui.select("vehicle", [["", t("All trucks", "Mọi xe")], ...trucks.map((v) => [v.vehicle_id, v.license_plate])], q.vehicle || "")}
            <span class="grow"></span><span class="small muted">${rows.length} ${esc(t("trips", "chuyến"))}</span>
            ${ui.btn(t("Export", "Xuất"), { icon: "download", action: "export-trips", sm: true })}
          </div>
          ${ui.table({ rows, columns: tripColumns(true), rowAttrs: (r) => `data-trip="${r.trip_id}" style="cursor:pointer"`, empty: t("No trip matches the filters", "Không có chuyến phù hợp") })}`,
      })}</div>`;
  }

  function weekStart() {
    const d = new Date(db.NOW);
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    return d.getTime();
  }
  function schedule() {
    const start = weekStart();
    const days = Array.from({ length: 7 }, (_, i) => start + i * DAY);
    const todayKey = localDate(Date.now());
    const drivers = scope.filter(db.drivers);
    const trips = scope.byVehicle(db.trips, false);
    const DOW = [t("Mon", "T2"), t("Tue", "T3"), t("Wed", "T4"), t("Thu", "T5"), t("Fri", "T6"), t("Sat", "T7"), t("Sun", "CN")];
    const rows = drivers.map((d) => {
      const shifts = days.map((_, i) => shiftOf(d, i));
      return { d, shifts, hours: sum(shifts, (s) => SHIFTS[s].hours) };
    });
    return `
      ${ui.callout("info", esc(t("Shift plan for the week (DRV-03, planned feature, mock data). Click a cell to change a shift; planned trips of the day are counted in it. More than 48 hours a week is flagged (DRV-06).", "Lịch ca trong tuần (DRV-03, tính năng dự kiến, dữ liệu giả lập). Bấm vào ô để đổi ca; số chuyến đã lên lịch trong ngày hiện trong ô. Quá 48 giờ/tuần sẽ được đánh dấu (DRV-06).")))}
      <div class="mt">${ui.card({
        title: `${t("Week of", "Tuần từ")} ${fmt.date(start)}`, sub: `${drivers.length} ${t("drivers", "tài xế")}`, flush: true,
        actions: ui.legend(["S1", "S2", "N", "LEAVE"].map((k) => [t(...SHIFTS[k].label), SHIFTS[k].color])),
        body: `<div class="table-wrap"><table class="tbl"><thead><tr><th>${esc(t("Driver", "Tài xế"))}</th>${days.map((ms, i) => `<th style="${localDate(ms) === todayKey ? "color:var(--primary-strong)" : ""}">${DOW[i]} ${dm(ms)}</th>`).join("")}<th class="right">${esc(t("Hours", "Giờ"))}</th></tr></thead><tbody>
          ${rows.map(({ d, shifts, hours }) => `<tr><td>${driverLink(d.driver_id)}<span class="sub">${esc(d.license_class)}</span></td>${shifts.map((s, i) => {
            const count = trips.filter((x) => x.driver_id === d.driver_id && localDate(x.planned_departure_at) === localDate(days[i]) && x.status !== "CANCELLED").length;
            return `<td data-shift="${d.driver_id}|${i}" style="cursor:pointer;background:${SHIFTS[s].color};${localDate(days[i]) === todayKey ? "box-shadow:inset 0 0 0 2px var(--primary)" : ""}"><span class="small ${s === "OFF" ? "muted" : "strong"}">${esc(t(...SHIFTS[s].label))}</span>${count ? `<span class="sub" style="${SHIFTS[s].hours ? "" : "color:var(--danger);font-weight:600"}">${count} ${esc(t("trip(s)", "chuyến"))}${SHIFTS[s].hours ? "" : " — " + esc(t("day off!", "trùng ngày nghỉ!"))}</span>` : ""}</td>`;
          }).join("")}<td class="right num">${hours > 48 ? `<b style="color:var(--danger)">${hours}</b>` : hours}</td></tr>`).join("")}
        </tbody></table></div>`,
      })}</div>`;
  }

  function tripsPage(ctx) {
    const tab = ctx.query.tab === "schedule" ? "schedule" : "list";
    return `${ui.pageHead({
      title: t("Trips", "Chuyến đi"),
      sub: esc(t("Trips planned by the fleet and run by drivers with Start and Finish in the app; distance, energy, stops and load per trip.", "Chuyến do đội xe lên kế hoạch và tài xế bấm Bắt đầu/Kết thúc trên ứng dụng; quãng đường, điện năng, điểm dừng và tải theo từng chuyến.")),
      features: ["MON-11", "MON-12", "MON-13", "DRV-03"],
      actions: ui.btn(t("Plan a trip", "Lên kế hoạch chuyến"), { icon: "plus", kind: "primary", action: "plan-trip" }),
    })}${ui.tabs([["list", t("Trips", "Chuyến đi")], ["schedule", t("Shift plan", "Lịch ca")]], tab)}${tab === "schedule" ? schedule() : tripsList(ctx.query)}`;
  }

  function planTrip() {
    const trucks = scope.vehicles().filter((v) => v.status === "ACTIVE" && v.handover_at);
    const drivers = scope.filter(db.drivers).filter((d) => d.status === "ACTIVE");
    const places = Object.entries(db.PLACES).map(([k, p]) => [k, p.name]);
    const dep = Math.ceil((Date.now() + 2 * HOUR) / (15 * MIN)) * 15 * MIN;
    ui.formModal({
      title: t("Plan a trip", "Lên kế hoạch chuyến"), wide: true,
      intro: `<p class="small muted">${esc(t("The driver receives a TRIP_ASSIGNED notification and starts the trip in the app.", "Tài xế nhận thông báo Giao chuyến và bấm bắt đầu trên ứng dụng."))}</p>`,
      fields: [
        { name: "origin", label: t("Origin", "Điểm đi"), type: "select", options: places, value: "catlai", required: true },
        { name: "destination", label: t("Destination", "Điểm đến"), type: "select", options: places, value: "caimep", required: true },
        { name: "dep", label: t("Planned departure", "Khởi hành dự kiến"), type: "datetime-local", value: toInput(dep), required: true },
        { name: "arr", label: t("Planned arrival", "Đến dự kiến"), type: "datetime-local", value: toInput(dep + 2 * HOUR), required: true },
        { name: "vehicle_id", label: t("Truck", "Xe"), type: "select", options: trucks.map((v) => [v.vehicle_id, `${v.license_plate} · ${db.model(v.vehicle_model_id).code}${scope.isInternal() ? " · " + db.org(v.organization_id).short_name : ""}`]), required: true },
        { name: "driver_id", label: t("Driver", "Tài xế"), type: "select", options: drivers.map((d) => [d.driver_id, `${d.full_name}${scope.isInternal() ? " · " + db.org(d.organization_id).short_name : ""}`]), required: true },
      ],
      submitLabel: t("Create trip", "Tạo chuyến"),
      onSubmit(values) {
        if (values.origin === values.destination) return t("Origin and destination must differ.", "Điểm đi và điểm đến phải khác nhau.");
        const depMs = new Date(values.dep).getTime();
        const arrMs = new Date(values.arr).getTime();
        if (!(arrMs > depMs)) return t("The arrival must be after the departure.", "Giờ đến phải sau giờ khởi hành.");
        if (depMs < Date.now() - 30 * MIN) return t("The departure cannot be in the past.", "Giờ khởi hành không được ở quá khứ.");
        const v = db.vehicle(values.vehicle_id);
        const d = db.driver(values.driver_id);
        if (v.organization_id !== d.organization_id) return t("The driver and the truck must belong to the same organization.", "Tài xế và xe phải thuộc cùng một tổ chức.");
        let no = 10421 + db.trips.length;
        while (db.trips.some((x) => x.trip_id === `TRP-${no}`)) no++;
        const trip = { trip_id: `TRP-${no}`, organization_id: v.organization_id, vehicle_id: v.vehicle_id, driver_id: d.driver_id, origin: db.PLACES[values.origin].name, destination: db.PLACES[values.destination].name, origin_key: values.origin, destination_key: values.destination, planned_departure_at: new Date(depMs).toISOString(), planned_arrival_at: new Date(arrMs).toISOString(), started_at: null, ended_at: null, status: "PLANNED", distance_km: null, energy_kwh: null, declared_load: null, inferred_load: null, stops: 0, stop_minutes: 0 };
        db.trips.unshift(trip);
        const title = [`Trip ${trip.trip_id} ${trip.origin} → ${trip.destination} assigned to ${d.full_name}`, `Đã giao chuyến ${trip.trip_id} ${trip.origin} → ${trip.destination} cho ${d.full_name}`];
        db.notifications.unshift({ notification_id: Math.max(...db.notifications.map((n) => n.notification_id)) + 1, notification_type: "TRIP_ASSIGNED", severity: "INFO", vehicle_id: v.vehicle_id, organization_id: v.organization_id, title, created_at: new Date().toISOString(), read_at: null, payload: { trip_id: trip.trip_id, driver_id: d.driver_id }, station_id: null });
        G3.render();
        ui.toast(t(`${trip.trip_id} planned; ${d.full_name} was notified`, `Đã tạo ${trip.trip_id}; đã báo cho ${d.full_name}`), "", "route");
      },
    });
  }

  G3.page({
    id: "trips",
    path: "/trips",
    title: ["Trips", "Chuyến đi"],
    roles: ["FLEET_MANAGER", "DISPATCHER", "ORG_ADMIN"],
    features: ["MON-11", "MON-12", "MON-13", "DRV-03"],
    nav: { group: "monitoring", order: 3, icon: "route" },
    render: tripsPage,
    mount(root) {
      bindCommon(root);
      bindTripRows(root);
      root.querySelector("[data-action=plan-trip]").addEventListener("click", planTrip);
      const exp = root.querySelector("[data-action=export-trips]");
      if (exp) exp.addEventListener("click", () => ui.downloadCsv("trips.csv", [
        { label: "trip_id", key: "trip_id" }, { label: "status", key: "status" }, { label: "plate", csv: (r) => db.vehicle(r.vehicle_id).license_plate }, { label: "driver", csv: (r) => (db.driver(r.driver_id) || {}).full_name },
        { label: "origin", key: "origin" }, { label: "destination", key: "destination" }, { label: "planned_departure_at", key: "planned_departure_at" }, { label: "started_at", key: "started_at" }, { label: "ended_at", key: "ended_at" },
        { label: "distance_km", key: "distance_km" }, { label: "energy_kwh", key: "energy_kwh" }, { label: "cost_vnd", key: "cost_vnd" }, { label: "declared_load", key: "declared_load" }, { label: "inferred_load", key: "inferred_load" },
      ], scope.byVehicle(db.trips, false)));
      root.querySelectorAll("[data-shift]").forEach((td) => td.addEventListener("click", () => {
        const [driverId, day] = td.dataset.shift.split("|");
        ui.popover(td, [{ title: t("Set shift", "Chọn ca") }, ...Object.keys(SHIFTS).map((k) => ({ label: t(...SHIFTS[k].label), on: shiftOf(db.driver(driverId), Number(day)) === k, onClick: () => { shiftOverrides[`${driverId}|${day}`] = k; G3.render(); ui.toast(t("Shift updated; the driver sees it in the app", "Đã đổi ca; tài xế thấy trên ứng dụng")); } }))]);
      }));
    },
  });

  /* ==========================================================
     Battery health (fleet)
     ========================================================== */
  function batteryHealthPage(ctx) {
    const q = ctx.query;
    const all = scope.vehicles().filter((v) => v.battery_id && v.live);
    const rows = all.filter((v) => (!q.model || v.vehicle_model_id === q.model) && (!q.below || v.soh_percent < 90)).map((v) => {
      const a = cellAnalysis(v);
      const war = db.warranties.find((w) => w.object_kind === "BATTERY" && w.object_id === v.battery_id);
      return { v, a, war, usage: war ? warrantyUsage(war) : null, trend: db.sohSeries(v.vehicle_id, 180).filter((_, i) => i % 15 === 0).map((p) => p.soh_percent) };
    }).sort((x, y) => (q.sort === "cycles" ? y.v.cycle_count - x.v.cycle_count : q.sort === "temp" ? y.v.live.battery_temperature_celsius - x.v.live.battery_temperature_celsius : x.v.soh_percent - y.v.soh_percent));
    const avg = all.length ? sum(all, (v) => v.soh_percent) / all.length : 0;
    const below = all.filter((v) => v.soh_percent < 90);
    const hot = all.filter((v) => v.live.battery_temperature_celsius >= 45);
    const weakTrucks = rows.filter((r) => r.a.weak.length);
    const buckets = [["< 88", 0, 88], ["88–90", 88, 90], ["90–92", 90, 92], ["92–94", 92, 94], ["94–96", 94, 96], ["96–98", 96, 98], ["≥ 98", 98, 101]];
    const fleetAvg = Array.from({ length: 180 }, () => 0);
    all.forEach((v) => db.sohSeries(v.vehicle_id, 180).forEach((p, i) => { fleetAvg[i] += p.soh_percent / all.length; }));
    const avgLabels = Array.from({ length: 180 }, (_, i) => dm(db.NOW - (179 - i) * DAY));
    return `${ui.pageHead({
      title: t("Battery health", "Sức khỏe pin"),
      sub: esc(t("State of health, charge cycles, temperature and weak cells of every pack in your scope. The alert line is 90% SOH.", "SOH, chu kỳ sạc, nhiệt độ và cell yếu của mọi khối pin trong phạm vi của bạn. Ngưỡng cảnh báo là SOH 90%.")),
      features: ["MON-07", "MON-08", "MON-05"],
      actions: ui.btn(t("Export", "Xuất"), { icon: "download", action: "export-soh" }),
    })}
      <div class="grid cols-5">
        ${ui.kpi({ label: t("Average SOH", "SOH trung bình"), icon: "battery", value: fmt.num(avg, 1), unit: "%", foot: `${all.length} ${t("packs", "khối pin")}` })}
        ${ui.kpi({ label: t("Below 90%", "Dưới 90%"), icon: "alert", value: below.length, tone: below.length ? "danger" : "ok", foot: below.map((v) => v.license_plate).join(", ") || t("none", "không có") })}
        ${ui.kpi({ label: t("Average cycles", "Chu kỳ trung bình"), icon: "refresh", value: fmt.num(all.length ? sum(all, (v) => v.cycle_count) / all.length : 0), foot: `${t("max", "cao nhất")} ${fmt.num(Math.max(0, ...all.map((v) => v.cycle_count)))}` })}
        ${ui.kpi({ label: t("Hot packs ≥ 45 °C", "Pin nóng ≥ 45 °C"), icon: "thermometer", value: hot.length, tone: hot.length ? "danger" : undefined, foot: "MON-05" })}
        ${ui.kpi({ label: t("Trucks with weak cells", "Xe có cell yếu"), icon: "grid", value: weakTrucks.length, tone: weakTrucks.length ? "warn" : undefined, foot: `${sum(weakTrucks, (r) => r.a.weak.length)} ${t("cells", "cell")} · MON-08` })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Fleet average SOH (180 days)", "SOH trung bình đội xe (180 ngày)"),
          actions: ui.legend([[t("Average", "Trung bình"), ui.PALETTE[0]], [t("Alert line 90%", "Ngưỡng 90%"), "#dc2626"]]),
          body: ui.lineChart({ labels: avgLabels, series: [{ name: "SOH", values: fleetAvg.map((x) => Math.round(x * 100) / 100), area: true }, { name: t("Threshold", "Ngưỡng"), values: fleetAvg.map(() => 90), color: "#dc2626", dashed: true }], yMin: 88, yMax: 100, unit: "%", yFormat: (x) => fmt.num(x, 0), height: 220 }),
        })}
        ${ui.card({
          title: t("SOH distribution", "Phân bố SOH"), sub: t("packs per band", "số pin theo khoảng"),
          body: ui.hBars({ labels: buckets.map((b) => `SOH ${b[0]}%`), series: [{ values: buckets.map(([, lo, hi]) => all.filter((v) => v.soh_percent >= lo && v.soh_percent < hi).length), color: ui.PALETTE[0] }], unit: t(" packs", " pin") }),
        })}
      </div>
      <div class="mt">${ui.card({
        flush: true,
        body: `<div class="toolbar">
            ${ui.select("model", [["", t("All models", "Mọi mẫu xe")], ...db.vehicle_models.map((m) => [m.vehicle_model_id, m.name])], q.model || "")}
            ${ui.select("sort", [["soh", t("Sort: lowest SOH", "Sắp xếp: SOH thấp nhất")], ["cycles", t("Sort: most cycles", "Sắp xếp: nhiều chu kỳ nhất")], ["temp", t("Sort: hottest", "Sắp xếp: nóng nhất")]], q.sort || "soh")}
            <label class="check small"><input type="checkbox" data-check="below" ${q.below ? "checked" : ""}> ${esc(t("Below 90% only", "Chỉ dưới 90%"))}</label>
            <span class="grow"></span><span class="small muted">${rows.length} ${esc(t("packs", "khối pin"))}</span>
          </div>
          ${ui.table({
            rows, href: G3.canSee("vehicle-detail") ? (r) => `/vehicles/${r.v.vehicle_id}?tab=battery` : null,
            columns: [
              { label: t("Truck", "Xe"), render: (r) => `<b>${esc(r.v.license_plate)}</b><span class="sub">${esc(db.model(r.v.vehicle_model_id).code)}${scope.isInternal() ? " · " + ui.orgName(r.v.organization_id) : ""}</span>` },
              { label: "SOH", render: (r) => `<div class="row" style="gap:8px;flex-wrap:nowrap"><b class="num" style="width:44px;${r.v.soh_percent < 90 ? "color:var(--danger)" : ""}">${fmt.num(r.v.soh_percent, 1)}%</b><div style="width:90px">${ui.meter(((r.v.soh_percent - 80) / 20) * 100, r.v.soh_percent < 90 ? "danger" : r.v.soh_percent < 93 ? "warn" : "")}</div></div>` },
              { label: t("Trend 180 d", "Xu hướng 180 ngày"), render: (r) => ui.sparkline(r.trend, r.v.soh_percent < 90 ? "var(--danger)" : "var(--primary)") },
              { label: t("Cycles", "Chu kỳ"), align: "right", render: (r) => fmt.num(r.v.cycle_count) },
              { label: t("Temp.", "Nhiệt độ"), align: "right", render: (r) => `<span style="${r.v.live.battery_temperature_celsius >= 45 ? "color:var(--danger);font-weight:700" : ""}">${r.v.live.battery_temperature_celsius} °C</span>` },
              { label: t("Imbalance", "Lệch áp"), align: "right", render: (r) => `${r.a.imbalanceMv} mV` },
              { label: t("Weak cells", "Cell yếu"), render: (r) => (r.a.weak.length ? `<span class="badge danger">${r.a.weak.length}</span> <span class="small muted">${r.a.weak.map((c) => "#" + c.cell).join(" ")}</span>` : '<span class="muted">0</span>') },
              { label: t("Battery warranty used", "Bảo hành pin đã dùng"), render: (r) => (r.usage ? `<div class="small">${fmt.num(r.usage.used)} / ${fmt.num(r.usage.limit)} ${esc(t("cycles", "chu kỳ"))} · ${fmt.pct(r.usage.timePct)} ${esc(t("of time", "thời gian"))}</div>${ui.meter(Math.max(r.usage.counterPct, r.usage.timePct), Math.max(r.usage.counterPct, r.usage.timePct) > 80 ? "warn" : "")}${r.war.status !== "ACTIVE" ? ui.badge(r.war.status) : ""}` : '<span class="muted">—</span>') },
            ],
          })}`,
      })}</div>`;
  }

  G3.page({
    id: "battery-health",
    path: "/battery-health",
    title: ["Battery health", "Sức khỏe pin"],
    roles: ["FLEET_MANAGER", "WARRANTY", "MAINTENANCE", "ORG_ADMIN", "OPERATIONS"],
    features: ["MON-07", "MON-08", "MON-05"],
    nav: { group: "monitoring", order: 4, icon: "battery" },
    render: batteryHealthPage,
    mount(root) {
      bindCommon(root);
      root.querySelector("[data-action=export-soh]").addEventListener("click", () => ui.downloadCsv("battery-health.csv", [
        { label: "plate", key: "license_plate" }, { label: "vin", key: "vin" }, { label: "battery_serial", csv: (v) => db.battery(v.battery_id).battery_serial },
        { label: "soh_percent", key: "soh_percent" }, { label: "cycle_count", key: "cycle_count" }, { label: "temperature_celsius", csv: (v) => v.live.battery_temperature_celsius }, { label: "weak_cells", csv: (v) => cellAnalysis(v).weak.length },
      ], scope.vehicles().filter((v) => v.battery_id && v.live)));
    },
  });

  /* ==========================================================
     Component faults and the fault-code catalog
     ========================================================== */
  function faultBoard(q) {
    const all = scope.byVehicle(FAULTS, false);
    const active = all.filter((f) => f.status === "ACTIVE");
    const rows = (q.show === "cleared" ? all.filter((f) => f.status === "CLEARED") : active).filter((f) => {
      const d = dtc(f.dtc);
      return (!q.component || d.component === q.component) && (!q.severity || d.severity === q.severity) && (!q.drivable || d.drivable === q.drivable);
    }).sort((a, b) => ["CRITICAL", "WARNING", "INFO"].indexOf(dtc(a.dtc).severity) - ["CRITICAL", "WARNING", "INFO"].indexOf(dtc(b.dtc).severity) || (a.detected_at < b.detected_at ? 1 : -1));
    const workshop = [...new Set(active.filter((f) => dtc(f.dtc).drivable !== "YES").map((f) => f.vehicle_id))].map((id) => ({ v: db.vehicle(id), faults: active.filter((f) => f.vehicle_id === id) }));
    const byComponent = Object.keys(COMPONENTS).map((c) => ({ c, n: active.filter((f) => dtc(f.dtc).component === c).length })).filter((x) => x.n).sort((a, b) => b.n - a.n);
    const workshops = db.partner_profiles.filter((p) => p.partner_type === "WORKSHOP");
    const canClear = scope.has("HEAD_ADMIN", "MAINTENANCE", "OPERATIONS");
    return `
      <div class="grid cols-4">
        ${ui.kpi({ label: t("Active faults", "Lỗi đang có"), icon: "wrench", value: active.length, foot: `${new Set(active.map((f) => f.vehicle_id)).size} ${t("trucks", "xe")}` })}
        ${ui.kpi({ label: t("Do not drive", "Không được chạy"), icon: "alert", value: active.filter((f) => dtc(f.dtc).drivable === "NO").length, tone: active.some((f) => dtc(f.dtc).drivable === "NO") ? "danger" : "ok" })}
        ${ui.kpi({ label: t("Trucks needing a workshop", "Xe cần vào xưởng"), icon: "truck", value: workshop.length, tone: workshop.length ? "warn" : undefined })}
        ${ui.kpi({ label: t("Cleared (7 d)", "Đã xử lý (7 ngày)"), icon: "check", value: all.filter((f) => f.status === "CLEARED").length })}
      </div>
      <div class="mt">
        ${ui.card({
          flush: true,
          body: `<div class="toolbar">
              ${ui.segmented([["active", t("Active", "Đang có")], ["cleared", t("Cleared", "Đã xử lý")]], q.show === "cleared" ? "cleared" : "active", "show")}
              ${ui.select("component", [["", t("All components", "Mọi bộ phận")], ...Object.keys(COMPONENTS).map((c) => [c, compLabel(c)])], q.component || "")}
              ${ui.select("severity", [["", t("All severities", "Mọi mức độ")], ...["CRITICAL", "WARNING", "INFO"].map((s) => [s, G3.label(s)])], q.severity || "")}
              ${ui.select("drivable", [["", t("Any drivability", "Mọi khả năng vận hành")], ...Object.keys(DRIVABLE).map((k) => [k, t(...DRIVABLE[k][0])])], q.drivable || "")}
            </div>
            ${ui.table({
              rows, empty: t("No fault matches the filters", "Không có lỗi phù hợp"),
              columns: [
                { label: t("Truck", "Xe"), render: (f) => `${vehicleLink(db.vehicle(f.vehicle_id))}${scope.isInternal() ? `<span class="sub">${ui.orgName(f.organization_id)}</span>` : ""}` },
                { label: t("Fault", "Lỗi"), render: (f) => `<b class="mono">${esc(f.dtc)}</b> <span class="small muted">${esc(compLabel(dtc(f.dtc).component))}</span><span class="sub">${esc(t(...dtc(f.dtc).description))}</span>` },
                { label: t("Severity", "Mức độ"), render: (f) => ui.badge(dtc(f.dtc).severity) },
                { label: t("Verdict and advice", "Kết luận và hướng xử lý"), render: (f) => `${drivableBadge(dtc(f.dtc).drivable)}<span class="sub">${esc(t(...dtc(f.dtc).advice))}</span>` },
                { label: t("Since", "Từ lúc"), cls: "nowrap", render: (f) => (f.status === "CLEARED" ? `${fmt.ago(f.detected_at)}<span class="sub">${esc(t("cleared", "xử lý"))} ${fmt.ago(f.cleared_at)}</span>` : fmt.ago(f.detected_at)) },
                canClear && q.show !== "cleared" ? { label: "", render: (f) => ui.btn(t("Clear", "Xóa lỗi"), { sm: true, attrs: `data-clear="${f.fault_id}"` }) } : null,
              ].filter(Boolean),
            })}`,
        })}
      </div>
      <div class="grid cols-2 mt">
          ${ui.card({
            title: t("Trucks needing a workshop", "Xe cần vào xưởng"), sub: "MON-06", flush: true,
            body: workshop.length ? `<ul class="list">${workshop.map(({ v, faults }) => {
              const worst = faults.map((f) => dtc(f.dtc)).sort((a, b) => ["NO", "LIMITED", "YES"].indexOf(a.drivable) - ["NO", "LIMITED", "YES"].indexOf(b.drivable))[0];
              const shop = v.live ? workshops.map((p) => ({ p, d: distKm(p, v.live) })).sort((a, b) => a.d - b.d)[0] : null;
              return `<li><div class="sev-icon ${worst.severity}">${ui.icon("truck", 16)}</div><div class="grow"><div class="title">${vehicleLink(v)}</div><div class="meta">${faults.map((f) => esc(f.dtc)).join(", ")} · ${shop ? `${esc(db.org(shop.p.organization_id).short_name)} ${fmt.num(shop.d, 0)} km` : ""}</div></div>${drivableBadge(worst.drivable)}</li>`;
            }).join("")}</ul>` : ui.empty(t("No truck needs a workshop", "Không có xe cần vào xưởng")),
          })}
          ${ui.card({ title: t("Active faults by component", "Lỗi đang có theo bộ phận"), body: byComponent.length ? ui.hBars({ labels: byComponent.map((x) => compLabel(x.c)), series: [{ values: byComponent.map((x) => x.n), color: ui.PALETTE[2] }] }) : ui.empty() })}
      </div>`;
  }

  function dtcCatalog(q) {
    const version = DTC_VERSIONS.some((x) => x.version === q.version) ? q.version : DTC_VERSIONS[0].version;
    const text = (q.q || "").toLowerCase();
    const rows = dtcFor(version).filter((d) => !text || d.code.toLowerCase().includes(text) || d.description.join(" ").toLowerCase().includes(text) || compLabel(d.component).toLowerCase().includes(text)).filter((d) => !q.component || d.component === q.component);
    const meta = DTC_VERSIONS.find((x) => x.version === version);
    return `
      ${ui.callout("info", esc(t(`Manufacturer fault-code table for Tri-Ring EVT trucks, version ${version} (${fmt.date(meta.released_on)}): ${t(...meta.note)}. Older versions stay available so a past alert is always read with the table it was raised under.`, `Bảng mã lỗi của nhà sản xuất cho xe Tri-Ring EVT, phiên bản ${version} (${fmt.date(meta.released_on)}): ${t(...meta.note)}. Các phiên bản cũ vẫn được giữ để mỗi cảnh báo cũ được đọc theo đúng bảng lúc phát sinh.`)))}
      <div class="mt">${ui.card({
        flush: true,
        body: `<div class="toolbar">
            ${ui.select("version", DTC_VERSIONS.map((x) => [x.version, `${t("Version", "Phiên bản")} ${x.version}${x === DTC_VERSIONS[0] ? " · " + t("current", "hiện hành") : ""}`]), version)}
            ${ui.select("component", [["", t("All components", "Mọi bộ phận")], ...Object.keys(COMPONENTS).map((c) => [c, compLabel(c)])], q.component || "")}
            ${ui.search("q", q.q, t("Code or description", "Mã hoặc mô tả"))}
            <span class="grow"></span><span class="small muted">${rows.length} ${esc(t("codes", "mã"))}</span>
          </div>
          ${ui.table({
            rows, empty: t("No code found", "Không tìm thấy mã"),
            columns: [
              { label: t("Code", "Mã"), render: (d) => `<b class="mono">${esc(d.code)}</b>` },
              { label: t("Component", "Bộ phận"), render: (d) => esc(compLabel(d.component)) },
              { label: t("Description", "Mô tả"), render: (d) => esc(t(...d.description)) },
              { label: t("Severity", "Mức độ"), render: (d) => ui.badge(d.severity) },
              { label: t("Can keep driving?", "Có chạy tiếp được?"), render: (d) => drivableBadge(d.drivable) },
              { label: t("What to do", "Cần làm gì"), render: (d) => `<span class="small">${esc(t(...d.advice))}</span>` },
              { label: t("Since", "Từ bản"), render: (d) => `<span class="tag">v${esc(d.since)}</span>` },
            ],
          })}`,
      })}</div>`;
  }

  G3.page({
    id: "faults",
    path: "/faults",
    title: ["Component faults", "Lỗi linh kiện"],
    roles: ["FLEET_MANAGER", "MAINTENANCE", "CUSTOMER_CARE", "OPERATIONS", "WARRANTY"],
    features: ["MON-06", "VEH-06", "MON-05"],
    nav: { group: "monitoring", order: 5, icon: "wrench" },
    render(ctx) {
      const tab = ctx.query.tab === "catalog" ? "catalog" : "board";
      return `${ui.pageHead({
        title: t("Component faults", "Lỗi linh kiện"),
        sub: esc(t("Motor, controller, auxiliary battery, brakes, tyres, lights, doors and air-conditioning faults reported by the trucks, with a drivability verdict from the manufacturer's code table.", "Lỗi động cơ, bộ điều khiển, ắc quy phụ, phanh, lốp, đèn, cửa và điều hòa do xe báo về, kèm kết luận có chạy tiếp được hay không theo bảng mã của nhà sản xuất.")),
        features: tab === "catalog" ? ["VEH-06"] : ["MON-06", "MON-05"],
      })}${ui.tabs([["board", t("Fleet board", "Bảng theo dõi")], ["catalog", t("Fault-code catalog", "Danh mục mã lỗi")]], tab)}${tab === "catalog" ? dtcCatalog(ctx.query) : faultBoard(ctx.query)}`;
    },
    mount(root) {
      bindCommon(root);
      root.querySelectorAll("[data-clear]").forEach((b) => b.addEventListener("click", () => {
        const f = FAULTS.find((x) => x.fault_id === b.dataset.clear);
        ui.reasonModal({
          title: t(`Clear ${f.dtc} on ${db.vehicle(f.vehicle_id).license_plate}`, `Xóa lỗi ${f.dtc} xe ${db.vehicle(f.vehicle_id).license_plate}`), danger: false, submitLabel: t("Clear fault", "Xóa lỗi"),
          intro: `<p class="small muted">${esc(t("If the truck reports the code again, the fault reopens.", "Nếu xe báo lại mã này, lỗi sẽ mở lại."))}</p>`,
          onSubmit(reason) { f.status = "CLEARED"; f.cleared_at = new Date().toISOString(); f.cleared_reason = reason; G3.render(); ui.toast(t("Fault cleared", "Đã xóa lỗi")); },
        });
      }));
    },
  });

  /* ==========================================================
     Reports
     ========================================================== */
  const reportVehicles = () => scope.vehicles().filter((v) => v.handover_at);
  function bucketize(rows, gran) {
    const map = new Map();
    rows.forEach((r) => {
      const d = new Date(r.date + "T00:00:00");
      let key = r.date, label = dm(d.getTime());
      if (gran === "week") { const m = new Date(d); m.setDate(d.getDate() - ((d.getDay() + 6) % 7)); key = localDate(m); label = t("Wk ", "Tuần ") + dm(m.getTime()); }
      if (gran === "month") { key = r.date.slice(0, 7); label = `${r.date.slice(5, 7)}/${r.date.slice(0, 4)}`; }
      const b = map.get(key) || { key, label, distance_km: 0, energy_kwh: 0, driving_hours: 0, days: new Set() };
      b.distance_km += r.distance_km; b.energy_kwh += r.energy_kwh; b.driving_hours += r.driving_hours; b.days.add(r.date);
      map.set(key, b);
    });
    return [...map.values()].sort((a, b) => (a.key < b.key ? -1 : 1));
  }

  function operatingReport(q) {
    const trucks = reportVehicles();
    const days = ["7", "30", "90"].includes(q.days) ? Number(q.days) : 30;
    const gran = ["day", "week", "month"].includes(q.gran) ? q.gran : days > 30 ? "week" : "day";
    const price = Number(q.price) > 0 ? Number(q.price) : 4180;
    const chosen = q.vehicle ? trucks.filter((v) => v.vehicle_id === q.vehicle) : trucks;
    const daily = [];
    chosen.forEach((v) => db.dailyReport(v.vehicle_id, days).forEach((r) => daily.push(r)));
    const buckets = bucketize(daily, gran);
    const km = sum(buckets, (b) => b.distance_km);
    const kwh = sum(buckets, (b) => b.energy_kwh);
    const hours = sum(buckets, (b) => b.driving_hours);
    const r = seeded((q.vehicle || "all") + days);
    const aux = [
      { key: ["Air-conditioning", "Điều hòa"], hours: hours * (0.82 + r() * 0.1), kw: 2.8 },
      { key: ["Lights", "Đèn"], hours: hours * (0.3 + r() * 0.08), kw: 0.45 },
      { key: ["Tail lift / PTO", "Bửng nâng / PTO"], hours: hours * (0.06 + r() * 0.05), kw: 6.5 },
      { key: ["Cabin heater", "Sưởi cabin"], hours: 0, kw: 1.5 },
    ];
    return `
      <div class="toolbar card mb">
        ${ui.select("vehicle", [["", t(`All trucks (${trucks.length})`, `Tất cả xe (${trucks.length})`)], ...trucks.map((v) => [v.vehicle_id, v.license_plate])], q.vehicle || "")}
        ${ui.segmented([["7", t("7 days", "7 ngày")], ["30", t("30 days", "30 ngày")], ["90", t("90 days", "90 ngày")]], String(days), "days")}
        ${ui.segmented([["day", t("Day", "Ngày")], ["week", t("Week", "Tuần")], ["month", t("Month", "Tháng")]], gran, "gran")}
        <label class="small muted">${esc(t("Electricity price", "Giá điện"))} <input class="input" type="number" min="1000" step="10" data-filter="price" value="${price}" style="width:96px"> ₫/kWh</label>
        <span class="grow"></span>${ui.btn(t("Export CSV", "Xuất CSV"), { icon: "download", action: "export-operating", sm: true })}
      </div>
      <div class="grid cols-5">
        ${ui.kpi({ label: t("Distance", "Quãng đường"), icon: "route", value: fmt.num(km), unit: "km", foot: `${fmt.num(km / days / Math.max(1, chosen.length), 0)} km/${t("truck/day", "xe/ngày")}` })}
        ${ui.kpi({ label: t("Energy", "Điện năng"), icon: "bolt", value: fmt.num(kwh), unit: "kWh" })}
        ${ui.kpi({ label: "kWh/km", icon: "gauge", value: fmt.num(km ? kwh / km : 0, 2) })}
        ${ui.kpi({ label: t("Electricity cost", "Tiền điện"), icon: "receipt", value: fmt.vndShort(kwh * price), foot: `${fmt.num(price)} ₫/kWh` })}
        ${ui.kpi({ label: t("Cost per km", "Chi phí/km"), icon: "wallet", value: fmt.num(km ? (kwh * price) / km : 0), unit: "₫/km", foot: `${fmt.num(hours, 0)} ${t("driving hours", "giờ lái")}` })}
      </div>
      <div class="grid side mt">
        ${ui.card({ title: t("Distance and energy", "Quãng đường và điện năng"), sub: { day: t("per day", "theo ngày"), week: t("per week", "theo tuần"), month: t("per month", "theo tháng") }[gran], actions: ui.legend([["km", ui.PALETTE[1]], ["kWh", ui.PALETTE[0]]]), body: ui.barChart({ labels: buckets.map((b) => b.label), series: [{ name: "km", values: buckets.map((b) => b.distance_km), color: ui.PALETTE[1] }, { name: "kWh", values: buckets.map((b) => b.energy_kwh), color: ui.PALETTE[0] }], height: 240 }) })}
        ${ui.card({
          title: t("Auxiliary loads", "Tải phụ"), sub: "MON-16",
          body: `${ui.hBars({ labels: aux.map((a) => t(...a.key)), series: [{ values: aux.map((a) => Math.round(a.hours)), color: ui.PALETTE[3] }], unit: t(" h", " giờ") })}
            <div class="divider"></div>${ui.kv(aux.filter((a) => a.hours).map((a) => [t(...a.key), `${fmt.num(a.hours * a.kw, 0)} kWh · ${fmt.pct(kwh ? ((a.hours * a.kw) / kwh) * 100 : 0, 1)} ${esc(t("of energy", "điện năng"))}`]))}`,
        })}
      </div>
      <div class="mt">${ui.card({
        title: t("Operating report", "Báo cáo vận hành"), sub: "MON-14", flush: true,
        body: ui.table({
          rows: buckets.slice().reverse(),
          columns: [
            { label: t("Period", "Kỳ"), render: (b) => `<b>${esc(b.label)}</b>` },
            { label: "km", align: "right", render: (b) => fmt.num(b.distance_km) },
            { label: "kWh", align: "right", render: (b) => fmt.num(b.energy_kwh) },
            { label: "kWh/km", align: "right", render: (b) => (b.distance_km ? fmt.num(b.energy_kwh / b.distance_km, 2) : "—") },
            { label: t("Cost", "Chi phí"), align: "right", render: (b) => fmt.vnd(b.energy_kwh * price) },
            { label: "₫/km", align: "right", render: (b) => (b.distance_km ? fmt.num((b.energy_kwh * price) / b.distance_km) : "—") },
            { label: t("Driving hours", "Giờ lái"), align: "right", render: (b) => fmt.num(b.driving_hours, 1) },
          ],
        }),
      })}</div>`;
  }

  function costRows(by) {
    let rows = [];
    if (by === "truck") {
      rows = reportVehicles().map((v) => { const d = db.dailyReport(v.vehicle_id, 30); return { label: v.license_plate, sub: db.model(v.vehicle_model_id).code, href: `/vehicles/${v.vehicle_id}`, km: sum(d, (r) => r.distance_km), kwh: sum(d, (r) => r.energy_kwh), cost: sum(d, (r) => r.cost_vnd), n: d.filter((r) => r.distance_km).length }; });
    } else {
      const trips = scope.byVehicle(db.trips, false).filter((x) => x.status === "COMPLETED");
      const groups = new Map();
      trips.forEach((x) => {
        const key = by === "driver" ? x.driver_id || "-" : `${x.origin} → ${x.destination}`;
        const g = groups.get(key) || { label: by === "driver" ? (db.driver(x.driver_id) || { full_name: "—" }).full_name : key, sub: "", href: by === "driver" && x.driver_id ? `/drivers/${x.driver_id}` : null, km: 0, kwh: 0, cost: 0, n: 0 };
        g.km += x.distance_km; g.kwh += x.energy_kwh; g.cost += x.cost_vnd; g.n++;
        groups.set(key, g);
      });
      rows = [...groups.values()];
    }
    return rows.filter((r) => r.km > 0).map((r) => ({ ...r, perKm: r.cost / r.km })).sort((a, b) => b.perKm - a.perKm);
  }
  function costReport(q) {
    const by = ["truck", "driver", "route"].includes(q.by) ? q.by : "truck";
    const rows = costRows(by);
    const total = { km: sum(rows, (r) => r.km), cost: sum(rows, (r) => r.cost), kwh: sum(rows, (r) => r.kwh) };
    const avg = total.km ? total.cost / total.km : 0;
    const linkPage = by === "truck" ? "vehicle-detail" : by === "driver" ? "driver-detail" : null;
    return `
      <div class="toolbar card mb">${ui.segmented([["truck", t("By truck", "Theo xe")], ["driver", t("By driver", "Theo tài xế")], ["route", t("By route", "Theo tuyến")]], by, "by")}<span class="small muted">${esc(by === "truck" ? t("Last 30 days, daily telemetry", "30 ngày qua, dữ liệu theo ngày") : t("Completed trips, last 14 days", "Chuyến hoàn tất, 14 ngày qua"))}</span><span class="grow"></span>${ui.btn(t("Export CSV", "Xuất CSV"), { icon: "download", action: "export-cost", sm: true })}</div>
      <div class="grid cols-4">
        ${ui.kpi({ label: t("Electricity cost", "Tiền điện"), icon: "receipt", value: fmt.vndShort(total.cost) })}
        ${ui.kpi({ label: t("Distance", "Quãng đường"), icon: "route", value: fmt.num(total.km), unit: "km" })}
        ${ui.kpi({ label: t("Average cost per km", "Chi phí/km trung bình"), icon: "wallet", value: fmt.num(avg), unit: "₫/km" })}
        ${ui.kpi({ label: t("Spread", "Chênh lệch"), icon: "activity", value: rows.length ? fmt.pct(((rows[0].perKm - rows[rows.length - 1].perKm) / avg) * 100) : "—", foot: t("highest vs lowest ₫/km", "cao nhất so với thấp nhất ₫/km") })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Cost per km", "Chi phí trên mỗi km"), sub: "FLT-07", flush: true,
          body: ui.table({
            rows, href: linkPage && G3.canSee(linkPage) ? (r) => r.href : null,
            columns: [
              { label: by === "truck" ? t("Truck", "Xe") : by === "driver" ? t("Driver", "Tài xế") : t("Route", "Tuyến"), render: (r) => `<b>${esc(r.label)}</b>${r.sub ? `<span class="sub">${esc(r.sub)}</span>` : ""}` },
              { label: by === "truck" ? t("Days driven", "Ngày chạy") : t("Trips", "Chuyến"), align: "right", render: (r) => r.n },
              { label: "km", align: "right", render: (r) => fmt.num(r.km) },
              { label: "kWh", align: "right", render: (r) => fmt.num(r.kwh) },
              { label: "kWh/km", align: "right", render: (r) => fmt.num(r.kwh / r.km, 2) },
              { label: t("Cost", "Chi phí"), align: "right", render: (r) => fmt.vnd(r.cost) },
              { label: "₫/km", align: "right", render: (r) => `<b style="color:var(--${r.perKm > avg * 1.08 ? "danger" : r.perKm < avg * 0.92 ? "ok" : "text"})">${fmt.num(r.perKm)}</b>` },
            ],
          }),
        })}
        ${ui.card({ title: t("Ranking ₫/km", "Xếp hạng ₫/km"), sub: t("highest first", "cao nhất trước"), body: ui.hBars({ labels: rows.slice(0, 12).map((r) => r.label), series: [{ values: rows.slice(0, 12).map((r) => Math.round(r.perKm)), color: ui.PALETTE[2] }], unit: " ₫" }) })}
      </div>`;
  }

  function chargingWarrantyReport(q) {
    const fleets = scope.filter(db.fleets);
    const trucks = reportVehicles().filter((v) => !q.fleet || db.fleetAndDescendants(q.fleet).includes(v.fleet_id));
    const rows = trucks.map((v) => {
      const sessions = db.charging_sessions.filter((s) => s.vehicle_id === v.vehicle_id && s.status !== "ABANDONED");
      const c = db.compliance.find((x) => x.vehicle_id === v.vehicle_id) || { score: null, violations_30d: 0, risk: null, trend: [] };
      const bw = db.warranties.find((w) => w.object_kind === "BATTERY" && w.object_id === v.battery_id);
      const vw = db.warranties.find((w) => w.object_kind === "VEHICLE" && w.object_id === v.vehicle_id);
      return { v, sessions: sessions.length, kwh: sum(sessions, (s) => s.energy_kwh), cost: sum(sessions, (s) => s.cost_vnd), dcFull: sessions.filter((s) => s.soc_end_percent >= 98).length, c, bw, vw };
    }).sort((a, b) => (a.c.score ?? 101) - (b.c.score ?? 101));
    const fleetRows = fleets.map((f) => { const ids = db.fleetAndDescendants(f.fleet_id); const rs = rows.filter((r) => ids.includes(r.v.fleet_id)); return { f, n: rs.length, score: rs.length ? sum(rs, (r) => r.c.score) / rs.length : 0 }; }).filter((x) => x.n);
    return `
      <div class="toolbar card mb">${ui.select("fleet", [["", t("All fleets", "Mọi đội xe")], ...fleets.map((f) => [f.fleet_id, (f.parent_fleet_id ? "— " : "") + f.name])], q.fleet || "")}<span class="small muted">${esc(t("Sessions of the last 30 days; compliance with the charging policy (POL-04).", "Phiên sạc 30 ngày qua; tuân thủ chính sách sạc (POL-04)."))}</span><span class="grow"></span>${ui.btn(t("Export CSV", "Xuất CSV"), { icon: "download", action: "export-fltwar", sm: true })}${ui.btn("PDF", { icon: "file", action: "pdf", sm: true })}</div>
      <div class="grid cols-4">
        ${ui.kpi({ label: t("Sessions", "Phiên sạc"), icon: "plug", value: fmt.num(sum(rows, (r) => r.sessions)), foot: fmt.kwh(sum(rows, (r) => r.kwh), 0) })}
        ${ui.kpi({ label: t("Charging cost", "Chi phí sạc"), icon: "receipt", value: fmt.vndShort(sum(rows, (r) => r.cost)) })}
        ${ui.kpi({ label: t("Average compliance", "Tuân thủ trung bình"), icon: "shield", value: fmt.num(rows.length ? sum(rows, (r) => r.c.score) / rows.length : 0), unit: "/100", foot: `${rows.filter((r) => r.c.risk === "HIGH").length} ${t("high-risk trucks", "xe rủi ro cao")}` })}
        ${ui.kpi({ label: t("Battery warranties at risk", "Bảo hành pin có rủi ro"), icon: "alert", value: rows.filter((r) => (r.bw && r.bw.status !== "ACTIVE") || r.c.risk === "HIGH").length, tone: "warn", foot: `${rows.filter((r) => r.bw && r.bw.status === "VOIDED").length} ${t("voided", "đã hủy")}` })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Per truck", "Theo xe"), sub: "FLT-08", flush: true,
          body: ui.table({
            rows, href: G3.canSee("vehicle-detail") ? (r) => `/vehicles/${r.v.vehicle_id}?tab=charging` : null,
            columns: [
              { label: t("Truck", "Xe"), render: (r) => `<b>${esc(r.v.license_plate)}</b><span class="sub">${esc((db.fleet(r.v.fleet_id) || { name: "—" }).name)}</span>` },
              { label: t("Sessions", "Phiên"), align: "right", render: (r) => `${r.sessions}<span class="sub">${r.dcFull} ${esc(t("to 100%", "sạc đầy"))}</span>` },
              { label: t("Energy · cost", "Điện · chi phí"), align: "right", cls: "nowrap", render: (r) => `${fmt.num(r.kwh)} kWh<span class="sub">${fmt.vndShort(r.cost)}</span>` },
              { label: t("Compliance", "Tuân thủ"), cls: "nowrap", render: (r) => (r.c.score === null ? "—" : `<b class="num">${r.c.score}</b> ${ui.sparkline(r.c.trend, r.c.risk === "HIGH" ? "var(--danger)" : "var(--primary)", 56, 18)} ${ui.badge(r.c.risk)}<span class="sub">${r.c.violations_30d} ${esc(t("violations in 30 d", "vi phạm 30 ngày"))}</span>`) },
              { label: t("Warranty truck / battery", "Bảo hành xe / pin"), render: (r) => `<span class="nowrap">${ui.badge(r.vw ? r.vw.status : null)} ${ui.badge(r.bw ? r.bw.status : null)}</span>${r.bw && r.bw.status_reason ? `<span class="sub">${esc(r.bw.status_reason)}</span>` : ""}` },
            ],
          }),
        })}
        ${ui.card({ title: t("Compliance by fleet", "Tuân thủ theo đội xe"), body: fleetRows.length ? ui.hBars({ labels: fleetRows.map((x) => `${x.f.name} (${x.n})`), series: [{ values: fleetRows.map((x) => Math.round(x.score)) }], unit: "/100" }) : ui.empty() })}
      </div>`;
  }

  /** Mock daily driving per driver for the last 7 days (DRV-06), from check-in sessions and stops. */
  function drivingWeek(d) {
    const r = seeded(d.driver_id + "hos");
    return Array.from({ length: 7 }, (_, i) => {
      const off = d.status !== "ACTIVE" || r() < 0.14;
      const hours = off ? 0 : round1(5.5 + r() * 6.2);
      const continuous = off ? 0 : round1(Math.min(hours, 2.2 + r() * 3));
      return { date: db.NOW - (6 - i) * DAY, hours, continuous };
    });
  }
  function complianceReport() {
    const drivers = scope.filter(db.drivers);
    const rows = drivers.map((d) => {
      const week = drivingWeek(d);
      const total = round1(sum(week, (x) => x.hours));
      const maxDay = Math.max(...week.map((x) => x.hours));
      const maxCont = Math.max(...week.map((x) => x.continuous));
      const v = { cont: week.filter((x) => x.continuous > 4).length, day: week.filter((x) => x.hours > 10).length, week: total > 48 ? 1 : 0 };
      return { d, week, total, maxDay, maxCont, v, n: v.cont + v.day + v.week };
    }).sort((a, b) => b.n - a.n || b.total - a.total);
    const heat = (h) => (h === 0 ? "var(--surface-3)" : h > 10 ? "#ef4444" : h > 9 ? "#f59e0b" : h > 7 ? "#34d399" : "#a7f3d0");
    return `
      ${ui.callout("info", esc(t("Driving time per driver over the last 7 days, from check-in sessions; a stop of 15 minutes or more counts as rest. Limits: 4 h continuous, 10 h a day, 48 h a week (DRV-06, planned — mock data).", "Thời gian lái của từng tài xế trong 7 ngày qua, tính từ các phiên nhận xe; dừng từ 15 phút trở lên được tính là nghỉ. Giới hạn: 4 giờ liên tục, 10 giờ/ngày, 48 giờ/tuần (DRV-06, dự kiến — dữ liệu giả lập).")))}
      <div class="grid cols-4 mt">
        ${ui.kpi({ label: t("Drivers", "Tài xế"), icon: "id", value: rows.length })}
        ${ui.kpi({ label: t("Over 4 h continuous", "Quá 4 giờ liên tục"), icon: "clock", value: sum(rows, (r) => r.v.cont), tone: "warn", foot: `${rows.filter((r) => r.v.cont).length} ${t("drivers", "tài xế")}` })}
        ${ui.kpi({ label: t("Over 10 h a day", "Quá 10 giờ/ngày"), icon: "alert", value: sum(rows, (r) => r.v.day), tone: "danger", foot: `${rows.filter((r) => r.v.day).length} ${t("drivers", "tài xế")}` })}
        ${ui.kpi({ label: t("Over 48 h a week", "Quá 48 giờ/tuần"), icon: "users", value: sum(rows, (r) => r.v.week), tone: rows.some((r) => r.v.week) ? "danger" : "ok" })}
      </div>
      <div class="mt">${ui.card({
        title: t("Driving and rest compliance", "Tuân thủ thời gian lái và nghỉ"), sub: "DRV-06", flush: true,
        actions: ui.btn(t("Export CSV", "Xuất CSV"), { icon: "download", action: "export-hos", sm: true }),
        body: ui.table({
          rows, href: G3.canSee("driver-detail") ? (r) => `/drivers/${r.d.driver_id}` : null,
          columns: [
            { label: t("Driver", "Tài xế"), render: (r) => `<b>${esc(r.d.full_name)}</b><span class="sub">${scope.isInternal() ? ui.orgName(r.d.organization_id) : esc(r.d.license_class)}</span>` },
            { label: t("Last 7 days", "7 ngày qua"), render: (r) => `<div class="heat" style="grid-template-columns:repeat(7,18px)">${r.week.map((x) => `<div style="background:${heat(x.hours)}" data-tip="${esc(`${fmt.date(x.date)}: ${fmt.num(x.hours, 1)} h · ${t("longest", "liên tục dài nhất")} ${fmt.num(x.continuous, 1)} h`)}"></div>`).join("")}</div>` },
            { label: t("Week", "Tuần"), render: (r) => `<div class="small"><b>${fmt.num(r.total, 1)}</b> / 48 h</div>${ui.meter((r.total / 48) * 100, r.total > 48 ? "danger" : r.total > 42 ? "warn" : "")}` },
            { label: t("Longest day", "Ngày dài nhất"), align: "right", render: (r) => `<span style="${r.maxDay > 10 ? "color:var(--danger);font-weight:700" : ""}">${fmt.num(r.maxDay, 1)} h</span>` },
            { label: t("Longest continuous", "Liên tục dài nhất"), align: "right", render: (r) => `<span style="${r.maxCont > 4 ? "color:var(--warn);font-weight:700" : ""}">${fmt.num(r.maxCont, 1)} h</span>` },
            { label: t("Violations", "Vi phạm"), render: (r) => (r.n ? [r.v.cont ? `<span class="badge warn">${r.v.cont}× &gt;4h</span>` : "", r.v.day ? `<span class="badge danger">${r.v.day}× &gt;10h</span>` : "", r.v.week ? `<span class="badge danger">&gt;48h</span>` : ""].join(" ") : ui.badge("OK", "ok", t("Compliant", "Đạt"))) },
          ],
        }),
      })}</div>`;
  }

  function efficiencyRows() {
    const drivers = scope.filter(db.drivers).filter((d) => d.status === "ACTIVE");
    const byOrg = {};
    drivers.forEach((d) => { (byOrg[d.organization_id] = byOrg[d.organization_id] || []).push(d); });
    const acc = new Map(drivers.map((d) => [d.driver_id, { d, kwh: 0, cost: 0, offKwh: 0, peakCost: 0, sessions: 0 }]));
    scope.byVehicle(db.charging_sessions, false).filter((s) => s.status === "COMPLETED").forEach((s) => {
      const pool = byOrg[s.organization_id];
      if (!pool) return;
      // Attribute each session to a driver of the truck's organization, stable per session.
      const row = acc.get(pool[hashOf(s.session_id) % pool.length].driver_id);
      const slot = slotAt(s.location_id, s.started_at);
      const tariff = db.tariffs.find((x) => x.location_id === s.location_id);
      const peak = tariff ? Math.max(...tariff.slots.map((x) => x.price_vnd_kwh)) : s.price_vnd_kwh;
      row.kwh += s.energy_kwh; row.cost += s.cost_vnd; row.sessions++; row.peakCost += s.energy_kwh * peak;
      if (["OFF_PEAK", "SOLAR"].includes(slot.label)) row.offKwh += s.energy_kwh;
    });
    return [...acc.values()].filter((r) => r.sessions).map((r) => ({ ...r, share: (r.offKwh / r.kwh) * 100, perKm: r.cost / (r.kwh / 1.12), savings: r.peakCost - r.cost })).sort((a, b) => b.share - a.share);
  }
  function efficiencyReport() {
    const rows = efficiencyRows();
    const share = rows.length ? (sum(rows, (r) => r.offKwh) / sum(rows, (r) => r.kwh)) * 100 : 0;
    return `
      ${ui.callout("info", esc(t("Per driver: share of energy charged off-peak or from solar, electricity cost per km and savings against charging at the peak price. Distance per kWh uses the fleet average of 1.12 kWh/km (DRV-07, planned — mock attribution of sessions to drivers).", "Theo tài xế: tỷ lệ điện sạc giờ thấp điểm hoặc từ điện mặt trời, chi phí điện mỗi km và khoản tiết kiệm so với sạc giá cao điểm. Quãng đường tính theo trung bình đội 1,12 kWh/km (DRV-07, dự kiến — gán phiên sạc cho tài xế là giả lập).")))}
      <div class="grid cols-4 mt">
        ${ui.kpi({ label: t("Off-peak or solar share", "Tỷ lệ thấp điểm/mặt trời"), icon: "sun", value: fmt.pct(share), foot: t("of charged energy", "trên tổng điện sạc") })}
        ${ui.kpi({ label: t("Savings vs peak", "Tiết kiệm so với cao điểm"), icon: "wallet", value: fmt.vndShort(sum(rows, (r) => r.savings)), tone: "ok" })}
        ${ui.kpi({ label: t("Best driver", "Tài xế tốt nhất"), icon: "check", value: rows[0] ? esc(rows[0].d.full_name.split(" ").slice(-2).join(" ")) : "—", foot: rows[0] ? fmt.pct(rows[0].share) : "" })}
        ${ui.kpi({ label: t("Average ₫/km", "₫/km trung bình"), icon: "receipt", value: fmt.num(rows.length ? sum(rows, (r) => r.cost) / sum(rows, (r) => r.kwh / 1.12) : 0), unit: "₫/km" })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Driver charging efficiency", "Hiệu quả sạc của tài xế"), sub: "DRV-07", flush: true,
          actions: ui.btn(t("Export CSV", "Xuất CSV"), { icon: "download", action: "export-eff", sm: true }),
          body: ui.table({
            rows, href: G3.canSee("driver-detail") ? (r) => `/drivers/${r.d.driver_id}` : null,
            columns: [
              { label: "#", render: (r) => `<b>${rows.indexOf(r) + 1}</b>` },
              { label: t("Driver", "Tài xế"), render: (r) => `<b>${esc(r.d.full_name)}</b>${scope.isInternal() ? `<span class="sub">${ui.orgName(r.d.organization_id)}</span>` : ""}` },
              { label: t("Sessions", "Phiên"), align: "right", render: (r) => r.sessions },
              { label: "kWh", align: "right", render: (r) => fmt.num(r.kwh) },
              { label: t("Off-peak / solar", "Thấp điểm / mặt trời"), render: (r) => `<div class="small"><b>${fmt.pct(r.share)}</b></div>${ui.meter(r.share, r.share < 30 ? "warn" : "")}` },
              { label: "₫/km", align: "right", render: (r) => fmt.num(r.perKm) },
              { label: t("Savings vs peak", "Tiết kiệm"), align: "right", render: (r) => fmt.vnd(r.savings) },
            ],
          }),
        })}
        ${ui.card({ title: t("Ranking: off-peak share", "Xếp hạng: tỷ lệ thấp điểm"), body: ui.hBars({ labels: rows.slice(0, 12).map((r) => r.d.full_name), series: [{ values: rows.slice(0, 12).map((r) => Math.round(r.share)), color: ui.PALETTE[0] }], unit: "%" }) })}
      </div>`;
  }

  const REPORT_TABS = {
    operating: { label: ["Operating", "Vận hành"], features: ["MON-14", "MON-16"], render: operatingReport },
    cost: { label: ["Operating cost", "Chi phí vận hành"], features: ["FLT-07"], render: costReport },
    charging: { label: ["Charging & warranty", "Sạc & bảo hành"], features: ["FLT-08"], render: chargingWarrantyReport },
    hos: { label: ["Driving & rest", "Thời gian lái & nghỉ"], features: ["DRV-06"], render: complianceReport },
    efficiency: { label: ["Charging efficiency", "Hiệu quả sạc"], features: ["DRV-07"], render: efficiencyReport },
  };

  G3.page({
    id: "reports",
    path: "/reports",
    title: ["Reports", "Báo cáo"],
    roles: ["FLEET_MANAGER", "ACCOUNTANT", "ORG_ADMIN", "WARRANTY"],
    features: ["MON-14", "MON-16", "FLT-07", "FLT-08", "DRV-06", "DRV-07"],
    nav: { group: "monitoring", order: 6, icon: "chart" },
    render(ctx) {
      const tab = REPORT_TABS[ctx.query.tab] ? ctx.query.tab : "operating";
      return `${ui.pageHead({
        title: t("Reports", "Báo cáo"),
        sub: esc(scope.fleetLimit() ? t("Limited to the trucks of your fleets (FLT-03).", "Giới hạn trong các xe thuộc đội của bạn (FLT-03).") : scope.isInternal() ? t("Every organization in your filter · internal view", "Mọi tổ chức trong bộ lọc · góc nhìn nội bộ") : t(`${scope.org().name} · every truck`, `${scope.org().name} · toàn bộ xe`)),
        features: REPORT_TABS[tab].features,
      })}${ui.tabs(Object.entries(REPORT_TABS).map(([k, x]) => [k, t(...x.label)]), tab)}${REPORT_TABS[tab].render(ctx.query)}`;
    },
    mount(root, ctx) {
      bindCommon(root);
      const on = (action, fn) => { const el = root.querySelector(`[data-action=${action}]`); if (el) el.addEventListener("click", fn); };
      const q = ctx.query;
      on("export-operating", () => {
        const days = ["7", "30", "90"].includes(q.days) ? Number(q.days) : 30;
        const price = Number(q.price) > 0 ? Number(q.price) : 4180;
        const rows = [];
        reportVehicles().filter((v) => !q.vehicle || v.vehicle_id === q.vehicle).forEach((v) => db.dailyReport(v.vehicle_id, days).forEach((r) => rows.push({ ...r, plate: v.license_plate, cost: Math.round(r.energy_kwh * price) })));
        ui.downloadCsv(`operating-report-${days}d.csv`, [{ label: "date", key: "date" }, { label: "plate", key: "plate" }, { label: "distance_km", key: "distance_km" }, { label: "energy_kwh", key: "energy_kwh" }, { label: "kwh_per_km", key: "kwh_per_km" }, { label: "cost_vnd", key: "cost" }, { label: "driving_hours", key: "driving_hours" }], rows);
      });
      on("export-cost", () => {
        const by = ["truck", "driver", "route"].includes(q.by) ? q.by : "truck";
        ui.downloadCsv(`operating-cost-by-${by}.csv`, [{ label: by, key: "label" }, { label: by === "truck" ? "days_driven" : "trips", key: "n" }, { label: "distance_km", csv: (r) => Math.round(r.km) }, { label: "energy_kwh", csv: (r) => Math.round(r.kwh) }, { label: "cost_vnd", csv: (r) => Math.round(r.cost) }, { label: "cost_per_km_vnd", csv: (r) => Math.round(r.perKm) }], costRows(by));
      });
      on("export-fltwar", () => ui.downloadCsv("charging-warranty-report.csv", [{ label: "plate", key: "license_plate" }, { label: "compliance_score", csv: (v) => (db.compliance.find((c) => c.vehicle_id === v.vehicle_id) || {}).score }, { label: "violations_30d", csv: (v) => (db.compliance.find((c) => c.vehicle_id === v.vehicle_id) || {}).violations_30d }, { label: "battery_warranty", csv: (v) => (db.warranties.find((w) => w.object_kind === "BATTERY" && w.object_id === v.battery_id) || {}).status }], reportVehicles().filter((v) => !q.fleet || db.fleetAndDescendants(q.fleet).includes(v.fleet_id))));
      on("pdf", () => ui.toast(t("PDF report generated (illustrative in the prototype)", "Đã tạo báo cáo PDF (minh họa trong bản mẫu)"), "", "file"));
      on("export-hos", () => ui.auditedExport({
        dataType: "DRIVING_SESSIONS", subject: null, filename: "driving-rest-compliance.csv",
        columns: [{ label: "driver", csv: (d) => d.full_name }, { label: "week_hours", csv: (d) => round1(sum(drivingWeek(d), (x) => x.hours)) }, { label: "max_day_hours", csv: (d) => Math.max(...drivingWeek(d).map((x) => x.hours)) }, { label: "max_continuous_hours", csv: (d) => Math.max(...drivingWeek(d).map((x) => x.continuous)) }],
        rows: scope.filter(db.drivers),
      }));
      on("export-eff", () => ui.downloadCsv("driver-charging-efficiency.csv", [{ label: "driver", csv: (r) => r.d.full_name }, { label: "sessions", key: "sessions" }, { label: "energy_kwh", csv: (r) => Math.round(r.kwh) }, { label: "off_peak_share_percent", csv: (r) => Math.round(r.share) }, { label: "cost_per_km_vnd", csv: (r) => Math.round(r.perKm) }, { label: "savings_vs_peak_vnd", csv: (r) => Math.round(r.savings) }], efficiencyRows()));
    },
  });
})();
