/* Live fleet map (FLT-04, MON-02, MON-09): every visible truck on a list and
   on the map, moving on the live tick; filter by state and fleet, search by
   plate or VIN; click a truck for its live card. */

(function () {
  const { ui, db, fmt, scope } = G3;
  const esc = G3.esc;

  function stateOf(v) {
    if (!v.live) return "NO_DATA";
    return v.live.is_online ? v.live.state : "OFFLINE";
  }

  function filtered(query) {
    const q = (query.q || "").toLowerCase();
    return scope.vehicles().filter((v) => v.live).filter((v) => {
      if (query.state && stateOf(v) !== query.state) return false;
      if (query.fleet && !db.fleetAndDescendants(query.fleet).includes(v.fleet_id)) return false;
      if (q && !(v.license_plate.toLowerCase().includes(q) || v.vin.toLowerCase().includes(q))) return false;
      return true;
    });
  }

  function listItem(v, selected) {
    const driver = db.driver(v.driver_id);
    return `<li class="clickable ${selected ? "unread" : ""}" data-select="${v.vehicle_id}">
      <span class="dot-status" style="background:${ui.STATE_COLORS[stateOf(v)] || "#94a3b8"};margin-top:6px"></span>
      <div class="grow"><div class="row between"><span class="title">${esc(v.license_plate)}</span><span data-live-soc="${v.vehicle_id}">${ui.soc(v.live.soc_percent)}</span></div>
      <div class="meta">${G3.label(stateOf(v))}${v.live.state === "DRIVING" && v.live.is_online ? ` · <span data-live-speed="${v.vehicle_id}">${Math.round(v.live.speed_kmh)}</span> km/h` : ""} · ${driver ? esc(driver.full_name) : t("no driver", "chưa có tài xế")}</div></div></li>`;
  }

  function detailCard(v) {
    if (!v) return `<div class="card-body muted small">${esc(t("Click a truck on the map or in the list.", "Chọn một xe trên bản đồ hoặc danh sách."))}</div>`;
    const model = db.model(v.vehicle_model_id);
    const driver = db.driver(v.driver_id);
    const fleet = db.fleet(v.fleet_id);
    const nearest = db.charging_locations.filter((l) => l.status === "ACTIVE" && (l.is_public || (l.allowed_organization_ids || []).includes(v.organization_id)))
      .map((l) => ({ l, d: Math.hypot((l.lat - v.live.lat) * 111, (l.lon - v.live.lon) * 109) })).sort((a, b) => a.d - b.d)[0];
    return `<div class="card-head"><h3>${esc(v.license_plate)} <span class="sub">${esc(model.code)}</span></h3>${ui.vehicleState(v)}</div>
      <div class="card-body">
        ${!v.driver_id && v.live.state === "DRIVING" ? ui.callout("warn", esc(t("Driving with nobody checked in (DRV-02).", "Xe đang chạy nhưng chưa có tài xế nhận xe (DRV-02)."))) + '<div style="height:10px"></div>' : ""}
        ${ui.kv([
          [t("Battery", "Pin"), `<span data-live-soc="${v.vehicle_id}">${ui.soc(v.live.soc_percent)}</span>`],
          [t("Speed", "Tốc độ"), `<span data-live-speed="${v.vehicle_id}">${Math.round(v.live.speed_kmh)}</span> km/h`],
          [t("Driver", "Tài xế"), driver ? `<a href="#/drivers/${driver.driver_id}">${esc(driver.full_name)}</a>` : ui.driverName(null)],
          [t("Fleet", "Đội xe"), fleet ? esc(fleet.name) : "—"],
          [t("Road", "Tuyến đường"), esc(v.live.road)],
          [t("Battery temp.", "Nhiệt độ pin"), `${v.live.battery_temperature_celsius} °C`],
          [t("Last data", "Dữ liệu cuối"), `<span data-live-ago="${v.vehicle_id}">${fmt.ago(v.live.received_at)}</span>`],
          nearest ? [t("Nearest station", "Trạm gần nhất"), `${esc(nearest.l.name)} · ${fmt.num(nearest.d, 1)} km`] : null,
        ])}
        <div class="row mt">${ui.btn(t("Vehicle detail", "Chi tiết xe"), { href: `#/vehicles/${v.vehicle_id}`, kind: "primary", sm: true })}${ui.btn(t("Replay route", "Xem lại lộ trình"), { href: `#/vehicles/${v.vehicle_id}?tab=route`, icon: "route", sm: true })}</div>
      </div>`;
  }

  G3.page({
    id: "live-map",
    path: "/map",
    title: ["Live fleet map", "Bản đồ đội xe"],
    roles: ["FLEET_MANAGER", "DISPATCHER", "CUSTOMER_CARE", "OPERATIONS", "ORG_ADMIN", "CO_ADMIN"],
    features: ["FLT-04", "MON-02", "MON-09", "FLT-05"],
    nav: { group: "monitoring", order: 1, icon: "map" },
    flush: true,
    render({ query, entering }) {
      const vehicles = filtered(query);
      const all = scope.vehicles().filter((v) => v.live);
      const selected = db.vehicle(query.v) || null;
      const counts = {};
      all.forEach((v) => { counts[stateOf(v)] = (counts[stateOf(v)] || 0) + 1; });
      const fleets = scope.filter(db.fleets);
      const geofences = G3.prefs.mapGeofences === false ? [] : scope.filter(db.geofences);
      const locations = db.charging_locations.filter((l) => l.is_public || scope.orgIds().some((id) => (l.allowed_organization_ids || []).includes(id)) || scope.isInternal());
      if (entering) G3.audit("VIEW", "LIVE_POSITION", null, null);
      return `<div style="display:grid;grid-template-columns:340px 1fr;height:100%">
        <div style="border-right:1px solid var(--border);background:var(--surface);display:flex;flex-direction:column;min-height:0">
          <div style="padding:16px 16px 10px"><h2>${esc(t("Live fleet map", "Bản đồ đội xe"))}</h2>${ui.featureChips(["FLT-04", "MON-02", "MON-09"])}</div>
          <div style="padding:0 16px 10px" class="stack" >
            ${ui.search("q", query.q, t("Plate or VIN", "Biển số hoặc VIN"))}
            <div class="row">${ui.select("fleet", [["", t("All fleets", "Mọi đội xe")], ...fleets.map((f) => [f.fleet_id, (f.parent_fleet_id ? "— " : "") + f.name])], query.fleet || "")}</div>
            <div class="row" style="gap:6px">${[["", t("All", "Tất cả"), all.length], ...["DRIVING", "PARKED", "CHARGING", "OFF", "OFFLINE"].map((s) => [s, G3.label(s), counts[s] || 0])].map(([s, label, n]) => `<button class="btn sm ${String(query.state || "") === s ? "primary" : ""}" data-state="${s}">${esc(label)} <span class="pill-count" style="background:transparent">${n}</span></button>`).join("")}</div>
          </div>
          <ul class="list" style="overflow-y:auto;flex:1;border-top:1px solid var(--border)">${vehicles.map((v) => listItem(v, selected && selected.vehicle_id === v.vehicle_id)).join("") || `<li>${ui.empty()}</li>`}</ul>
        </div>
        <div style="position:relative;min-width:0">
          ${ui.map({ vehicles, locations, geofences, selectedVehicleId: selected && selected.vehicle_id, height: "100%" })}
          <div class="card" style="position:absolute;top:12px;left:12px;width:320px;max-width:calc(100% - 80px)" data-detail>${detailCard(selected && selected.live ? selected : null)}</div>
          <label class="check small card" style="position:absolute;top:12px;right:56px;padding:6px 10px"><input type="checkbox" data-toggle-geofences ${G3.prefs.mapGeofences === false ? "" : "checked"}> ${esc(t("Geofences", "Vùng địa lý"))}</label>
        </div></div>`;
    },
    mount(root, { query }) {
      root.querySelector("[data-filter=q]").addEventListener("change", (e) => G3.setQuery({ q: e.target.value }));
      root.querySelector("[data-filter=fleet]").addEventListener("change", (e) => G3.setQuery({ fleet: e.target.value }));
      root.querySelectorAll("[data-state]").forEach((b) => b.addEventListener("click", () => G3.setQuery({ state: b.dataset.state })));
      root.querySelectorAll("[data-select]").forEach((li) => li.addEventListener("click", () => G3.setQuery({ v: li.dataset.select })));
      root.querySelector("[data-toggle-geofences]").addEventListener("change", (e) => { G3.prefs.mapGeofences = e.target.checked; G3.render(); });
      const wrap = root.querySelector("[data-map-wrap]");
      root.querySelectorAll("[data-vehicle]").forEach((g) => g.addEventListener("click", () => { if (!wrap.dataset.justDragged) G3.setQuery({ v: g.dataset.vehicle }); }));
      root.querySelectorAll("[data-location]").forEach((g) => g.addEventListener("click", () => { if (!wrap.dataset.justDragged && G3.canSee("station-detail")) G3.navigate(`/stations/${g.dataset.location}`); }));
      const selected = db.vehicle(query.v);
      if (selected && selected.live && query.zoom !== "0") setTimeout(() => wrap.zoomTo && wrap.zoomTo(selected.live.lat, selected.live.lon, 420), 0);
      G3.onTick(() => {
        ui.refreshMarkers(root);
        root.querySelectorAll("[data-live-soc]").forEach((el) => { const v = db.vehicle(el.dataset.liveSoc); el.innerHTML = ui.soc(v.live.soc_percent); });
        root.querySelectorAll("[data-live-speed]").forEach((el) => { el.textContent = Math.round(db.vehicle(el.dataset.liveSpeed).live.speed_kmh); });
        root.querySelectorAll("[data-live-ago]").forEach((el) => { el.textContent = fmt.ago(db.vehicle(el.dataset.liveAgo).live.received_at); });
      });
    },
  });
})();
