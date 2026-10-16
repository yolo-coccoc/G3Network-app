/* Fleet & drivers area.
   - fleets        /fleets        FLT-01 tree, FLT-02 truck membership, FLT-03 manager fleet limits
   - drivers       /drivers       DRV-01 profiles, ACC-10 register by phone, DRV-02 who is checked in
   - driver-detail /drivers/:id   DRV-01, DRV-02 check-in/out and history, DRV-06, DRV-07, SAF-01, MON-11
   - geofences     /geofences     FLT-05 zones drawn on the map, enter/exit alerts, trucks inside
   - safety        /safety        SAF-01/02/03/04/06/07/09 scores, events, cameras, evidence, retention
   Mock data that the core does not hold (membership history, safety events,
   camera health) is generated here, deterministically from the record IDs. */

(function () {
  const { ui, db, fmt, scope } = G3;
  const esc = G3.esc;
  const DAY = db.DAY;
  const HOUR = db.HOUR;
  const MIN = db.MIN;

  /* ---------- Small helpers ---------- */
  function hash(text) {
    let h = 2166136261;
    for (const ch of String(text)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  /** Deterministic random generator keyed by a string. */
  function rng(key) {
    let s = hash(key) || 1;
    return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; };
  }
  const iso = (ms) => new Date(ms).toISOString();
  const currentUserId = () => (scope.user() ? scope.user().user_id : null);
  const daysUntil = (date) => Math.floor((new Date(date + "T23:59:59").getTime() - Date.now()) / DAY);

  /** Bind a listener on the page root for this render only (the root element survives re-renders). */
  function on(root, type, fn) {
    root.addEventListener(type, fn);
    G3.onLeave(() => root.removeEventListener(type, fn));
  }
  /** Filters ([data-filter]), segmented controls and tabs write to the URL query. */
  function bindQueryControls(root) {
    root.querySelectorAll("[data-filter]").forEach((el) => el.addEventListener("change", () => G3.setQuery({ [el.dataset.filter]: el.value })));
    root.querySelectorAll("[data-seg]").forEach((seg) => seg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => G3.setQuery({ [seg.dataset.seg]: b.dataset.value }))));
    root.querySelectorAll("[data-tabs]").forEach((tabs) => tabs.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => G3.setQuery({ [tabs.dataset.tabs]: b.dataset.tab }))));
  }
  function confirmModal({ title, body, label, danger = true, onConfirm }) {
    ui.modal({
      title, body,
      foot: `<button class="btn" data-close>${esc(t("Cancel", "Hủy"))}</button><button class="btn ${danger ? "danger" : "primary"}" data-ok>${esc(label)}</button>`,
      onMount(el, close) { el.querySelector("[data-ok]").addEventListener("click", () => { close(); onConfirm(); }); },
    });
  }
  function plateLink(v) {
    if (!v) return '<span class="muted">—</span>';
    const visible = scope.vehicleIds().has(v.vehicle_id);
    const plate = `<b>${esc(v.license_plate)}</b>`;
    return visible && G3.canSee("vehicle-detail") ? `<a href="#/vehicles/${v.vehicle_id}">${plate}</a>` : plate;
  }
  function truckCell(v) {
    if (!v) return '<span class="muted">—</span>';
    return `${plateLink(v)}<span class="sub mono" style="display:block;color:var(--text-3);font-size:12px">${esc(v.vin)}</span>`;
  }
  const fleetName = (f) => (f ? f.name || f.code : "—");
  const internalMulti = () => scope.isInternal() && !scope.orgFilter();
  const customerOrgs = () => db.organizations.filter((o) => o.kind === "CUSTOMER" && scope.orgIds().includes(o.organization_id));
  const openSessionOfDriver = (driverId) => db.driving_sessions.find((s) => s.driver_id === driverId && !s.ended_at);
  const openSessionOfVehicle = (vehicleId) => db.driving_sessions.find((s) => s.vehicle_id === vehicleId && !s.ended_at);

  /* ---------- Fleet membership history (FLT-02, fleet_vehicle_memberships) ---------- */
  // The core keeps only the current vehicle.fleet_id; this file holds the open/close periods.
  const fleetMemberships = [];
  let membershipNo = 1;
  function addMembership(vehicleId, fleetId, addedAt, addedBy) {
    const row = { fleet_vehicle_membership_id: `fvm-${membershipNo++}`, fleet_id: fleetId, vehicle_id: vehicleId, added_at: addedAt, added_by: addedBy, removed_at: null, removed_by: null, end_kind: null, moved_to_fleet_id: null };
    fleetMemberships.push(row);
    return row;
  }
  function closeMembership(row, kind, byUserId, movedTo) {
    row.removed_at = new Date().toISOString();
    row.removed_by = byUserId;
    row.end_kind = kind;
    row.moved_to_fleet_id = movedTo || null;
  }
  const ORG_MANAGER = { "org-tcx": db.people.tcxFm.user.user_id, "org-mn": db.people.mnAdmin.user.user_id, "org-dn": db.people.dnAdmin.user.user_id };
  (function seedMemberships() {
    db.vehicles.forEach((v, i) => {
      const orgFleets = db.fleets.filter((f) => f.organization_id === v.organization_id);
      if (!orgFleets.length) return;
      const start = new Date(v.acquired_at).getTime();
      const by = ORG_MANAGER[v.organization_id] || null;
      if (!v.fleet_id) {
        const past = addMembership(v.vehicle_id, orgFleets[i % orgFleets.length].fleet_id, iso(start), by);
        Object.assign(past, { removed_at: iso(start + 12 * DAY), removed_by: by, end_kind: "REMOVED" });
        return;
      }
      const other = orgFleets.find((f) => f.fleet_id !== v.fleet_id && !db.fleets.some((c) => c.parent_fleet_id === f.fleet_id));
      if (i % 4 === 1 && other) {
        const moveAt = start + Math.round((Date.now() - start) * 0.4);
        const past = addMembership(v.vehicle_id, other.fleet_id, iso(start), by);
        Object.assign(past, { removed_at: iso(moveAt), removed_by: by, end_kind: "MOVED", moved_to_fleet_id: v.fleet_id });
        addMembership(v.vehicle_id, v.fleet_id, iso(moveAt), by);
      } else {
        addMembership(v.vehicle_id, v.fleet_id, iso(start), by);
      }
    });
  })();
  /** Follow fleet changes made elsewhere in the prototype (another screen moving a truck). */
  function syncMemberships() {
    db.vehicles.forEach((v) => {
      const open = fleetMemberships.find((m) => m.vehicle_id === v.vehicle_id && !m.removed_at);
      if (open && open.fleet_id !== v.fleet_id) closeMembership(open, v.fleet_id ? "MOVED" : "REMOVED", null, v.fleet_id);
      if (v.fleet_id && (!open || open.fleet_id !== v.fleet_id)) addMembership(v.vehicle_id, v.fleet_id, new Date().toISOString(), null);
    });
  }
  /** Put a truck in a fleet: a truck is in at most one fleet, so its open period ends first. */
  function setVehicleFleet(vehicle, fleetId, endKind) {
    const open = fleetMemberships.find((m) => m.vehicle_id === vehicle.vehicle_id && !m.removed_at);
    if (open) closeMembership(open, fleetId ? "MOVED" : endKind || "REMOVED", currentUserId(), fleetId);
    vehicle.fleet_id = fleetId;
    if (fleetId) addMembership(vehicle.vehicle_id, fleetId, new Date().toISOString(), currentUserId());
  }

  /* ---------- Safety model (SAF-01) ---------- */
  const TELEMATICS_EVENTS = {
    HARD_BRAKING: ["Hard braking", "Phanh gấp"],
    HARSH_ACCELERATION: ["Sudden acceleration", "Tăng tốc đột ngột"],
    SHARP_CORNERING: ["Sharp cornering", "Vào cua gấp"],
    SPEEDING: ["Speeding", "Quá tốc độ"],
    LONG_DRIVING: ["Over 4 h driving", "Lái liên tục quá 4 giờ"],
  };
  const CAMERA_EVENTS = {
    DROWSINESS: ["Drowsiness", "Buồn ngủ"],
    DISTRACTION: ["Distraction", "Mất tập trung"],
    PHONE_USE: ["Phone use", "Dùng điện thoại"],
    TAILGATING: ["Tailgating", "Bám đuôi xe trước"],
  };
  const eventLabel = (type) => { const pair = TELEMATICS_EVENTS[type] || CAMERA_EVENTS[type]; return pair ? t(pair[0], pair[1]) : type; };
  const DEFAULT_WEIGHTS = { HARD_BRAKING: 4, HARSH_ACCELERATION: 3, SHARP_CORNERING: 3, SPEEDING: 5, LONG_DRIVING: 6, DROWSINESS: 8, DISTRACTION: 5, PHONE_USE: 7, TAILGATING: 5 };
  // Weights the manager edits on the safety screen; scores are recomputed from them.
  const weights = { ...DEFAULT_WEIGHTS };

  function orgPlan(orgId) {
    const sub = db.subscriptions.find((s) => s.organization_id === orgId);
    return sub ? db.find("plans", sub.plan_id) : null;
  }
  const orgHasAdas = (orgId) => { const p = orgPlan(orgId); return !!(p && p.features.includes("ADAS")); };
  const orgHasCamera = (orgId) => { const p = orgPlan(orgId); return !!(p && p.features.includes("SAF")); };

  const safetyCache = new Map();
  /**
   * Per-100 km event rates of one driver over 30 days. The rates are split
   * from the stored safety_score with the default weights, so the default
   * score equals the one the rest of the prototype shows.
   */
  function driverSafety(driver) {
    if (driver.safety_score === null || driver.safety_score === undefined) return null;
    if (!safetyCache.has(driver.driver_id)) {
      const r = rng(driver.driver_id + "safety");
      const types = Object.keys(TELEMATICS_EVENTS).concat(orgHasAdas(driver.organization_id) ? Object.keys(CAMERA_EVENTS) : []);
      const parts = types.map((type) => (type === "LONG_DRIVING" ? 0.4 : 0.6) + r());
      const total = parts.reduce((a, b) => a + b, 0);
      const penalty = 100 - driver.safety_score;
      const km = Math.round(1800 + r() * 3600);
      const rates = {};
      types.forEach((type, i) => { rates[type] = (penalty * parts[i]) / total / DEFAULT_WEIGHTS[type]; });
      const trend = Array.from({ length: 8 }, (_, i) => Math.max(40, Math.min(100, driver.safety_score + Math.round((r() - 0.5) * 10 + (i - 7) * (r() - 0.4)))));
      safetyCache.set(driver.driver_id, { km, rates, trend });
    }
    const base = safetyCache.get(driver.driver_id);
    const penalty = Object.entries(base.rates).reduce((a, [type, rate]) => a + weights[type] * rate, 0);
    const score = Math.max(0, Math.min(100, Math.round(100 - penalty)));
    const counts = Object.fromEntries(Object.entries(base.rates).map(([type, rate]) => [type, Math.round((rate * base.km) / 100)]));
    const trend = base.trend.slice(0, 7).concat([score]);
    return { ...base, score, counts, trend, eventsPer100: Object.values(base.rates).reduce((a, b) => a + b, 0) };
  }
  const scoreTone = (score) => (score >= 85 ? "ok" : score >= 70 ? "warn" : "danger");
  function scoreCell(driver) {
    const s = driverSafety(driver);
    if (!s) return `<span class="muted small">${esc(t("No driving yet", "Chưa lái"))}</span>`;
    const tone = scoreTone(s.score);
    return `<span class="soc" style="min-width:96px">${ui.meter(s.score, tone === "ok" ? "" : tone)}<b style="color:var(--${tone})">${s.score}</b></span>`;
  }

  /* Safety events per truck (SAF-02): telematics for every plan, camera/ADAS only on the advanced plan. */
  const eventCache = new Map();
  function vehicleEvents(vehicle) {
    if (eventCache.has(vehicle.vehicle_id)) return eventCache.get(vehicle.vehicle_id);
    const r = rng(vehicle.vehicle_id + "events");
    const out = [];
    if (vehicle.live && vehicle.organization_id !== "org-g3" && vehicle.status === "ACTIVE") {
      const orgDrivers = db.drivers.filter((d) => d.organization_id === vehicle.organization_id && d.status === "ACTIVE");
      const adas = orgHasAdas(vehicle.organization_id);
      const tTypes = Object.keys(TELEMATICS_EVENTS);
      const cTypes = Object.keys(CAMERA_EVENTS);
      for (let day = 0; day < 56; day++) {
        const n = Math.floor(r() * 2.6);
        const c = adas ? Math.floor(r() * 1.5) : 0;
        for (let k = 0; k < n + c; k++) {
          const isCamera = k >= n;
          const type = isCamera ? cTypes[Math.floor(r() * cTypes.length)] : tTypes[Math.floor(r() * (r() < 0.15 ? tTypes.length : tTypes.length - 1))];
          const at = db.NOW - day * DAY - Math.floor(r() * 14 * HOUR) - 5 * HOUR;
          const session = db.driving_sessions.find((s) => s.vehicle_id === vehicle.vehicle_id && new Date(s.checked_in_at).getTime() <= at && (!s.ended_at || new Date(s.ended_at).getTime() >= at));
          const driver = session ? session.driver_id : orgDrivers.length ? orgDrivers[Math.floor(r() * orgDrivers.length)].driver_id : null;
          const speed = type === "SPEEDING" ? 72 + Math.floor(r() * 18) : 18 + Math.floor(r() * 52);
          const severe = type === "SPEEDING" ? speed > 82 : type === "DROWSINESS" || type === "PHONE_USE" ? r() < 0.6 : r() < 0.18;
          out.push({ event_id: `se-${vehicle.vehicle_id.slice(4)}-${out.length}`, vehicle_id: vehicle.vehicle_id, organization_id: vehicle.organization_id, driver_id: driver, kind: isCamera ? "CAMERA" : "TELEMATICS", type, severity: severe ? "CRITICAL" : "WARNING", at: iso(at), speed_kmh: speed, road: db.ROADS[(hash(vehicle.vehicle_id) + day) % db.ROADS.length].name, has_clip: isCamera || r() < 0.5 });
        }
      }
    }
    out.sort((a, b) => (a.at < b.at ? 1 : -1));
    eventCache.set(vehicle.vehicle_id, out);
    return out;
  }

  /* Camera health per truck (SAF-07). */
  const CAMERA_STATUS = {
    OK: ["Recording", "Đang ghi", "ok"],
    SIGNAL_LOST: ["Signal lost", "Mất tín hiệu", "muted"],
    COVERED: ["Lens covered", "Bị che ống kính", "danger"],
    MISALIGNED: ["Misaligned", "Lệch góc quay", "warn"],
    POWER_LOSS: ["Power loss", "Mất nguồn", "danger"],
    STORAGE_FULL: ["Storage full", "Đầy bộ nhớ", "warn"],
    CLOCK_DRIFT: ["Clock drift", "Lệch đồng hồ", "warn"],
  };
  const cameraBadge = (status) => { const s = CAMERA_STATUS[status]; return ui.badge(status, s[2], t(s[0], s[1])); };
  const PINNED_CAMERAS = { "veh-007": "SIGNAL_LOST", "veh-019": "SIGNAL_LOST", "veh-010": "COVERED", "veh-016": "MISALIGNED", "veh-021": "STORAGE_FULL", "veh-005": "CLOCK_DRIFT", "veh-025": "POWER_LOSS" };
  function cameraOf(vehicle) {
    if (!vehicle.telematic_id || vehicle.organization_id === "org-g3" || !orgHasCamera(vehicle.organization_id)) return null;
    const r = rng(vehicle.vehicle_id + "camera");
    const status = PINNED_CAMERAS[vehicle.vehicle_id] || (r() < 0.94 ? "OK" : "CLOCK_DRIFT");
    const cause = status === "COVERED" ? "DELIBERATE" : status === "SIGNAL_LOST" ? "SIGNAL" : status === "OK" ? null : "FAULT";
    return {
      serial: `VMC-${String(hash(vehicle.vehicle_id) % 90000 + 10000)}`,
      status, cause,
      uptime: status === "OK" ? 97 + r() * 2.9 : status === "SIGNAL_LOST" ? 61 + r() * 15 : 84 + r() * 10,
      upload: status === "OK" ? 96 + r() * 3.8 : status === "STORAGE_FULL" ? 71 + r() * 8 : 80 + r() * 14,
      storage: status === "STORAGE_FULL" ? 99 : Math.round(20 + r() * 55),
      clock_offset_s: status === "CLOCK_DRIFT" ? 46 + Math.round(r() * 80) : Math.round(r() * 3),
      firmware: r() < 0.8 ? "VM-DMS 4.2.1" : "VM-DMS 4.1.7",
      adas: orgHasAdas(vehicle.organization_id),
      last_frame_at: status === "SIGNAL_LOST" || status === "POWER_LOSS" ? vehicle.live ? iso(new Date(vehicle.live.received_at).getTime() - 20 * MIN) : null : iso(Date.now() - Math.round(r() * 40) * 1000),
      device_days: status === "STORAGE_FULL" ? 2 : 31 + Math.round(r() * 14),
      since: iso(Date.now() - Math.round(10 + r() * 300) * MIN),
    };
  }
  const CAUSE_LABEL = { DELIBERATE: ["Deliberate covering", "Cố ý che"], FAULT: ["Device fault", "Lỗi thiết bị"], SIGNAL: ["Signal loss", "Mất sóng"] };

  /* ================================================================== */
  /* Fleets (FLT-01, FLT-02, FLT-03)                                     */
  /* ================================================================== */
  const canManageFleets = () => scope.has("FLEET_MANAGER", "ORG_ADMIN", "HEAD_ADMIN");
  const canAssignLimits = () => scope.has("ORG_ADMIN", "HEAD_ADMIN", "CO_ADMIN");

  function visibleFleets(includeClosed) {
    const limit = scope.fleetLimit();
    return scope.filter(db.fleets).filter((f) => (includeClosed || f.status !== "CLOSED") && (!limit || limit.includes(f.fleet_id)));
  }
  function fleetPath(fleet) {
    const names = [];
    let f = fleet;
    while (f) { names.unshift(fleetName(f)); f = db.fleet(f.parent_fleet_id); }
    return names.join(" › ");
  }
  const liveChildren = (fleetId) => db.fleets.filter((f) => f.parent_fleet_id === fleetId && f.status !== "CLOSED");
  const vehiclesIn = (fleetIds) => scope.vehicles().filter((v) => fleetIds.includes(v.fleet_id));
  const coversFleet = (m, fleetId) => !m.fleet_ids.length || m.fleet_ids.some((id) => db.fleetAndDescendants(id).includes(fleetId));
  const fleetRoleMembers = (orgId) => db.memberships.filter((m) => m.organization_id === orgId && m.status !== "LOCKED" && !m.left_at && m.roles.some((r) => r === "FLEET_MANAGER" || r === "DISPATCHER"));

  function fleetTotals(fleetId) {
    const ids = db.fleetAndDescendants(fleetId);
    const vehicles = vehiclesIn(ids);
    let km = 0, kwh = 0;
    vehicles.filter((v) => v.live).forEach((v) => db.dailyReport(v.vehicle_id, 7).forEach((r) => { km += r.distance_km; kwh += r.energy_kwh; }));
    const online = vehicles.filter((v) => v.live && v.live.is_online).length;
    const driving = vehicles.filter((v) => v.live && v.live.is_online && v.live.state === "DRIVING").length;
    const soh = vehicles.length ? vehicles.reduce((a, v) => a + v.soh_percent, 0) / vehicles.length : null;
    const vehicleIds = new Set(vehicles.map((v) => v.vehicle_id));
    const alerts = db.notifications.filter((n) => vehicleIds.has(n.vehicle_id) && !n.read_at && n.severity !== "INFO").length;
    const checkedIn = vehicles.filter((v) => openSessionOfVehicle(v.vehicle_id)).length;
    return { vehicles, own: vehicles.filter((v) => v.fleet_id === fleetId).length, online, driving, km, kwh, soh, alerts, checkedIn };
  }

  function treeHtml(fleets, selectedId, query) {
    const visibleIds = new Set(fleets.map((f) => f.fleet_id));
    const q = (query.q || "").toLowerCase();
    const item = (f, depth) => {
      const own = vehiclesIn([f.fleet_id]).length;
      const rolled = vehiclesIn(db.fleetAndDescendants(f.fleet_id)).length;
      const kids = fleets.filter((c) => c.parent_fleet_id === f.fleet_id);
      return `<li class="clickable ${f.fleet_id === selectedId ? "unread" : ""}" data-href="/fleets?fleet=${f.fleet_id}${query.closed ? "&closed=1" : ""}" style="padding-left:${14 + depth * 20}px">
        <span style="color:var(--${f.status === "CLOSED" ? "text-3" : "primary"});margin-top:2px">${ui.icon(kids.length ? "layers" : "truck", 16)}</span>
        <div class="grow"><div class="title">${esc(fleetName(f))} ${f.code ? ui.tag(f.code) : ""}</div>
        <div class="meta">${esc(t(`${own} trucks`, `${own} xe`))}${rolled !== own ? ` · ${esc(t(`${rolled} with sub-fleets`, `${rolled} gồm đội con`))}` : ""}</div></div>
        ${f.status === "CLOSED" ? ui.badge("CLOSED") : ""}</li>${kids.map((c) => item(c, depth + 1)).join("")}`;
    };
    if (q) {
      const matches = fleets.filter((f) => (f.name || "").toLowerCase().includes(q) || (f.code || "").toLowerCase().includes(q));
      if (!matches.length) return ui.empty(t("No fleet matches", "Không có đội xe phù hợp"));
      return `<ul class="list">${matches.map((f) => `<li class="clickable ${f.fleet_id === selectedId ? "unread" : ""}" data-href="/fleets?fleet=${f.fleet_id}"><div class="grow"><div class="title">${esc(fleetName(f))} ${f.code ? ui.tag(f.code) : ""}</div><div class="meta">${esc(fleetPath(f))}${internalMulti() ? " · " + ui.orgName(f.organization_id) : ""}</div></div></li>`).join("")}</ul>`;
    }
    const roots = fleets.filter((f) => !f.parent_fleet_id || !visibleIds.has(f.parent_fleet_id));
    if (!roots.length) return ui.empty(t("No fleets yet", "Chưa có đội xe"));
    const orgIds = [...new Set(roots.map((f) => f.organization_id))];
    return orgIds.map((orgId) => `${orgIds.length > 1 || internalMulti() ? `<div class="small muted strong" style="padding:10px 16px 4px;text-transform:uppercase;letter-spacing:.05em">${ui.orgName(orgId)}</div>` : ""}<ul class="list">${roots.filter((f) => f.organization_id === orgId).map((f) => item(f, 0)).join("")}</ul>`).join("");
  }

  function membershipEnd(m) {
    if (!m.removed_at) return ui.badge("ACTIVE", "ok", t("Member now", "Đang là thành viên"));
    if (m.end_kind === "MOVED") return `<span class="small">${esc(t("Moved to", "Chuyển sang"))} <b>${esc(fleetName(db.fleet(m.moved_to_fleet_id)))}</b></span>`;
    if (m.end_kind === "FLEET_CLOSED") return `<span class="small">${esc(t("Fleet closed", "Đội xe đã đóng"))}</span>`;
    if (m.end_kind === "SOLD") return `<span class="small">${esc(t("Truck sold", "Xe đã bán"))}</span>`;
    return `<span class="small">${esc(t("Removed from the fleet", "Đưa ra khỏi đội"))}</span>`;
  }

  function fleetDetail(f, query) {
    const totals = fleetTotals(f.fleet_id);
    // Sub-fleets are included by default, so a parent fleet with no direct trucks still shows its trucks.
    const includeSub = query.sub !== "0";
    const children = db.fleets.filter((c) => c.parent_fleet_id === f.fleet_id && (query.closed || c.status !== "CLOSED"));
    const members = includeSub ? totals.vehicles : totals.vehicles.filter((v) => v.fleet_id === f.fleet_id);
    const closed = f.status === "CLOSED";
    const manage = canManageFleets() && !closed;
    const historyFleets = includeSub ? db.fleetAndDescendants(f.fleet_id) : [f.fleet_id];
    const history = fleetMemberships.filter((m) => historyFleets.includes(m.fleet_id) && scope.vehicleIds().has(m.vehicle_id)).sort((a, b) => (a.added_at < b.added_at ? 1 : -1));
    const managers = fleetRoleMembers(f.organization_id);
    const covering = managers.filter((m) => coversFleet(m, f.fleet_id));
    const notCovering = managers.filter((m) => !coversFleet(m, f.fleet_id));

    const head = `<div class="card mb"><div class="card-head"><h3>${esc(fleetName(f))} ${f.code ? ui.tag(f.code) : ""} <span class="sub">${esc(fleetPath(f))}${internalMulti() ? " · " + ui.orgName(f.organization_id) : ""}</span></h3>${closed ? ui.badge("CLOSED") : ui.badge("ACTIVE")}</div>
      <div class="card-body row between">${closed ? `<span class="small muted">${esc(t("Closed", "Đã đóng"))} ${fmt.date(f.deleted_at)} · ${esc(f.status_reason || "")}</span>` : `<span class="small muted">${esc(t("Totals roll up from every fleet below (FLT-01).", "Số liệu cộng dồn từ mọi đội con bên dưới (FLT-01)."))}</span>`}
      ${manage ? `<div class="row">${ui.btn(t("Sub-fleet", "Đội con"), { icon: "plus", sm: true, action: "fleet-create-child" })}${ui.btn(t("Rename", "Đổi tên"), { icon: "edit", sm: true, action: "fleet-rename" })}${scope.fleetLimit() && scope.fleetLimit().includes(f.fleet_id) && scope.membership().fleet_ids.includes(f.fleet_id) ? "" : ui.btn(t("Move", "Di chuyển"), { icon: "layers", sm: true, action: "fleet-move" })}${ui.btn(t("Close fleet", "Đóng đội"), { icon: "x", sm: true, kind: "danger", action: "fleet-close" })}</div>` : ""}</div></div>`;

    const kpis = `<div class="grid cols-4">
      ${ui.kpi({ label: t("Trucks", "Số xe"), icon: "truck", value: totals.vehicles.length, foot: esc(t(`${totals.own} direct · ${totals.vehicles.length - totals.own} below`, `${totals.own} trực tiếp · ${totals.vehicles.length - totals.own} ở đội con`)) })}
      ${ui.kpi({ label: t("Online now", "Trực tuyến"), icon: "signal", value: `${totals.online}<small>/ ${totals.vehicles.filter((v) => v.live).length}</small>`, foot: esc(t(`${totals.driving} driving · ${totals.checkedIn} with a driver`, `${totals.driving} đang chạy · ${totals.checkedIn} có tài xế`)) })}
      ${ui.kpi({ label: t("Distance, 7 days", "Quãng đường 7 ngày"), icon: "route", value: fmt.num(totals.km), unit: "km", foot: `${fmt.num(totals.kwh / 1000, 1)} MWh · ${fmt.num(totals.km ? totals.kwh / totals.km : 0, 2)} kWh/km` })}
      ${ui.kpi({ label: t("Open alerts", "Cảnh báo mở"), icon: "bell", value: totals.alerts, tone: totals.alerts ? "danger" : "ok", foot: `${t("Avg. SOH", "SOH TB")} ${totals.soh === null ? "—" : fmt.pct(totals.soh, 1)}` })}
    </div>`;

    const childCard = children.length ? ui.card({
      title: t("Sub-fleets", "Đội con"), sub: esc(t("each with everything below it", "mỗi đội gồm cả các đội bên dưới")), flush: true,
      body: ui.table({
        rows: children, href: (c) => `/fleets?fleet=${c.fleet_id}`,
        columns: [
          { label: t("Fleet", "Đội xe"), render: (c) => `<b>${esc(fleetName(c))}</b> ${c.code ? ui.tag(c.code) : ""}` },
          { label: t("Trucks", "Xe"), align: "right", render: (c) => vehiclesIn(db.fleetAndDescendants(c.fleet_id)).length },
          { label: t("Driving now", "Đang chạy"), align: "right", render: (c) => vehiclesIn(db.fleetAndDescendants(c.fleet_id)).filter((v) => v.live && v.live.is_online && v.live.state === "DRIVING").length },
          { label: t("km (7 d)", "km (7 ngày)"), align: "right", render: (c) => fmt.num(fleetTotals(c.fleet_id).km) },
          { label: t("Status", "Trạng thái"), render: (c) => ui.badge(c.status) },
        ],
      }),
    }) : "";

    const memberCard = ui.card({
      title: t("Member trucks", "Xe thành viên"), sub: `${members.length} · FLT-02`, flush: true,
      actions: `<label class="check small"><input type="checkbox" data-sub ${includeSub ? "checked" : ""}> ${esc(t("Include sub-fleets", "Gồm đội con"))}</label>${manage ? ui.btn(t("Add truck by VIN", "Thêm xe theo VIN"), { icon: "plus", sm: true, kind: "primary", action: "fleet-add-vehicle" }) : ""}`,
      body: ui.table({
        rows: members, empty: t("No truck in this fleet yet", "Đội chưa có xe nào"),
        columns: [
          { label: t("Truck", "Xe"), render: truckCell },
          { label: includeSub ? t("Fleet · model", "Đội · mẫu xe") : t("Model", "Mẫu xe"), cls: "nowrap", render: (v) => (includeSub ? `${esc(fleetName(db.fleet(v.fleet_id)))}<span class="sub">${esc(db.model(v.vehicle_model_id).code)}</span>` : esc(db.model(v.vehicle_model_id).code)) },
          { label: t("State", "Trạng thái"), render: (v) => ui.vehicleState(v) },
          { label: t("Battery", "Pin"), render: (v) => (v.live ? ui.soc(v.live.soc_percent) : '<span class="muted">—</span>') },
          { label: t("Driver now", "Tài xế hiện tại"), cls: "nowrap", render: (v) => (v.driver_id ? `<a href="#/drivers/${v.driver_id}">${ui.driverName(v.driver_id)}</a>` : `<span class="muted small">${esc(t("Nobody", "Chưa có"))}</span>`) },
          manage ? { label: "", align: "right", render: (v) => (v.fleet_id === f.fleet_id ? ui.btn("", { icon: "x", sm: true, kind: "ghost", attrs: `data-remove-vehicle="${v.vehicle_id}" title="${esc(t("Remove from the fleet", "Đưa ra khỏi đội"))}"` }) : "") } : null,
        ].filter(Boolean),
      }),
    });

    const historyCard = ui.card({
      title: t("Membership history", "Lịch sử thành viên"), sub: esc(t("who was in this fleet, and when", "xe nào thuộc đội, vào lúc nào")), flush: true,
      body: ui.table({
        rows: history.slice(0, 14), empty: t("No membership yet", "Chưa có lịch sử"),
        columns: [
          { label: t("Truck", "Xe"), render: (m) => `${plateLink(db.vehicle(m.vehicle_id))}${includeSub ? `<span class="sub">${esc(fleetName(db.fleet(m.fleet_id)))}</span>` : ""}` },
          { label: t("Added", "Thêm vào"), render: (m) => `${fmt.date(m.added_at)}<span class="sub">${ui.userName(m.added_by)}</span>` },
          { label: t("Left · how", "Rời đội · lý do"), render: (m) => (m.removed_at ? `${fmt.date(m.removed_at)} · ${esc(t(`${Math.round((new Date(m.removed_at).getTime() - new Date(m.added_at).getTime()) / DAY)} d`, `${Math.round((new Date(m.removed_at).getTime() - new Date(m.added_at).getTime()) / DAY)} ngày`))}<span class="sub">${membershipEnd(m)} · ${ui.userName(m.removed_by)}</span>` : membershipEnd(m)) },
        ],
      }),
    });

    const managerCard = ui.card({
      title: t("Managers of this fleet", "Người quản lý đội này"), sub: "FLT-03",
      actions: canAssignLimits() && !closed && notCovering.some((m) => m.fleet_ids.length) ? ui.btn(t("Give access", "Cấp quyền"), { icon: "plus", sm: true, action: "fleet-grant" }) : "",
      flush: true,
      body: `${covering.length ? `<ul class="list">${covering.map((m) => {
        const user = db.user(m.user_id);
        const scopeText = m.fleet_ids.length ? t(`Limited to ${m.fleet_ids.map((id) => fleetName(db.fleet(id))).join(", ")} and below`, `Giới hạn: ${m.fleet_ids.map((id) => fleetName(db.fleet(id))).join(", ")} và các đội con`) : t("No limit = whole organization", "Không giới hạn = toàn tổ chức");
        return `<li><span class="avatar">${esc(ui.initials(user.full_name))}</span><div class="grow"><div class="title">${esc(user.full_name)} ${m.status === "INVITED" ? ui.badge("INVITED") : ""}</div><div class="meta">${esc(m.roles.map(G3.roleLabel).join(", "))} · ${esc(scopeText)}</div></div>${canAssignLimits() ? ui.btn(t("Fleet limit", "Giới hạn đội"), { sm: true, kind: "ghost", icon: "edit", attrs: `data-edit-limit="${m.membership_id}"` }) : ""}</li>`;
      }).join("")}</ul>` : ui.empty(t("No fleet manager or dispatcher covers this fleet", "Chưa có quản lý đội hay điều vận nào phụ trách đội này"))}
      <div class="card-body" style="border-top:1px solid var(--border)">${ui.callout("info", esc(t("A member whose fleet-level roles (fleet manager, dispatcher) have no fleet limit sees the whole organization; a limit covers the chosen fleets and everything below them. Only the organization admin assigns limits.", "Thành viên có vai trò cấp đội (quản lý đội, điều vận) mà không bị giới hạn đội sẽ xem toàn bộ tổ chức; giới hạn bao gồm các đội được chọn và mọi đội bên dưới. Chỉ quản trị tổ chức mới gán giới hạn.")))}</div>`,
    });

    return `${head}${kpis}${childCard ? `<div class="mt">${childCard}</div>` : ""}<div class="mt">${memberCard}</div><div class="grid cols-2 mt">${historyCard}${managerCard}</div>`;
  }

  function fleetForm({ title, fleet, parentId, onSave }) {
    const orgId = fleet ? fleet.organization_id : parentId ? db.fleet(parentId).organization_id : scope.isInternal() ? scope.orgFilter() : scope.org().organization_id;
    const limit = scope.fleetLimit();
    const fields = [];
    if (!fleet && !parentId && !orgId) fields.push({ name: "organization_id", label: t("Organization", "Tổ chức"), type: "select", required: true, options: customerOrgs().map((o) => [o.organization_id, o.short_name]) });
    fields.push({ name: "name", label: t("Name", "Tên đội"), value: fleet ? fleet.name || "" : "", placeholder: t("Depot Long Thành", "Depot Long Thành") });
    fields.push({ name: "code", label: t("Code", "Mã đội"), value: fleet ? fleet.code || "" : "", placeholder: "LT", hint: t("Optional, unique in the organization. A fleet needs a name or a code.", "Không bắt buộc, duy nhất trong tổ chức. Đội cần có tên hoặc mã.") });
    if (!fleet) {
      const parents = visibleFleets(false).filter((p) => !orgId || p.organization_id === orgId);
      fields.push({ name: "parent", label: t("Sits under", "Thuộc đội"), type: "select", value: parentId || "", options: [...(limit ? [] : [["", t("— Top level —", "— Cấp cao nhất —")]]), ...parents.map((p) => [p.fleet_id, fleetPath(p)])], hint: limit ? t("Your fleet limit lets you create fleets only under your fleets.", "Giới hạn đội chỉ cho phép bạn tạo đội con trong các đội của mình.") : "" });
    }
    if (fleet) fields.push({ name: "reason", label: t("Change reason", "Lý do thay đổi"), required: true, hint: t("Fleets keep a change history (FL-08).", "Đội xe có lịch sử thay đổi (FL-08).") });
    ui.formModal({
      title, fields, submitLabel: t("Save", "Lưu"),
      onSubmit(values) {
        if (!values.name && !values.code) return t("Give the fleet a name or a code.", "Đội cần có tên hoặc mã.");
        const org = (fleet && fleet.organization_id) || (values.parent && db.fleet(values.parent).organization_id) || orgId || values.organization_id;
        if (values.code && db.fleets.some((x) => x.organization_id === org && x.status !== "CLOSED" && x.code && x.code.toLowerCase() === values.code.toLowerCase() && (!fleet || x.fleet_id !== fleet.fleet_id))) return t(`Code "${values.code}" is already used by another fleet of this organization.`, `Mã "${values.code}" đã được đội khác trong tổ chức sử dụng.`);
        return onSave(values, org);
      },
    });
  }

  function mountFleets(root, ctx, selected) {
    bindQueryControls(root);
    const subBox = root.querySelector("[data-sub]");
    if (subBox) subBox.addEventListener("change", (e) => G3.setQuery({ sub: e.target.checked ? null : "0" }));
    const closedBox = root.querySelector("[data-closed]");
    if (closedBox) closedBox.addEventListener("change", (e) => G3.setQuery({ closed: e.target.checked ? "1" : null }));
    on(root, "click", (e) => {
      const btn = e.target.closest("[data-action], [data-remove-vehicle], [data-edit-limit]");
      if (!btn) return;
      const action = btn.dataset.action;
      if (action === "fleet-create" || action === "fleet-create-child") {
        fleetForm({
          title: t("New fleet", "Tạo đội xe"), parentId: action === "fleet-create-child" ? selected.fleet_id : scope.fleetLimit() ? scope.membership().fleet_ids[0] : null,
          onSave(values, org) {
            if (!org) return t("Choose the organization.", "Chọn tổ chức.");
            const fleet = { fleet_id: db.uid("flt"), organization_id: org, name: values.name || null, code: values.code || null, parent_fleet_id: values.parent || (action === "fleet-create-child" ? selected.fleet_id : null), status: "ACTIVE", created_at: new Date().toISOString() };
            db.fleets.push(fleet);
            G3.setQuery({ fleet: fleet.fleet_id, q: null });
            ui.toast(t(`Fleet ${fleetName(fleet)} created`, `Đã tạo đội ${fleetName(fleet)}`));
          },
        });
      } else if (action === "fleet-rename") {
        fleetForm({
          title: t("Rename fleet", "Đổi tên đội xe"), fleet: selected,
          onSave(values) { selected.name = values.name || null; selected.code = values.code || null; selected.status_reason = values.reason; G3.render(); ui.toast(t("Fleet renamed", "Đã đổi tên đội")); },
        });
      } else if (action === "fleet-move") {
        const blocked = db.fleetAndDescendants(selected.fleet_id);
        const limit = scope.fleetLimit();
        const options = db.fleets.filter((p) => p.organization_id === selected.organization_id && p.status !== "CLOSED" && !blocked.includes(p.fleet_id) && (!limit || limit.includes(p.fleet_id)));
        ui.formModal({
          title: t(`Move ${fleetName(selected)}`, `Di chuyển ${fleetName(selected)}`),
          intro: ui.callout("info", esc(t("Moving a fleet changes what managers limited to its old and new parents can see (FLT-03). Its trucks and sub-fleets move with it.", "Di chuyển đội làm thay đổi phạm vi xem của người quản lý được giới hạn ở đội cha cũ và mới (FLT-03). Xe và đội con đi cùng đội."))),
          fields: [
            { name: "parent", label: t("New parent", "Đội cha mới"), type: "select", value: selected.parent_fleet_id || "", options: [...(limit ? [] : [["", t("— Top level —", "— Cấp cao nhất —")]]), ...options.map((p) => [p.fleet_id, fleetPath(p)])] },
            { name: "reason", label: t("Change reason", "Lý do thay đổi"), required: true },
          ],
          submitLabel: t("Move", "Di chuyển"),
          onSubmit(values) {
            if ((values.parent || null) === (selected.parent_fleet_id || null)) return t("The fleet is already there.", "Đội đã ở vị trí này.");
            selected.parent_fleet_id = values.parent || null;
            selected.status_reason = values.reason;
            G3.render();
            ui.toast(t(`Moved under ${values.parent ? fleetName(db.fleet(values.parent)) : "the top level"}`, `Đã chuyển vào ${values.parent ? fleetName(db.fleet(values.parent)) : "cấp cao nhất"}`));
          },
        });
      } else if (action === "fleet-close") {
        const kids = liveChildren(selected.fleet_id);
        if (kids.length) {
          ui.modal({ title: t("Cannot close this fleet", "Không thể đóng đội này"), body: ui.callout("warn", esc(t(`It still has ${kids.length} open sub-fleet(s): ${kids.map(fleetName).join(", ")}. Move or close them first.`, `Đội vẫn còn ${kids.length} đội con đang hoạt động: ${kids.map(fleetName).join(", ")}. Hãy di chuyển hoặc đóng chúng trước.`))) });
          return;
        }
        const trucks = db.vehicles.filter((v) => v.fleet_id === selected.fleet_id);
        const limited = db.memberships.filter((m) => m.fleet_ids.includes(selected.fleet_id));
        const zones = db.geofences.filter((g) => g.fleet_id === selected.fleet_id);
        ui.reasonModal({
          title: t(`Close ${fleetName(selected)}`, `Đóng ${fleetName(selected)}`),
          intro: ui.callout("warn", esc(t(`${trucks.length} truck(s) leave the fleet (their membership ends today), ${limited.length} member(s) lose this fleet from their limit and ${zones.length} geofence(s) stop applying. The history is kept.`, `${trucks.length} xe rời đội (kết thúc thành viên hôm nay), ${limited.length} thành viên mất đội này khỏi giới hạn và ${zones.length} vùng địa lý ngừng áp dụng. Lịch sử được giữ lại.`))),
          submitLabel: t("Close fleet", "Đóng đội"),
          onSubmit(reason) {
            trucks.forEach((v) => { const open = fleetMemberships.find((m) => m.vehicle_id === v.vehicle_id && !m.removed_at); if (open) closeMembership(open, "FLEET_CLOSED", null); v.fleet_id = null; });
            limited.forEach((m) => { m.fleet_ids = m.fleet_ids.filter((id) => id !== selected.fleet_id); });
            Object.assign(selected, { status: "CLOSED", status_reason: reason, deleted_at: new Date().toISOString() });
            G3.setQuery({ closed: "1" });
            ui.toast(t("Fleet closed", "Đã đóng đội xe"));
          },
        });
      } else if (action === "fleet-add-vehicle") {
        const candidates = scope.vehicles().filter((v) => v.organization_id === selected.organization_id && v.fleet_id !== selected.fleet_id);
        let confirmedVin = null;
        ui.formModal({
          title: t(`Add a truck to ${fleetName(selected)}`, `Thêm xe vào ${fleetName(selected)}`),
          intro: `<p class="small muted">${esc(t("A truck is in at most one fleet at a time: adding a truck that is in another fleet ends its old membership.", "Mỗi xe chỉ thuộc tối đa một đội tại một thời điểm: thêm xe đang ở đội khác sẽ kết thúc thành viên cũ."))}</p><datalist id="fleet-vins">${candidates.map((v) => `<option value="${esc(v.vin)}">${esc(v.license_plate)}</option>`).join("")}</datalist>`,
          fields: [{ name: "vin", label: "VIN", required: true, placeholder: "LZT262…", attrs: 'list="fleet-vins" maxlength="17" style="text-transform:uppercase;font-family:var(--mono)"', hint: t("17 characters, as on the registration papers.", "17 ký tự, như trên giấy đăng ký xe.") }],
          submitLabel: t("Add", "Thêm"),
          onSubmit(values) {
            const vin = values.vin.toUpperCase();
            if (!/^[A-Z0-9]{17}$/.test(vin)) return t("A VIN has exactly 17 letters and digits.", "VIN gồm đúng 17 chữ cái và chữ số.");
            const v = db.vehicles.find((x) => x.vin === vin);
            if (!v || (!scope.vehicleIds().has(v.vehicle_id) && v.organization_id === selected.organization_id)) return t("No truck of yours has this VIN.", "Không tìm thấy xe nào của bạn có VIN này.");
            if (v.organization_id !== selected.organization_id) return t("This truck belongs to another organization: a fleet only holds its own organization's trucks.", "Xe này thuộc tổ chức khác: đội xe chỉ chứa xe của chính tổ chức.");
            if (v.fleet_id === selected.fleet_id) return t(`${v.license_plate} is already in this fleet.`, `Xe ${v.license_plate} đã ở trong đội này.`);
            if (v.fleet_id && confirmedVin !== vin) {
              confirmedVin = vin;
              return t(`${v.license_plate} is in ${fleetName(db.fleet(v.fleet_id))} now. Press Add again to move it (its old membership ends).`, `Xe ${v.license_plate} đang thuộc ${fleetName(db.fleet(v.fleet_id))}. Bấm Thêm lần nữa để chuyển (thành viên cũ sẽ kết thúc).`);
            }
            setVehicleFleet(v, selected.fleet_id);
            G3.render();
            ui.toast(t(`${v.license_plate} added to ${fleetName(selected)}`, `Đã thêm ${v.license_plate} vào ${fleetName(selected)}`));
          },
        });
      } else if (btn.dataset.removeVehicle) {
        const v = db.vehicle(btn.dataset.removeVehicle);
        confirmModal({
          title: t(`Remove ${v.license_plate}`, `Đưa ${v.license_plate} ra khỏi đội`),
          body: `<p>${esc(t(`${v.license_plate} leaves ${fleetName(selected)} and is in no fleet until it is added again. Its membership period is closed today; who removed it and when stay in the history.`, `Xe ${v.license_plate} rời ${fleetName(selected)} và không thuộc đội nào cho đến khi được thêm lại. Giai đoạn thành viên kết thúc hôm nay; người thực hiện và thời điểm được lưu trong lịch sử.`))}</p>${ui.callout("info", esc(t("Managers limited to this fleet no longer see the truck (FLT-03).", "Người quản lý bị giới hạn ở đội này sẽ không còn thấy xe (FLT-03).")))}`,
          label: t("Remove", "Đưa ra"),
          onConfirm() { setVehicleFleet(v, null, "REMOVED"); G3.render(); ui.toast(t(`${v.license_plate} removed from the fleet`, `Đã đưa ${v.license_plate} ra khỏi đội`)); },
        });
      } else if (btn.dataset.editLimit || action === "fleet-grant") {
        const orgFleets = db.fleets.filter((x) => x.organization_id === selected.organization_id && x.status !== "CLOSED");
        const members = fleetRoleMembers(selected.organization_id);
        const fixed = btn.dataset.editLimit ? db.memberships.find((m) => m.membership_id === btn.dataset.editLimit) : null;
        const grantable = members.filter((m) => m.fleet_ids.length && !coversFleet(m, selected.fleet_id));
        const fields = [];
        if (!fixed) fields.push({ name: "membership", label: t("Member", "Thành viên"), type: "select", options: grantable.map((m) => [m.membership_id, `${db.user(m.user_id).full_name} — ${m.roles.map(G3.roleLabel).join(", ")}`]) });
        fields.push({ name: "fleets", label: t("Fleets", "Các đội"), type: "checkboxes", value: fixed ? fixed.fleet_ids : [selected.fleet_id], options: orgFleets.map((x) => [x.fleet_id, fleetPath(x)]), full: true, hint: t("Leave every box empty for no limit (the whole organization).", "Bỏ chọn tất cả = không giới hạn (toàn tổ chức).") });
        ui.formModal({
          title: fixed ? t(`Fleet limit of ${db.user(fixed.user_id).full_name}`, `Giới hạn đội của ${db.user(fixed.user_id).full_name}`) : t(`Give access to ${fleetName(selected)}`, `Cấp quyền ${fleetName(selected)}`),
          intro: `<p class="small muted">${esc(t("Applies to the member's fleet-level roles (fleet manager, dispatcher) and to every fleet below the chosen ones.", "Áp dụng cho vai trò cấp đội của thành viên (quản lý đội, điều vận) và mọi đội bên dưới các đội được chọn."))}</p>`,
          fields, submitLabel: t("Save limit", "Lưu giới hạn"),
          onSubmit(values) {
            const m = fixed || db.memberships.find((x) => x.membership_id === values.membership);
            if (!m) return t("Choose a member.", "Chọn thành viên.");
            if (fixed) m.fleet_ids = values.fleets;
            else m.fleet_ids = [...new Set(m.fleet_ids.concat(values.fleets))];
            G3.render();
            ui.toast(m.fleet_ids.length ? t("Fleet limit saved", "Đã lưu giới hạn đội") : t("Limit removed: the member sees the whole organization", "Đã bỏ giới hạn: thành viên xem toàn tổ chức"));
          },
        });
      }
    });
  }

  G3.page({
    id: "fleets",
    path: "/fleets",
    title: ["Fleets", "Đội xe"],
    roles: ["FLEET_MANAGER", "ORG_ADMIN"],
    features: ["FLT-01", "FLT-02", "FLT-03"],
    nav: { group: "fleet", order: 1, icon: "layers" },
    render(ctx) {
      syncMemberships();
      const { query } = ctx;
      const fleets = visibleFleets(!!query.closed);
      const selected = fleets.find((f) => f.fleet_id === query.fleet) || fleets.find((f) => f.status !== "CLOSED") || null;
      ctx.selected = selected;
      const limit = scope.fleetLimit();
      const withoutFleet = limit ? [] : scope.vehicles().filter((v) => !v.fleet_id && v.organization_id !== "org-g3" && db.fleets.some((f) => f.organization_id === v.organization_id));
      const sub = limit
        ? t(`Your fleet limit: ${scope.membership().fleet_ids.map((id) => fleetName(db.fleet(id))).join(", ")} and the fleets below (FLT-03).`, `Giới hạn đội của bạn: ${scope.membership().fleet_ids.map((id) => fleetName(db.fleet(id))).join(", ")} và các đội con (FLT-03).`)
        : internalMulti() ? t("Every customer's fleet structure · internal view", "Cơ cấu đội xe của mọi khách hàng · góc nhìn nội bộ") : t(`${scope.isInternal() ? db.org(scope.orgFilter()).name : scope.org().name} · regions, branches and depots as you organize them`, `${scope.isInternal() ? db.org(scope.orgFilter()).name : scope.org().name} · miền, chi nhánh, bãi xe theo cách bạn tổ chức`);
      return `${ui.pageHead({
        title: t("Fleets", "Đội xe"), sub: esc(sub), features: ["FLT-01", "FLT-02", "FLT-03"],
        actions: canManageFleets() ? ui.btn(t("New fleet", "Tạo đội xe"), { icon: "plus", kind: "primary", action: "fleet-create" }) : "",
      })}
      <div class="grid side-l">
        <div class="stack">
          ${ui.card({
            title: t("Fleet tree", "Cây đội xe"), sub: `${fleets.filter((f) => f.status !== "CLOSED").length}`, flush: true,
            body: `<div class="toolbar">${ui.search("q", query.q, t("Name or code", "Tên hoặc mã"))}<label class="check small"><input type="checkbox" data-closed ${query.closed ? "checked" : ""}> ${esc(t("Closed", "Đã đóng"))}</label></div>${treeHtml(fleets, selected && selected.fleet_id, query)}`,
          })}
          ${withoutFleet.length ? ui.card({
            title: t("Trucks in no fleet", "Xe chưa thuộc đội nào"), sub: `${withoutFleet.length}`, flush: true,
            body: `<ul class="list">${withoutFleet.map((v) => `<li><div class="grow"><div>${plateLink(v)}</div><div class="meta mono">${esc(v.vin)}</div></div><span class="small muted">${internalMulti() ? ui.orgName(v.organization_id) : esc(db.model(v.vehicle_model_id).code)}</span></li>`).join("")}</ul>`,
          }) : ""}
        </div>
        <div>${selected ? fleetDetail(selected, query) : ui.card({ body: ui.empty(t("Create the first fleet to group your trucks.", "Hãy tạo đội xe đầu tiên để nhóm các xe.")) })}</div>
      </div>`;
    },
    mount(root, ctx) { mountFleets(root, ctx, ctx.selected); },
  });

  /* ================================================================== */
  /* Drivers (DRV-01, ACC-10, DRV-02)                                    */
  /* ================================================================== */
  const LICENSE_CLASSES = ["B", "C1", "C", "D1", "D2", "D", "BE", "C1E", "CE", "D1E", "D2E", "DE"];
  const canEditDrivers = () => scope.has("FLEET_MANAGER", "ORG_ADMIN", "HEAD_ADMIN");
  // Profile change history kept by this file (driver_history in the backend).
  const driverHistory = {};

  function licenceCell(d, withNumber = true) {
    const left = daysUntil(d.license_expires_on);
    const badge = left < 0 ? ui.badge("EXPIRED") : left < 30 ? ui.badge("WARN", "warn", t(`${left} days left`, `Còn ${left} ngày`)) : "";
    return `${withNumber ? `<span class="mono">${esc(d.license_number)}</span> ${ui.tag(d.license_class)}<span class="sub">` : ""}${fmt.date(d.license_expires_on)} ${badge}${withNumber ? "</span>" : ""}`;
  }
  function accountBadge(d) {
    const m = db.memberships.find((x) => x.membership_id === d.membership_id);
    return m && m.status === "INVITED" ? ui.badge("INVITED", "info", t("Awaiting app claim", "Chờ kích hoạt trên app")) : "";
  }
  function checkedInCell(d) {
    const s = openSessionOfDriver(d.driver_id);
    if (!s) return `<span class="muted small">${esc(t("Not checked in", "Chưa nhận xe"))}</span>`;
    const v = db.vehicle(s.vehicle_id);
    return `${plateLink(v)}<span class="sub">${G3.label(s.check_in_method)} · ${fmt.ago(s.checked_in_at)}${v.organization_id !== d.organization_id ? " · " + esc(t("other organization", "tổ chức khác")) : ""}</span>`;
  }

  function filteredDrivers(query) {
    const q = (query.q || "").toLowerCase().replace(/\s/g, "");
    return scope.filter(db.drivers).filter((d) => {
      if (query.status && d.status !== query.status) return false;
      const left = daysUntil(d.license_expires_on);
      if (query.licence === "expired" && left >= 0) return false;
      if (query.licence === "expiring" && (left < 0 || left >= 30)) return false;
      if (query.checked === "in" && !openSessionOfDriver(d.driver_id)) return false;
      if (q && !(d.full_name.toLowerCase().replace(/\s/g, "").includes(q) || d.phone_number.includes(q) || d.license_number.includes(q))) return false;
      return true;
    }).sort((a, b) => (a.status === b.status ? a.full_name.localeCompare(b.full_name, "vi") : a.status === "ACTIVE" ? -1 : 1));
  }

  function statusChange(d) {
    const deactivate = d.status === "ACTIVE";
    const session = openSessionOfDriver(d.driver_id);
    ui.reasonModal({
      title: deactivate ? t(`Deactivate ${d.full_name}`, `Ngừng hoạt động ${d.full_name}`) : t(`Reactivate ${d.full_name}`, `Kích hoạt lại ${d.full_name}`),
      intro: deactivate
        ? ui.callout("warn", esc(t(`An inactive driver cannot check in to any truck. ${session ? `Their open session on ${db.vehicle(session.vehicle_id).license_plate} ends now.` : ""} The profile and its history are kept.`, `Tài xế ngừng hoạt động không thể nhận xe. ${session ? `Phiên lái trên xe ${db.vehicle(session.vehicle_id).license_plate} kết thúc ngay.` : ""} Hồ sơ và lịch sử được giữ lại.`)))
        : `<p class="small muted">${esc(t("The driver can check in again, as long as the licence is valid and the account is active.", "Tài xế có thể nhận xe lại, nếu giấy phép còn hạn và tài khoản đang hoạt động."))}</p>`,
      submitLabel: deactivate ? t("Deactivate", "Ngừng hoạt động") : t("Reactivate", "Kích hoạt lại"),
      danger: deactivate,
      onSubmit(reason) {
        if (deactivate && session) endSession(session, "DRIVER_REMOVED");
        logDriverChange(d, deactivate ? t("Status → Inactive", "Trạng thái → Ngừng hoạt động") : t("Status → Active", "Trạng thái → Hoạt động"), reason);
        d.status = deactivate ? "INACTIVE" : "ACTIVE";
        d.status_reason = reason;
        G3.render();
        ui.toast(deactivate ? t("Driver deactivated", "Đã ngừng hoạt động tài xế") : t("Driver reactivated", "Đã kích hoạt lại tài xế"));
      },
    });
  }
  function logDriverChange(d, change, reason) {
    (driverHistory[d.driver_id] = driverHistory[d.driver_id] || []).unshift({ at: new Date().toISOString(), by: currentUserId(), change, reason });
  }
  function endSession(session, cause) {
    session.ended_at = new Date().toISOString();
    session.end_cause = cause;
    const v = db.vehicle(session.vehicle_id);
    if (v && v.driver_id === session.driver_id) v.driver_id = null;
  }

  function registerDriver() {
    const orgId = scope.isInternal() ? scope.orgFilter() : scope.org().organization_id;
    const fields = [];
    if (!orgId) fields.push({ name: "organization_id", label: t("Organization", "Tổ chức"), type: "select", required: true, options: customerOrgs().map((o) => [o.organization_id, o.short_name]) });
    fields.push(
      { name: "phone", label: t("Phone number", "Số điện thoại"), type: "tel", required: true, placeholder: "09xx xxx xxx", hint: t("The driver logs in with this number.", "Tài xế đăng nhập bằng số này.") },
      { name: "full_name", label: t("Full name", "Họ và tên"), required: true },
      { name: "license_number", label: t("Licence number", "Số giấy phép lái xe"), required: true, placeholder: "79xxxxxxxxxx", attrs: 'style="font-family:var(--mono)"' },
      { name: "license_class", label: t("Licence class", "Hạng giấy phép"), type: "select", value: "CE", options: LICENSE_CLASSES.map((c) => [c, c]), hint: t("A tractor head needs CE (Law 36/2024).", "Đầu kéo cần hạng CE (Luật 36/2024).") },
      { name: "license_expires_on", label: t("Licence expires on", "Ngày hết hạn"), type: "date", required: true },
    );
    ui.formModal({
      title: t("Register a driver", "Đăng ký tài xế"), wide: true,
      intro: ui.callout("info", esc(t("This creates an invited account with the driver role and a driver profile (ACC-10). The driver installs the G3 Driver app and claims the account with a one-time code sent by SMS to this number, then sets a password. No password is handed out.", "Hệ thống tạo tài khoản ở trạng thái mời, có vai trò tài xế và hồ sơ tài xế (ACC-10). Tài xế cài app G3 Driver và kích hoạt tài khoản bằng mã OTP gửi qua SMS đến số này, rồi tự đặt mật khẩu. Không cần giao mật khẩu."))),
      fields, submitLabel: t("Register", "Đăng ký"),
      onSubmit(values) {
        const org = orgId || values.organization_id;
        const phone = values.phone.replace(/[\s.]/g, "");
        if (!/^0\d{9}$/.test(phone)) return t("A phone number has 10 digits and starts with 0.", "Số điện thoại gồm 10 chữ số, bắt đầu bằng 0.");
        if (!/^[0-9A-Z]{6,20}$/i.test(values.license_number)) return t("The licence number has 6 to 20 letters or digits.", "Số giấy phép gồm 6 đến 20 chữ cái hoặc chữ số.");
        if (daysUntil(values.license_expires_on) < 0) return t("This licence has already expired.", "Giấy phép này đã hết hạn.");
        let user = db.users.find((u) => u.phone_number === phone);
        let membership = user && db.memberships.find((m) => m.user_id === user.user_id && m.organization_id === org && !m.left_at);
        if (membership && db.drivers.some((d) => d.membership_id === membership.membership_id)) return t("This person already has a driver profile in the organization.", "Người này đã có hồ sơ tài xế trong tổ chức.");
        const now = new Date().toISOString();
        if (!user) {
          user = { user_id: db.uid("usr"), full_name: values.full_name, phone_number: phone, email: null, status: "INVITED", status_reason: null, created_at: now, last_login_at: null };
          db.users.push(user);
        }
        if (!membership) {
          membership = { membership_id: db.uid("mbr"), user_id: user.user_id, organization_id: org, status: "INVITED", status_reason: null, roles: ["DRIVER"], fleet_ids: [], invited_at: now, joined_at: null, left_at: null };
          db.memberships.push(membership);
        } else if (!membership.roles.includes("DRIVER")) membership.roles.push("DRIVER");
        const driver = { driver_id: db.uid("drv"), membership_id: membership.membership_id, user_id: user.user_id, organization_id: org, full_name: user.full_name, phone_number: phone, license_number: values.license_number.toUpperCase(), license_class: values.license_class, license_expires_on: values.license_expires_on, status: "ACTIVE", status_reason: null, safety_score: null, created_at: now };
        db.drivers.push(driver);
        logDriverChange(driver, t("Profile created", "Tạo hồ sơ"), t("Registered from the portal", "Đăng ký từ cổng web"));
        G3.render();
        ui.toast(t(`${user.full_name} registered. An SMS invites them to claim the account in the G3 Driver app.`, `Đã đăng ký ${user.full_name}. SMS mời tài xế kích hoạt tài khoản trên app G3 Driver.`), "", "send");
      },
    });
  }

  G3.page({
    id: "drivers",
    path: "/drivers",
    title: ["Drivers", "Tài xế"],
    roles: ["FLEET_MANAGER", "ORG_ADMIN", "DISPATCHER"],
    features: ["DRV-01", "ACC-10", "DRV-02"],
    nav: { group: "fleet", order: 2, icon: "id" },
    render({ query }) {
      const all = scope.filter(db.drivers);
      const rows = filteredDrivers(query);
      const active = all.filter((d) => d.status === "ACTIVE");
      const checkedIn = all.filter((d) => openSessionOfDriver(d.driver_id));
      const expiring = all.filter((d) => { const l = daysUntil(d.license_expires_on); return l >= 0 && l < 30; });
      const expired = all.filter((d) => daysUntil(d.license_expires_on) < 0);
      const scored = active.map(driverSafety).filter(Boolean);
      const avgScore = scored.length ? scored.reduce((a, s) => a + s.score, 0) / scored.length : 0;
      const noDriver = scope.vehicles().filter((v) => v.live && v.live.is_online && v.live.state === "DRIVING" && !openSessionOfVehicle(v.vehicle_id));
      return `${ui.pageHead({
        title: t("Drivers", "Tài xế"),
        sub: esc(t("Driver profiles of the organization, their licence and who is at the wheel now.", "Hồ sơ tài xế của tổ chức, giấy phép lái xe và ai đang cầm lái.")),
        features: ["DRV-01", "ACC-10", "DRV-02"],
        actions: `${ui.btn(t("Export", "Xuất"), { icon: "download", action: "drivers-export" })}${canEditDrivers() ? ui.btn(t("Register a driver", "Đăng ký tài xế"), { icon: "plus", kind: "primary", action: "driver-register" }) : ""}`,
      })}
      <div class="grid cols-5">
        ${ui.kpi({ label: t("Active drivers", "Tài xế hoạt động"), icon: "users", value: `${active.length}<small>/ ${all.length}</small>`, foot: esc(t(`${all.length - active.length} inactive`, `${all.length - active.length} ngừng hoạt động`)) })}
        ${ui.kpi({ label: t("Checked in now", "Đang nhận xe"), icon: "truck", value: checkedIn.length, foot: noDriver.length ? `<span style="color:var(--warn)">${esc(t(`${noDriver.length} truck(s) driving with nobody`, `${noDriver.length} xe chạy không có tài xế`))}</span>` : esc(t("every moving truck has a driver", "mọi xe đang chạy đều có tài xế")) })}
        ${ui.kpi({ label: t("Licence expires < 30 d", "GPLX hết hạn < 30 ngày"), icon: "clock", value: expiring.length, tone: expiring.length ? "warn" : undefined, foot: esc(t("reminder sent to the manager", "đã nhắc người quản lý")) })}
        ${ui.kpi({ label: t("Licence expired", "GPLX đã hết hạn"), icon: "alert", value: expired.length, tone: expired.length ? "danger" : "ok", foot: esc(t("check-in blocked", "không thể nhận xe")) })}
        ${ui.kpi({ label: t("Avg. safety score", "Điểm an toàn TB"), icon: "shield", value: fmt.num(avgScore), unit: "/100", foot: esc(t("last 30 days · SAF-01", "30 ngày qua · SAF-01")) })}
      </div>
      ${noDriver.length ? `<div class="mt">${ui.callout("warn", `${esc(t("Driving with nobody checked in (DRV-02):", "Đang chạy nhưng chưa có tài xế nhận xe (DRV-02):"))} ${noDriver.map((v) => plateLink(v)).join(", ")}`)}</div>` : ""}
      <div class="mt">${ui.card({
        flush: true,
        body: `<div class="toolbar">${ui.search("q", query.q, t("Name, phone, licence", "Tên, SĐT, số GPLX"))}
          ${ui.select("status", [["", t("Every status", "Mọi trạng thái")], ["ACTIVE", G3.label("ACTIVE")], ["INACTIVE", G3.label("INACTIVE")]], query.status || "")}
          ${ui.select("licence", [["", t("Every licence", "Mọi giấy phép")], ["expiring", t("Expires in < 30 days", "Hết hạn trong 30 ngày")], ["expired", t("Expired", "Đã hết hạn")]], query.licence || "")}
          ${ui.select("checked", [["", t("Checked in or not", "Đã/chưa nhận xe")], ["in", t("Checked in now", "Đang nhận xe")]], query.checked || "")}
          <span class="grow"></span><span class="small muted">${esc(t(`${rows.length} of ${all.length}`, `${rows.length} / ${all.length}`))}</span></div>
          ${ui.table({
            rows, href: (d) => `/drivers/${d.driver_id}`, empty: t("No driver matches the filters", "Không có tài xế phù hợp"),
            columns: [
              { label: t("Driver", "Tài xế"), render: (d) => `<b>${esc(d.full_name)}</b> ${accountBadge(d)}<span class="sub">${esc(d.phone_number)}${internalMulti() ? " · " + ui.orgName(d.organization_id) : ""}</span>` },
              { label: t("Licence", "Giấy phép"), render: (d) => licenceCell(d) },
              { label: t("Status", "Trạng thái"), render: (d) => `${ui.badge(d.status)}${d.status_reason ? `<span class="sub">${esc(d.status_reason)}</span>` : ""}` },
              { label: t("Checked in now", "Đang nhận xe"), render: checkedInCell },
              { label: t("Safety score", "Điểm an toàn"), render: scoreCell },
              canEditDrivers() ? { label: "", align: "right", render: (d) => ui.btn(d.status === "ACTIVE" ? t("Deactivate", "Ngừng") : t("Reactivate", "Kích hoạt"), { sm: true, kind: d.status === "ACTIVE" ? "ghost" : "", attrs: `data-status-driver="${d.driver_id}"` }) } : null,
            ].filter(Boolean),
          })}`,
      })}</div>`;
    },
    mount(root, { query }) {
      bindQueryControls(root);
      on(root, "click", (e) => {
        const btn = e.target.closest("[data-action], [data-status-driver]");
        if (!btn) return;
        if (btn.dataset.statusDriver) statusChange(db.driver(btn.dataset.statusDriver));
        else if (btn.dataset.action === "driver-register") registerDriver();
        else if (btn.dataset.action === "drivers-export") {
          ui.auditedExport({
            dataType: "DRIVER_PROFILE", subject: t("Driver list", "Danh sách tài xế"), filename: "drivers.csv", rows: filteredDrivers(query),
            columns: [{ label: "full_name", key: "full_name" }, { label: "phone_number", key: "phone_number" }, { label: "license_number", key: "license_number" }, { label: "license_class", key: "license_class" }, { label: "license_expires_on", key: "license_expires_on" }, { label: "status", key: "status" }, { label: "status_reason", key: "status_reason" }, { label: "checked_in_vehicle", csv: (d) => { const s = openSessionOfDriver(d.driver_id); return s ? db.vehicle(s.vehicle_id).license_plate : ""; } }, { label: "safety_score", csv: (d) => { const s = driverSafety(d); return s ? s.score : ""; } }],
          });
        }
      });
    },
  });

  /* ---------- Driver detail ---------- */
  /** Daily driving hours and the longest continuous stint, last 14 days (DRV-06). */
  function drivingTime(d) {
    const r = rng(d.driver_id + "hours");
    return Array.from({ length: 14 }, (_, i) => {
      const date = new Date(db.NOW - (13 - i) * DAY);
      const off = d.status !== "ACTIVE" || r() < 0.2 || (date.getDay() === 0 && r() < 0.7);
      const hours = off ? 0 : Math.round((5 + r() * 6.8) * 10) / 10;
      const stint = off ? 0 : Math.round(Math.min(hours, 1.8 + r() * 3) * 10) / 10;
      return { label: `${date.getDate()}/${date.getMonth() + 1}`, hours, stint };
    });
  }
  /** Charging sessions attributed to a driver (the backend links them through the driving session at the time). */
  function driverChargingSessions(d) {
    const pool = db.drivers.filter((x) => x.organization_id === d.organization_id && x.status === "ACTIVE" && x.safety_score !== null).map((x) => x.driver_id);
    if (!pool.includes(d.driver_id)) return [];
    return db.charging_sessions.filter((s) => s.organization_id === d.organization_id && s.status === "COMPLETED" && pool[hash(s.session_id) % pool.length] === d.driver_id);
  }
  function slotAt(locationId, at) {
    const tariff = db.tariffs.find((x) => x.location_id === locationId);
    const dt = new Date(at);
    const hm = `${String(dt.getHours()).padStart(2, "0")}:${String(dt.getMinutes()).padStart(2, "0")}`;
    return tariff.slots.find((s) => hm >= s.from && hm < s.to) || tariff.slots[0];
  }
  function chargingEfficiency(d) {
    const sessions = driverChargingSessions(d);
    const kwh = sessions.reduce((a, s) => a + s.energy_kwh, 0);
    const cost = sessions.reduce((a, s) => a + s.cost_vnd, 0);
    const green = sessions.filter((s) => ["OFF_PEAK", "SOLAR"].includes(slotAt(s.location_id, s.started_at).label)).reduce((a, s) => a + s.energy_kwh, 0);
    const safety = driverSafety(d);
    return { sessions: sessions.length, kwh, cost, greenShare: kwh ? (green / kwh) * 100 : 0, savings: kwh * 6290 - cost, costPerKm: safety && cost ? cost / safety.km : null };
  }

  function safetyCard(d) {
    const s = driverSafety(d);
    if (!s) return ui.card({ title: t("Safety score", "Điểm an toàn"), sub: "SAF-01", body: ui.empty(t("No driving recorded yet.", "Chưa có dữ liệu lái xe.")) });
    const peers = scope.filter(db.drivers).filter((x) => x.organization_id === d.organization_id && x.status === "ACTIVE").map((x) => ({ x, s: driverSafety(x) })).filter((p) => p.s).sort((a, b) => b.s.score - a.s.score);
    const rank = peers.findIndex((p) => p.x.driver_id === d.driver_id) + 1;
    const types = Object.keys(s.rates);
    const tone = scoreTone(s.score);
    return ui.card({
      title: t("Safety score", "Điểm an toàn"), sub: esc(t("last 30 days · SAF-01", "30 ngày qua · SAF-01")),
      actions: G3.canSee("safety") ? ui.btn(t("Ranking", "Xếp hạng"), { href: "#/safety", sm: true, kind: "ghost" }) : "",
      body: `<div class="row" style="gap:18px;align-items:flex-start;flex-wrap:nowrap">
        <div style="text-align:center;min-width:96px"><div style="font-size:40px;font-weight:700;line-height:1;color:var(--${tone})">${s.score}</div><div class="small muted">/ 100</div><div class="small mt" style="margin-top:8px">${rank ? esc(t(`#${rank} of ${peers.length}`, `Hạng ${rank}/${peers.length}`)) : ""}</div>${ui.sparkline(s.trend, `var(--${tone})`, 90, 28)}<div class="small muted">${esc(t("8 weeks", "8 tuần"))}</div></div>
        <div class="grow" style="min-width:0">${ui.hBars({ labels: types.map(eventLabel), series: [{ values: types.map((type) => Math.round(s.rates[type] * 100) / 100), color: ui.PALETTE[2] }], yFormat: (v) => G3.fmt.num(v, 2), unit: t(" /100 km", " /100 km") })}
        <div class="small muted" style="margin-top:10px">${esc(t(`${fmt.num(s.km)} km driven · ${Object.values(s.counts).reduce((a, b) => a + b, 0)} events${orgHasAdas(d.organization_id) ? " (camera events merged: advanced plan)" : ""}`, `${fmt.num(s.km)} km đã lái · ${Object.values(s.counts).reduce((a, b) => a + b, 0)} sự kiện${orgHasAdas(d.organization_id) ? " (gồm sự kiện camera: gói nâng cao)" : ""}`))}</div></div></div>`,
    });
  }

  function complianceCard(d) {
    const days = drivingTime(d);
    const continuous = days.filter((x) => x.stint > 4).length;
    const daily = days.filter((x) => x.hours > 10).length;
    const weeks = [days.slice(0, 7), days.slice(7)].map((w) => w.reduce((a, x) => a + x.hours, 0));
    const weekly = weeks.filter((h) => h > 48).length;
    const ok = !continuous && !daily && !weekly;
    return ui.card({
      title: t("Driving and rest compliance", "Tuân thủ thời gian lái & nghỉ"), sub: esc(t("14 days · DRV-06", "14 ngày · DRV-06")),
      actions: ui.legend([[t("≤ 10 h", "≤ 10 giờ"), ui.PALETTE[1]], [t("over 10 h", "quá 10 giờ"), "#dc2626"]]),
      body: `${ui.barChart({ labels: days.map((x) => x.label), stacked: true, height: 150, unit: " h", series: [{ name: t("Driving", "Lái xe"), values: days.map((x) => Math.min(10, x.hours)), color: ui.PALETTE[1] }, { name: t("Over 10 h", "Quá 10 giờ"), values: days.map((x) => Math.max(0, x.hours - 10)), color: "#dc2626" }] })}
        <div class="grid cols-3 mt" style="gap:10px">
          <div><div class="small muted">${esc(t("Over 4 h continuous", "Lái liên tục > 4 giờ"))}</div><b style="color:var(--${continuous ? "danger" : "ok"})">${continuous}</b> <span class="small muted">${esc(t("days", "ngày"))}</span></div>
          <div><div class="small muted">${esc(t("Over 10 h a day", "Quá 10 giờ/ngày"))}</div><b style="color:var(--${daily ? "danger" : "ok"})">${daily}</b> <span class="small muted">${esc(t("days", "ngày"))}</span></div>
          <div><div class="small muted">${esc(t("Over 48 h a week", "Quá 48 giờ/tuần"))}</div><b style="color:var(--${weekly ? "danger" : "ok"})">${weekly}</b> <span class="small muted">· ${weeks.map((h) => fmt.num(h, 1)).join(" / ")} h</span></div>
        </div>${ok ? `<div class="mt">${ui.callout("ok", esc(t("No driving-time violation in the period.", "Không vi phạm thời gian lái trong kỳ.")))}</div>` : ""}`,
    });
  }

  function efficiencyCard(d) {
    const e = chargingEfficiency(d);
    const peers = scope.filter(db.drivers).filter((x) => x.organization_id === d.organization_id && x.status === "ACTIVE").map((x) => ({ id: x.driver_id, e: chargingEfficiency(x) })).filter((p) => p.e.sessions).sort((a, b) => b.e.greenShare - a.e.greenShare);
    const rank = peers.findIndex((p) => p.id === d.driver_id) + 1;
    return ui.card({
      title: t("Charging efficiency", "Hiệu quả sạc"), sub: esc(t("30 days · DRV-07", "30 ngày · DRV-07")),
      body: e.sessions ? `${ui.kv([
        [t("Sessions", "Số phiên"), `${e.sessions} · ${fmt.kwh(e.kwh, 0)}`],
        [t("Off-peak or solar share", "Tỷ lệ giờ thấp điểm/điện mặt trời"), `<span class="soc">${ui.meter(e.greenShare, e.greenShare < 40 ? "warn" : "")}<b>${fmt.pct(e.greenShare)}</b></span>`],
        [t("Average price", "Giá trung bình"), `${fmt.num(e.cost / e.kwh)} ₫/kWh`],
        [t("Electricity per km", "Tiền điện mỗi km"), e.costPerKm ? `${fmt.num(e.costPerKm)} ₫/km` : "—"],
        [t("Saved vs peak hours", "Tiết kiệm so với giờ cao điểm"), `<b style="color:var(--ok)">${fmt.vnd(e.savings)}</b>`],
        [t("Fleet ranking", "Xếp hạng trong đội"), rank ? esc(t(`#${rank} of ${peers.length} drivers`, `Hạng ${rank}/${peers.length} tài xế`)) : "—"],
      ])}` : ui.empty(t("No charging session in the period.", "Không có phiên sạc trong kỳ.")),
    });
  }

  function checkInModal(d) {
    const candidates = scope.vehicles().filter((v) => v.status === "ACTIVE" && v.live && !openSessionOfVehicle(v.vehicle_id));
    const current = openSessionOfDriver(d.driver_id);
    let confirmedKey = null;
    ui.formModal({
      title: t(`Check ${d.full_name} in from the portal`, `Nhận xe cho ${d.full_name} từ cổng web`),
      intro: `${ui.callout("info", esc(t("Method PORTAL (DRV-02). Any active driver may drive any organization's truck; you get a warning when the truck belongs to another organization. One driver at the wheel per truck: the driver already checked in to it is taken over.", "Phương thức PORTAL (DRV-02). Tài xế đang hoạt động được lái xe của bất kỳ tổ chức nào; hệ thống cảnh báo khi xe thuộc tổ chức khác. Mỗi xe chỉ có một tài xế cầm lái: tài xế đang nhận xe sẽ bị thay.")))}${current ? `<div class="mt">${ui.callout("warn", esc(t(`${d.full_name} is checked in to ${db.vehicle(current.vehicle_id).license_plate}; that session ends (other truck).`, `${d.full_name} đang nhận xe ${db.vehicle(current.vehicle_id).license_plate}; phiên đó sẽ kết thúc (nhận xe khác).`)))}</div>` : ""}<datalist id="checkin-trucks">${candidates.map((v) => `<option value="${esc(v.license_plate)}">${esc(v.vin)}</option>`).join("")}</datalist>`,
      fields: [{ name: "truck", label: t("Truck (plate or VIN)", "Xe (biển số hoặc VIN)"), required: true, placeholder: "51C-123.45", attrs: 'list="checkin-trucks"' }],
      submitLabel: t("Check in", "Nhận xe"),
      onSubmit(values) {
        const code = values.truck.toUpperCase().replace(/\s/g, "");
        const v = db.vehicles.find((x) => x.license_plate.toUpperCase() === code || x.vin === code);
        if (!v || v.status !== "ACTIVE") return t("No active truck with this plate or VIN.", "Không có xe đang hoạt động với biển số hoặc VIN này.");
        if (d.status !== "ACTIVE") return t("The driver is inactive.", "Tài xế đang ngừng hoạt động.");
        if (daysUntil(d.license_expires_on) < 0) return t("The driving licence has expired: check-in is refused.", "Giấy phép lái xe đã hết hạn: không thể nhận xe.");
        const m = db.memberships.find((x) => x.membership_id === d.membership_id);
        if (m && m.status !== "ACTIVE") return t("The driver has not claimed the account in the app yet.", "Tài xế chưa kích hoạt tài khoản trên app.");
        if (current && current.vehicle_id === v.vehicle_id) return t("Already checked in to this truck.", "Tài xế đã nhận xe này.");
        const warnings = [];
        if (v.organization_id !== d.organization_id) warnings.push(t(`${v.license_plate} belongs to another organization (${db.org(v.organization_id).short_name}); its owner gets an alert.`, `Xe ${v.license_plate} thuộc tổ chức khác (${db.org(v.organization_id).short_name}); chủ xe sẽ nhận cảnh báo.`));
        if (db.model(v.vehicle_model_id).kind.startsWith("Đầu kéo") && d.license_class !== "CE") warnings.push(t(`A tractor head needs licence class CE; the driver holds ${d.license_class}.`, `Đầu kéo cần GPLX hạng CE; tài xế có hạng ${d.license_class}.`));
        const taken = openSessionOfVehicle(v.vehicle_id);
        if (taken) warnings.push(t(`${db.driver(taken.driver_id) ? db.driver(taken.driver_id).full_name : "Another driver"} is checked in to it and will be taken over.`, `${db.driver(taken.driver_id) ? db.driver(taken.driver_id).full_name : "Tài xế khác"} đang nhận xe này và sẽ bị thay.`));
        if (warnings.length && confirmedKey !== v.vehicle_id) {
          confirmedKey = v.vehicle_id;
          return warnings.join(" ") + " " + t("Press Check in again to confirm.", "Bấm Nhận xe lần nữa để xác nhận.");
        }
        if (current) endSession(current, "OTHER_TRUCK");
        if (taken) endSession(taken, "TAKEN_OVER");
        db.driving_sessions.unshift({ driving_session_id: db.uid("ds"), driver_id: d.driver_id, vehicle_id: v.vehicle_id, organization_id: v.organization_id, check_in_method: "PORTAL", checked_in_at: new Date().toISOString(), ended_at: null, end_cause: null });
        v.driver_id = d.driver_id;
        G3.render();
        ui.toast(t(`${d.full_name} checked in to ${v.license_plate}`, `${d.full_name} đã nhận xe ${v.license_plate}`));
      },
    });
  }

  function editDriver(d) {
    ui.formModal({
      title: t("Edit driver profile", "Sửa hồ sơ tài xế"), wide: true,
      intro: `<p class="small muted">${esc(t("Name and phone belong to the person's account; the profile holds the licence facts of this organization.", "Họ tên và số điện thoại thuộc tài khoản của người dùng; hồ sơ giữ thông tin giấy phép tại tổ chức này."))}</p>`,
      fields: [
        { name: "license_number", label: t("Licence number", "Số giấy phép"), value: d.license_number, required: true },
        { name: "license_class", label: t("Licence class", "Hạng giấy phép"), type: "select", value: d.license_class, options: LICENSE_CLASSES.map((c) => [c, c]) },
        { name: "license_expires_on", label: t("Expires on", "Ngày hết hạn"), type: "date", value: d.license_expires_on, required: true },
        { name: "reason", label: t("Change reason", "Lý do thay đổi"), required: true, placeholder: t("Licence renewed", "Đổi giấy phép mới") },
      ],
      onSubmit(values) {
        if (!/^[0-9A-Z]{6,20}$/i.test(values.license_number)) return t("The licence number has 6 to 20 letters or digits.", "Số giấy phép gồm 6 đến 20 chữ cái hoặc chữ số.");
        const changes = [];
        if (values.license_number !== d.license_number) changes.push(t("licence number", "số GPLX"));
        if (values.license_class !== d.license_class) changes.push(`${t("class", "hạng")} ${d.license_class} → ${values.license_class}`);
        if (values.license_expires_on !== d.license_expires_on) changes.push(`${t("expiry", "hạn")} ${fmt.date(d.license_expires_on)} → ${fmt.date(values.license_expires_on)}`);
        if (!changes.length) return t("Nothing changed.", "Không có thay đổi.");
        logDriverChange(d, changes.join(", "), values.reason);
        Object.assign(d, { license_number: values.license_number.toUpperCase(), license_class: values.license_class, license_expires_on: values.license_expires_on });
        G3.render();
        ui.toast(t("Profile saved", "Đã lưu hồ sơ"));
      },
    });
  }

  G3.page({
    id: "driver-detail",
    path: "/drivers/:id",
    title: ["Driver", "Tài xế"],
    roles: ["FLEET_MANAGER", "ORG_ADMIN", "DISPATCHER"],
    features: ["DRV-01", "DRV-02", "DRV-06", "DRV-07", "SAF-01", "MON-11"],
    navParent: "drivers",
    render({ params, query }) {
      const d = scope.filter(db.drivers).find((x) => x.driver_id === params.id);
      const crumbs = [{ label: t("Drivers", "Tài xế"), href: "#/drivers" }];
      if (!d) return ui.pageHead({ title: t("Driver not found", "Không tìm thấy tài xế"), crumbs }) + ui.card({ body: ui.empty(t("This driver does not exist or belongs to an organization outside your reach.", "Tài xế không tồn tại hoặc thuộc tổ chức ngoài phạm vi của bạn.")) });
      const tab = query.tab || "overview";
      const user = db.user(d.user_id);
      const membership = db.memberships.find((m) => m.membership_id === d.membership_id);
      const session = openSessionOfDriver(d.driver_id);
      const sessions = db.driving_sessions.filter((s) => s.driver_id === d.driver_id).sort((a, b) => (a.checked_in_at < b.checked_in_at ? 1 : -1));
      const trips = db.trips.filter((x) => x.driver_id === d.driver_id).sort((a, b) => (a.planned_departure_at < b.planned_departure_at ? 1 : -1));
      const left = daysUntil(d.license_expires_on);
      const edit = canEditDrivers();
      const actions = `${edit ? ui.btn(t("Edit", "Sửa"), { icon: "edit", action: "driver-edit" }) : ""}${edit ? ui.btn(d.status === "ACTIVE" ? t("Deactivate", "Ngừng hoạt động") : t("Reactivate", "Kích hoạt lại"), { kind: d.status === "ACTIVE" ? "danger" : "", action: "driver-status" }) : ""}${session ? ui.btn(t("Check out", "Trả xe"), { icon: "logout", action: "driver-checkout" }) : ""}${ui.btn(t("Check in from the portal", "Nhận xe từ cổng web"), { icon: "truck", kind: "primary", action: "driver-checkin", disabled: d.status !== "ACTIVE" })}`;
      const head = ui.pageHead({
        title: d.full_name, crumbs,
        sub: `${ui.badge(d.status)} ${accountBadge(d)} <span class="muted">${esc(d.phone_number)} · ${ui.orgName(d.organization_id)}${d.status_reason ? " · " + esc(d.status_reason) : ""}</span>`,
        features: ["DRV-01", "DRV-02", "DRV-06", "DRV-07", "SAF-01", "MON-11"], actions,
      });
      const tabs = ui.tabs([["overview", t("Overview", "Tổng quan")], ["sessions", t(`Driving sessions (${sessions.length})`, `Phiên lái (${sessions.length})`)], ["trips", t(`Trips (${trips.length})`, `Chuyến đi (${trips.length})`)]], tab);
      let body;
      if (tab === "sessions") {
        G3.audit("VIEW", "DRIVING_SESSIONS", d.full_name, null);
        const recent = sessions.filter((s) => Date.now() - new Date(s.checked_in_at).getTime() < 30 * DAY);
        const hours = recent.reduce((a, s) => a + ((s.ended_at ? new Date(s.ended_at).getTime() : Date.now()) - new Date(s.checked_in_at).getTime()) / HOUR, 0);
        body = `<div class="grid cols-4">
          ${ui.kpi({ label: t("Sessions (30 d)", "Số phiên (30 ngày)"), icon: "list", value: recent.length })}
          ${ui.kpi({ label: t("Hours at the wheel", "Giờ cầm lái"), icon: "clock", value: fmt.num(hours, 1), unit: "h" })}
          ${ui.kpi({ label: t("Trucks driven", "Số xe đã lái"), icon: "truck", value: new Set(recent.map((s) => s.vehicle_id)).size })}
          ${ui.kpi({ label: t("Auto-ended", "Tự kết thúc"), icon: "refresh", value: recent.filter((s) => s.end_cause === "AUTO_ENDED").length, foot: esc(t("truck idle past the organization's limit", "xe đứng yên quá thời gian của tổ chức")) })}
        </div><div class="mt">${ui.card({
          title: t("Driving-session history", "Lịch sử phiên lái"), sub: esc(t("viewing is logged (ACC-18)", "lượt xem được ghi nhật ký (ACC-18)")), flush: true,
          body: ui.table({
            rows: sessions,
            columns: [
              { label: t("Truck", "Xe"), render: (s) => `${plateLink(db.vehicle(s.vehicle_id))}${s.organization_id !== d.organization_id ? `<span class="sub">${ui.orgName(s.organization_id)}</span>` : ""}` },
              { label: t("Method", "Cách nhận"), render: (s) => ui.tag(G3.label(s.check_in_method)) },
              { label: t("Checked in", "Nhận xe"), render: (s) => fmt.dateTime(s.checked_in_at) },
              { label: t("Ended", "Kết thúc"), render: (s) => (s.ended_at ? fmt.dateTime(s.ended_at) : ui.badge("ACTIVE", "info", t("At the wheel", "Đang lái"))) },
              { label: t("Duration", "Thời lượng"), align: "right", render: (s) => fmt.duration(((s.ended_at ? new Date(s.ended_at).getTime() : Date.now()) - new Date(s.checked_in_at).getTime()) / MIN) },
              { label: t("End cause", "Lý do kết thúc"), render: (s) => (s.end_cause ? ui.badge(s.end_cause, s.end_cause === "AUTO_ENDED" ? "warn" : "muted") : '<span class="muted">—</span>') },
            ],
          }),
        })}</div>`;
      } else if (tab === "trips") {
        const done = trips.filter((x) => x.status === "COMPLETED");
        const km = done.reduce((a, x) => a + x.distance_km, 0);
        const kwh = done.reduce((a, x) => a + x.energy_kwh, 0);
        body = `<div class="grid cols-4">
          ${ui.kpi({ label: t("Completed trips", "Chuyến hoàn tất"), icon: "route", value: done.length })}
          ${ui.kpi({ label: t("Distance", "Quãng đường"), icon: "map", value: fmt.num(km), unit: "km" })}
          ${ui.kpi({ label: t("Energy", "Điện năng"), icon: "bolt", value: fmt.num(kwh), unit: "kWh", foot: `${fmt.num(km ? kwh / km : 0, 2)} kWh/km` })}
          ${ui.kpi({ label: t("Planned", "Đã lên kế hoạch"), icon: "clock", value: trips.filter((x) => x.status === "PLANNED").length })}
        </div><div class="mt">${ui.card({
          title: t("Trips", "Chuyến đi"), sub: "MON-11", flush: true,
          actions: G3.canSee("trips") ? ui.btn(t("All trips", "Mọi chuyến"), { href: "#/trips", sm: true, kind: "ghost" }) : "",
          body: ui.table({
            rows: trips, empty: t("No trip yet", "Chưa có chuyến nào"),
            columns: [
              { label: t("Trip", "Chuyến"), render: (x) => `<b class="mono">${esc(x.trip_id)}</b>` },
              { label: t("Route", "Tuyến"), render: (x) => `${esc(x.origin)} → ${esc(x.destination)}` },
              { label: t("Truck", "Xe"), render: (x) => plateLink(db.vehicle(x.vehicle_id)) },
              { label: t("Departure", "Khởi hành"), render: (x) => fmt.dateTime(x.started_at || x.planned_departure_at) },
              { label: t("Status", "Trạng thái"), render: (x) => ui.badge(x.status) },
              { label: t("Load", "Tải"), render: (x) => (x.declared_load ? ui.tag(G3.label(x.declared_load)) : "—") },
              { label: "km", align: "right", render: (x) => fmt.num(x.distance_km, 1) },
              { label: "kWh/km", align: "right", render: (x) => (x.distance_km ? fmt.num(x.energy_kwh / x.distance_km, 2) : "—") },
            ],
          }),
        })}</div>`;
      } else {
        const v = session && db.vehicle(session.vehicle_id);
        const history = (driverHistory[d.driver_id] || []).concat([{ at: d.created_at, by: null, change: t("Profile created", "Tạo hồ sơ"), reason: t("Registered by the fleet manager", "Quản lý đội đăng ký") }]);
        body = `<div class="grid cols-3">
          ${ui.card({
            title: t("Licence", "Giấy phép lái xe"), sub: "DRV-01",
            body: `${left < 0 ? ui.callout("danger", esc(t("Expired: the driver cannot check in until the licence is renewed.", "Đã hết hạn: tài xế không thể nhận xe cho đến khi đổi giấy phép."))) + '<div style="height:10px"></div>' : left < 30 ? ui.callout("warn", esc(t(`Expires in ${left} days.`, `Hết hạn sau ${left} ngày.`))) + '<div style="height:10px"></div>' : ""}${ui.kv([
              [t("Number", "Số"), `<span class="mono">${esc(d.license_number)}</span>`],
              [t("Class", "Hạng"), `${ui.tag(d.license_class)} <span class="small muted">${esc(d.license_class === "CE" ? t("trucks and tractor heads", "xe tải và đầu kéo") : t("no tractor heads", "không lái đầu kéo"))}</span>`],
              [t("Expires on", "Hết hạn"), licenceCell(d, false)],
              [t("Profile since", "Hồ sơ từ"), fmt.date(d.created_at)],
            ])}`,
          })}
          ${ui.card({
            title: t("Person and membership", "Người dùng & thành viên"),
            body: ui.kv([
              [t("Full name", "Họ tên"), esc(user ? user.full_name : d.full_name)],
              [t("Phone", "Điện thoại"), esc(d.phone_number)],
              [t("Account", "Tài khoản"), user ? ui.badge(user.status) : "—"],
              [t("Membership", "Thành viên"), membership ? `${ui.badge(membership.status)} <span class="small muted">${ui.orgName(membership.organization_id)}</span>` : "—"],
              [t("Roles", "Vai trò"), membership ? esc(membership.roles.map(G3.roleLabel).join(", ")) : "—"],
              [t("Joined", "Tham gia"), membership && membership.joined_at ? fmt.date(membership.joined_at) : `<span class="small muted">${esc(t("not yet: claims the account in the app by OTP (ACC-10)", "chưa: kích hoạt tài khoản trên app bằng OTP (ACC-10)"))}</span>`],
              [t("Last login", "Đăng nhập cuối"), user && user.last_login_at ? fmt.ago(user.last_login_at) : "—"],
            ]),
          })}
          ${ui.card({
            title: t("At the wheel now", "Đang cầm lái"), sub: "DRV-02",
            body: session ? `${v.organization_id !== d.organization_id ? ui.callout("warn", esc(t(`Truck of another organization (${db.org(v.organization_id).short_name}).`, `Xe của tổ chức khác (${db.org(v.organization_id).short_name}).`))) + '<div style="height:10px"></div>' : ""}${ui.kv([
              [t("Truck", "Xe"), truckCell(v)],
              [t("State", "Trạng thái"), v.live ? ui.vehicleState(v) : "—"],
              [t("Battery", "Pin"), v.live ? ui.soc(v.live.soc_percent) : "—"],
              [t("Since", "Từ lúc"), `${fmt.time(session.checked_in_at)} · ${fmt.ago(session.checked_in_at)}`],
              [t("Method", "Cách nhận"), ui.tag(G3.label(session.check_in_method))],
            ])}<div class="row mt">${ui.btn(t("Check out", "Trả xe"), { icon: "logout", sm: true, action: "driver-checkout" })}${G3.canSee("live-map") && scope.vehicleIds().has(v.vehicle_id) ? ui.btn(t("On the map", "Trên bản đồ"), { icon: "map", sm: true, href: `#/map?v=${v.vehicle_id}` }) : ""}</div>`
              : `${ui.empty(t("Not checked in to any truck", "Chưa nhận xe nào"))}<div class="row" style="justify-content:center">${ui.btn(t("Check in from the portal", "Nhận xe từ cổng web"), { icon: "truck", sm: true, kind: "primary", action: "driver-checkin", disabled: d.status !== "ACTIVE" })}</div><p class="small muted" style="text-align:center">${esc(t("Sessions end at check-out, when another driver takes over, when the driver takes another truck, or after the truck stands still for the organization's auto-end time (2 h).", "Phiên kết thúc khi trả xe, khi tài xế khác nhận xe, khi tài xế nhận xe khác, hoặc khi xe đứng yên quá thời gian tự kết thúc của tổ chức (2 giờ)."))}</p>`,
          })}
        </div>
        <div class="grid cols-3 mt">${safetyCard(d)}${complianceCard(d)}${efficiencyCard(d)}</div>
        <div class="grid cols-2 mt">
          ${ui.card({
            title: t("Recent sessions", "Phiên lái gần đây"), flush: true,
            actions: ui.btn(t("Full history", "Toàn bộ lịch sử"), { sm: true, kind: "ghost", attrs: 'data-tab-go="sessions"' }),
            body: ui.table({ rows: sessions.slice(0, 5), columns: [
              { label: t("Truck", "Xe"), render: (s) => plateLink(db.vehicle(s.vehicle_id)) },
              { label: t("Checked in", "Nhận xe"), render: (s) => fmt.dateTime(s.checked_in_at) },
              { label: t("Duration", "Thời lượng"), render: (s) => fmt.duration(((s.ended_at ? new Date(s.ended_at).getTime() : Date.now()) - new Date(s.checked_in_at).getTime()) / MIN) },
              { label: t("End cause", "Lý do kết thúc"), render: (s) => (s.end_cause ? ui.badge(s.end_cause, "muted") : ui.badge("ACTIVE", "info", t("At the wheel", "Đang lái"))) },
            ] }),
          })}
          ${ui.card({
            title: t("Change history", "Lịch sử thay đổi"), sub: "driver_history",
            body: `<ul class="timeline">${history.map((h) => `<li><div class="when">${fmt.dateTime(h.at)} · ${ui.userName(h.by)}</div><div><b>${esc(h.change)}</b></div><div class="small muted">${esc(h.reason || "")}</div></li>`).join("")}</ul>`,
          })}
        </div>`;
      }
      return head + tabs + body;
    },
    mount(root, { params }) {
      bindQueryControls(root);
      const d = scope.filter(db.drivers).find((x) => x.driver_id === params.id);
      if (!d) return;
      root.querySelectorAll("[data-tab-go]").forEach((b) => b.addEventListener("click", () => G3.setQuery({ tab: b.dataset.tabGo })));
      on(root, "click", (e) => {
        const btn = e.target.closest("[data-action]");
        if (!btn) return;
        const action = btn.dataset.action;
        if (action === "driver-edit") editDriver(d);
        else if (action === "driver-status") statusChange(d);
        else if (action === "driver-checkin") checkInModal(d);
        else if (action === "driver-checkout") {
          const s = openSessionOfDriver(d.driver_id);
          if (!s) return;
          const v = db.vehicle(s.vehicle_id);
          confirmModal({
            title: t("Check out", "Trả xe"), label: t("Check out", "Trả xe"), danger: false,
            body: `<p>${esc(t(`End ${d.full_name}'s session on ${v.license_plate}? If the truck keeps driving with nobody checked in, the portal raises an alert.`, `Kết thúc phiên lái của ${d.full_name} trên xe ${v.license_plate}? Nếu xe vẫn chạy mà không có tài xế, cổng web sẽ cảnh báo.`))}</p>`,
            onConfirm() { endSession(s, "CHECKED_OUT"); G3.render(); ui.toast(t(`${d.full_name} checked out of ${v.license_plate}`, `${d.full_name} đã trả xe ${v.license_plate}`)); },
          });
        }
      });
    },
  });

  /* ================================================================== */
  /* Geofences (FLT-05)                                                  */
  /* ================================================================== */
  const KINDS = {
    DEPOT: ["Depot", "Bãi xe / kho", "#0e9f6e"],
    RESTRICTED: ["Restricted area", "Vùng cấm", "#dc2626"],
    CUSTOMER_SITE: ["Customer site", "Điểm khách hàng", "#2563eb"],
  };
  const kindBadge = (kind) => { const k = KINDS[kind] || KINDS.DEPOT; return `<span class="badge plain" style="background:${k[2]}1f;color:${k[2]}">${esc(t(k[0], k[1]))}</span>`; };
  const SVGNS = "http://www.w3.org/2000/svg";
  // The zone being drawn: { points: [[lat, lon]], targetId } (targetId = redrawing an existing zone).
  let draft = null;

  function onSegment(p, a, b) {
    const cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]);
    if (Math.abs(cross) > 1e-10) return false;
    return p[0] >= Math.min(a[0], b[0]) - 1e-12 && p[0] <= Math.max(a[0], b[0]) + 1e-12 && p[1] >= Math.min(a[1], b[1]) - 1e-12 && p[1] <= Math.max(a[1], b[1]) + 1e-12;
  }
  /** Containment like PostGIS ST_Covers: a point on the boundary is inside. */
  function covers(polygon, lat, lon) {
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const a = polygon[i], b = polygon[j];
      if (onSegment([lat, lon], a, b)) return true;
      if ((a[0] > lat) !== (b[0] > lat) && lon < ((b[1] - a[1]) * (lat - a[0])) / (b[0] - a[0]) + a[1]) inside = !inside;
    }
    return inside;
  }
  function visibleGeofences() {
    const limit = scope.fleetLimit();
    return scope.filter(db.geofences).filter((g) => !limit || limit.includes(g.fleet_id));
  }
  const trucksInside = (g) => scope.vehicles().filter((v) => v.live && covers(g.boundary, v.live.lat, v.live.lon));
  function areaKm2(boundary) {
    let s = 0;
    const pts = boundary.map(([lat, lon]) => [lon * 111.32 * Math.cos((lat * Math.PI) / 180), lat * 110.57]);
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) s += (pts[j][0] + pts[i][0]) * (pts[j][1] - pts[i][1]);
    return Math.abs(s / 2);
  }
  /** Recent GEOFENCE_ALERT rows of a zone: the real notifications plus a generated history. */
  function zoneAlerts(g) {
    const real = scope.byVehicle(db.notifications).filter((n) => n.notification_type === "GEOFENCE_ALERT" && ((n.payload && n.payload.geofence === g.name) || n.title[0].includes(g.name) || n.title[1].includes(g.name)))
      .map((n) => ({ at: n.created_at, vehicle_id: n.vehicle_id, transition: (n.payload && n.payload.transition) || (/ left /.test(n.title[0]) ? "EXIT" : "ENTER"), severity: n.severity }));
    const r = rng(g.geofence_id + "alerts");
    const members = scope.vehicles().filter((v) => v.live && db.fleetAndDescendants(g.fleet_id).includes(v.fleet_id));
    const generated = [];
    if (members.length) {
      for (let k = 0; k < 9; k++) {
        const v = members[Math.floor(r() * members.length)];
        const transition = r() < 0.5 ? "ENTER" : "EXIT";
        if ((transition === "ENTER" && !g.alert_on_enter) || (transition === "EXIT" && !g.alert_on_exit)) continue;
        generated.push({ at: iso(db.NOW - Math.round((90 + r() * 70 * 60) * MIN)), vehicle_id: v.vehicle_id, transition, severity: g.kind === "RESTRICTED" && transition === "ENTER" ? "CRITICAL" : "INFO" });
      }
    }
    return real.concat(generated).sort((a, b) => (a.at < b.at ? 1 : -1)).slice(0, 10);
  }
  const transitionBadge = (tr) => (tr === "ENTER" ? ui.badge("ENTER", "info", t("Entered", "Vào vùng")) : ui.badge("EXIT", "accent", t("Left", "Rời vùng")));

  function geofenceForm({ g, boundary }) {
    const fleets = visibleFleets(false);
    ui.formModal({
      title: g ? t(`Edit ${g.name}`, `Sửa ${g.name}`) : t("New geofence", "Vùng địa lý mới"), wide: true,
      intro: boundary ? `<p class="small muted">${esc(t(`${boundary.length} corners · ${fmt.num(areaKm2(boundary), 2)} km². Alerts are raised per reading when a member truck of the chosen fleet crosses the boundary.`, `${boundary.length} góc · ${fmt.num(areaKm2(boundary), 2)} km². Cảnh báo được phát theo từng bản tin khi xe thuộc đội đã chọn đi qua ranh giới.`))}</p>` : "",
      fields: [
        { name: "name", label: t("Name", "Tên vùng"), required: true, value: g ? g.name : "", hint: t("Shown in the alerts.", "Hiển thị trong cảnh báo.") },
        { name: "kind", label: t("Kind", "Loại"), type: "select", value: g ? g.kind : "DEPOT", options: Object.entries(KINDS).map(([k, l]) => [k, t(l[0], l[1])]) },
        { name: "fleet_id", label: t("Applies to fleet", "Áp dụng cho đội"), type: "select", required: true, value: g ? g.fleet_id : (fleets[0] || {}).fleet_id, options: fleets.map((f) => [f.fleet_id, `${internalMulti() ? db.org(f.organization_id).short_name + " · " : ""}${fleetPath(f)}`]), hint: t("Its member trucks, sub-fleets included.", "Các xe thành viên, gồm cả đội con.") },
        { name: "enter", label: t("Alerts", "Cảnh báo"), type: "checkbox", value: g ? g.alert_on_enter : true, checkLabel: t("When a truck enters", "Khi xe vào vùng") },
        { name: "exit", label: " ", type: "checkbox", value: g ? g.alert_on_exit : true, checkLabel: t("When a truck leaves", "Khi xe rời vùng") },
      ],
      submitLabel: g ? t("Save", "Lưu") : t("Create zone", "Tạo vùng"),
      onSubmit(values) {
        if (!values.fleet_id) return t("Create a fleet first.", "Hãy tạo đội xe trước.");
        const fleet = db.fleet(values.fleet_id);
        if (g) {
          Object.assign(g, { name: values.name, kind: values.kind, fleet_id: values.fleet_id, organization_id: fleet.organization_id, alert_on_enter: values.enter, alert_on_exit: values.exit, color: KINDS[values.kind][2] });
          G3.render();
          ui.toast(t("Geofence saved", "Đã lưu vùng địa lý"));
          return;
        }
        const zone = { geofence_id: db.uid("geo"), organization_id: fleet.organization_id, fleet_id: values.fleet_id, name: values.name, kind: values.kind, boundary, alert_on_enter: values.enter, alert_on_exit: values.exit, color: KINDS[values.kind][2], created_at: new Date().toISOString() };
        db.geofences.push(zone);
        draft = null;
        G3.setQuery({ zone: zone.geofence_id });
        ui.toast(t(`Zone ${zone.name} created`, `Đã tạo vùng ${zone.name}`));
      },
    });
  }

  function drawPreview(root) {
    const wrap = root.querySelector("[data-map-wrap]");
    if (!wrap) return;
    const svg = wrap.querySelector("svg");
    let g = svg.querySelector("[data-draw-preview]");
    if (!draft) { if (g) g.remove(); delete wrap.dataset.drawing; return; }
    wrap.dataset.drawing = "1";
    if (!g) { g = document.createElementNS(SVGNS, "g"); g.setAttribute("data-draw-preview", ""); svg.appendChild(g); }
    const pts = draft.points.map(([lat, lon]) => ui.project(lat, lon));
    const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("") + (pts.length > 2 ? "Z" : "");
    g.innerHTML = `${pts.length > 1 ? `<path d="${d}" fill="${pts.length > 2 ? "#7c3aed" : "none"}" fill-opacity="0.16" stroke="#7c3aed" stroke-width="2.5" stroke-dasharray="6 4" vector-effect="non-scaling-stroke"/>` : ""}${pts.map((p, i) => `<g style="transform:translate(${p[0].toFixed(1)}px,${p[1].toFixed(1)}px) scale(var(--k,1))"><circle r="${i === 0 ? 7 : 5}" fill="#fff" stroke="#7c3aed" stroke-width="2.5" ${i === 0 && pts.length > 2 ? 'data-close-shape style="cursor:pointer"' : ""}/></g>`).join("")}`;
    const count = root.querySelector("[data-draw-count]");
    if (count) count.textContent = t(`${draft.points.length} corner(s)`, `${draft.points.length} góc`);
    const finish = root.querySelector("[data-draw=finish]");
    if (finish) finish.disabled = draft.points.length < 3;
  }
  function finishDrawing(root) {
    if (!draft || draft.points.length < 3) return;
    if (draft.targetId) {
      const g = db.geofences.find((x) => x.geofence_id === draft.targetId);
      g.boundary = draft.points.slice();
      draft = null;
      G3.render();
      ui.toast(t(`Boundary of ${g.name} redrawn`, `Đã vẽ lại ranh giới ${g.name}`));
      return;
    }
    geofenceForm({ boundary: draft.points.slice() });
    drawPreview(root);
  }

  G3.page({
    id: "geofences",
    path: "/geofences",
    title: ["Geofences", "Vùng địa lý"],
    roles: ["FLEET_MANAGER", "DISPATCHER", "ORG_ADMIN"],
    features: ["FLT-05"],
    nav: { group: "fleet", order: 3, icon: "shape" },
    render({ query }) {
      const zones = visibleGeofences().filter((g) => !query.kind || g.kind === query.kind);
      const all = visibleGeofences();
      const selected = all.find((g) => g.geofence_id === query.zone) || null;
      const vehicles = scope.vehicles().filter((v) => v.live);
      const locations = db.charging_locations.filter((l) => l.is_public || scope.isInternal() || scope.orgIds().some((id) => (l.allowed_organization_ids || []).includes(id)));
      const insideTotal = new Set(all.flatMap((g) => trucksInside(g).map((v) => v.vehicle_id))).size;
      const alerts24 = all.flatMap(zoneAlerts).filter((a) => Date.now() - new Date(a.at).getTime() < DAY);
      const restricted = all.filter((g) => g.kind === "RESTRICTED");
      const inRestricted = restricted.flatMap(trucksInside);
      const canEdit = scope.has("FLEET_MANAGER", "ORG_ADMIN", "HEAD_ADMIN");
      const drawing = !!draft;

      const toggle = (g, which) => `<label class="row small" style="gap:6px;flex-wrap:nowrap;cursor:pointer" title="${esc(which === "enter" ? t("Alert on enter", "Cảnh báo khi vào") : t("Alert on exit", "Cảnh báo khi ra"))}"><span class="switch" style="display:inline-block"><input type="checkbox" data-toggle-alert="${which}" data-id="${g.geofence_id}" ${(which === "enter" ? g.alert_on_enter : g.alert_on_exit) ? "checked" : ""} ${canEdit ? "" : "disabled"}><span></span></span>${esc(which === "enter" ? t("Enter", "Vào") : t("Exit", "Ra"))}</label>`;
      const list = zones.length ? `<ul class="list">${zones.map((g) => `<li class="clickable ${selected && g.geofence_id === selected.geofence_id ? "unread" : ""}" data-zone-row="${g.geofence_id}">
          <span class="dot-status" style="background:${g.color};margin-top:7px"></span>
          <div class="grow"><div class="row between" style="flex-wrap:nowrap"><span class="title">${esc(g.name)}</span><span class="small muted nowrap"><b data-inside="${g.geofence_id}" style="color:var(--text)">${trucksInside(g).length}</b> ${esc(t("inside", "trong vùng"))}</span></div>
          <div class="meta" style="margin:2px 0 6px">${kindBadge(g.kind)} ${esc(fleetName(db.fleet(g.fleet_id)))}${internalMulti() ? " · " + ui.orgName(g.organization_id) : ""}</div>
          <div class="row" style="gap:16px">${toggle(g, "enter")}${toggle(g, "exit")}</div></div></li>`).join("")}</ul>` : ui.empty(t("No zone yet: draw one on the map", "Chưa có vùng: hãy vẽ trên bản đồ"));

      let detail = "";
      if (selected) {
        const inside = trucksInside(selected);
        const alerts = zoneAlerts(selected);
        detail = `<div class="grid cols-3 mt">
          ${ui.card({
            title: selected.name, sub: kindBadge(selected.kind),
            actions: canEdit ? `${ui.btn("", { icon: "edit", sm: true, kind: "ghost", action: "zone-edit", attrs: `title="${esc(t("Edit", "Sửa"))}"` })}${ui.btn("", { icon: "shape", sm: true, kind: "ghost", action: "zone-redraw", attrs: `title="${esc(t("Redraw boundary", "Vẽ lại ranh giới"))}"` })}${ui.btn("", { icon: "trash", sm: true, kind: "ghost", action: "zone-delete", attrs: `title="${esc(t("Delete", "Xóa"))}"` })}` : "",
            body: `${ui.kv([
              [t("Fleet", "Đội xe"), esc(fleetPath(db.fleet(selected.fleet_id)))],
              [t("Organization", "Tổ chức"), ui.orgName(selected.organization_id)],
              [t("Area", "Diện tích"), `${fmt.num(areaKm2(selected.boundary), 2)} km² · ${selected.boundary.length} ${esc(t("corners", "góc"))}`],
              [t("Alert on enter", "Cảnh báo khi vào"), selected.alert_on_enter ? ui.badge("ACTIVE", "ok", t("On", "Bật")) : ui.badge("OFF")],
              [t("Alert on exit", "Cảnh báo khi ra"), selected.alert_on_exit ? ui.badge("ACTIVE", "ok", t("On", "Bật")) : ui.badge("OFF")],
            ])}<div class="mt small muted">${esc(t("Containment rule: a truck exactly on the boundary counts as inside (ST_Covers). Each reading is checked against the zones of the truck's current fleet.", "Quy tắc: xe nằm đúng trên ranh giới được tính là ở trong vùng (ST_Covers). Mỗi bản tin được đối chiếu với các vùng của đội hiện tại của xe."))}</div>`,
          })}
          ${ui.card({
            title: t("Trucks inside now", "Xe đang trong vùng"), sub: `${inside.length}`, flush: true,
            body: ui.table({ rows: inside, empty: t("No truck inside", "Không có xe trong vùng"), href: G3.canSee("vehicle-detail") ? (v) => `/vehicles/${v.vehicle_id}` : null, columns: [
              { label: t("Truck", "Xe"), render: (v) => `<b>${esc(v.license_plate)}</b>${db.fleetAndDescendants(selected.fleet_id).includes(v.fleet_id) ? "" : `<span class="sub">${esc(t("not in the zone's fleet: no alert", "không thuộc đội của vùng: không cảnh báo"))}</span>`}` },
              { label: t("State", "Trạng thái"), render: (v) => ui.vehicleState(v) },
              { label: t("Driver", "Tài xế"), render: (v) => ui.driverName(v.driver_id) },
            ] }),
          })}
          ${ui.card({
            title: t("Recent alerts", "Cảnh báo gần đây"), sub: "GEOFENCE_ALERT", flush: true,
            body: alerts.length ? `<ul class="list">${alerts.map((a) => { const v = db.vehicle(a.vehicle_id); return `<li><div class="sev-icon ${a.severity}">${ui.icon(a.transition === "ENTER" ? "download" : "upload", 15)}</div><div class="grow"><div class="title">${esc(v ? v.license_plate : "—")} ${transitionBadge(a.transition)}</div><div class="meta">${fmt.dateTime(a.at)} · ${fmt.ago(a.at)}</div></div>${a.severity === "CRITICAL" ? ui.badge("CRITICAL") : ""}</li>`; }).join("")}</ul>` : ui.empty(t("No alert for this zone", "Chưa có cảnh báo cho vùng này")),
          })}
        </div>`;
      }

      return `${ui.pageHead({
        title: t("Geofences", "Vùng địa lý"),
        sub: esc(t("Draw depots, customer sites and restricted areas; get an alert when a truck of the zone's fleet enters or leaves.", "Vẽ bãi xe, điểm khách hàng và vùng cấm; nhận cảnh báo khi xe thuộc đội của vùng đi vào hoặc rời vùng.")),
        features: ["FLT-05"],
        actions: `${ui.segmented([["", t("All", "Tất cả")], ...Object.entries(KINDS).map(([k, l]) => [k, t(l[0], l[1])])], query.kind || "", "kind")}${canEdit ? ui.btn(t("Draw a zone", "Vẽ vùng"), { icon: "shape", kind: "primary", action: "zone-draw", disabled: drawing }) : ""}`,
      })}
      <div class="grid cols-4">
        ${ui.kpi({ label: t("Zones", "Số vùng"), icon: "shape", value: all.length, foot: Object.keys(KINDS).map((k) => `${all.filter((g) => g.kind === k).length} ${esc(t(KINDS[k][0].toLowerCase(), KINDS[k][1].toLowerCase()))}`).join(" · ") })}
        ${ui.kpi({ label: t("Trucks inside a zone", "Xe đang trong vùng"), icon: "truck", value: insideTotal, foot: `${esc(t("of", "trên"))} ${vehicles.length} ${esc(t("trucks with a position", "xe có vị trí"))}` })}
        ${ui.kpi({ label: t("Zone alerts (24 h)", "Cảnh báo vùng (24 giờ)"), icon: "bell", value: alerts24.length, foot: `${alerts24.filter((a) => a.transition === "ENTER").length} ${esc(t("entries", "lượt vào"))} · ${alerts24.filter((a) => a.transition === "EXIT").length} ${esc(t("exits", "lượt ra"))}` })}
        ${ui.kpi({ label: t("In a restricted area now", "Đang trong vùng cấm"), icon: "alert", value: inRestricted.length, tone: inRestricted.length ? "danger" : "ok", foot: inRestricted.length ? inRestricted.map((v) => esc(v.license_plate)).join(", ") : esc(t("none", "không có")) })}
      </div>
      <div class="grid side mt">
        <div class="card" style="overflow:hidden;position:relative">
          ${ui.map({ vehicles, locations, geofences: all, height: 540, legend: !drawing, id: "geofences" })}
          ${drawing ? `<div class="card" style="position:absolute;left:12px;top:12px;padding:10px 12px;max-width:calc(100% - 80px);border-color:#7c3aed">
            <div class="small strong">${esc(draft.targetId ? t("Redraw the boundary", "Vẽ lại ranh giới") : t("Draw a zone", "Vẽ vùng"))} · <span data-draw-count></span></div>
            <div class="small muted" style="margin:2px 0 8px">${esc(t("Click on the map to add corners; click the first corner or Finish to close the shape. Dragging is off while drawing; zoom with the wheel.", "Bấm lên bản đồ để thêm góc; bấm góc đầu tiên hoặc Hoàn tất để khép vùng. Không kéo bản đồ khi đang vẽ; phóng to bằng con lăn."))}</div>
            <div class="row">${ui.btn(t("Finish", "Hoàn tất"), { sm: true, kind: "primary", attrs: 'data-draw="finish"' })}${ui.btn(t("Undo", "Hoàn tác"), { sm: true, attrs: 'data-draw="undo"' })}${ui.btn(t("Cancel", "Hủy"), { sm: true, kind: "ghost", attrs: 'data-draw="cancel"' })}</div></div>` : ""}
        </div>
        ${ui.card({ title: t("Zones", "Danh sách vùng"), sub: `${zones.length}`, flush: true, body: `<div style="max-height:488px;overflow-y:auto">${list}</div>` })}
      </div>
      ${detail || `<div class="mt">${ui.callout("info", esc(t("Select a zone in the list or on the map to see the trucks inside and its recent alerts.", "Chọn một vùng trong danh sách hoặc trên bản đồ để xem xe trong vùng và cảnh báo gần đây.")))}</div>`}`;
    },
    mount(root, { query }) {
      bindQueryControls(root);
      const wrap = root.querySelector("[data-map-wrap]");
      const svg = wrap.querySelector("svg");
      const selected = visibleGeofences().find((g) => g.geofence_id === query.zone);
      if (selected) {
        const path = svg.querySelector(`[data-geofence="${selected.geofence_id}"]`);
        if (path) { path.style.fillOpacity = "0.3"; path.style.strokeWidth = "3.5"; path.style.strokeDasharray = "none"; }
        if (!draft) {
          const xs = selected.boundary.map(([lat, lon]) => ui.project(lat, lon));
          const w = Math.max(...xs.map((p) => p[0])) - Math.min(...xs.map((p) => p[0]));
          const lat = selected.boundary.reduce((a, p) => a + p[0], 0) / selected.boundary.length;
          const lon = selected.boundary.reduce((a, p) => a + p[1], 0) / selected.boundary.length;
          setTimeout(() => wrap.zoomTo && wrap.zoomTo(lat, lon, Math.max(220, w * 6)), 0);
        }
      }
      drawPreview(root);
      svg.addEventListener("click", (e) => {
        if (e.target.closest(".map-controls")) return;
        if (!draft) {
          if (wrap.dataset.justDragged) return;
          const zone = e.target.closest("[data-geofence]");
          const truck = e.target.closest("[data-vehicle]");
          if (truck && G3.canSee("vehicle-detail")) G3.navigate(`/vehicles/${truck.dataset.vehicle}`);
          else if (zone) G3.setQuery({ zone: zone.dataset.geofence });
          return;
        }
        if (e.target.closest("[data-close-shape]")) { finishDrawing(root); return; }
        const [x, y] = wrap.toMapPoint(e);
        draft.points.push(ui.unproject(x, y));
        drawPreview(root);
      });
      root.querySelectorAll("[data-zone-row]").forEach((row) => row.addEventListener("click", (e) => { if (!e.target.closest("label, input")) G3.setQuery({ zone: row.dataset.zoneRow }); }));
      root.querySelectorAll("[data-toggle-alert]").forEach((box) => box.addEventListener("change", () => {
        const g = db.geofences.find((x) => x.geofence_id === box.dataset.id);
        if (box.dataset.toggleAlert === "enter") g.alert_on_enter = box.checked; else g.alert_on_exit = box.checked;
        G3.render();
        ui.toast(t(`${g.name}: alert on ${box.dataset.toggleAlert === "enter" ? "enter" : "exit"} ${box.checked ? "on" : "off"}`, `${g.name}: ${box.checked ? "bật" : "tắt"} cảnh báo khi ${box.dataset.toggleAlert === "enter" ? "vào" : "ra"}`));
      }));
      on(root, "click", (e) => {
        const btn = e.target.closest("[data-action], [data-draw]");
        if (!btn) return;
        const action = btn.dataset.action || "draw-" + btn.dataset.draw;
        if (action === "zone-draw") {
          if (!visibleFleets(false).length) { ui.toast(t("Create a fleet first: a zone applies to a fleet's trucks.", "Hãy tạo đội xe trước: vùng áp dụng cho xe của một đội."), "alert"); return; }
          draft = { points: [], targetId: null };
          G3.render();
        } else if (action === "zone-redraw") {
          draft = { points: [], targetId: selected.geofence_id };
          G3.render();
        } else if (action === "draw-undo") { draft.points.pop(); drawPreview(root); }
        else if (action === "draw-cancel") { draft = null; G3.render(); }
        else if (action === "draw-finish") finishDrawing(root);
        else if (action === "zone-edit") geofenceForm({ g: selected });
        else if (action === "zone-delete") {
          confirmModal({
            title: t(`Delete ${selected.name}`, `Xóa ${selected.name}`), label: t("Delete", "Xóa"),
            body: `<p>${esc(t("The zone stops raising alerts at once. Alerts already sent stay in the notification center.", "Vùng ngừng phát cảnh báo ngay. Các cảnh báo đã gửi vẫn còn trong trung tâm thông báo."))}</p>`,
            onConfirm() { db.geofences.splice(db.geofences.indexOf(selected), 1); G3.setQuery({ zone: null }); ui.toast(t("Zone deleted", "Đã xóa vùng")); },
          });
        }
      });
      G3.onTick(() => {
        ui.refreshMarkers(root);
        root.querySelectorAll("[data-inside]").forEach((el) => { const g = db.geofences.find((x) => x.geofence_id === el.dataset.inside); if (g) el.textContent = trucksInside(g).length; });
      });
    },
  });

  /* ================================================================== */
  /* Safety (SAF-01, SAF-02, SAF-03, SAF-04, SAF-06, SAF-07, SAF-09)     */
  /* ================================================================== */
  const safetyVehicles = () => scope.vehicles().filter((v) => v.live && v.organization_id !== "org-g3");

  function evidencePack(vehicleId, atIso) {
    const trucks = safetyVehicles().filter((v) => cameraOf(v));
    if (!trucks.length) { ui.toast(t("No truck with a camera in your scope.", "Không có xe gắn camera trong phạm vi của bạn."), "alert"); return; }
    const at = atIso ? new Date(atIso) : new Date(Date.now() - 2 * HOUR);
    const local = new Date(at.getTime() - at.getTimezoneOffset() * MIN).toISOString().slice(0, 16);
    ui.formModal({
      title: t("Evidence pack", "Hồ sơ trích xuất"), wide: true,
      intro: `<p class="small muted">${esc(t("Clips, route, speed and safety events around an incident, in one pack for insurance or a dispute (SAF-06). The next step asks for the reason, written to the access audit log.", "Clip, lộ trình, tốc độ và sự kiện an toàn quanh thời điểm sự cố, gộp trong một hồ sơ cho bảo hiểm hoặc khiếu nại (SAF-06). Bước tiếp theo yêu cầu lý do, được ghi vào nhật ký truy cập."))}</p>`,
      fields: [
        { name: "vehicle", label: t("Truck", "Xe"), type: "select", value: vehicleId || trucks[0].vehicle_id, options: trucks.map((v) => [v.vehicle_id, `${v.license_plate} · ${v.vin}`]) },
        { name: "at", label: t("Incident time", "Thời điểm sự cố"), type: "datetime-local", value: local, required: true },
        { name: "window", label: t("Window", "Khoảng thời gian"), type: "select", value: "10", options: [["5", t("± 5 minutes", "± 5 phút")], ["10", t("± 10 minutes", "± 10 phút")], ["30", t("± 30 minutes", "± 30 phút")]] },
        { name: "parts", label: t("Include", "Bao gồm"), type: "checkboxes", value: ["CLIPS", "ROUTE", "SPEED", "EVENTS"], options: [["CLIPS", t("Camera clips", "Clip camera")], ["ROUTE", t("Route", "Lộ trình")], ["SPEED", t("Speed", "Tốc độ")], ["EVENTS", t("Safety events", "Sự kiện an toàn")]] },
      ],
      submitLabel: t("Continue", "Tiếp tục"),
      onSubmit(values) {
        const center = new Date(values.at).getTime();
        if (Number.isNaN(center)) return t("Enter the incident time.", "Nhập thời điểm sự cố.");
        if (center > Date.now()) return t("The incident time is in the future.", "Thời điểm sự cố ở tương lai.");
        if (Date.now() - center > 365 * DAY) return t("Older than 1 year: the server no longer keeps this journey data (SAF-09).", "Quá 1 năm: máy chủ không còn lưu dữ liệu hành trình này (SAF-09).");
        if (!values.parts.length) return t("Choose at least one part.", "Chọn ít nhất một phần.");
        const v = db.vehicle(values.vehicle);
        const half = Number(values.window) * MIN;
        const r = rng(v.vehicle_id + values.at);
        const events = vehicleEvents(v).filter((x) => Math.abs(new Date(x.at).getTime() - center) <= half);
        const rows = [];
        const clipsOk = Date.now() - center <= 30 * DAY;
        for (let ms = center - half; ms <= center + half; ms += 30 * 1000) {
          const ev = events.find((x) => Math.abs(new Date(x.at).getTime() - ms) < 15 * 1000) || (Math.abs(ms - center) < 15 * 1000 ? { type: "INCIDENT_MARK" } : null);
          rows.push({
            recorded_at: new Date(ms).toISOString(),
            lat: values.parts.includes("ROUTE") ? (v.live.lat + (ms - center) / HOUR * 0.08 * (r() - 0.3)).toFixed(5) : "",
            lon: values.parts.includes("ROUTE") ? (v.live.lon + (ms - center) / HOUR * 0.1).toFixed(5) : "",
            speed_kmh: values.parts.includes("SPEED") ? Math.max(0, Math.round(42 + Math.sin(ms / 60000) * 14 + (r() - 0.5) * 8)) : "",
            event: values.parts.includes("EVENTS") && ev ? (ev.type === "INCIDENT_MARK" ? "INCIDENT" : ev.type) : "",
            clip_id: values.parts.includes("CLIPS") && clipsOk && (ms - (center - half)) % (60 * 1000) === 0 ? `CLIP-${v.vehicle_id.slice(4)}-${Math.round(ms / 60000)}` : "",
          });
        }
        if (!clipsOk && values.parts.includes("CLIPS")) ui.toast(t("Clips older than 30 days are no longer on the device; the pack holds journey data only.", "Clip quá 30 ngày không còn trên thiết bị; hồ sơ chỉ có dữ liệu hành trình."), "alert");
        ui.auditedExport({
          dataType: "CAMERA_FOOTAGE", subject: v.license_plate, filename: `evidence-${v.license_plate}-${values.at.replace(/[:T]/g, "")}.csv`, rows,
          columns: [{ label: "recorded_at", key: "recorded_at" }, { label: "lat", key: "lat" }, { label: "lon", key: "lon" }, { label: "speed_kmh", key: "speed_kmh" }, { label: "event", key: "event" }, { label: "clip_id", key: "clip_id" }],
        });
      },
    });
  }

  function editWeights() {
    const types = Object.keys(weights);
    ui.formModal({
      title: t("Safety score weights", "Trọng số điểm an toàn"), wide: true,
      intro: `<p class="small muted">${esc(t("Points taken off the 100-point score per event per 100 km. Thresholds are fixed per vehicle model at first and recalibrated on real data.", "Số điểm trừ khỏi thang 100 cho mỗi sự kiện trên 100 km. Ngưỡng ban đầu cố định theo mẫu xe và được hiệu chỉnh lại theo dữ liệu thực tế."))}</p>`,
      fields: types.map((type) => ({ name: type, label: eventLabel(type) + (CAMERA_EVENTS[type] ? " · ADAS" : ""), type: "number", value: weights[type], required: true, attrs: 'min="0" max="20" step="0.5"' })),
      submitLabel: t("Apply", "Áp dụng"),
      onSubmit(values) {
        for (const type of types) { const n = Number(values[type]); if (!(n >= 0 && n <= 20)) return t("Each weight is between 0 and 20.", "Mỗi trọng số trong khoảng 0 đến 20."); }
        types.forEach((type) => { weights[type] = Number(values[type]); });
        G3.render();
        ui.toast(t("Weights applied: every score was recomputed", "Đã áp dụng trọng số: mọi điểm được tính lại"));
      },
    });
  }

  function weekLabels() {
    return Array.from({ length: 8 }, (_, i) => { const d = new Date(db.NOW - (7 - i) * 7 * DAY); return `${d.getDate()}/${d.getMonth() + 1}`; });
  }

  function rankingTab(query) {
    const drivers = scope.filter(db.drivers).filter((d) => d.status === "ACTIVE").map((d) => ({ d, s: driverSafety(d) })).filter((x) => x.s).sort((a, b) => b.s.score - a.s.score);
    const anyAdas = drivers.some((x) => orgHasAdas(x.d.organization_id));
    const types = Object.keys(TELEMATICS_EVENTS);
    const vehicles = safetyVehicles();
    const allEvents = vehicles.flatMap(vehicleEvents);
    const perWeek = (filterFn) => Array.from({ length: 8 }, (_, i) => allEvents.filter((e) => filterFn(e) && Math.floor((db.NOW - new Date(e.at).getTime()) / (7 * DAY)) === 7 - i).length);
    const series = types.map((type, i) => ({ name: eventLabel(type), values: perWeek((e) => e.type === type), color: ui.PALETTE[i] }));
    if (anyAdas) series.push({ name: t("Camera / ADAS", "Camera / ADAS"), values: perWeek((e) => e.kind === "CAMERA"), color: ui.PALETTE[5] });
    return `<div class="grid side">
      ${ui.card({
        title: t("Events per week", "Sự kiện theo tuần"), sub: esc(t("8 weeks · SAF-02", "8 tuần · SAF-02")),
        body: `${ui.legend(series.map((s) => [s.name, s.color]))}<div style="height:10px"></div>${ui.barChart({ labels: weekLabels(), series, stacked: true, height: 250 })}`,
      })}
      ${ui.card({
        title: t("Score weights", "Trọng số điểm"), sub: esc(t("points per event / 100 km", "điểm trừ mỗi sự kiện / 100 km")),
        actions: scope.has("FLEET_MANAGER", "HEAD_ADMIN") ? ui.btn(t("Edit", "Sửa"), { icon: "edit", sm: true, kind: "ghost", action: "safety-weights" }) : "",
        body: `${ui.hBars({ labels: Object.keys(weights).filter((k) => TELEMATICS_EVENTS[k] || anyAdas).map((k) => eventLabel(k) + (CAMERA_EVENTS[k] ? " (ADAS)" : "")), series: [{ values: Object.keys(weights).filter((k) => TELEMATICS_EVENTS[k] || anyAdas).map((k) => weights[k]), color: ui.PALETTE[3] }], yFormat: (v) => fmt.num(v, 1) })}<p class="small muted" style="margin:10px 0 0">${esc(t("Score = 100 − Σ weight × events per 100 km, over 30 days. Camera events count only where the advanced plan is used.", "Điểm = 100 − Σ trọng số × số sự kiện/100 km, trong 30 ngày. Sự kiện camera chỉ tính khi dùng gói nâng cao."))}</p>`,
      })}
    </div>
    <div class="mt">${ui.card({
      title: t("Driver ranking", "Xếp hạng tài xế"), sub: esc(t("events per 100 km, last 30 days · SAF-01", "sự kiện/100 km, 30 ngày qua · SAF-01")), flush: true,
      actions: ui.btn(t("Export", "Xuất"), { icon: "download", sm: true, action: "safety-export" }),
      body: ui.table({
        rows: drivers.slice(0, query.all ? 200 : 15), href: G3.canSee("driver-detail") ? (x) => `/drivers/${x.d.driver_id}` : null,
        columns: [
          { label: "#", render: (x) => `<b class="num">${drivers.indexOf(x) + 1}</b>` },
          { label: t("Driver", "Tài xế"), render: (x) => `<b>${esc(x.d.full_name)}</b><span class="sub">${internalMulti() ? ui.orgName(x.d.organization_id) + " · " : ""}${fmt.num(x.s.km)} km</span>` },
          ...types.map((type) => ({ label: eventLabel(type), align: "right", render: (x) => { const rate = x.s.rates[type]; return `<span style="color:${rate > 0.6 ? "var(--danger)" : rate > 0.3 ? "var(--warn)" : "inherit"}">${fmt.num(rate, 2)}</span>`; } })),
          anyAdas ? { label: "ADAS", align: "right", render: (x) => (orgHasAdas(x.d.organization_id) ? fmt.num(Object.keys(CAMERA_EVENTS).reduce((a, k) => a + (x.s.rates[k] || 0), 0), 2) : '<span class="muted">—</span>') } : null,
          { label: t("Trend", "Xu hướng"), render: (x) => ui.sparkline(x.s.trend, `var(--${scoreTone(x.s.score)})`, 70, 22) },
          { label: t("Score", "Điểm"), render: (x) => scoreCell(x.d) },
        ].filter(Boolean),
      }) + (drivers.length > 15 && !query.all ? `<div class="card-body" style="text-align:center">${ui.btn(t(`Show all ${drivers.length}`, `Hiện cả ${drivers.length}`), { sm: true, kind: "ghost", attrs: 'data-show-all' })}</div>` : ""),
    })}</div>`;
  }

  function eventsTab(query) {
    const vehicles = safetyVehicles();
    const days = Number(query.days || 7);
    const inWindow = (e) => Date.now() - new Date(e.at).getTime() < days * DAY;
    const pickVehicles = query.truck ? vehicles.filter((v) => v.vehicle_id === query.truck) : vehicles;
    const events = pickVehicles.flatMap(vehicleEvents).filter(inWindow).filter((e) => !query.type || e.type === query.type).sort((a, b) => (a.at < b.at ? 1 : -1));
    const telematics = events.filter((e) => e.kind === "TELEMATICS");
    const camera = events.filter((e) => e.kind === "CAMERA");
    const adasOrgs = [...new Set(vehicles.map((v) => v.organization_id))].filter(orgHasAdas);
    const columns = (withClip) => [
      { label: t("Time", "Thời gian"), render: (e) => `${fmt.dateTime(e.at)}<span class="sub">${fmt.ago(e.at)}</span>` },
      { label: t("Event", "Sự kiện"), render: (e) => `<b>${esc(eventLabel(e.type))}</b>${e.type === "SPEEDING" ? `<span class="sub">${e.speed_kmh} km/h · ${esc(t("limit 70", "giới hạn 70"))}</span>` : `<span class="sub">${e.speed_kmh} km/h</span>`}` },
      { label: t("Severity", "Mức độ"), render: (e) => ui.badge(e.severity) },
      { label: t("Truck", "Xe"), render: (e) => plateLink(db.vehicle(e.vehicle_id)) },
      { label: t("Driver", "Tài xế"), render: (e) => (e.driver_id && G3.canSee("driver-detail") ? `<a href="#/drivers/${e.driver_id}">${ui.driverName(e.driver_id)}</a>` : ui.driverName(e.driver_id)) },
      { label: t("Road", "Tuyến"), render: (e) => `<span class="small">${esc(e.road)}</span>` },
      { label: "", align: "right", render: (e) => (withClip || e.has_clip ? ui.btn(t("Evidence", "Trích xuất"), { sm: true, kind: "ghost", icon: "camera", attrs: `data-evidence="${e.vehicle_id}" data-at="${e.at}"` }) : "") },
    ];
    const typeOptions = [["", t("Every event type", "Mọi loại sự kiện")], ...Object.keys(TELEMATICS_EVENTS).map((k) => [k, eventLabel(k)]), ...(adasOrgs.length ? Object.keys(CAMERA_EVENTS).map((k) => [k, eventLabel(k) + " (ADAS)"]) : [])];
    const byType = (list, map) => Object.keys(map).map((k) => [k, list.filter((e) => e.type === k).length]);
    const summary = (list, map) => `<div class="row" style="gap:6px;padding:10px 16px;border-bottom:1px solid var(--border)">${byType(list, map).map(([k, n]) => `<span class="tag">${esc(eventLabel(k))} <b>${n}</b></span>`).join("")}</div>`;
    return `<div class="card mb"><div class="toolbar">
        ${ui.select("truck", [["", t("Every truck", "Mọi xe")], ...vehicles.map((v) => [v.vehicle_id, v.license_plate])], query.truck || "")}
        ${ui.select("type", typeOptions, query.type || "")}
        ${ui.segmented([["7", t("7 days", "7 ngày")], ["30", t("30 days", "30 ngày")]], String(days), "days")}
        <span class="grow"></span><span class="small muted">${esc(t(`${events.length} events`, `${events.length} sự kiện`))}</span></div></div>
      ${ui.card({
        title: t("Telematics events", "Sự kiện từ T-Box"), sub: esc(t("basic plan · from acceleration, speed and route limits", "gói cơ bản · từ gia tốc, tốc độ và giới hạn tuyến")), flush: true,
        body: summary(telematics, TELEMATICS_EVENTS) + ui.table({ rows: telematics.slice(0, 25), columns: columns(false), empty: t("No telematics event in the period", "Không có sự kiện T-Box trong kỳ") }) + (telematics.length > 25 ? `<div class="small muted" style="padding:10px 16px">${esc(t(`Showing 25 of ${telematics.length}`, `Hiện 25 / ${telematics.length}`))}</div>` : ""),
      })}
      <div class="mt">${ui.card({
        title: t("Camera / ADAS events", "Sự kiện camera / ADAS"), sub: esc(t("advanced plan · in-cab ADAS (SAF-04)", "gói nâng cao · ADAS trong cabin (SAF-04)")), flush: true,
        body: adasOrgs.length
          ? summary(camera, CAMERA_EVENTS) + ui.table({ rows: camera.slice(0, 30), columns: columns(true), empty: t("No camera event in the period", "Không có sự kiện camera trong kỳ") })
          : `<div class="card-body">${ui.callout("info", esc(t("Drowsiness, distraction, phone use and tailgating alerts come from the in-cab ADAS of the Advanced plan. Your trucks record the legal driver camera (Basic plan) without ADAS analysis.", "Cảnh báo buồn ngủ, mất tập trung, dùng điện thoại và bám đuôi đến từ ADAS trong cabin của gói Nâng cao. Xe của bạn ghi hình tài xế theo quy định (gói Cơ bản) nhưng chưa phân tích ADAS.")))}${G3.canSee("plans") ? `<div class="mt">${ui.btn(t("Compare plans", "So sánh gói"), { href: "#/plans", sm: true })}</div>` : ""}</div>`,
      })}</div>`;
  }

  function camerasTab() {
    const vehicles = safetyVehicles();
    const rows = vehicles.map((v) => ({ v, c: cameraOf(v) })).filter((x) => x.c);
    const issues = rows.filter((x) => x.c.status !== "OK").sort((a, b) => (a.c.cause === "DELIBERATE" ? -1 : b.c.cause === "DELIBERATE" ? 1 : 0));
    const avg = (fn) => (rows.length ? rows.reduce((a, x) => a + fn(x.c), 0) / rows.length : 0);
    const retention = scope.isInternal() ? retentionCard(rows) : "";
    return `<div class="grid cols-4">
        ${ui.kpi({ label: t("Cameras recording", "Camera đang ghi"), icon: "camera", value: `${rows.filter((x) => x.c.status === "OK").length}<small>/ ${rows.length}</small>`, foot: esc(t("legal driver camera · SAF-03", "camera giám sát người lái · SAF-03")) })}
        ${ui.kpi({ label: t("Deliberate covering", "Cố ý che camera"), icon: "eye", value: rows.filter((x) => x.c.cause === "DELIBERATE").length, tone: rows.some((x) => x.c.cause === "DELIBERATE") ? "danger" : "ok", foot: esc(t("told apart from faults and signal loss", "phân biệt với lỗi và mất sóng")) })}
        ${ui.kpi({ label: t("Uptime (7 d)", "Thời gian hoạt động (7 ngày)"), icon: "activity", value: fmt.pct(avg((c) => c.uptime), 1) })}
        ${ui.kpi({ label: t("Upload success", "Tải lên thành công"), icon: "upload", value: fmt.pct(avg((c) => c.upload), 1), foot: esc(t("clips and journey data to the server", "clip và dữ liệu hành trình lên máy chủ")) })}
      </div>
      ${issues.length ? `<div class="mt">${ui.card({
        title: t("Open camera alerts", "Cảnh báo camera đang mở"), sub: "SAF-07", flush: true,
        body: `<ul class="list">${issues.map(({ v, c }) => `<li><div class="sev-icon ${c.cause === "DELIBERATE" || c.status === "POWER_LOSS" ? "CRITICAL" : "WARNING"}">${ui.icon(c.cause === "DELIBERATE" ? "eye" : c.cause === "SIGNAL" ? "signal" : "camera", 16)}</div><div class="grow"><div class="title">${plateLink(v)} · ${esc(t(CAMERA_STATUS[c.status][0], CAMERA_STATUS[c.status][1]))}</div><div class="meta">${esc(t(CAUSE_LABEL[c.cause][0], CAUSE_LABEL[c.cause][1]))} · ${esc(c.cause === "DELIBERATE" ? t("lens blocked while the ignition is on and the T-Box reports normally", "ống kính bị che khi xe nổ máy và T-Box vẫn báo bình thường") : c.cause === "SIGNAL" ? t("camera and T-Box silent together: coverage or power, not tampering", "camera và T-Box cùng im lặng: do sóng hoặc nguồn, không phải can thiệp") : t("device self-test reported it", "thiết bị tự kiểm tra báo lỗi"))} · ${esc(t("since", "từ"))} ${fmt.ago(c.since)}</div></div>${cameraBadge(c.status)}</li>`).join("")}</ul>`,
      })}</div>` : ""}
      <div class="mt">${ui.card({
        title: t("Camera health per truck", "Tình trạng camera theo xe"), sub: esc(t("lost signal, covering, misalignment, power, storage, clock", "mất tín hiệu, che, lệch góc, nguồn, bộ nhớ, đồng hồ")), flush: true,
        body: ui.table({
          rows, empty: t("No truck with a camera in your scope", "Không có xe gắn camera trong phạm vi"),
          columns: [
            { label: t("Truck", "Xe"), render: (x) => `${plateLink(x.v)}<span class="sub mono">${esc(x.c.serial)}${x.c.adas ? " · ADAS" : ""}</span>` },
            internalMulti() ? { label: t("Organization", "Tổ chức"), cls: "nowrap", render: (x) => ui.orgName(x.v.organization_id) } : null,
            { label: t("Status", "Trạng thái"), render: (x) => cameraBadge(x.c.status) },
            { label: t("Uptime 7 d", "Hoạt động 7 ngày"), align: "right", render: (x) => `<span style="color:${x.c.uptime < 90 ? "var(--danger)" : "inherit"}">${fmt.pct(x.c.uptime, 1)}</span>` },
            { label: t("Upload", "Tải lên"), align: "right", render: (x) => `<span style="color:${x.c.upload < 90 ? "var(--warn)" : "inherit"}">${fmt.pct(x.c.upload, 1)}</span>` },
            { label: t("Storage", "Bộ nhớ"), render: (x) => `<span class="soc" style="min-width:90px">${ui.meter(x.c.storage, x.c.storage > 90 ? "danger" : x.c.storage > 75 ? "warn" : "")}<b>${x.c.storage}%</b></span>` },
            { label: t("Clock", "Đồng hồ"), align: "right", render: (x) => `<span style="color:${x.c.clock_offset_s > 30 ? "var(--warn)" : "inherit"}">${x.c.clock_offset_s > 0 ? "+" : ""}${x.c.clock_offset_s} s</span>` },
            { label: t("Firmware", "Phần mềm"), cls: "nowrap", render: (x) => `<span class="mono small">${esc(x.c.firmware)}</span>` },
            { label: t("Last frame", "Khung hình cuối"), render: (x) => fmt.ago(x.c.last_frame_at) },
            { label: "", align: "right", render: (x) => ui.btn("", { icon: "download", sm: true, kind: "ghost", attrs: `data-evidence="${x.v.vehicle_id}" title="${esc(t("Evidence pack", "Hồ sơ trích xuất"))}"` }) },
          ].filter(Boolean),
        }),
      })}</div>${retention}`;
  }

  function retentionCard(rows) {
    const shortDevice = rows.filter((x) => x.c.device_days < 3);
    const shortJourney = rows.filter((x) => x.c.device_days < 30);
    const serverDays = Math.round((Date.now() - new Date(db.org("org-g3").created_at).getTime()) / DAY);
    const items = [
      [t("Journey data", "Dữ liệu hành trình"), t("On the device", "Trên thiết bị"), t("≥ 30 days", "≥ 30 ngày"), shortJourney.length ? t(`${shortJourney.length} truck(s) below`, `${shortJourney.length} xe chưa đạt`) : t("every truck ≥ 31 days", "mọi xe ≥ 31 ngày"), shortJourney.length ? "warn" : "ok"],
      [t("Journey data", "Dữ liệu hành trình"), t("Service server", "Máy chủ dịch vụ"), t("≥ 1 year", "≥ 1 năm"), t(`${serverDays} days kept (since go-live)`, `Đang lưu ${serverDays} ngày (từ khi vận hành)`), "ok"],
      [t("Journey data", "Dữ liệu hành trình"), t("Traffic police", "Cục CSGT"), t("≥ 3 years", "≥ 3 năm"), t("sent in real time · receipts kept (SAF-08)", "truyền thời gian thực · lưu biên nhận (SAF-08)"), "ok"],
      [t("Driver images", "Hình ảnh người lái"), t("On the device", "Trên thiết bị"), t("≥ 3 days", "≥ 3 ngày"), shortDevice.length ? t(`${shortDevice.map((x) => x.v.license_plate).join(", ")}: storage full, 2 days left`, `${shortDevice.map((x) => x.v.license_plate).join(", ")}: đầy bộ nhớ, chỉ còn 2 ngày`) : t("every truck ≥ 3 days", "mọi xe ≥ 3 ngày"), shortDevice.length ? "danger" : "ok"],
      [t("Violation data", "Dữ liệu vi phạm"), t("Service server", "Máy chủ dịch vụ"), t("≥ 3 months", "≥ 3 tháng"), t("kept 12 months", "lưu 12 tháng"), "ok"],
    ];
    return `<div class="mt">${ui.card({
      title: t("Retention compliance", "Tuân thủ thời hạn lưu trữ"), sub: esc(t("internal · SAF-09", "nội bộ · SAF-09")), flush: true,
      body: ui.table({
        rows: items,
        columns: [
          { label: t("Data", "Dữ liệu"), render: (x) => `<b>${esc(x[0])}</b>` },
          { label: t("Where", "Nơi lưu"), render: (x) => esc(x[1]) },
          { label: t("Required", "Yêu cầu"), render: (x) => esc(x[2]) },
          { label: t("Now", "Hiện tại"), render: (x) => `<span class="small">${esc(x[3])}</span>` },
          { label: t("Status", "Trạng thái"), render: (x) => ui.badge(x[4] === "ok" ? "OK" : "WARNING", x[4], x[4] === "ok" ? t("Compliant", "Đạt") : x[4] === "warn" ? t("At risk", "Có rủi ro") : t("Data would be missing", "Sẽ thiếu dữ liệu")) },
        ],
      }) + `<div class="card-body" style="border-top:1px solid var(--border)">${ui.callout("info", esc(t("An alert is raised before data would be missing at an inspection: a camera whose storage holds less than the required days is flagged to Operations and the fleet manager.", "Hệ thống cảnh báo trước khi thiếu dữ liệu khi thanh tra: camera có bộ nhớ chứa ít hơn số ngày yêu cầu được báo cho Vận hành và quản lý đội.")))}</div>`,
    })}</div>`;
  }

  G3.page({
    id: "safety",
    path: "/safety",
    title: ["Driver safety", "An toàn lái xe"],
    roles: ["FLEET_MANAGER", "OPERATIONS", "CUSTOMER_CARE"],
    features: ["SAF-01", "SAF-02", "SAF-03", "SAF-04", "SAF-06", "SAF-07", "SAF-09"],
    nav: { group: "fleet", order: 4, icon: "shield" },
    render({ query }) {
      const tab = query.tab || "ranking";
      const vehicles = safetyVehicles();
      const scored = scope.filter(db.drivers).filter((d) => d.status === "ACTIVE").map(driverSafety).filter(Boolean);
      const avg = scored.length ? scored.reduce((a, s) => a + s.score, 0) / scored.length : 0;
      const km = scored.reduce((a, s) => a + s.km, 0);
      const events30 = vehicles.flatMap(vehicleEvents).filter((e) => Date.now() - new Date(e.at).getTime() < 30 * DAY);
      const lastWeek = events30.filter((e) => Date.now() - new Date(e.at).getTime() < 7 * DAY).length;
      const prevWeek = events30.filter((e) => { const age = Date.now() - new Date(e.at).getTime(); return age >= 7 * DAY && age < 14 * DAY; }).length;
      const cams = vehicles.map(cameraOf).filter(Boolean);
      const canEvidence = scope.has("FLEET_MANAGER", "CUSTOMER_CARE", "HEAD_ADMIN");
      const body = tab === "events" ? eventsTab(query) : tab === "cameras" ? camerasTab() : rankingTab(query);
      return `${ui.pageHead({
        title: t("Driver safety", "An toàn lái xe"),
        sub: esc(t("Safety score per driver, safety events and the legal driver camera.", "Điểm an toàn theo tài xế, sự kiện an toàn và camera giám sát người lái theo quy định.")),
        features: ["SAF-01", "SAF-02", "SAF-03", "SAF-04", "SAF-06", "SAF-07", "SAF-09"],
        actions: canEvidence ? ui.btn(t("Evidence pack", "Hồ sơ trích xuất"), { icon: "camera", kind: "primary", action: "safety-evidence" }) : "",
      })}
      <div class="grid cols-5">
        ${ui.kpi({ label: t("Average score", "Điểm trung bình"), icon: "shield", value: fmt.num(avg), unit: "/100", tone: scoreTone(avg) === "ok" ? undefined : scoreTone(avg), foot: esc(t(`${scored.length} active drivers`, `${scored.length} tài xế hoạt động`)) })}
        ${ui.kpi({ label: t("Drivers below 70", "Tài xế dưới 70 điểm"), icon: "alert", value: scored.filter((s) => s.score < 70).length, tone: scored.some((s) => s.score < 70) ? "danger" : "ok", foot: esc(t("to coach this week", "cần kèm cặp tuần này")) })}
        ${ui.kpi({ label: t("Events per 100 km", "Sự kiện / 100 km"), icon: "activity", value: fmt.num(scored.length ? scored.reduce((a, s) => a + s.eventsPer100 * s.km, 0) / (km || 1) : 0, 2), foot: `${fmt.num(km)} km · 30 ${esc(t("days", "ngày"))}` })}
        ${ui.kpi({ label: t("Events this week", "Sự kiện tuần này"), icon: "bell", value: lastWeek, foot: `<span class="${lastWeek <= prevWeek ? "trend-up" : "trend-down"}">${lastWeek <= prevWeek ? "▼" : "▲"} ${fmt.pct(prevWeek ? Math.abs(lastWeek - prevWeek) / prevWeek * 100 : 0)}</span> ${esc(t("vs last week", "so với tuần trước"))}` })}
        ${ui.kpi({ label: t("Cameras recording", "Camera đang ghi"), icon: "camera", value: `${cams.filter((c) => c.status === "OK").length}<small>/ ${cams.length}</small>`, tone: cams.some((c) => c.cause === "DELIBERATE") ? "danger" : undefined, foot: esc(t(`${cams.filter((c) => c.cause === "DELIBERATE").length} covered on purpose`, `${cams.filter((c) => c.cause === "DELIBERATE").length} bị cố ý che`)) })}
      </div>
      <div class="mt">${ui.tabs([["ranking", t("Ranking", "Xếp hạng")], ["events", t("Events", "Sự kiện")], ["cameras", t("Cameras", "Camera")]], tab)}</div>
      ${body}`;
    },
    mount(root, { query }) {
      bindQueryControls(root);
      const showAll = root.querySelector("[data-show-all]");
      if (showAll) showAll.addEventListener("click", () => G3.setQuery({ all: "1" }));
      on(root, "click", (e) => {
        const btn = e.target.closest("[data-action], [data-evidence]");
        if (!btn) return;
        if (btn.dataset.evidence) evidencePack(btn.dataset.evidence, btn.dataset.at);
        else if (btn.dataset.action === "safety-evidence") evidencePack(query.truck || null, null);
        else if (btn.dataset.action === "safety-weights") editWeights();
        else if (btn.dataset.action === "safety-export") {
          const rows = scope.filter(db.drivers).filter((d) => d.status === "ACTIVE").map((d) => ({ d, s: driverSafety(d) })).filter((x) => x.s).sort((a, b) => b.s.score - a.s.score);
          ui.auditedExport({
            dataType: "DRIVER_PROFILE", subject: t("Safety ranking", "Xếp hạng an toàn"), filename: "safety-ranking.csv", rows,
            columns: [{ label: "driver", csv: (x) => x.d.full_name }, { label: "km_30d", csv: (x) => x.s.km }, ...Object.keys(DEFAULT_WEIGHTS).map((k) => ({ label: `${k.toLowerCase()}_per_100km`, csv: (x) => (x.s.rates[k] === undefined ? "" : x.s.rates[k].toFixed(2)) })), { label: "score", csv: (x) => x.s.score }],
          });
        }
      });
    },
  });
})();
