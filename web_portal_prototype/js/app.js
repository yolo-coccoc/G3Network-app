/* App shell: personas and data scope, page registry and hash router, the
   sidebar/topbar, login and organization picker, legal acceptance, the live
   simulation tick and the notification poll.

   Page modules call G3.page({...}) to register a screen (see README.md,
   "Adding a screen"). */

(function () {
  const ui = G3.ui;
  const db = G3.db;
  const esc = G3.esc;

  /* ---------- Preferences ---------- */
  const store = {
    get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private window */ } },
    sget(key) { try { return sessionStorage.getItem(key); } catch (e) { return null; } },
    sset(key, value) { try { if (value === null) sessionStorage.removeItem(key); else sessionStorage.setItem(key, value); } catch (e) { /* ignore */ } },
  };
  G3.store = store;
  G3.prefs = { showFeatureCodes: store.get("g3.showFeatureCodes", true), theme: store.get("g3.theme", "light") };
  document.documentElement.dataset.theme = G3.prefs.theme;

  /* ---------- Personas (demo: one per role) ---------- */
  const P = db.people;
  const PERSONAS = [
    { key: "head", person: P.head, blurb: ["Sees every organization", "Xem mọi tổ chức"] },
    { key: "coadmin", person: P.coadmin, blurb: ["Users, roles, audit", "Người dùng, vai trò, nhật ký"] },
    { key: "sales", person: P.sales, blurb: ["Onboarding, plans", "Khách hàng mới, gói dịch vụ"] },
    { key: "ops", person: P.ops, blurb: ["Stations, devices, alerts", "Trạm sạc, thiết bị, cảnh báo"] },
    { key: "care", person: P.care, blurb: ["Tickets, SOS, rescue", "Yêu cầu hỗ trợ, SOS, cứu hộ"] },
    { key: "warranty", person: P.warranty, blurb: ["Policy, warranty risk", "Chính sách sạc, rủi ro bảo hành"] },
    { key: "accountant", person: P.accountant, blurb: ["Invoices, revenue", "Hóa đơn, doanh thu"] },
    { key: "maintenance", person: P.maintenance, blurb: ["Batteries, repairs", "Pin, sửa chữa"] },
    { key: "tcxAdmin", person: P.tcxAdmin, blurb: ["Customer admin", "Quản trị khách hàng"] },
    { key: "tcxFm", person: P.tcxFm, blurb: ["Two organizations", "Thuộc hai tổ chức"] },
    { key: "tcxFmLimited", person: P.tcxFmLimited, blurb: ["Limited to Depot Cái Mép", "Giới hạn Depot Cái Mép"] },
    { key: "tcxDispatch", person: P.tcxDispatch, blurb: ["Trips, live map", "Chuyến, bản đồ"] },
    { key: "tcxAcc", person: P.tcxAcc, blurb: ["Charging costs, invoices", "Chi phí sạc, hóa đơn"] },
    { key: "mnAdmin", person: P.mnAdmin, blurb: ["Small fleet owner", "Chủ đội xe nhỏ"] },
  ];
  G3.PERSONAS = PERSONAS;

  /* ---------- Session and data scope ---------- */
  const session = { membershipId: store.sget("g3.membership"), orgFilter: store.sget("g3.orgFilter") || null };

  const scope = {
    membership: () => db.memberships.find((m) => m.membership_id === session.membershipId) || null,
    user() { const m = scope.membership(); return m ? db.user(m.user_id) : null; },
    org() { const m = scope.membership(); return m ? db.org(m.organization_id) : null; },
    roles() { const m = scope.membership(); return m ? m.roles : []; },
    has(...roles) { return scope.roles().some((r) => roles.includes(r)); },
    /** Internal staff see every organization (ID-50); customers only their own. */
    isInternal() { const o = scope.org(); return !!(o && o.is_internal); },
    /** Organization IDs whose data the current persona reaches (narrowed by the internal org filter). */
    orgIds() {
      if (!scope.isInternal()) return [scope.org().organization_id];
      if (session.orgFilter) return [session.orgFilter];
      return db.organizations.map((o) => o.organization_id);
    },
    orgFilter: () => session.orgFilter,
    setOrgFilter(id) { session.orgFilter = id || null; store.sset("g3.orgFilter", session.orgFilter); },
    /** Keep only rows of reachable organizations. */
    filter(rows, key = "organization_id") {
      const ids = scope.orgIds();
      return rows.filter((r) => ids.includes(r[key]));
    },
    /** Fleet limit of the current membership (FLT-03), expanded to sub-fleets; null = no limit. */
    fleetLimit() {
      const m = scope.membership();
      if (!m || !m.fleet_ids.length) return null;
      return m.fleet_ids.flatMap((id) => db.fleetAndDescendants(id));
    },
    /** Trucks the persona may see: organization scope plus the fleet limit. */
    vehicles() {
      const limit = scope.fleetLimit();
      return scope.filter(db.vehicles).filter((v) => !limit || limit.includes(v.fleet_id));
    },
    vehicleIds() { return new Set(scope.vehicles().map((v) => v.vehicle_id)); },
    /** Rows that point to a visible truck (or, with allowNoVehicle, to none but in scope). */
    byVehicle(rows, allowNoVehicle = true) {
      const ids = scope.vehicleIds();
      const orgIds = scope.orgIds();
      return rows.filter((r) => (r.vehicle_id ? ids.has(r.vehicle_id) : allowNoVehicle && orgIds.includes(r.organization_id)));
    },
    login(membershipId) { session.membershipId = membershipId; store.sset("g3.membership", membershipId); session.orgFilter = null; store.sset("g3.orgFilter", null); },
    logout() { session.membershipId = null; store.sset("g3.membership", null); },
  };
  G3.scope = scope;

  /** Append an access-audit row (ACC-18). */
  G3.audit = function (action, dataType, subject, reason) {
    const m = scope.membership();
    db.access_audit_logs.unshift({ access_audit_log_id: 520000 + db.access_audit_logs.length + 100, user_id: m ? m.user_id : null, organization_id: m ? m.organization_id : null, action, data_type: dataType, subject: subject || null, reason: reason || null, ip_address: "113.161.72.18", device: "Chrome 129 / Windows", occurred_at: new Date().toISOString() });
  };

  /* ---------- Page registry and router ---------- */
  const NAV_GROUPS = [
    ["overview", ["Overview", "Tổng quan"]],
    ["monitoring", ["Monitoring", "Giám sát"]],
    ["fleet", ["Fleet & drivers", "Đội xe & tài xế"]],
    ["assets", ["Assets", "Tài sản"]],
    ["charging", ["Charging network", "Mạng lưới sạc"]],
    ["support", ["Support", "Hỗ trợ"]],
    ["billing", ["Billing", "Thanh toán"]],
    ["sustainability", ["Sustainability", "Phát triển xanh"]],
    ["admin", ["Administration", "Quản trị"]],
    ["prototype", ["Prototype", "Bản mẫu"]],
  ];
  const pages = [];
  /**
   * Register a screen.
   * def: { id, path ("/vehicles/:id"), title: [en, vi], roles: "*" | [...],
   *        features: ["VEH-01"], nav: { group, order, icon, label?: [en, vi], badge?: () => n },
   *        flush: bool, render(ctx) -> html, mount(root, ctx) }
   * ctx: { params, query, page }
   */
  G3.page = function (def) {
    const keys = [];
    const pattern = new RegExp("^" + def.path.replace(/:[a-zA-Z_]+/g, (m) => { keys.push(m.slice(1)); return "([^/]+)"; }) + "$");
    pages.push({ ...def, keys, pattern });
  };
  G3.pages = pages;

  function canSee(page) {
    if (page.roles === "*" || scope.has("HEAD_ADMIN")) return true;
    return scope.has(...page.roles);
  }
  G3.canSee = (pageId) => { const p = pages.find((x) => x.id === pageId); return !!p && canSee(p); };

  function parseHash() {
    const raw = location.hash.replace(/^#/, "") || "/";
    const [pathPart, queryPart] = raw.split("?");
    const query = Object.fromEntries(new URLSearchParams(queryPart || ""));
    return { path: pathPart || "/", query };
  }
  G3.navigate = (hash) => { location.hash = hash.replace(/^#/, ""); };
  /** Update query parameters without adding a history entry, then re-render. */
  G3.setQuery = function (changes) {
    const { path, query } = parseHash();
    const next = { ...query, ...changes };
    Object.keys(next).forEach((k) => (next[k] === null || next[k] === "" || next[k] === undefined) && delete next[k]);
    const qs = new URLSearchParams(next).toString();
    history.replaceState(null, "", "#" + path + (qs ? "?" + qs : ""));
    G3.render();
  };

  /* Per-route cleanup: ticks and timers registered by the page. */
  let leaveFns = [];
  G3.onLeave = (fn) => leaveFns.push(fn);
  const tickFns = new Set();
  /** Run fn on every live tick (3 s) while the current page is shown. */
  G3.onTick = function (fn) { tickFns.add(fn); G3.onLeave(() => tickFns.delete(fn)); };

  /* ---------- Shell ---------- */
  const app = document.getElementById("app");
  let shellKey = null;

  function navHtml(activePage) {
    return NAV_GROUPS.map(([groupKey, label]) => {
      const items = pages.filter((p) => p.nav && p.nav.group === groupKey && canSee(p)).sort((a, b) => (a.nav.order || 50) - (b.nav.order || 50));
      if (!items.length) return "";
      return `<div class="nav-group"><div class="nav-group-title">${esc(t(label[0], label[1]))}</div>${items.map((p) => {
        const count = p.nav.badge ? p.nav.badge() : 0;
        const navLabel = p.nav.label || p.title;
        const active = activePage && (activePage.id === p.id || (activePage.navParent && activePage.navParent === p.id));
        return `<a href="#${p.path}" class="${active ? "active" : ""}" data-nav="${p.id}">${ui.icon(p.nav.icon || "list", 17)}<span>${esc(t(navLabel[0], navLabel[1]))}</span>${count ? `<span class="nav-count">${count}</span>` : ""}</a>`;
      }).join("")}</div>`;
    }).join("");
  }

  function unreadCount() {
    return scope.byVehicle(db.notifications).filter((n) => !n.read_at).length;
  }

  function topbarHtml() {
    const user = scope.user();
    const org = scope.org();
    const memberships = db.memberships.filter((m) => m.user_id === user.user_id && m.status === "ACTIVE");
    const unread = unreadCount();
    const orgFilter = scope.isInternal()
      ? `<select class="input" data-org-filter title="${esc(t("Organization filter (internal staff)", "Lọc tổ chức (nhân viên nội bộ)"))}"><option value="">${esc(t("All organizations", "Tất cả tổ chức"))}</option>${db.organizations.filter((o) => !o.is_internal).map((o) => `<option value="${o.organization_id}" ${scope.orgFilter() === o.organization_id ? "selected" : ""}>${esc(o.short_name)}</option>`).join("")}</select>`
      : "";
    return `
      <div class="search">${ui.icon("search", 16)}<input data-global-search placeholder="${esc(t("Search plate, VIN, station, ticket…", "Tìm biển số, VIN, trạm, yêu cầu…"))}"></div>
      ${orgFilter}
      <div class="spacer"></div>
      <button class="persona-switch" data-persona title="${esc(t("Demo: switch persona", "Demo: đổi vai trò"))}">${ui.icon("users", 15)}<span class="name">${esc(scope.roles().map(G3.roleLabel).join(", "))}</span>${ui.icon("down", 14)}</button>
      <button class="org-switch" data-org-switch ${memberships.length > 1 ? "" : "disabled"}>${ui.icon("building", 15)}<span class="name">${esc(org.short_name)}</span>${memberships.length > 1 ? ui.icon("down", 14) : ""}</button>
      <button class="icon-btn" data-lang title="Language / Ngôn ngữ"><b style="font-size:12px">${G3.lang === "vi" ? "VI" : "EN"}</b></button>
      <button class="icon-btn" data-theme title="${esc(t("Theme", "Giao diện"))}">${ui.icon(G3.prefs.theme === "dark" ? "sun" : "moon")}</button>
      <button class="icon-btn" data-bell title="${esc(t("Notifications", "Thông báo"))}">${ui.icon("bell")}${unread ? `<span class="dot">${unread > 99 ? "99+" : unread}</span>` : ""}</button>
      <button class="icon-btn" data-user-menu style="width:auto;padding:0 4px"><span class="avatar">${esc(ui.initials(user.full_name))}</span></button>`;
  }

  function renderShell(activePage) {
    const key = `${session.membershipId}|${G3.lang}|${G3.prefs.theme}|${session.orgFilter}`;
    if (shellKey !== key || !document.querySelector(".app")) {
      app.innerHTML = `<div class="app"><aside class="sidebar"><div class="brand"><div class="brand-logo">${ui.icon("bolt", 19)}</div><div>G3 Network<small>${esc(t("Fleet & charging portal", "Cổng quản lý đội xe & sạc"))}</small></div></div><nav class="nav" id="nav"></nav><div class="sidebar-foot">${esc(t("Prototype · mock data", "Bản mẫu · dữ liệu giả lập"))}<br>${esc(t("Catalog snapshot", "Danh mục tính năng"))} ${esc((window.G3_FEATURES || {}).taken_on || "")}<br><label class="check" style="margin-top:8px"><input type="checkbox" data-toggle-codes ${G3.prefs.showFeatureCodes ? "checked" : ""}> ${esc(t("Show feature codes", "Hiện mã tính năng"))}</label></div></aside><div class="main"><header class="topbar" id="topbar"></header><main class="content" id="content"></main></div></div>`;
      shellKey = key;
      bindShell();
    }
    document.getElementById("nav").innerHTML = navHtml(activePage);
    document.getElementById("topbar").innerHTML = topbarHtml();
  }
  G3.refreshChrome = function () {
    const nav = document.getElementById("nav");
    if (!nav) return;
    const current = matchRoute(parseHash().path);
    nav.innerHTML = navHtml(current && current.page);
    document.getElementById("topbar").innerHTML = topbarHtml();
  };

  function bindShell() {
    const root = document.querySelector(".app");
    root.addEventListener("change", (e) => {
      if (e.target.matches("[data-org-filter]")) { scope.setOrgFilter(e.target.value); G3.render(); }
      if (e.target.matches("[data-toggle-codes]")) { G3.prefs.showFeatureCodes = e.target.checked; store.set("g3.showFeatureCodes", e.target.checked); G3.render(); }
    });
    root.addEventListener("click", (e) => {
      const target = e.target.closest("button");
      if (!target) return;
      if (target.matches("[data-lang]")) { G3.setLang(G3.lang === "vi" ? "en" : "vi"); G3.render(); }
      else if (target.matches("[data-theme]")) { G3.prefs.theme = G3.prefs.theme === "dark" ? "light" : "dark"; store.set("g3.theme", G3.prefs.theme); document.documentElement.dataset.theme = G3.prefs.theme; G3.render(); }
      else if (target.matches("[data-persona]")) personaMenu(target);
      else if (target.matches("[data-org-switch]")) orgMenu(target);
      else if (target.matches("[data-bell]")) bellMenu(target);
      else if (target.matches("[data-user-menu]")) userMenu(target);
    });
    root.addEventListener("keydown", (e) => {
      if (e.target.matches("[data-global-search]") && e.key === "Enter") globalSearch(e.target);
    });
    root.addEventListener("input", (e) => {
      if (e.target.matches("[data-global-search]") && e.target.value.length >= 2) globalSearch(e.target);
    });
  }

  function personaMenu(anchor) {
    const groups = [{ title: t("Internal (G3 Network)", "Nội bộ (G3 Network)") }];
    const add = (p) => groups.push({
      label: `${p.person.user.full_name} — ${p.person.membership.roles.map(G3.roleLabel).join(", ")}`,
      sub: `${db.org(p.person.membership.organization_id).short_name} · ${t(p.blurb[0], p.blurb[1])}`,
      on: session.membershipId === p.person.membership.membership_id,
      onClick: () => enterAs(p.person.user.user_id, p.person.membership.membership_id),
    });
    PERSONAS.filter((p) => p.person.membership.organization_id === "org-g3").forEach(add);
    groups.push({ title: t("Customers", "Khách hàng") });
    PERSONAS.filter((p) => p.person.membership.organization_id !== "org-g3").forEach(add);
    ui.popover(anchor, groups, { align: "right" });
  }

  function orgMenu(anchor) {
    const user = scope.user();
    const items = db.memberships.filter((m) => m.user_id === user.user_id && m.status === "ACTIVE").map((m) => ({
      label: db.org(m.organization_id).short_name,
      sub: m.roles.map(G3.roleLabel).join(", "),
      icon: "building",
      on: m.membership_id === session.membershipId,
      onClick: () => { scope.login(m.membership_id); store.set("g3.lastOrg." + user.user_id, m.organization_id); G3.navigate("/"); G3.render(); ui.toast(t("Switched organization", "Đã chuyển tổ chức")); },
    }));
    ui.popover(anchor, [{ title: t("Your organizations", "Tổ chức của bạn") }, ...items], { align: "right" });
  }

  function bellMenu(anchor) {
    const rows = scope.byVehicle(db.notifications).slice(0, 7);
    const items = rows.map((n) => ({
      label: t(n.title[0], n.title[1]),
      sub: `${G3.label(n.severity)} · ${G3.fmt.ago(n.created_at)}${n.read_at ? "" : " · " + t("unread", "chưa đọc")}`,
      icon: n.severity === "CRITICAL" ? "alert" : n.severity === "WARNING" ? "alert" : "info",
      onClick: () => { n.read_at = n.read_at || new Date().toISOString(); G3.navigate(n.vehicle_id && G3.canSee("vehicle-detail") ? `/vehicles/${n.vehicle_id}` : "/notifications"); },
    }));
    items.push({ label: t("Open the notification center →", "Mở trung tâm thông báo →"), icon: "bell", onClick: () => G3.navigate("/notifications") });
    const pop = ui.popover(anchor, [{ title: t("Latest notifications", "Thông báo mới nhất") }, ...items], { align: "right" });
    pop.style.width = "380px";
  }

  function userMenu(anchor) {
    const user = scope.user();
    ui.popover(anchor, [
      { title: user.full_name },
      { label: t("My account & sessions", "Tài khoản & phiên đăng nhập"), icon: "user", onClick: () => G3.navigate("/account") },
      { label: t("Log out", "Đăng xuất"), icon: "logout", onClick: () => { scope.logout(); G3.navigate("/login"); G3.render(); } },
    ], { align: "right" });
  }

  function globalSearch(input) {
    const q = input.value.trim().toLowerCase();
    if (q.length < 2) return;
    const results = [];
    scope.vehicles().filter((v) => v.license_plate.toLowerCase().includes(q) || v.vin.toLowerCase().includes(q)).slice(0, 5).forEach((v) => results.push({ label: v.license_plate, sub: `${v.vin} · ${db.org(v.organization_id).short_name}`, icon: "truck", onClick: () => G3.navigate(`/vehicles/${v.vehicle_id}`) }));
    db.charging_locations.filter((l) => l.name.toLowerCase().includes(q)).slice(0, 3).forEach((l) => results.push({ label: l.name, sub: l.address, icon: "station", onClick: () => G3.navigate(`/stations/${l.location_id}`) }));
    scope.filter(db.support_cases).filter((c) => c.support_case_id.toLowerCase().includes(q)).slice(0, 3).forEach((c) => results.push({ label: c.support_case_id, sub: t(c.subject[0], c.subject[1]), icon: "lifebuoy", onClick: () => G3.navigate(`/support/${c.support_case_id}`) }));
    scope.filter(db.drivers).filter((d) => d.full_name.toLowerCase().includes(q) || d.phone_number.includes(q)).slice(0, 4).forEach((d) => results.push({ label: d.full_name, sub: d.phone_number, icon: "id", onClick: () => G3.navigate(`/drivers/${d.driver_id}`) }));
    if (!results.length) results.push({ label: t("No match", "Không tìm thấy"), onClick: () => {} });
    const pop = ui.popover(input, [{ title: t("Results", "Kết quả") }, ...results]);
    pop.style.width = input.offsetWidth + 40 + "px";
  }

  /* ---------- Login, organization picker, legal acceptance (ACC-04, ACC-12, ACC-17) ---------- */
  function enterAs(userId, membershipId) {
    const active = db.memberships.filter((m) => m.user_id === userId && m.status === "ACTIVE");
    if (!membershipId && active.length > 1) {
      store.sset("g3.pendingUser", userId);
      G3.navigate("/choose-organization");
      G3.render();
      return;
    }
    scope.login(membershipId || active[0].membership_id);
    G3.audit("LOGIN_SUCCESS", "ACCOUNT", null, null);
    const user = db.user(userId);
    user.last_login_at = new Date().toISOString();
    G3.navigate("/");
    G3.render();
  }

  function renderLogin() {
    const personaCards = PERSONAS.map((p) => `<button class="persona-card" data-persona-login="${p.key}"><b>${esc(p.person.user.full_name)}</b><span>${esc(p.person.membership.roles.map(G3.roleLabel).join(", "))} · ${esc(db.org(p.person.membership.organization_id).short_name)}</span></button>`).join("");
    app.innerHTML = `<div class="login-page">
      <div class="login-hero"><div class="brand" style="padding:0">${`<div class="brand-logo">${ui.icon("bolt", 19)}</div>`}<div>G3 Network</div></div>
        <div><h1>${esc(t("Electric trucks, chargers and fleets in one place.", "Xe tải điện, trạm sạc và đội xe trên một nền tảng."))}</h1><p>${esc(t("Live vehicle and battery monitoring, charging, warranty-safe charging rules, support and billing for fleets and for our own teams.", "Giám sát xe và pin theo thời gian thực, sạc điện, quy tắc sạc an toàn cho bảo hành, hỗ trợ và thanh toán cho đội xe và các bộ phận nội bộ."))}</p></div>
        <div class="small" style="color:#6f8396">${esc(t("Prototype with mock data — not connected to the backend.", "Bản mẫu với dữ liệu giả lập — chưa kết nối backend."))}</div></div>
      <div class="login-panel"><div class="login-box">
        <div class="row between mb"><h2>${esc(t("Log in", "Đăng nhập"))}</h2><button class="btn ghost sm" data-login-lang>${G3.lang === "vi" ? "English" : "Tiếng Việt"}</button></div>
        <form class="form" data-login-form>
          <div class="field"><label>${esc(t("Phone number", "Số điện thoại"))}</label><input class="input" name="phone" type="tel" placeholder="09xx xxx xxx" value="${esc(P.tcxFm.user.phone_number)}"></div>
          <div class="field"><label>${esc(t("Password", "Mật khẩu"))}</label><input class="input" name="password" type="password" value="demo-password"></div>
          <div class="row between small"><label class="check"><input type="checkbox" checked> ${esc(t("Keep me logged in", "Ghi nhớ đăng nhập"))}</label><a href="#" data-forgot>${esc(t("Forgot password?", "Quên mật khẩu?"))}</a></div>
          <button class="btn primary" style="justify-content:center">${esc(t("Log in", "Đăng nhập"))}</button>
          <div class="small muted login-error" style="display:none;color:var(--danger)"></div>
        </form>
        <div class="divider"></div>
        <div class="small muted mb">${esc(t("Demo: log in as a persona (one per role)", "Demo: đăng nhập theo vai trò"))}</div>
        <div class="persona-grid">${personaCards}</div>
      </div></div></div>`;
    app.querySelector("[data-login-lang]").addEventListener("click", () => { G3.setLang(G3.lang === "vi" ? "en" : "vi"); renderLogin(); });
    app.querySelectorAll("[data-persona-login]").forEach((b) => b.addEventListener("click", () => {
      const p = PERSONAS.find((x) => x.key === b.dataset.personaLogin);
      enterAs(p.person.user.user_id, p.key === "tcxFm" ? null : p.person.membership.membership_id);
    }));
    let failures = 0;
    app.querySelector("[data-login-form]").addEventListener("submit", (e) => {
      e.preventDefault();
      const phoneValue = e.target.phone.value.replace(/\s/g, "");
      const user = db.users.find((u) => u.phone_number === phoneValue && u.status === "ACTIVE");
      const err = app.querySelector(".login-error");
      if (!user || !e.target.password.value) {
        failures++;
        err.style.display = "block";
        err.textContent = failures >= 5 ? t("Too many attempts: login locked for 15 minutes. You can still reset the password by OTP.", "Sai quá nhiều lần: tạm khóa đăng nhập 15 phút. Bạn vẫn có thể đặt lại mật khẩu bằng OTP.") : t(`Wrong phone number or password (${failures}/5).`, `Sai số điện thoại hoặc mật khẩu (${failures}/5).`);
        return;
      }
      enterAs(user.user_id, null);
    });
    app.querySelector("[data-forgot]").addEventListener("click", (e) => {
      e.preventDefault();
      ui.formModal({
        title: t("Reset password", "Đặt lại mật khẩu"),
        intro: `<p class="muted small">${esc(t("We send a one-time code by SMS to the registered phone (ACC-05, ACC-06). The code expires after 5 minutes and works once.", "Mã OTP được gửi qua SMS đến số đã đăng ký (ACC-05, ACC-06). Mã hết hạn sau 5 phút và chỉ dùng một lần."))}</p>`,
        fields: [{ name: "phone", label: t("Phone number", "Số điện thoại"), type: "tel", required: true }, { name: "otp", label: t("One-time code", "Mã OTP"), placeholder: "6 chữ số", required: true }, { name: "password", label: t("New password", "Mật khẩu mới"), type: "password", required: true }],
        submitLabel: t("Set new password", "Đặt mật khẩu mới"),
        onSubmit() { ui.toast(t("Password changed. Every other session was logged out.", "Đã đổi mật khẩu. Mọi phiên đăng nhập khác đã bị đăng xuất.")); },
      });
    });
  }

  function renderOrgPicker() {
    const userId = store.sget("g3.pendingUser");
    const user = db.user(userId);
    if (!user) { G3.navigate("/login"); return renderLogin(); }
    const last = store.get("g3.lastOrg." + userId, null);
    const options = db.memberships.filter((m) => m.user_id === userId && m.status === "ACTIVE");
    app.innerHTML = `<div class="login-page" style="grid-template-columns:1fr"><div class="login-panel"><div class="login-box" style="width:min(460px,100%)">
      <h2>${esc(t("Choose an organization", "Chọn tổ chức"))}</h2><p class="muted">${esc(t(`${user.full_name}, you belong to ${options.length} organizations. Every screen shows the data of the one you pick.`, `${user.full_name}, bạn thuộc ${options.length} tổ chức. Mọi màn hình hiển thị dữ liệu của tổ chức bạn chọn.`))}</p>
      <div class="stack" style="gap:8px;margin-top:16px">${options.map((m) => `<button class="persona-card" data-pick="${m.membership_id}" style="padding:14px"><b>${esc(db.org(m.organization_id).name)}</b><span>${esc(m.roles.map(G3.roleLabel).join(", "))}${last === m.organization_id ? " · " + esc(t("last used", "dùng gần nhất")) : ""}</span></button>`).join("")}</div>
      <p class="small muted" style="margin-top:14px">ACC-04 · ACC-12</p></div></div></div>`;
    app.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => {
      const m = db.memberships.find((x) => x.membership_id === b.dataset.pick);
      store.set("g3.lastOrg." + userId, m.organization_id);
      store.sset("g3.pendingUser", null);
      enterAs(userId, m.membership_id);
    }));
  }

  /** A customer's organization admin accepts the data processing agreement before anyone uses the system (ACC-17). */
  function legalGate() {
    const org = scope.org();
    if (org.is_internal || !scope.has("ORG_ADMIN")) return;
    const accepted = store.get("g3.dpa", {});
    if (accepted[org.organization_id]) return;
    ui.modal({
      title: t("Data processing agreement", "Thỏa thuận xử lý dữ liệu"),
      wide: true,
      foot: `<button class="btn primary" data-accept disabled>${esc(t("Accept for the organization", "Chấp nhận thay mặt tổ chức"))}</button>`,
      body: `${ui.callout("info", esc(t(`As administrator of ${org.name}, you accept version 2.1 for the company. Members cannot use the system until it is accepted (Decree 13/2023).`, `Là quản trị viên của ${org.name}, bạn chấp nhận phiên bản 2.1 thay mặt công ty. Thành viên chưa thể sử dụng hệ thống cho đến khi được chấp nhận (Nghị định 13/2023).`)))}
        <div class="card mt" style="max-height:220px;overflow:auto;padding:14px 16px;box-shadow:none" class="small"><h4>${esc(t("Data processing agreement — v2.1 (effective 01/09/2026)", "Thỏa thuận xử lý dữ liệu — v2.1 (hiệu lực 01/09/2026)"))}</h4>
        <p class="small muted">${esc(t("1. G3 Network processes vehicle, location, charging and driver data on behalf of the customer to provide the services in the subscribed plan. 2. Purposes: fleet monitoring, battery safety, warranty-compliant charging, billing and support. 3. Location history is kept 12 months hot and 5 years cold. 4. Every access to personal data is logged. 5. Data stays in Vietnam unless the customer agrees otherwise…", "1. G3 Network xử lý dữ liệu xe, vị trí, sạc và tài xế thay mặt khách hàng để cung cấp dịch vụ trong gói đã đăng ký. 2. Mục đích: giám sát đội xe, an toàn pin, sạc tuân thủ bảo hành, thanh toán và hỗ trợ. 3. Lịch sử vị trí lưu 12 tháng nóng và 5 năm lạnh. 4. Mọi truy cập dữ liệu cá nhân đều được ghi nhật ký. 5. Dữ liệu được lưu tại Việt Nam trừ khi khách hàng đồng ý khác…"))}</p></div>
        <label class="check mt"><input type="checkbox" data-agree> ${esc(t("I have read and accept this agreement on behalf of the organization.", "Tôi đã đọc và chấp nhận thỏa thuận này thay mặt tổ chức."))}</label>
        <p class="small muted">${esc(t("Proof kept with the acceptance: exact version, time, IP and device.", "Bằng chứng lưu kèm: phiên bản, thời gian, IP và thiết bị."))}</p>`,
      onMount(el, close) {
        el.querySelector("[data-close]").remove();
        const accept = el.querySelector("[data-accept]");
        el.querySelector("[data-agree]").addEventListener("change", (e) => { accept.disabled = !e.target.checked; });
        accept.addEventListener("click", () => { accepted[org.organization_id] = new Date().toISOString(); store.set("g3.dpa", accepted); close(); ui.toast(t("Agreement accepted", "Đã chấp nhận thỏa thuận")); });
      },
    });
  }

  /* ---------- Render ---------- */
  function matchRoute(path) {
    for (const page of pages) {
      const m = path.match(page.pattern);
      if (m) return { page, params: Object.fromEntries(page.keys.map((k, i) => [k, decodeURIComponent(m[i + 1])])) };
    }
    return null;
  }

  G3.render = function () {
    leaveFns.forEach((fn) => { try { fn(); } catch (e) { console.error(e); } });
    leaveFns = [];
    document.querySelectorAll(".popover").forEach((p) => p.remove());
    const { path, query } = parseHash();
    // Demo links: "#/map?as=tcxFm" logs in as that persona (also used for screenshots).
    if (query.as) {
      const persona = PERSONAS.find((p) => p.key === query.as);
      if (persona) scope.login(persona.person.membership.membership_id);
      if (query.lang) G3.setLang(query.lang);
      if (query.legal === "skip") { const accepted = store.get("g3.dpa", {}); accepted[scope.org().organization_id] = "demo"; store.set("g3.dpa", accepted); }
    }
    if (path === "/login" || !scope.membership()) return renderLogin();
    if (path === "/choose-organization") return renderOrgPicker();

    const match = matchRoute(path);
    renderShell(match && match.page);
    const content = document.getElementById("content");
    const scrollKey = path;
    const keepScroll = content.dataset.path === scrollKey;
    const scrollTop = content.scrollTop;
    content.dataset.path = scrollKey;
    if (!match) {
      content.className = "content";
      content.innerHTML = ui.pageHead({ title: t("Page not found", "Không tìm thấy trang") }) + ui.empty(path);
      return;
    }
    if (!canSee(match.page)) {
      content.className = "content";
      content.innerHTML = ui.pageHead({ title: t(match.page.title[0], match.page.title[1]), features: match.page.features }) + ui.callout("warn", esc(t("Your roles do not include this feature (ACC-14). The menu hides what you cannot use; this page is shown because you opened its link.", "Vai trò của bạn không có tính năng này (ACC-14). Menu ẩn những gì bạn không dùng được; trang này hiện vì bạn mở trực tiếp đường dẫn.")), "lock");
      return;
    }
    const ctx = { params: match.params, query, page: match.page };
    content.className = "content" + (match.page.flush ? " flush" : "");
    try {
      content.innerHTML = match.page.render(ctx);
      if (match.page.mount) match.page.mount(content, ctx);
      ui.mountMaps(content);
    } catch (err) {
      console.error(err);
      content.innerHTML = ui.callout("danger", `<b>${esc(t("This screen failed to render", "Màn hình bị lỗi"))}</b><br><code>${esc(err && err.stack ? err.stack : err)}</code>`);
    }
    if (keepScroll) content.scrollTop = scrollTop;
    document.title = `${t(match.page.title[0], match.page.title[1])} · G3 Network`;
    legalGate();
  };

  /* ---------- Live simulation (stands in for MQTT ingestion and OCPP status) ---------- */
  const TICK_MS = 3000;
  setInterval(() => {
    const now = new Date().toISOString();
    db.vehicles.forEach((v) => {
      const live = v.live;
      if (!live || !live.is_online) return;
      if (live.state === "DRIVING") {
        const road = db.ROADS[live.road_index];
        live.dir = live.dir || 1;
        live.road_f += live.dir * (live.speed_kmh / 60) * 0.0012;
        if (live.road_f > 0.995 || live.road_f < 0.005) live.dir *= -1;
        const p = db.pointOnRoad(road, live.road_f);
        live.lat = p.lat; live.lon = p.lon;
        live.heading = live.dir > 0 ? p.heading : (p.heading + 180) % 360;
        live.speed_kmh = Math.max(18, Math.min(78, live.speed_kmh + (Math.random() - 0.5) * 6));
        live.soc_percent = Math.max(3, Math.round((live.soc_percent - 0.04) * 100) / 100);
        v.odometer_km += live.speed_kmh * (TICK_MS / 3600000);
      } else if (live.state === "CHARGING") {
        live.soc_percent = Math.min(100, Math.round((live.soc_percent + 0.25) * 100) / 100);
      }
      live.received_at = now;
    });
    tickFns.forEach((fn) => { try { fn(); } catch (e) { console.error(e); } });
  }, TICK_MS);

  /* Notification poll (NTF-01: the portal checks about every 10 s); every few polls a new alert arrives. */
  let polls = 0;
  setInterval(() => {
    if (!scope.membership()) return;
    polls++;
    if (polls % 5 === 0) {
      const candidates = db.vehicles.filter((v) => v.live && v.live.is_online && v.live.state === "DRIVING");
      const v = candidates[polls % candidates.length];
      if (v) {
        const templates = [
          ["GEOFENCE_ALERT", "INFO", `${v.license_plate} left Depot Nhơn Trạch`, `Xe ${v.license_plate} rời Depot Nhơn Trạch`],
          ["BATTERY_ALERT", "WARNING", `Battery 20% on ${v.license_plate}`, `Pin 20% trên xe ${v.license_plate}`],
          ["FAULT_ALERT", "WARNING", `Brake pad wear warning on ${v.license_plate}`, `Cảnh báo mòn má phanh xe ${v.license_plate}`],
        ];
        const [type, severity, en, vi] = templates[(polls / 5) % templates.length];
        const n = { notification_id: 88000 + db.notifications.length + 500, notification_type: type, severity, vehicle_id: v.vehicle_id, organization_id: v.organization_id, title: [en, vi], created_at: new Date().toISOString(), read_at: null, payload: {}, station_id: null };
        db.notifications.unshift(n);
        if (scope.vehicleIds().has(v.vehicle_id)) {
          ui.toast(`<b>${esc(G3.label(type))}</b><br>${esc(t(en, vi))}`, severity === "INFO" ? "" : "alert", "bell");
          G3.refreshChrome();
        }
      }
    }
  }, 10000);

  window.addEventListener("hashchange", G3.render);
  G3.start = () => G3.render();
})();
