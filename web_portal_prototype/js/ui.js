/* UI kit shared by every screen: icons, badges, cards, tables, charts, the
   SVG map, modals and toasts. Components return HTML strings; behaviour is
   bound by the page's mount() (or by the global handlers at the end). */

(function () {
  const esc = G3.esc;
  const ui = {};

  /* ---------- Icons (24x24 stroke paths, lucide-like) ---------- */
  const ICONS = {
    home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9.5 21v-6h5v6"/>',
    map: '<path d="m9 4-6 2.5v13.5l6-2.5 6 2.5 6-2.5V4l-6 2.5z"/><path d="M9 4v13.5M15 6.5V20"/>',
    truck: '<path d="M2 6h11v10H2z"/><path d="M13 9h4.5l3.5 3.5V16h-8"/><circle cx="6" cy="17.5" r="2"/><circle cx="17" cy="17.5" r="2"/>',
    battery: '<rect x="2" y="7" width="17" height="10" rx="2"/><path d="M22 11v2"/><path d="M6 10v4M9.5 10v4"/>',
    bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    plug: '<path d="M9 2v5M15 2v5"/><path d="M6 7h12v4a6 6 0 0 1-12 0z"/><path d="M12 17v5"/>',
    bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8"/><path d="M10.3 20a2 2 0 0 0 3.4 0"/>',
    users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M21.5 20a6.5 6.5 0 0 0-4-6"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    id: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="11" r="2.5"/><path d="M5.5 17a3.5 3.5 0 0 1 7 0M15 9h3M15 13h3"/>',
    layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
    shape: '<path d="M4 7 12 3l8 5-2 10-9 3-5-7z"/>',
    chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
    shield: '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
    wrench: '<path d="M14.5 6.5a4 4 0 0 0 5 5L21 13l-8 8-3-3 8-8z" transform="rotate(0)"/><path d="M14.5 6.5 10 2 7 5l4.5 4.5"/><path d="M3 21l6-6"/>',
    station: '<path d="M4 21V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v16"/><path d="M3 21h12"/><path d="M14 9h2a2 2 0 0 1 2 2v5a1.5 1.5 0 0 0 3 0V8l-3-3"/><path d="m9.5 7-2 4h3l-2 4"/>',
    receipt: '<path d="M5 3h14v18l-3-2-2 2-2-2-2 2-2-2-3 2z"/><path d="M9 8h6M9 12h6M9 16h3"/>',
    tag: '<path d="M3 12V3h9l9 9-9 9z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
    gauge: '<path d="M12 14 16 9"/><path d="M3.5 18a9 9 0 1 1 17 0"/><circle cx="12" cy="14" r="1.5"/>',
    lifebuoy: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><path d="m5.6 5.6 3.6 3.6M14.8 14.8l3.6 3.6M18.4 5.6l-3.6 3.6M9.2 14.8l-3.6 3.6"/>',
    handshake: '<path d="m11 17 2 2a1.5 1.5 0 0 0 2-2"/><path d="m14 14 2.5 2.5a1.5 1.5 0 0 0 2-2L15 11"/><path d="M2 12l4-4 4 1 3-2 3 1 6 5"/><path d="m2 12 7 7"/>',
    card: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20M6 15h4"/>',
    wallet: '<path d="M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0 0 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5"/><circle cx="16.5" cy="13.5" r="1.3"/>',
    file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
    leaf: '<path d="M5 21c0-9 5-15 16-16-1 11-7 16-16 16"/><path d="M5 21 13 13"/>',
    building: '<rect x="4" y="3" width="16" height="18" rx="1"/><path d="M9 7h1M14 7h1M9 11h1M14 11h1M9 15h1M14 15h1M10 21v-3h4v3"/>',
    key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.7 12.3 9.8-9.8M17 6l3 3M14.5 8.5l2 2"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1"/><circle cx="3.5" cy="12" r="1"/><circle cx="3.5" cy="18" r="1"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.6 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1"/>',
    activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    chart: '<path d="M3 3v18h18"/><path d="M7 15v3M11 10v8M15 12v6M19 6v12"/>',
    route: '<circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="5" r="2.5"/><path d="M8.5 19H16a3.5 3.5 0 0 0 0-7H8a3.5 3.5 0 0 1 0-7h7.5"/>',
    camera: '<path d="M23 7 16 12l7 5z"/><rect x="1" y="5" width="15" height="14" rx="2"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    chevron: '<path d="m9 18 6-6-6-6"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    download: '<path d="M12 3v12M7 10l5 5 5-5M4 21h16"/>',
    upload: '<path d="M12 21V9M7 14l5-5 5 5M4 3h16"/>',
    edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4z"/>',
    trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15"/>',
    lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    unlock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.5-2"/>',
    alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    phone: '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
    power: '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.8 0"/>',
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    sos: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5h.01"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    thermometer: '<path d="M14 14.8V4a2 2 0 1 0-4 0v10.8a4 4 0 1 0 4 0z"/>',
    signal: '<path d="M2 20h.01M7 20v-4M12 20v-8M17 20V8M22 4v16"/>',
    send: '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
    qr: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><path d="M14 14h3v3h-3zM20 14v3M14 20h7"/>',
    menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
    filter: '<path d="M22 3H2l8 9.5V19l4 2v-8.5z"/>',
  };
  ui.icon = function (name, size = 18, extra = "") {
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" ${extra}>${ICONS[name] || ICONS.info}</svg>`;
  };

  /* ---------- Status badges ---------- */
  const TONES = {
    ok: ["ACTIVE", "ACTIVATED", "HEALTHY", "Available", "COMPLETED", "PAID", "BILLED", "ACCEPTED", "RESOLVED", "LOW", "LOADED", "SUCCEEDED", "OK"],
    info: ["DRIVING", "IN_PROGRESS", "INFO", "ACKNOWLEDGED", "ISSUED", "QUOTED", "Preparing", "Finishing", "PLANNED", "INVITED"],
    violet: ["CHARGING", "Charging", "SuspendedEV", "SuspendedEVSE", "Occupied", "Reserved"],
    accent: ["PARKED"],
    warn: ["WARNING", "ATTENTION", "AWAITING_DATA", "PENDING", "ON_HOLD", "SUSPENDED", "MEDIUM", "OPEN", "TIMEOUT", "OVERDUE", "NOT_SENT"],
    danger: ["CRITICAL", "Faulted", "SILENT", "LOCKED", "BLOCKED", "VOIDED", "REJECTED", "ERROR", "HIGH", "SOS", "OFFLINE", "FAILED", "EXPIRED"],
    muted: ["INACTIVE", "OFF", "CLOSED", "CANCELLED", "ABANDONED", "VOID", "NO_DEVICE", "NOT_MOUNTED", "NO_DATA", "Unknown", "Unavailable", "DRAFT", "EMPTY"],
  };
  ui.tone = function (value) {
    for (const [tone, values] of Object.entries(TONES)) if (values.includes(value)) return tone;
    return "muted";
  };
  /** Badge for an enum value, labelled in the current language. */
  ui.badge = function (value, tone, label) {
    if (value === null || value === undefined) return '<span class="muted">—</span>';
    return `<span class="badge ${tone || ui.tone(value)}">${esc(label || G3.label(value))}</span>`;
  };
  ui.tag = (text) => `<span class="tag">${esc(text)}</span>`;

  /* ---------- Feature chips (catalog status) ---------- */
  ui.featureChips = function (codes) {
    if (!codes || !codes.length || !G3.prefs.showFeatureCodes) return "";
    const features = (window.G3_FEATURES && window.G3_FEATURES.features) || [];
    return `<div class="feature-chips">${codes.map((code) => {
      const f = features.find((x) => x.code === code);
      const be = f ? f.backend : "todo";
      const title = f ? `${code} — ${t(f.name, f.name_vi)}\n${t("Backend", "Backend")}: ${be} · ${t("Portal", "Portal")}: ${f.portal} · ${f.priority} ${f.release}` : code;
      return `<span class="fchip" title="${esc(title)}"><i class="${be}"></i>${esc(code)}</span>`;
    }).join("")}</div>`;
  };

  /* ---------- Layout pieces ---------- */
  ui.pageHead = function ({ title, sub, crumbs, features, actions }) {
    const crumbHtml = crumbs ? `<div class="crumbs">${crumbs.map((c) => (c.href ? `<a href="${c.href}">${esc(c.label)}</a>` : esc(c.label))).join(" / ")}</div>` : "";
    return `<div class="page-head"><div class="titles">${crumbHtml}<h1>${esc(title)}</h1>${sub ? `<p>${sub}</p>` : ""}${ui.featureChips(features)}</div>${actions ? `<div class="actions">${actions}</div>` : ""}</div>`;
  };
  ui.card = function ({ title, sub, actions, body, flush, id, cls }) {
    const head = title ? `<div class="card-head"><h3>${esc(title)}${sub ? ` <span class="sub">${sub}</span>` : ""}</h3>${actions || ""}</div>` : "";
    return `<section class="card ${cls || ""}" ${id ? `id="${id}"` : ""}>${head}<div class="card-body ${flush ? "flush" : ""}">${body}</div></section>`;
  };
  ui.kpi = function ({ label, value, unit, foot, icon, tone }) {
    return `<div class="card kpi"><div class="label">${icon ? ui.icon(icon, 15) : ""}${esc(label)}</div><div class="value" ${tone ? `style="color:var(--${tone})"` : ""}>${value}${unit ? `<small>${esc(unit)}</small>` : ""}</div>${foot ? `<div class="foot">${foot}</div>` : ""}</div>`;
  };
  ui.kv = function (pairs) {
    return `<dl class="kv">${pairs.filter(Boolean).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v === null || v === undefined || v === "" ? '<span class="muted">—</span>' : v}</dd>`).join("")}</dl>`;
  };
  ui.callout = function (kind, html, icon) {
    return `<div class="callout ${kind}">${ui.icon(icon || (kind === "ok" ? "check" : kind === "info" ? "info" : "alert"), 17)}<div>${html}</div></div>`;
  };
  ui.empty = (text) => `<div class="empty">${esc(text || t("Nothing to show", "Không có dữ liệu"))}</div>`;
  ui.btn = function (label, { icon, kind, action, href, attrs, sm, disabled } = {}) {
    const cls = `btn ${kind || ""} ${sm ? "sm" : ""}`;
    const inner = `${icon ? ui.icon(icon, sm ? 14 : 16) : ""}${label ? esc(label) : ""}`;
    if (href) return `<a class="${cls}" href="${href}" ${attrs || ""}>${inner}</a>`;
    return `<button class="${cls}" ${action ? `data-action="${action}"` : ""} ${attrs || ""} ${disabled ? "disabled" : ""}>${inner}</button>`;
  };
  ui.tabs = function (items, active, name = "tab") {
    return `<div class="tabs" data-tabs="${name}">${items.map(([key, label]) => `<button data-tab="${key}" class="${key === active ? "on" : ""}">${esc(label)}</button>`).join("")}</div>`;
  };
  ui.segmented = function (options, value, name) {
    return `<div class="segmented" data-seg="${name}">${options.map(([key, label]) => `<button data-value="${key}" class="${key === value ? "on" : ""}">${esc(label)}</button>`).join("")}</div>`;
  };
  ui.select = function (name, options, value, attrs = "") {
    return `<select class="input" name="${name}" data-filter="${name}" ${attrs}>${options.map(([k, label]) => `<option value="${esc(k)}" ${String(k) === String(value) ? "selected" : ""}>${esc(label)}</option>`).join("")}</select>`;
  };
  ui.search = function (name, value, placeholder) {
    return `<input class="input search-input" type="search" data-filter="${name}" name="${name}" value="${esc(value || "")}" placeholder="${esc(placeholder || t("Search…", "Tìm kiếm…"))}">`;
  };

  /* ---------- Meters ---------- */
  ui.meter = function (percent, tone) {
    const p = Math.max(0, Math.min(100, percent || 0));
    return `<div class="meter ${tone || ""}"><span style="width:${p}%"></span></div>`;
  };
  ui.soc = function (percent) {
    if (percent === null || percent === undefined) return '<span class="muted">—</span>';
    const tone = percent <= 10 ? "danger" : percent <= 20 ? "warn" : "";
    return `<span class="soc">${ui.meter(percent, tone)}<b>${Math.round(percent)}%</b></span>`;
  };

  /* ---------- Tables ---------- */
  /**
   * columns: [{ key, label, render(row) -> html, align: "right", cls }]
   * rows: records; href(row) makes a row clickable (navigates).
   */
  ui.table = function ({ columns, rows, href, empty, rowAttrs, selected }) {
    if (!rows.length) return ui.empty(empty);
    const head = columns.map((c) => `<th class="${c.align === "right" ? "right" : ""}">${esc(c.label)}</th>`).join("");
    const body = rows.map((row) => {
      const link = href ? href(row) : null;
      const attrs = rowAttrs ? rowAttrs(row) : "";
      return `<tr class="${link ? "clickable" : ""} ${selected && selected(row) ? "selected" : ""}" ${link ? `data-href="${link}"` : ""} ${attrs}>${columns.map((c) => `<td class="${c.align === "right" ? "right num" : ""} ${c.cls || ""}">${c.render ? c.render(row) : esc(row[c.key] ?? "—")}</td>`).join("")}</tr>`;
    }).join("");
    return `<div class="table-wrap"><table class="tbl"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  };

  /* ---------- Domain cells ---------- */
  ui.vehicleCell = function (vehicle) {
    if (!vehicle) return '<span class="muted">—</span>';
    return `<b>${esc(vehicle.license_plate)}</b><span class="sub mono">${esc(vehicle.vin)}</span>`;
  };
  ui.orgName = function (organizationId) {
    const org = G3.db.org(organizationId);
    return org ? esc(org.short_name) : "—";
  };
  ui.userName = function (userId) {
    const user = G3.db.user(userId);
    return user ? esc(user.full_name) : `<span class="muted">${t("System", "Hệ thống")}</span>`;
  };
  ui.driverName = function (driverId) {
    const driver = G3.db.driver(driverId);
    return driver ? esc(driver.full_name) : `<span class="muted">${t("Nobody checked in", "Chưa có tài xế")}</span>`;
  };
  /** Operating state shown for a truck: OFFLINE wins over the last reported state. */
  ui.vehicleState = function (vehicle) {
    if (!vehicle.live) return ui.badge("NO_DATA");
    if (!vehicle.live.is_online) return ui.badge("OFFLINE");
    return ui.badge(vehicle.live.state);
  };
  ui.initials = (name) => name.split(" ").filter(Boolean).slice(-2).map((p) => p[0]).join("").toUpperCase();

  /* ---------- Charts (inline SVG) ---------- */
  ui.PALETTE = ["#0e9f6e", "#2563eb", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d"];

  function niceMax(value) {
    if (value <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(value)));
    const n = value / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }
  function axisFmt(v) {
    if (Math.abs(v) >= 1e9) return G3.fmt.num(v / 1e9, Math.abs(v) >= 1e10 ? 0 : 1) + t("B", " tỷ");
    if (Math.abs(v) >= 1e6) return G3.fmt.num(v / 1e6, Math.abs(v) >= 1e7 ? 0 : 1) + t("M", "tr");
    if (Math.abs(v) >= 1e4) return G3.fmt.num(v / 1e3, 0) + "k";
    return G3.fmt.num(v, v % 1 ? 1 : 0);
  }

  /**
   * Line or area chart.
   * series: [{ name, values: number[], color?, area?, dashed? }], labels: string[]
   */
  ui.lineChart = function ({ series, labels, height = 220, yMin, yMax, yFormat, unit = "" }) {
    const W = 640, H = height, L = 52, R = 12, T = 12, B = 26;
    const all = series.flatMap((s) => s.values.filter((v) => v !== null && v !== undefined));
    const lo = yMin !== undefined ? yMin : Math.min(0, ...all);
    const hi = yMax !== undefined ? yMax : niceMax(Math.max(...all, 1));
    const n = labels.length;
    const x = (i) => L + (n <= 1 ? 0 : (i / (n - 1)) * (W - L - R));
    const y = (v) => T + (1 - (v - lo) / (hi - lo || 1)) * (H - T - B);
    const fmt = yFormat || axisFmt;
    let out = `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px">`;
    for (let k = 0; k <= 4; k++) {
      const v = lo + ((hi - lo) * k) / 4;
      out += `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${esc(fmt(v))}</text>`;
    }
    const step = Math.max(1, Math.ceil(n / 8));
    labels.forEach((label, i) => { if ((i % step === 0 && n - 1 - i >= step / 2) || i === n - 1) out += `<text x="${x(i)}" y="${H - 7}" text-anchor="${i === n - 1 && n > 1 ? "end" : i === 0 ? "start" : "middle"}">${esc(label)}</text>`; });
    series.forEach((s, si) => {
      const color = s.color || ui.PALETTE[si % ui.PALETTE.length];
      const pts = s.values.map((v, i) => (v === null || v === undefined ? null : [x(i), y(v)])).filter(Boolean);
      if (!pts.length) return;
      const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
      if (s.area) out += `<path d="${d}L${pts[pts.length - 1][0]},${y(lo)}L${pts[0][0]},${y(lo)}Z" fill="${color}" opacity="0.12"/>`;
      out += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.2" ${s.dashed ? 'stroke-dasharray="5 4"' : ""} stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
    });
    // hover targets
    const colW = (W - L - R) / Math.max(1, n - 1);
    labels.forEach((label, i) => {
      const tip = `${label}: ` + series.map((s) => `${s.name} ${s.values[i] === null || s.values[i] === undefined ? "—" : fmt(s.values[i])}${unit}`).join(" · ");
      out += `<rect x="${x(i) - colW / 2}" y="${T}" width="${colW}" height="${H - T - B}" fill="transparent" data-tip="${esc(tip)}"/>`;
    });
    return out + "</svg>";
  };

  /**
   * Bar chart (grouped or stacked).
   * series: [{ name, values, color? }], labels: string[]
   */
  ui.barChart = function ({ series, labels, height = 220, stacked = false, yFormat, unit = "", horizontal = false }) {
    if (horizontal) return ui.hBars({ series, labels, unit, yFormat });
    const W = 640, H = height, L = 52, R = 12, T = 12, B = 26;
    const n = labels.length;
    const totals = labels.map((_, i) => (stacked ? series.reduce((a, s) => a + (s.values[i] || 0), 0) : Math.max(...series.map((s) => s.values[i] || 0))));
    const top = Math.max(...totals, 1);
    // Small whole-number counts get whole-number ticks (4 gridlines).
    const counts = series.every((s) => s.values.every((v) => Number.isInteger(v || 0)));
    const hi = counts && top <= 8 ? (top <= 4 ? 4 : 8) : niceMax(top);
    const fmt = yFormat || axisFmt;
    const y = (v) => T + (1 - v / hi) * (H - T - B);
    const band = (W - L - R) / n;
    const groupW = band * 0.68;
    let out = `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px">`;
    for (let k = 0; k <= 4; k++) {
      const v = (hi * k) / 4;
      out += `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${esc(fmt(v))}</text>`;
    }
    const step = Math.max(1, Math.ceil(n / 12));
    labels.forEach((label, i) => {
      const x0 = L + band * i + (band - groupW) / 2;
      let acc = 0;
      series.forEach((s, si) => {
        const color = s.color || ui.PALETTE[si % ui.PALETTE.length];
        const v = s.values[i] || 0;
        const tip = `${label} · ${s.name}: ${fmt(v)}${unit}`;
        if (stacked) {
          out += `<rect x="${x0}" y="${y(acc + v)}" width="${groupW}" height="${Math.max(0, y(acc) - y(acc + v))}" fill="${color}" data-tip="${esc(tip)}"/>`;
          acc += v;
        } else {
          const w = groupW / series.length;
          out += `<rect x="${x0 + w * si}" y="${y(v)}" width="${Math.max(1, w - 2)}" height="${Math.max(0, y(0) - y(v))}" rx="2" fill="${color}" data-tip="${esc(tip)}"/>`;
        }
      });
      if (i % step === 0) out += `<text x="${L + band * i + band / 2}" y="${H - 7}" text-anchor="middle">${esc(label)}</text>`;
    });
    return out + "</svg>";
  };

  /** Horizontal bars, one row per label (rankings). */
  ui.hBars = function ({ series, labels, unit = "", yFormat }) {
    const values = series[0].values;
    const max = Math.max(...values, 1);
    const color = series[0].color || ui.PALETTE[0];
    const fmt = yFormat || ((v) => G3.fmt.num(v));
    return `<div class="stack" style="gap:9px">${labels.map((label, i) => `<div><div class="row between small"><span>${esc(label)}</span><b class="num">${esc(fmt(values[i]))}${esc(unit)}</b></div><div class="meter" style="height:8px"><span style="width:${(values[i] / max) * 100}%;background:${color}"></span></div></div>`).join("")}</div>`;
  };

  /** Donut with a legend. segments: [{ label, value, color? }] */
  ui.donut = function ({ segments, size = 150, center, centerSub }) {
    const total = segments.reduce((a, s) => a + s.value, 0) || 1;
    const r = 42, c = 2 * Math.PI * r;
    let offset = 0;
    let arcs = "";
    segments.forEach((s, i) => {
      const len = (s.value / total) * c;
      const color = s.color || ui.PALETTE[i % ui.PALETTE.length];
      arcs += `<circle r="${r}" cx="60" cy="60" fill="none" stroke="${color}" stroke-width="16" stroke-dasharray="${len} ${c - len}" stroke-dashoffset="${-offset}" transform="rotate(-90 60 60)" data-tip="${esc(`${s.label}: ${G3.fmt.num(s.value)} (${G3.fmt.pct((s.value / total) * 100)})`)}"/>`;
      offset += len;
    });
    const legend = `<div class="legend" style="flex-direction:column;gap:6px">${segments.map((s, i) => `<span><i style="background:${s.color || ui.PALETTE[i % ui.PALETTE.length]}"></i>${esc(s.label)} <b class="num">${G3.fmt.num(s.value)}</b></span>`).join("")}</div>`;
    return `<div class="row" style="gap:20px;flex-wrap:nowrap"><svg width="${size}" height="${size}" viewBox="0 0 120 120"><circle r="${r}" cx="60" cy="60" fill="none" stroke="var(--surface-3)" stroke-width="16"/>${arcs}${center ? `<text x="60" y="60" text-anchor="middle" font-size="18" font-weight="700" fill="var(--text)">${esc(center)}</text><text x="60" y="76" text-anchor="middle" font-size="8" fill="var(--text-3)">${esc(centerSub || "")}</text>` : ""}</svg>${legend}</div>`;
  };

  ui.sparkline = function (values, color = "var(--primary)", width = 90, height = 26) {
    if (!values.length) return "";
    const lo = Math.min(...values), hi = Math.max(...values);
    const pts = values.map((v, i) => `${((i / (values.length - 1 || 1)) * width).toFixed(1)},${(height - 2 - ((v - lo) / (hi - lo || 1)) * (height - 4)).toFixed(1)}`).join(" ");
    return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round"/></svg>`;
  };

  ui.legend = function (items) {
    return `<div class="legend">${items.map(([label, color], i) => `<span><i style="background:${color || ui.PALETTE[i]}"></i>${esc(label)}</span>`).join("")}</div>`;
  };

  /* ---------- Map (schematic SVG of the southern corridor) ---------- */
  const BOUNDS = { north: 11.16, south: 10.30, west: 106.40, east: 107.32 };
  const MAP_W = 1000;
  const MAP_H = Math.round(((BOUNDS.north - BOUNDS.south) / ((BOUNDS.east - BOUNDS.west) * Math.cos((10.73 * Math.PI) / 180))) * MAP_W);
  ui.MAP_SIZE = { width: MAP_W, height: MAP_H };
  /** lat/lon -> SVG user units. */
  ui.project = function (lat, lon) {
    return [((lon - BOUNDS.west) / (BOUNDS.east - BOUNDS.west)) * MAP_W, ((BOUNDS.north - lat) / (BOUNDS.north - BOUNDS.south)) * MAP_H];
  };
  /** SVG user units -> lat/lon (for drawing geofences). */
  ui.unproject = function (x, y) {
    return [BOUNDS.north - (y / MAP_H) * (BOUNDS.north - BOUNDS.south), BOUNDS.west + (x / MAP_W) * (BOUNDS.east - BOUNDS.west)];
  };
  const path = (points) => points.map(([lat, lon], i) => { const [x, y] = ui.project(lat, lon); return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`; }).join("");
  const COAST = [[11.2, 106.36], [11.2, 107.36], [10.45, 107.36], [10.42, 107.22], [10.37, 107.14], [10.32, 107.08], [10.36, 107.05], [10.44, 107.03], [10.47, 106.98], [10.41, 106.95], [10.38, 106.86], [10.44, 106.76], [10.54, 106.63], [10.58, 106.52], [10.6, 106.36]];
  const RIVERS = [
    [[11.2, 106.55], [11.0, 106.62], [10.88, 106.70], [10.80, 106.72], [10.77, 106.715], [10.765, 106.76], [10.70, 106.78], [10.60, 106.82], [10.45, 106.88]],
    [[11.12, 107.02], [11.0, 106.9], [10.94, 106.83], [10.86, 106.8], [10.78, 106.795], [10.72, 106.79]],
    [[10.73, 107.0], [10.62, 107.02], [10.52, 107.0], [10.46, 106.99]],
  ];
  const STATE_COLORS = { DRIVING: "#0e9f6e", PARKED: "#2563eb", CHARGING: "#7c3aed", OFF: "#94a3b8", OFFLINE: "#94a3b8" };
  ui.STATE_COLORS = STATE_COLORS;

  ui.stationTone = function (location) {
    const chargers = G3.db.charging_stations.filter((s) => s.location_id === location.location_id);
    if (location.status !== "ACTIVE") return "#94a3b8";
    const connectors = chargers.flatMap((s) => s.connectors);
    if (connectors.some((c) => c.status === "Faulted") || chargers.some((s) => !s.is_online)) return "#dc2626";
    if (connectors.some((c) => c.status === "Available")) return "#0e9f6e";
    return "#d97706";
  };

  ui.vehicleMarker = function (vehicle, selected) {
    const live = vehicle.live;
    const [x, y] = ui.project(live.lat, live.lon);
    const state = live.is_online ? live.state : "OFFLINE";
    const color = STATE_COLORS[state];
    const arrow = state === "DRIVING" ? `<path d="M0,-17 L5,-10 L-5,-10 Z" fill="${color}" transform="rotate(${90 - live.heading})"/>` : "";
    const low = live.soc_percent <= 20 ? `<circle r="15" fill="none" stroke="#dc2626" stroke-width="2" stroke-dasharray="3 3"/>` : "";
    return `<g class="map-vehicle ${selected ? "selected" : ""}" data-vehicle="${vehicle.vehicle_id}" style="transform:translate(${x.toFixed(1)}px,${y.toFixed(1)}px) scale(var(--k,1))" data-tip="${esc(`${vehicle.license_plate} · ${G3.label(state)} · ${live.soc_percent}%`)}"><circle class="halo" r="13" fill="${color}"/>${low}${arrow}<circle class="body" r="7.5" fill="${color}" stroke="#fff" stroke-width="2"/></g>`;
  };

  /**
   * The map. options: vehicles, locations (charging_locations), geofences,
   * route ([{lat, lon}]), selectedVehicleId, height, labels (true), legend (true).
   */
  ui.map = function ({ vehicles = [], locations = [], geofences = [], route = null, selectedVehicleId, height = 520, legend = true, id = "map" } = {}) {
    let svg = `<svg viewBox="0 0 ${MAP_W} ${MAP_H}" data-map="${id}" preserveAspectRatio="xMidYMid slice">`;
    svg += `<rect x="-2000" y="-2000" width="${MAP_W + 4000}" height="${MAP_H + 4000}" fill="var(--map-water)"/>`;
    svg += `<path class="map-land" d="${path(COAST)}Z"/>`;
    RIVERS.forEach((r) => { svg += `<path d="${path(r)}" fill="none" stroke="var(--map-water)" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>`; });
    G3.db.ROADS.forEach((road) => { svg += `<path class="map-road-edge" d="${path(road.points)}" stroke-width="${road.width + 3}"/>`; });
    G3.db.ROADS.forEach((road) => { svg += `<path class="map-road" d="${path(road.points)}" stroke-width="${road.width}"/>`; });
    Object.values(G3.db.PLACES).forEach((p) => {
      const [x, y] = ui.project(p.lat, p.lon);
      svg += `<g style="transform:translate(${x.toFixed(1)}px,${y.toFixed(1)}px) scale(var(--k,1))"><circle r="2.5" fill="var(--text-3)"/><text class="map-label ${p.name.startsWith("TP") ? "city" : ""}" x="6" y="-6">${esc(p.name)}</text></g>`;
    });
    geofences.forEach((g) => {
      svg += `<path class="map-geofence" vector-effect="non-scaling-stroke" data-geofence="${g.geofence_id}" d="${path(g.boundary)}Z" fill="${g.color}" stroke="${g.color}" data-tip="${esc(g.name)}"/>`;
    });
    if (route && route.length > 1) {
      svg += `<path class="map-route" d="${path(route.map((p) => [p.lat, p.lon]))}"/>`;
      const [sx, sy] = ui.project(route[0].lat, route[0].lon);
      svg += `<g style="transform:translate(${sx}px,${sy}px) scale(var(--k,1))"><circle r="7" fill="#fff" stroke="var(--accent)" stroke-width="3"/></g>`;
      svg += `<g data-route-cursor style="transform:translate(${sx}px,${sy}px) scale(var(--k,1))"><circle r="9" fill="var(--accent)" stroke="#fff" stroke-width="3"/></g>`;
    }
    locations.forEach((loc) => {
      const [x, y] = ui.project(loc.lat, loc.lon);
      const color = ui.stationTone(loc);
      svg += `<g class="map-station" data-location="${loc.location_id}" style="transform:translate(${x.toFixed(1)}px,${y.toFixed(1)}px) scale(var(--k,1))" data-tip="${esc(loc.name)}"><rect x="-11" y="-11" width="22" height="22" rx="6" fill="${color}" stroke="#fff" stroke-width="2"/><path d="M1.5,-7 L-4.5,1 L-0.5,1 L-1.5,7 L4.5,-1 L0.5,-1 Z" fill="#fff"/>${loc.is_public ? "" : `<circle cx="10" cy="-10" r="4.5" fill="#7c3aed" stroke="#fff" stroke-width="1.5"/>`}</g>`;
    });
    vehicles.filter((v) => v.live).forEach((v) => { svg += ui.vehicleMarker(v, v.vehicle_id === selectedVehicleId); });
    svg += "</svg>";
    const legendHtml = legend ? `<div class="map-legend">${["DRIVING", "PARKED", "CHARGING", "OFFLINE"].map((s) => `<span><i style="background:${STATE_COLORS[s]}"></i>${G3.label(s)}</span>`).join("")}<span><i style="background:#0e9f6e;border-radius:3px"></i>${t("Station free", "Trạm còn chỗ")}</span><span><i style="background:#dc2626;border-radius:3px"></i>${t("Station fault", "Trạm lỗi")}</span></div>` : "";
    return `<div class="map-wrap" data-map-wrap="${id}" style="height:${typeof height === "number" ? height + "px" : height}">${svg}<div class="map-controls"><button data-map-zoom="in" title="Zoom in">+</button><button data-map-zoom="out" title="Zoom out">−</button><button data-map-zoom="reset" title="Reset">⟲</button></div>${legendHtml}</div>`;
  };

  /** Pan (drag) and zoom (wheel, buttons) for every map inside root. */
  ui.mountMaps = function (root) {
    root.querySelectorAll("[data-map-wrap]").forEach((wrap) => {
      const svg = wrap.querySelector("svg");
      let vb = [0, 0, MAP_W, MAP_H];
      // --k counter-scales markers and labels so they keep their on-screen size when
      // zoomed, and on maps narrower than the 1000-unit drawing.
      const apply = () => {
        svg.setAttribute("viewBox", vb.map((v) => v.toFixed(1)).join(" "));
        const width = svg.getBoundingClientRect().width || MAP_W;
        svg.style.setProperty("--k", (Math.min(1.2, vb[2] / MAP_W) * Math.min(2.4, Math.max(0.8, MAP_W / width))).toFixed(3));
      };
      apply();
      const zoom = (factor, cx = vb[0] + vb[2] / 2, cy = vb[1] + vb[3] / 2) => {
        const w = Math.min(MAP_W * 1.2, Math.max(120, vb[2] * factor));
        const h = (w / MAP_W) * MAP_H;
        vb = [cx - ((cx - vb[0]) / vb[2]) * w, cy - ((cy - vb[1]) / vb[3]) * h, w, h];
        apply();
      };
      wrap.zoomTo = function (lat, lon, width = 260) {
        const [x, y] = ui.project(lat, lon);
        const h = (width / MAP_W) * MAP_H;
        vb = [x - width / 2, y - h / 2, width, h];
        apply();
      };
      wrap.querySelectorAll("[data-map-zoom]").forEach((b) => b.addEventListener("click", (e) => {
        e.stopPropagation();
        const kind = b.dataset.mapZoom;
        if (kind === "reset") { vb = [0, 0, MAP_W, MAP_H]; apply(); } else zoom(kind === "in" ? 0.7 : 1.4);
      }));
      svg.addEventListener("wheel", (e) => {
        e.preventDefault();
        const rect = svg.getBoundingClientRect();
        const cx = vb[0] + ((e.clientX - rect.left) / rect.width) * vb[2];
        const cy = vb[1] + ((e.clientY - rect.top) / rect.height) * vb[3];
        zoom(e.deltaY < 0 ? 0.85 : 1.18, cx, cy);
      }, { passive: false });
      let drag = null;
      svg.addEventListener("pointerdown", (e) => { if (wrap.dataset.drawing) return; drag = { x: e.clientX, y: e.clientY, vb: vb.slice(), moved: false }; });
      window.addEventListener("pointermove", (e) => {
        if (!drag) return;
        const rect = svg.getBoundingClientRect();
        const dx = ((e.clientX - drag.x) / rect.width) * vb[2];
        const dy = ((e.clientY - drag.y) / rect.height) * vb[3];
        if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
        vb = [drag.vb[0] - dx, drag.vb[1] - dy, vb[2], vb[3]];
        apply();
      });
      window.addEventListener("pointerup", () => { if (drag && drag.moved) wrap.dataset.justDragged = "1"; drag = null; setTimeout(() => delete wrap.dataset.justDragged, 0); });
      /** SVG user coordinates of a mouse event (geofence drawing). */
      wrap.toMapPoint = function (e) {
        const rect = svg.getBoundingClientRect();
        // preserveAspectRatio slice: the scale is the larger ratio
        const scale = Math.max(rect.width / vb[2], rect.height / vb[3]);
        const offX = (rect.width - vb[2] * scale) / 2;
        const offY = (rect.height - vb[3] * scale) / 2;
        return [vb[0] + (e.clientX - rect.left - offX) / scale, vb[1] + (e.clientY - rect.top - offY) / scale];
      };
    });
  };

  /** Move every vehicle marker inside root to its current position (live tick). */
  ui.refreshMarkers = function (root) {
    root.querySelectorAll("[data-vehicle]").forEach((g) => {
      const v = G3.db.vehicle(g.dataset.vehicle);
      if (!v || !v.live) return;
      const fresh = document.createElementNS("http://www.w3.org/2000/svg", "g");
      fresh.innerHTML = ui.vehicleMarker(v, g.classList.contains("selected"));
      const replacement = fresh.firstChild;
      g.setAttribute("style", replacement.getAttribute("style"));
      g.setAttribute("data-tip", replacement.getAttribute("data-tip"));
      g.innerHTML = replacement.innerHTML;
    });
  };

  /* ---------- Overlays ---------- */
  /**
   * Open a modal. Returns { el, close }. foot: HTML of the buttons; a button
   * with data-close closes it. onMount(el, close) binds the content.
   */
  ui.modal = function ({ title, body, foot, wide, onMount }) {
    const overlay = document.createElement("div");
    overlay.className = "overlay";
    overlay.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true"><div class="modal-head"><h2>${esc(title)}</h2><button class="icon-btn" data-close>${ui.icon("x")}</button></div><div class="modal-body">${body}</div>${foot !== null ? `<div class="modal-foot">${foot || `<button class="btn" data-close>${t("Close", "Đóng")}</button>`}</div>` : ""}</div>`;
    const close = () => { overlay.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    overlay.addEventListener("click", (e) => { if (e.target === overlay || e.target.closest("[data-close]")) close(); });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
    if (onMount) onMount(overlay, close);
    const first = overlay.querySelector("input, select, textarea");
    if (first) first.focus();
    return { el: overlay, close };
  };

  /**
   * A form in a modal. fields: [{ name, label, type: text|number|select|textarea|date|datetime-local|checkbox|tel,
   * options: [[value, label]], value, required, hint, full, placeholder }]
   * onSubmit(values) returns true/undefined to close, or a string error to keep it open.
   */
  ui.formModal = function ({ title, intro, fields, submitLabel, onSubmit, wide, danger }) {
    const fieldHtml = fields.map((f) => {
      const id = `f-${f.name}`;
      const req = f.required ? "required" : "";
      let control;
      if (f.type === "select") control = `<select class="input" id="${id}" name="${f.name}" ${req}>${f.options.map(([k, l]) => `<option value="${esc(k)}" ${String(k) === String(f.value) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
      else if (f.type === "textarea") control = `<textarea class="input" id="${id}" name="${f.name}" rows="3" ${req} placeholder="${esc(f.placeholder || "")}">${esc(f.value || "")}</textarea>`;
      else if (f.type === "checkbox") control = `<label class="check"><input type="checkbox" name="${f.name}" ${f.value ? "checked" : ""}> ${esc(f.checkLabel || "")}</label>`;
      else if (f.type === "checkboxes") control = `<div class="row" style="gap:6px 14px">${f.options.map(([k, l]) => `<label class="check"><input type="checkbox" name="${f.name}" value="${esc(k)}" ${(f.value || []).includes(k) ? "checked" : ""}> ${esc(l)}</label>`).join("")}</div>`;
      else control = `<input class="input" id="${id}" name="${f.name}" type="${f.type || "text"}" value="${esc(f.value ?? "")}" ${req} placeholder="${esc(f.placeholder || "")}" ${f.attrs || ""}>`;
      return `<div class="field ${f.full ? "full" : ""}"><label for="${id}">${esc(f.label)}${f.required ? ' <span style="color:var(--danger)">*</span>' : ""}</label>${control}${f.hint ? `<span class="hint">${esc(f.hint)}</span>` : ""}</div>`;
    }).join("");
    const body = `${intro ? `<div class="mb">${intro}</div>` : ""}<form class="form ${wide ? "cols-2" : ""}" novalidate>${fieldHtml}<div class="field full form-error" style="color:var(--danger);display:none"></div></form>`;
    const foot = `<button class="btn" data-close>${t("Cancel", "Hủy")}</button><button class="btn ${danger ? "danger" : "primary"}" data-submit>${esc(submitLabel || t("Save", "Lưu"))}</button>`;
    return ui.modal({
      title, body, foot, wide,
      onMount(el, close) {
        const form = el.querySelector("form");
        const submit = () => {
          const values = {};
          for (const f of fields) {
            if (f.type === "checkbox") values[f.name] = form.querySelector(`[name="${f.name}"]`).checked;
            else if (f.type === "checkboxes") values[f.name] = Array.from(form.querySelectorAll(`[name="${f.name}"]:checked`)).map((i) => i.value);
            else values[f.name] = form.querySelector(`[name="${f.name}"]`).value.trim();
            if (f.required && (values[f.name] === "" || (Array.isArray(values[f.name]) && !values[f.name].length))) {
              const err = form.querySelector(".form-error");
              err.textContent = t(`"${f.label}" is required.`, `Vui lòng nhập "${f.label}".`);
              err.style.display = "block";
              return;
            }
          }
          const result = onSubmit(values);
          if (typeof result === "string") {
            const err = form.querySelector(".form-error");
            err.textContent = result;
            err.style.display = "block";
            return;
          }
          close();
        };
        el.querySelector("[data-submit]").addEventListener("click", submit);
        form.addEventListener("submit", (e) => { e.preventDefault(); submit(); });
      },
    });
  };

  /** Ask for a reason before a decided status change (status_reason, change_reason). */
  ui.reasonModal = function ({ title, intro, submitLabel, danger = true, onSubmit }) {
    return ui.formModal({
      title, intro, submitLabel, danger,
      fields: [{ name: "reason", label: t("Reason", "Lý do"), type: "textarea", required: true, hint: t("Saved with the change history (max 200 characters).", "Lưu vào lịch sử thay đổi (tối đa 200 ký tự).") }],
      onSubmit: (values) => onSubmit(values.reason.slice(0, 200)),
    });
  };

  ui.toast = function (message, kind = "", icon) {
    let host = document.querySelector(".toasts");
    if (!host) { host = document.createElement("div"); host.className = "toasts"; document.body.appendChild(host); }
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.innerHTML = `${ui.icon(icon || (kind === "alert" ? "alert" : "check"), 17)}<div>${message}</div>`;
    host.appendChild(el);
    setTimeout(() => el.remove(), kind === "alert" ? 6000 : 3200);
  };

  /** A small menu anchored under an element. items: [{ label, sub, icon, on, onClick }] or { title }. */
  ui.popover = function (anchor, items, { align = "left" } = {}) {
    document.querySelectorAll(".popover").forEach((p) => p.remove());
    const pop = document.createElement("div");
    pop.className = "popover";
    pop.innerHTML = items.map((it, i) => (it.title ? `<div class="pop-title">${esc(it.title)}</div>` : `<div class="pop-item ${it.on ? "on" : ""}" data-i="${i}">${it.icon ? ui.icon(it.icon, 16) : ""}<div style="min-width:0"><div>${esc(it.label)}</div>${it.sub ? `<div class="small muted">${esc(it.sub)}</div>` : ""}</div></div>`)).join("");
    document.body.appendChild(pop);
    const rect = anchor.getBoundingClientRect();
    pop.style.top = rect.bottom + 6 + "px";
    if (align === "right") pop.style.right = window.innerWidth - rect.right + "px";
    else pop.style.left = Math.min(rect.left, window.innerWidth - pop.offsetWidth - 10) + "px";
    pop.addEventListener("click", (e) => {
      const item = e.target.closest("[data-i]");
      if (!item) return;
      pop.remove();
      items[Number(item.dataset.i)].onClick();
    });
    setTimeout(() => document.addEventListener("click", function off(e) { if (!pop.contains(e.target)) { pop.remove(); document.removeEventListener("click", off); } }), 0);
    return pop;
  };

  /** Fake CSV download from rows (export buttons). */
  ui.downloadCsv = function (filename, columns, rows) {
    const line = (cells) => cells.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(",");
    const csv = [line(columns.map((c) => c.label)), ...rows.map((r) => line(columns.map((c) => (c.csv ? c.csv(r) : r[c.key]))))].join("\n");
    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    ui.toast(t(`Exported ${rows.length} rows`, `Đã xuất ${rows.length} dòng`), "", "download");
  };

  /** Ask for an export reason (ACC-18) then download. */
  ui.auditedExport = function ({ dataType, subject, filename, columns, rows }) {
    ui.formModal({
      title: t("Export personal data", "Xuất dữ liệu cá nhân"),
      intro: ui.callout("info", t("This export contains location or personal data. The reason is written to the access audit log (ACC-18).", "Bản xuất có dữ liệu vị trí hoặc cá nhân. Lý do sẽ được ghi vào nhật ký truy cập (ACC-18).")),
      fields: [{ name: "reason", label: t("Reason or ticket", "Lý do hoặc mã yêu cầu"), required: true, placeholder: "SC-2304…" }],
      submitLabel: t("Export", "Xuất"),
      onSubmit(values) {
        G3.audit("EXPORT", dataType, subject, values.reason);
        ui.downloadCsv(filename, columns, rows);
      },
    });
  };

  /* ---------- Global behaviour ---------- */
  // Chart / marker tooltips.
  let tipEl = null;
  document.addEventListener("mouseover", (e) => {
    const target = e.target.closest("[data-tip]");
    if (!target) { if (tipEl) tipEl.style.display = "none"; return; }
    if (!tipEl) { tipEl = document.createElement("div"); tipEl.className = "chart-tip"; document.body.appendChild(tipEl); }
    tipEl.textContent = target.getAttribute("data-tip");
    tipEl.style.display = "block";
  });
  document.addEventListener("mousemove", (e) => {
    if (tipEl && tipEl.style.display === "block") {
      tipEl.style.left = Math.min(e.clientX + 14, window.innerWidth - tipEl.offsetWidth - 8) + "px";
      tipEl.style.top = e.clientY + 14 + "px";
    }
  });
  // Clickable rows/cards: data-href navigates unless an inner control was clicked.
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-href]");
    if (!el || e.target.closest("button, a, input, select, label, textarea")) return;
    location.hash = el.dataset.href.replace(/^#/, "");
  });

  G3.ui = ui;
})();
