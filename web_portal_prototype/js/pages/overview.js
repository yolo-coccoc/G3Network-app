/* Overview: the home dashboard.
   Customers see their fleet KPIs (FLT-06); internal staff see the network and
   company KPIs (PLT-06). */

(function () {
  const { ui, db, fmt, scope } = G3;
  const esc = G3.esc;

  function sumDaily(vehicles, days) {
    const rows = Array.from({ length: days }, () => ({ km: 0, kwh: 0, cost: 0 }));
    vehicles.filter((v) => v.live).forEach((v) => {
      db.dailyReport(v.vehicle_id, days).forEach((r, i) => { rows[i].km += r.distance_km; rows[i].kwh += r.energy_kwh; rows[i].cost += r.cost_vnd; });
    });
    return rows;
  }
  const dayLabels = (days) => Array.from({ length: days }, (_, i) => { const d = new Date(db.NOW - (days - 1 - i) * db.DAY); return `${d.getDate()}/${d.getMonth() + 1}`; });

  function stateCounts(vehicles) {
    const counts = { DRIVING: 0, PARKED: 0, CHARGING: 0, OFF: 0, OFFLINE: 0 };
    vehicles.filter((v) => v.live).forEach((v) => { counts[v.live.is_online ? v.live.state : "OFFLINE"]++; });
    return counts;
  }

  function alertsCard(limit = 6) {
    const rows = scope.byVehicle(db.notifications).filter((n) => n.severity !== "INFO").slice(0, limit);
    const body = rows.length ? `<ul class="list">${rows.map((n) => `<li class="clickable ${n.read_at ? "" : "unread"}" data-href="${n.vehicle_id ? `/vehicles/${n.vehicle_id}` : "/notifications"}"><div class="sev-icon ${n.severity}">${ui.icon(n.notification_type === "SOS_ALERT" ? "sos" : n.notification_type.includes("BATTERY") || n.notification_type.includes("SOH") ? "battery" : "alert", 16)}</div><div class="grow"><div class="title">${esc(t(n.title[0], n.title[1]))}</div><div class="meta">${G3.label(n.notification_type)} · ${fmt.ago(n.created_at)}</div></div>${ui.badge(n.severity)}</li>`).join("")}</ul>` : ui.empty(t("No open alerts", "Không có cảnh báo"));
    return ui.card({ title: t("Latest alerts", "Cảnh báo mới nhất"), actions: ui.btn(t("All", "Tất cả"), { href: "#/notifications", sm: true, kind: "ghost" }), body, flush: true });
  }

  function lowBatteryCard(vehicles) {
    const rows = vehicles.filter((v) => v.live && v.live.soc_percent <= 30 && v.live.state !== "CHARGING").sort((a, b) => a.live.soc_percent - b.live.soc_percent).slice(0, 6);
    return ui.card({
      title: t("Low battery", "Pin yếu"), sub: "MON-04", flush: true,
      body: ui.table({
        rows, href: (v) => `/vehicles/${v.vehicle_id}`, empty: t("Every truck is above 30%", "Mọi xe đều trên 30%"),
        columns: [
          { label: t("Truck", "Xe"), render: (v) => `<b>${esc(v.license_plate)}</b>` },
          { label: t("Battery", "Pin"), render: (v) => ui.soc(v.live.soc_percent) },
          { label: t("State", "Trạng thái"), render: (v) => ui.vehicleState(v) },
        ],
      }),
    });
  }

  function customerDashboard() {
    const vehicles = scope.vehicles().filter((v) => v.status === "ACTIVE");
    const counts = stateCounts(vehicles);
    const days = 14;
    const daily = sumDaily(vehicles, days);
    const today = daily[days - 1];
    const week = daily.slice(-7).reduce((a, r) => ({ km: a.km + r.km, kwh: a.kwh + r.kwh, cost: a.cost + r.cost }), { km: 0, kwh: 0, cost: 0 });
    const avgSoh = vehicles.length ? vehicles.reduce((a, v) => a + v.soh_percent, 0) / vehicles.length : 0;
    const online = vehicles.filter((v) => v.live && v.live.is_online).length;
    const utilization = vehicles.length ? ((counts.DRIVING + counts.CHARGING) / vehicles.length) * 100 : 0;
    const sessions = scope.byVehicle(db.charging_sessions).filter((s) => Date.now() - new Date(s.started_at).getTime() < 7 * db.DAY);
    const openAlerts = scope.byVehicle(db.notifications).filter((n) => !n.read_at && n.severity !== "INFO").length;
    const limit = scope.fleetLimit();

    return `
      ${ui.pageHead({
        title: t("Fleet overview", "Tổng quan đội xe"),
        sub: esc(limit ? t(`Limited to ${scope.membership().fleet_ids.map((id) => db.fleet(id).name).join(", ")} and the fleets below it (FLT-03).`, `Giới hạn trong ${scope.membership().fleet_ids.map((id) => db.fleet(id).name).join(", ")} và các đội con (FLT-03).`) : t(`${scope.org().name} · every truck of the organization`, `${scope.org().name} · toàn bộ xe của tổ chức`)),
        features: ["FLT-06", "MON-02", "MON-09", "MON-14"],
        actions: `${ui.segmented([["7", t("7 days", "7 ngày")], ["14", t("14 days", "14 ngày")], ["30", t("30 days", "30 ngày")]], "14", "period")}${ui.btn(t("Export", "Xuất"), { icon: "download", action: "export" })}`,
      })}
      <div class="grid cols-5">
        ${ui.kpi({ label: t("Trucks online", "Xe trực tuyến"), icon: "truck", value: `${online}<small>/ ${vehicles.length}</small>`, foot: `${counts.OFFLINE} ${t("offline", "mất kết nối")}` })}
        ${ui.kpi({ label: t("Distance today", "Quãng đường hôm nay"), icon: "route", value: fmt.num(today.km), unit: "km", foot: `${fmt.num(week.km)} km ${t("this week", "tuần này")}` })}
        ${ui.kpi({ label: t("Energy today", "Điện năng hôm nay"), icon: "bolt", value: fmt.num(today.kwh), unit: "kWh", foot: `${fmt.num(today.km ? today.kwh / today.km : 0, 2)} kWh/km` })}
        ${ui.kpi({ label: t("Cost per km (7 d)", "Chi phí/km (7 ngày)"), icon: "receipt", value: fmt.num(week.km ? week.cost / week.km : 0), unit: "₫/km", foot: `${fmt.vndShort(week.cost)} ${t("electricity", "tiền điện")}` })}
        ${ui.kpi({ label: t("Open alerts", "Cảnh báo chưa xử lý"), icon: "bell", value: openAlerts, tone: openAlerts ? "danger" : "ok", foot: `${t("Avg. battery health", "SOH trung bình")} ${fmt.pct(avgSoh, 1)}` })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Distance and energy per day", "Quãng đường và điện năng theo ngày"), sub: `${days} ${t("days", "ngày")}`,
          actions: ui.legend([[t("km", "km"), ui.PALETTE[1]], ["kWh", ui.PALETTE[0]]]),
          body: ui.barChart({ labels: dayLabels(days), series: [{ name: "km", values: daily.map((r) => r.km), color: ui.PALETTE[1] }, { name: "kWh", values: daily.map((r) => r.kwh), color: ui.PALETTE[0] }], height: 240 }),
        })}
        ${ui.card({
          title: t("Operating state now", "Trạng thái vận hành hiện tại"), sub: "MON-09",
          body: `${ui.donut({ segments: ["DRIVING", "PARKED", "CHARGING", "OFF", "OFFLINE"].map((s) => ({ label: G3.label(s), value: counts[s], color: ui.STATE_COLORS[s] === "#94a3b8" && s === "OFF" ? "#cbd5e1" : ui.STATE_COLORS[s] })), center: fmt.pct(utilization), centerSub: t("in use", "khai thác") })}
            <div class="divider"></div>${ui.btn(t("Open live map", "Mở bản đồ trực tuyến"), { href: "#/map", icon: "map" })}`,
        })}
      </div>
      <div class="grid cols-3 mt">
        ${alertsCard()}
        ${lowBatteryCard(vehicles)}
        ${ui.card({
          title: t("Charging this week", "Sạc trong tuần"), sub: "CHG-04",
          body: `${ui.kv([
            [t("Sessions", "Số phiên"), fmt.num(sessions.length)],
            [t("Energy", "Điện năng"), fmt.kwh(sessions.reduce((a, s) => a + s.energy_kwh, 0), 0)],
            [t("Cost", "Chi phí"), fmt.vnd(sessions.reduce((a, s) => a + (s.cost_vnd || 0), 0))],
            [t("Off-peak share", "Tỷ lệ giờ thấp điểm"), fmt.pct((sessions.filter((s) => { const h = new Date(s.started_at).getHours(); return h >= 22 || h < 4; }).length / (sessions.length || 1)) * 100)],
            [t("Policy compliance", "Tuân thủ chính sách"), `${fmt.num(scope.byVehicle(db.compliance).reduce((a, c, _, arr) => a + c.score / arr.length, 0), 0)} / 100`],
          ])}<div class="divider"></div>${ui.btn(t("Charging history", "Lịch sử sạc"), { href: "#/charging-sessions", icon: "plug" })}`,
        })}
      </div>`;
  }

  function internalDashboard() {
    const customers = scope.filter(db.organizations).filter((o) => o.kind === "CUSTOMER");
    const vehicles = scope.vehicles().filter((v) => v.organization_id !== "org-g3");
    const weeklyActive = vehicles.filter((v) => v.live && Date.now() - new Date(v.live.received_at).getTime() < 7 * db.DAY && v.status === "ACTIVE").length;
    const chargers = db.charging_stations.filter((s) => s.status === "ACTIVE");
    const chargersOnline = chargers.filter((s) => s.is_online).length;
    const sessions30 = scope.filter(db.charging_sessions).filter((s) => s.status === "COMPLETED");
    const kwh30 = sessions30.reduce((a, s) => a + s.energy_kwh, 0);
    const revenue30 = sessions30.reduce((a, s) => a + s.cost_vnd, 0);
    const subsRevenue = scope.filter(db.subscriptions).reduce((a, s) => a + s.agreed_price_vnd_truck_month * db.vehicles.filter((v) => v.organization_id === s.organization_id).length, 0);
    const openCases = scope.filter(db.support_cases).filter((c) => ["OPEN", "ACKNOWLEDGED"].includes(c.case_type === "SOS" ? c.status : c.status));
    const breached = openCases.filter((c) => new Date(c.sla_due_at).getTime() < Date.now());
    const devices = db.telematics.filter((d) => d.vehicle_id);
    const healthy = devices.filter((d) => d.health === "HEALTHY").length;
    const activated = db.vehicles.filter((v) => v.activation === "ACTIVATED").length;
    const compliance = scope.byVehicle(db.compliance);
    const avgCompliance = compliance.reduce((a, c) => a + c.score, 0) / (compliance.length || 1);

    // kWh sold per day, network-wide (STN-14)
    const days = 30;
    const perDay = Array.from({ length: days }, (_, i) => ({ date: new Date(db.NOW - (days - 1 - i) * db.DAY).toISOString().slice(0, 10), kwh: 0, vnd: 0 }));
    sessions30.forEach((s) => { const d = perDay.find((x) => x.date === s.started_at.slice(0, 10)); if (d) { d.kwh += s.energy_kwh; d.vnd += s.cost_vnd; } });
    const byLocation = db.charging_locations.map((l) => ({ name: l.name, kwh: sessions30.filter((s) => s.location_id === l.location_id).reduce((a, s) => a + s.energy_kwh, 0) })).sort((a, b) => b.kwh - a.kwh);

    return `
      ${ui.pageHead({
        title: t("Network overview", "Tổng quan mạng lưới"),
        sub: esc(scope.orgFilter() ? t(`Filtered to ${db.org(scope.orgFilter()).name}`, `Đang lọc theo ${db.org(scope.orgFilter()).name}`) : t("Every organization · internal view", "Mọi tổ chức · góc nhìn nội bộ")),
        features: ["PLT-06", "STN-14", "VEH-05", "DEV-04", "SUP-03"],
      })}
      <div class="grid cols-5">
        ${ui.kpi({ label: t("Weekly active vehicles", "Xe hoạt động trong tuần"), icon: "activity", value: weeklyActive, foot: `<span class="trend-up">▲ 8%</span> North Star · ${t("target", "mục tiêu")} 300` })}
        ${ui.kpi({ label: t("Customers", "Khách hàng"), icon: "building", value: customers.length, foot: `${vehicles.length} ${t("trucks", "xe")} · ${customers.filter((c) => c.status === "SUSPENDED").length} ${t("suspended", "tạm ngưng")}` })}
        ${ui.kpi({ label: t("Chargers online", "Trụ trực tuyến"), icon: "station", value: `${chargersOnline}<small>/ ${chargers.length}</small>`, tone: chargersOnline < chargers.length ? "warn" : "ok", foot: `${db.charging_locations.filter((l) => l.status === "ACTIVE").length} ${t("active stations", "trạm hoạt động")}` })}
        ${ui.kpi({ label: t("Energy sold (30 d)", "Điện bán (30 ngày)"), icon: "bolt", value: fmt.num(kwh30 / 1000, 1), unit: "MWh", foot: fmt.vndShort(revenue30) })}
        ${ui.kpi({ label: t("Subscription MRR", "Doanh thu thuê bao/tháng"), icon: "receipt", value: fmt.vndShort(subsRevenue), foot: `${scope.filter(db.subscriptions).filter((s) => s.status === "OVERDUE").length} ${t("overdue", "quá hạn")}` })}
      </div>
      <div class="grid cols-4 mt">
        ${ui.kpi({ label: t("Activation rate", "Tỷ lệ kích hoạt"), icon: "check", value: fmt.pct((activated / db.vehicles.length) * 100), foot: `${db.vehicles.length - activated} ${t("trucks waiting", "xe đang chờ")} · VEH-05` })}
        ${ui.kpi({ label: t("Healthy devices", "Thiết bị ổn định"), icon: "chip", value: fmt.pct((healthy / devices.length) * 100), foot: `${devices.filter((d) => d.health === "SILENT").length} ${t("silent", "im lặng")} · DEV-04` })}
        ${ui.kpi({ label: t("Charging compliance", "Tuân thủ sạc"), icon: "shield", value: fmt.num(avgCompliance, 0), unit: "/100", foot: `${compliance.filter((c) => c.risk === "HIGH").length} ${t("high-risk trucks", "xe rủi ro cao")} · POL-04` })}
        ${ui.kpi({ label: t("Open tickets", "Yêu cầu đang mở"), icon: "lifebuoy", value: openCases.length, tone: breached.length ? "danger" : undefined, foot: `${breached.length} ${t("past SLA", "quá SLA")} · CSAT 4,6/5` })}
      </div>
      <div class="grid side mt">
        ${ui.card({ title: t("Energy sold per day", "Điện bán theo ngày"), sub: "kWh", body: ui.lineChart({ labels: perDay.map((d) => d.date.slice(8, 10) + "/" + d.date.slice(5, 7)), series: [{ name: "kWh", values: perDay.map((d) => Math.round(d.kwh)), area: true }], height: 240 }) })}
        ${ui.card({ title: t("Stations by energy (30 d)", "Trạm theo điện năng (30 ngày)"), body: ui.hBars({ labels: byLocation.map((l) => l.name), series: [{ values: byLocation.map((l) => Math.round(l.kwh)) }], unit: " kWh" }) })}
      </div>
      <div class="grid cols-2 mt">
        ${alertsCard(7)}
        ${ui.card({
          title: t("Customers", "Khách hàng"), flush: true, actions: G3.canSee("organizations") ? ui.btn(t("All", "Tất cả"), { href: "#/organizations", sm: true, kind: "ghost" }) : "",
          body: ui.table({
            rows: customers, href: G3.canSee("organizations") ? (o) => `/organizations/${o.organization_id}` : null,
            columns: [
              { label: t("Organization", "Tổ chức"), render: (o) => `<b>${esc(o.short_name)}</b><span class="sub">${G3.label(o.legal_form)}</span>` },
              { label: t("Trucks", "Xe"), align: "right", render: (o) => db.vehicles.filter((v) => v.organization_id === o.organization_id).length },
              { label: t("Plan", "Gói"), render: (o) => { const s = db.subscriptions.find((x) => x.organization_id === o.organization_id); return s ? esc(db.find("plans", s.plan_id).name) : "—"; } },
              { label: t("Status", "Trạng thái"), render: (o) => ui.badge(o.status) },
            ],
          }),
        })}
      </div>`;
  }

  G3.page({
    id: "dashboard",
    path: "/",
    title: ["Overview", "Tổng quan"],
    roles: "*",
    features: ["FLT-06", "PLT-06"],
    nav: { group: "overview", order: 1, icon: "home" },
    render: () => (scope.isInternal() ? internalDashboard() : customerDashboard()),
    mount(root) {
      root.querySelectorAll("[data-seg=period] button").forEach((b) => b.addEventListener("click", () => ui.toast(t("Period filter is illustrative in the prototype", "Bộ lọc thời gian chỉ minh họa trong bản mẫu"), "", "info")));
      const exportBtn = root.querySelector("[data-action=export]");
      if (exportBtn) exportBtn.addEventListener("click", () => {
        const vehicles = scope.vehicles();
        ui.downloadCsv("fleet-kpi.csv", [{ label: "plate", key: "license_plate" }, { label: "vin", key: "vin" }, { label: "soc_percent", csv: (v) => v.live && v.live.soc_percent }, { label: "soh_percent", key: "soh_percent" }, { label: "odometer_km", csv: (v) => Math.round(v.odometer_km) }], vehicles);
      });
    },
  });
})();
