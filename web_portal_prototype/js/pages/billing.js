/* Billing and sustainability screens:
   - plans       /plans       PAY-01..04, ACC-14: internal catalog, plans and subscriptions; the customer's "My plan"
   - wallets     /wallets     PAY-07, PAY-08: balances, transactions, VietQR top-up, staff adjustments
   - invoices    /invoices    PAY-08, PAY-11, PAY-13, CHG-05: monthly and retail e-invoices, platform revenue
   - promotions  /promotions  PAY-12: campaigns sent only to users with marketing consent
   - carbon      /carbon      CRB-01, CRB-04, CRB-07..11: inventory, intensity, factors, diesel baseline, certificates, MRV
   Price history, campaigns, emission factors, certificates and the diesel
   baseline are private mock data of this file (derived from ids, no Math.random). */

(function () {
  const { ui, db, fmt, scope } = G3;
  const esc = G3.esc;
  const DAY = db.DAY;
  const NOW = db.NOW;

  /* ---------- Shared helpers ---------- */
  function hashStr(text) {
    let h = 2166136261;
    for (const ch of String(text)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  /** Deterministic generator keyed by a string (same key, same numbers). */
  function seeded(key) {
    let a = hashStr(key) || 1;
    return function () {
      a = (a + 0x6d2b79f5) | 0;
      let x = Math.imul(a ^ (a >>> 15), 1 | a);
      x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
      return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
    };
  }
  const frac = (key) => seeded(key)();
  const isoDay = (ms) => new Date(ms).toISOString().slice(0, 10);
  const todayIso = () => isoDay(Date.now());
  const periodOf = (value) => { const d = new Date(value); return `${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`; };
  /** "MM/YYYY" labels, oldest first; the current month is last when includeCurrent. */
  function monthsBack(count, includeCurrent) {
    return Array.from({ length: count }, (_, i) => {
      const d = new Date(NOW);
      d.setDate(1);
      d.setMonth(d.getMonth() - (count - i - (includeCurrent ? 1 : 0)));
      return periodOf(d);
    });
  }
  const shortPeriod = (period) => period.slice(0, 3) + period.slice(-2);
  const daysUntil = (day) => Math.round((new Date(day).getTime() - Date.now()) / DAY);
  const tx = (pair) => (Array.isArray(pair) ? t(pair[0], pair[1]) : pair || "");
  const sum = (rows, fn) => rows.reduce((a, r) => a + (fn(r) || 0), 0);
  const feature = (code) => db.catalog_features.find((f) => f.code === code);
  const trucksOf = (orgId) => db.vehicles.filter((v) => v.organization_id === orgId).length;
  const customerOrgs = () => db.organizations.filter((o) => o.kind === "CUSTOMER");

  /* Labels of values that have no entry in i18n.js. */
  const LOCAL = {
    PUBLIC: ["Public", "Công khai"],
    PRIVATE: ["Private", "Riêng"],
    DEFAULT: ["Default (free)", "Mặc định (miễn phí)"],
    MONTHLY: ["Monthly", "Hàng tháng"],
    QUARTERLY: ["Quarterly", "Hàng quý"],
    YEARLY: ["Yearly", "Hàng năm"],
    TRUCK_MONTH: ["per truck / month", "mỗi xe / tháng"],
    ORGANIZATION_MONTH: ["per organization / month", "mỗi tổ chức / tháng"],
    USER_MONTH: ["per user / month", "mỗi người dùng / tháng"],
    PACK: ["per pack of 1,000 SMS", "mỗi gói 1.000 tin"],
    ENDED: ["Ended", "Đã kết thúc"],
    SCHEDULED: ["Scheduled", "Đã lên lịch"],
    SUPERSEDED: ["Superseded", "Đã thay thế"],
    RETIRED: ["Retired (claimed)", "Đã sử dụng"],
    FINAL: ["Final", "Chốt số"],
    PROVISIONAL: ["Provisional", "Tạm tính"],
    CHARGING_DISCOUNT: ["Charging discount", "Giảm giá sạc"],
    MAINTENANCE_DEAL: ["Maintenance deal", "Ưu đãi bảo dưỡng"],
    TOP_UP_BONUS: ["Top-up bonus", "Thưởng nạp ví"],
    ALL_DRIVERS: ["Every driver", "Mọi tài xế"],
    RETAIL_DRIVERS: ["Public app drivers", "Tài xế dùng app công cộng"],
    FLEET_DECISION_MAKERS: ["Fleet owners & managers", "Chủ & quản lý đội xe"],
    ORGANIZATION: ["One customer", "Một khách hàng"],
    EMAIL: ["E-mail", "E-mail"],
    SMS: ["SMS", "SMS"],
    GRID: ["Grid electricity", "Điện lưới"],
    SOLAR: ["Solar PV", "Điện mặt trời"],
    WIND: ["Wind", "Điện gió"],
    BIOMASS: ["Biomass", "Điện sinh khối"],
    STORAGE: ["Battery storage", "Lưu trữ pin"],
    DIESEL: ["Diesel fuel", "Nhiên liệu diesel"],
  };
  const lab = (value) => (LOCAL[value] ? t(LOCAL[value][0], LOCAL[value][1]) : G3.label(value));
  const pill = (value, tone) => ui.badge(value, tone, lab(value));
  const yes = (title) => `<span style="color:var(--ok)" title="${esc(title || "")}">${ui.icon("check", 16)}</span>`;
  const no = (title) => `<span class="muted" title="${esc(title || "")}">—</span>`;
  const locked = (title) => `<span style="color:var(--danger)" title="${esc(title || "")}">${ui.icon("lock", 15)}</span>`;
  const money = (value) => `<span class="num" style="color:var(${value < 0 ? "--danger" : "--ok"})">${value > 0 ? "+" : ""}${fmt.vnd(value)}</span>`;

  /** Tabs, segmented controls and filter inputs write their value into the URL query. */
  function bindCommon(root) {
    root.querySelectorAll("[data-tabs]").forEach((tabs) => tabs.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => G3.setQuery({ [tabs.dataset.tabs]: b.dataset.tab }))));
    root.querySelectorAll("[data-seg]").forEach((seg) => seg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => G3.setQuery({ [seg.dataset.seg]: b.dataset.value }))));
    root.querySelectorAll("[data-filter]").forEach((el) => el.addEventListener("change", () => G3.setQuery({ [el.dataset.filter]: el.value })));
  }
  /** Buttons with data-action call handlers[action](data-id). Bound per element: the content root is reused across renders. */
  function bindActions(root, handlers) {
    root.querySelectorAll("[data-action]").forEach((el) => {
      const fn = handlers[el.dataset.action];
      if (fn) el.addEventListener("click", (e) => { e.stopPropagation(); fn(el.dataset.id, el); });
    });
  }
  const actBtn = (label, action, id, opts = {}) => ui.btn(label, { ...opts, action, attrs: `data-id="${esc(id || "")}"` });

  /* =====================================================================
     PLANS (PAY-01, PAY-02, PAY-03, PAY-04, ACC-14)
     ===================================================================== */

  /* Price versions per catalog feature (PAY-01): a change is a new row with its effective date. */
  const PRICE_HISTORY = {};
  function priceVersions(f) {
    if (!PRICE_HISTORY[f.code]) {
      const r = seeded("price-" + f.code);
      const launch = Math.round((f.list_price_vnd * (0.78 + r() * 0.12)) / 10000) * 10000;
      PRICE_HISTORY[f.code] = [
        { price_vnd: launch, effective_from: isoDay(NOW - Math.round(380 + r() * 40) * DAY), reason: ["Launch price list", "Bảng giá ra mắt"], user_id: db.people.sales2.user.user_id },
        { price_vnd: f.list_price_vnd, effective_from: isoDay(NOW - Math.round(90 + r() * 120) * DAY), reason: ["Annual price review", "Rà soát giá hàng năm"], user_id: db.people.sales.user.user_id },
      ];
    }
    return PRICE_HISTORY[f.code];
  }
  const currentVersion = (f) => { const past = priceVersions(f).filter((v) => v.effective_from <= todayIso()); return past[past.length - 1]; };
  const scheduledVersion = (f) => priceVersions(f).find((v) => v.effective_from > todayIso());
  function versionState(f, v) {
    if (v.effective_from > todayIso()) return "SCHEDULED";
    return v === currentVersion(f) ? "ACTIVE" : "SUPERSEDED";
  }

  /* Who may use each sellable feature by role (the role half of ACC-14), and the screen it opens. */
  const FEATURE_ROLES = {
    MON: ["ORG_ADMIN", "FLEET_MANAGER", "DISPATCHER"],
    FLT: ["ORG_ADMIN", "FLEET_MANAGER", "DISPATCHER"],
    POL: ["ORG_ADMIN", "FLEET_MANAGER"],
    SAF: ["ORG_ADMIN", "FLEET_MANAGER"],
    ADAS: ["ORG_ADMIN", "FLEET_MANAGER"],
    CRB: ["ORG_ADMIN", "FLEET_MANAGER"],
    SMS: ["ORG_ADMIN", "FLEET_MANAGER", "DISPATCHER", "ACCOUNTANT"],
  };
  const FEATURE_SCREEN = { MON: "battery-health", FLT: "live-map", POL: "policy", SAF: "safety", ADAS: "safety", CRB: "carbon", SMS: "notifications" };
  const ADD_ON_CODES = ["ADAS", "SMS"];

  /* Upgrade requests sent by customers from "My plan"; shown to sales on the subscriptions tab. */
  const UPGRADE_REQUESTS = [];

  function planListValue(plan) {
    return plan.features.reduce((a, code) => { const f = feature(code); return a + (f && f.unit === "TRUCK_MONTH" ? f.list_price_vnd : 0); }, 0);
  }
  function addOnMonthly(sub) {
    return (sub.add_ons || []).reduce((a, x) => { const f = feature(x.code); return a + (x.agreed_price_vnd ?? (f ? f.list_price_vnd : 0)) * x.quantity; }, 0);
  }
  const subscriptionMonthly = (sub) => trucksOf(sub.organization_id) * sub.agreed_price_vnd_truck_month + addOnMonthly(sub);
  const planOf = (sub) => db.find("plans", sub.plan_id);
  function planFeatures(sub) {
    const codes = new Set(planOf(sub).features);
    (sub.add_ons || []).forEach((a) => codes.add(a.code));
    return codes;
  }
  function addOnTags(sub) {
    if (!(sub.add_ons || []).length) return '<span class="muted">—</span>';
    return sub.add_ons.map((a) => {
      if (a.code === "SMS") {
        const pct = a.quota ? (a.used / a.quota) * 100 : 0;
        return `<div style="min-width:140px"><div class="row between small"><span>SMS</span><span class="num">${fmt.num(a.used)}/${fmt.num(a.quota)}</span></div>${ui.meter(pct, pct > 85 ? "danger" : pct > 65 ? "warn" : "")}</div>`;
      }
      return ui.tag(`${a.code} × ${a.quantity}`);
    }).join("");
  }
  function renewalCell(sub) {
    if (!sub.ends_on) return `<span class="muted">${esc(t("Open-ended", "Không thời hạn"))}</span>`;
    const left = daysUntil(sub.ends_on);
    const due = left <= 30 ? `<div class="row" style="gap:4px;margin-top:3px">${ui.badge("WARNING", "warn", t(`Expires in ${left} d`, `Còn ${left} ngày`))}<span class="tag" title="${esc(t("Reminders go to the organization admin and the account manager 30, 7 and 1 day before expiry.", "Nhắc gia hạn gửi quản trị tổ chức và người phụ trách 30, 7 và 1 ngày trước khi hết hạn."))}">${esc(t("Reminder sent", "Đã gửi nhắc"))}</span></div>` : `<span class="sub">${esc(t(`${left} days left`, `còn ${left} ngày`))}</span>`;
    return `${fmt.date(sub.starts_on)} → ${fmt.date(sub.ends_on)}${due}`;
  }

  function plansInternal(query) {
    const tab = query.tab || "catalog";
    const subs = scope.filter(db.subscriptions);
    const mrr = sum(subs, subscriptionMonthly);
    const trucks = sum(subs, (s) => trucksOf(s.organization_id));
    const overdue = subs.filter((s) => s.status === "OVERDUE");
    const expiring = subs.filter((s) => s.ends_on && daysUntil(s.ends_on) <= 30 && daysUntil(s.ends_on) >= 0);
    const scheduled = db.catalog_features.filter((f) => scheduledVersion(f)).length;
    const canSell = scope.has("SALES", "HEAD_ADMIN");
    let body;
    if (tab === "plans") body = plansTab(canSell);
    else if (tab === "subscriptions") body = subscriptionsTab(subs, canSell);
    else body = catalogTab(query, canSell);
    return `
      ${ui.pageHead({
        title: t("Plans & subscriptions", "Gói dịch vụ & thuê bao"),
        sub: esc(t("What we sell: catalog features with list prices, plans that bundle them, and each customer's subscription.", "Những gì chúng ta bán: tính năng kèm giá niêm yết, gói dịch vụ gộp các tính năng và thuê bao của từng khách hàng.")),
        features: ["PAY-01", "PAY-02", "PAY-03", "PAY-04"],
      })}
      <div class="grid cols-5">
        ${ui.kpi({ label: t("Sellable features", "Tính năng bán được"), icon: "tag", value: db.catalog_features.length, foot: scheduled ? `${scheduled} ${t("price change scheduled", "thay đổi giá đã lên lịch")}` : t("No scheduled price change", "Không có thay đổi giá sắp tới") })}
        ${ui.kpi({ label: t("Plans", "Gói dịch vụ"), icon: "layers", value: db.plans.length, foot: ["PUBLIC", "PRIVATE", "DEFAULT"].map((v) => `${db.plans.filter((p) => p.visibility === v).length} ${lab(v).toLowerCase()}`).join(" · ") })}
        ${ui.kpi({ label: t("Subscriptions", "Thuê bao"), icon: "building", value: `${subs.filter((s) => s.status === "ACTIVE").length}<small>/ ${subs.length}</small>`, foot: `${fmt.num(trucks)} ${t("trucks billed", "xe tính phí")}` })}
        ${ui.kpi({ label: t("Monthly recurring revenue", "Doanh thu định kỳ/tháng"), icon: "receipt", value: fmt.vndShort(mrr), foot: t("excl. VAT, incl. add-ons", "chưa VAT, gồm tiện ích") })}
        ${ui.kpi({ label: t("Overdue", "Quá hạn"), icon: "alert", value: overdue.length, tone: overdue.length ? "danger" : "ok", foot: `${expiring.length} ${t("expiring within 30 d", "hết hạn trong 30 ngày")}` })}
      </div>
      <div class="mt">${ui.tabs([["catalog", t("Feature catalog & prices", "Danh mục & giá niêm yết")], ["plans", t("Plans", "Gói dịch vụ")], ["subscriptions", t("Customer subscriptions", "Thuê bao khách hàng")]], tab)}</div>
      ${body}`;
  }

  function catalogTab(query, canSell) {
    const features = db.catalog_features;
    const selected = feature(query.feature) || features[0];
    const table = ui.table({
      rows: features,
      selected: (f) => f.code === selected.code,
      rowAttrs: (f) => `data-pick-feature="${esc(f.code)}" style="cursor:pointer"`,
      columns: [
        { label: t("Code", "Mã"), render: (f) => `<b class="mono">${esc(f.code)}</b>` },
        { label: t("Feature", "Tính năng"), render: (f) => `<b>${esc(tx(f.name))}</b><span class="sub">${esc(lab(f.unit))}</span>` },
        { label: t("List price", "Giá niêm yết"), align: "right", render: (f) => `<b>${fmt.vnd(currentVersion(f).price_vnd)}</b>` },
        { label: t("Since", "Từ ngày"), render: (f) => fmt.date(currentVersion(f).effective_from) },
        { label: t("Next change", "Thay đổi kế tiếp"), render: (f) => { const s = scheduledVersion(f); return s ? `${pill("SCHEDULED", "info")}<span class="sub">${fmt.vnd(s.price_vnd)} · ${fmt.date(s.effective_from)}</span>` : '<span class="muted">—</span>'; } },
        { label: t("In plans", "Có trong gói"), render: (f) => { const names = db.plans.filter((p) => p.features.includes(f.code)).map((p) => ui.tag(p.name)); return names.length ? `<div class="row" style="gap:4px">${names.join("")}</div>` : ADD_ON_CODES.includes(f.code) ? `<span class="tag">${esc(t("Add-on only", "Chỉ bán thêm"))}</span>` : "—"; } },
        { label: "", render: (f) => (canSell ? actBtn(t("Change price", "Đổi giá"), "change-price", f.code, { sm: true, icon: "edit" }) : "") },
      ],
    });
    const versions = priceVersions(selected).slice().reverse();
    const history = `<ul class="timeline">${versions.map((v) => `<li><div class="row between"><b class="num">${fmt.vnd(v.price_vnd)}</b>${pill(versionState(selected, v), versionState(selected, v) === "SCHEDULED" ? "info" : versionState(selected, v) === "ACTIVE" ? "ok" : "muted")}</div><div class="when">${esc(t("Effective", "Hiệu lực"))} ${fmt.date(v.effective_from)} · ${ui.userName(v.user_id)}</div><div class="small muted">${esc(tx(v.reason))}</div></li>`).join("")}</ul>`;
    const prices = priceVersions(selected).filter((v) => v.effective_from <= todayIso());
    return `<div class="grid side">
      ${ui.card({ title: t("Catalog features", "Danh mục tính năng"), sub: "PAY-01", flush: true, body: table })}
      <div class="stack">
        ${ui.card({
          title: `${t("Price history", "Lịch sử giá")} · ${selected.code}`, sub: esc(tx(selected.name)),
          actions: canSell ? actBtn(t("Change price", "Đổi giá"), "change-price", selected.code, { sm: true, kind: "primary" }) : "",
          body: `${history}${prices.length > 1 ? `<div class="divider"></div><div class="small muted">${esc(t("Change since launch", "Thay đổi so với giá ra mắt"))}: <b>${fmt.pct(((prices[prices.length - 1].price_vnd - prices[0].price_vnd) / prices[0].price_vnd) * 100, 1)}</b></div>` : ""}`,
        })}
        ${ui.callout("info", esc(t("A new list price never changes running contracts: the agreed price is copied onto each subscription when it is signed (PAY-03).", "Giá niêm yết mới không làm thay đổi hợp đồng đang chạy: giá thỏa thuận được sao chép vào từng thuê bao khi ký (PAY-03).")))}
      </div>
    </div>`;
  }

  function plansTab(canSell) {
    const plans = db.plans;
    const cards = plans.map((p) => {
      const list = planListValue(p);
      const discount = list && p.price_vnd_truck_month ? (1 - p.price_vnd_truck_month / list) * 100 : 0;
      const subscribers = db.subscriptions.filter((s) => s.plan_id === p.plan_id);
      return `<div class="card kpi">
        <div class="row between"><b style="font-size:15px">${esc(p.name)}</b>${pill(p.visibility, p.visibility === "PUBLIC" ? "info" : p.visibility === "PRIVATE" ? "violet" : "muted")}</div>
        <div class="value">${p.price_vnd_truck_month ? fmt.num(p.price_vnd_truck_month) : t("Free", "Miễn phí")}${p.price_vnd_truck_month ? `<small>₫ ${esc(lab("TRUCK_MONTH"))}</small>` : ""}</div>
        <div class="foot">${list ? `${esc(t("List value", "Giá trị niêm yết"))} ${fmt.vnd(list)}${discount > 0 ? ` · <span class="trend-up">−${fmt.pct(discount)}</span>` : ""}` : esc(t("No per-truck price", "Không tính theo xe"))}</div>
        <div class="small muted" style="margin-top:8px;min-height:38px">${esc(tx(p.description))}${p.private_organization_id ? ` · <b>${ui.orgName(p.private_organization_id)}</b>` : ""}</div>
        <div class="row" style="gap:4px;margin-top:8px">${p.features.map((c) => ui.tag(c)).join("")}</div>
        <div class="divider" style="margin:10px 0"></div>
        <div class="small">${ui.icon("building", 13)} ${subscribers.length} ${esc(t("subscribers", "khách hàng"))} · ${fmt.num(sum(subscribers, (s) => trucksOf(s.organization_id)))} ${esc(t("trucks", "xe"))}</div>
      </div>`;
    }).join("");
    const matrix = ui.table({
      rows: db.catalog_features,
      columns: [
        { label: t("Feature", "Tính năng"), render: (f) => `<b>${esc(tx(f.name))}</b><span class="sub mono">${esc(f.code)} · ${fmt.vnd(currentVersion(f).price_vnd)} ${esc(lab(f.unit))}</span>` },
        ...plans.map((p) => ({ label: p.name, render: (f) => (p.features.includes(f.code) ? yes(t("Included", "Có trong gói")) : ADD_ON_CODES.includes(f.code) ? `<span class="tag">${esc(t("add-on", "mua thêm"))}</span>` : no()) })),
      ],
    });
    return `<div class="grid cols-4">${cards}</div>
      <div class="mt">${ui.card({ title: t("Features included per plan", "Tính năng theo từng gói"), sub: "PAY-02", flush: true, actions: canSell ? ui.btn(t("New plan", "Tạo gói"), { icon: "plus", kind: "primary", sm: true, action: "new-plan" }) : "", body: matrix })}</div>`;
  }

  function subscriptionsTab(subs, canSell) {
    const canLock = scope.has("SALES", "ACCOUNTANT", "HEAD_ADMIN");
    const overdueOpen = subs.filter((s) => s.status === "OVERDUE" && !s.features_locked);
    const requests = UPGRADE_REQUESTS.filter((r) => scope.orgIds().includes(r.organization_id));
    const table = ui.table({
      rows: subs,
      empty: t("No subscription in this scope", "Không có thuê bao trong phạm vi này"),
      columns: [
        { label: t("Organization", "Tổ chức"), render: (s) => `<b>${ui.orgName(s.organization_id)}</b><span class="sub">${esc(t("Account manager", "Phụ trách"))}: ${ui.userName((db.org(s.organization_id) || {}).account_manager_user_id)}</span>` },
        { label: t("Plan · agreed price", "Gói · giá thỏa thuận"), render: (s) => { const p = planOf(s); const list = planListValue(p); return `<b>${esc(p.name)}</b> ${pill(p.visibility, p.visibility === "PRIVATE" ? "violet" : p.visibility === "PUBLIC" ? "info" : "muted")}<span class="sub">${fmt.vnd(s.agreed_price_vnd_truck_month)} ${esc(lab("TRUCK_MONTH"))}${list && s.agreed_price_vnd_truck_month < p.price_vnd_truck_month ? ` · −${fmt.pct((1 - s.agreed_price_vnd_truck_month / p.price_vnd_truck_month) * 100)} ${esc(t("vs plan", "so với gói"))}` : ""} · ${esc(lab(s.billing_cycle))}</span>`; } },
        { label: t("Trucks", "Xe"), align: "right", render: (s) => fmt.num(trucksOf(s.organization_id)) },
        { label: t("Per month", "Mỗi tháng"), align: "right", render: (s) => `<b>${fmt.vnd(subscriptionMonthly(s))}</b>${addOnMonthly(s) ? `<span class="sub">${esc(t("add-ons", "tiện ích"))} ${fmt.vnd(addOnMonthly(s))}</span>` : ""}` },
        { label: t("Period · renewal", "Thời hạn · gia hạn"), render: renewalCell },
        { label: t("Add-ons", "Tiện ích"), render: addOnTags },
        { label: t("Status", "Trạng thái"), render: (s) => `${ui.badge(s.status)}${s.features_locked ? `<div style="margin-top:3px" title="${esc(s.status_reason || "")}">${ui.badge("LOCKED", "danger", t("Features locked", "Đã khóa tính năng"))}</div>` : ""}` },
        { label: "", render: (s) => `<div class="row" style="gap:4px;flex-wrap:nowrap">${canSell ? actBtn(t("Add-ons", "Tiện ích"), "edit-addons", s.subscription_id, { sm: true }) + actBtn(t("Change plan", "Đổi gói"), "change-plan", s.subscription_id, { sm: true }) : ""}${canLock ? (s.features_locked ? actBtn(t("Unlock", "Mở khóa"), "unlock-sub", s.subscription_id, { sm: true, icon: "unlock" }) : actBtn(t("Lock", "Khóa"), "lock-sub", s.subscription_id, { sm: true, kind: "danger", icon: "lock" })) : ""}</div>` },
      ],
    });
    const requestCard = requests.length ? ui.card({
      title: t("Upgrade requests from customers", "Yêu cầu nâng cấp từ khách hàng"), sub: `${requests.length}`, flush: true,
      body: `<ul class="list">${requests.map((r) => `<li><div class="sev-icon INFO">${ui.icon("send", 15)}</div><div class="grow"><div class="title">${ui.orgName(r.organization_id)} → ${esc(r.plan_name)}</div><div class="meta">${ui.userName(r.user_id)} · ${fmt.ago(r.created_at)}${r.note ? " · " + esc(r.note) : ""}</div></div>${ui.badge("PENDING")}</li>`).join("")}</ul>`,
    }) + '<div class="mt"></div>' : "";
    return `${overdueOpen.length ? ui.callout("warn", `<b>${esc(t("Overdue subscriptions", "Thuê bao quá hạn"))}:</b> ${overdueOpen.map((s) => ui.orgName(s.organization_id)).join(", ")}. ${esc(t("Lock the plan's features until the invoice is paid; charging and the wallet keep working.", "Khóa tính năng của gói cho đến khi hóa đơn được thanh toán; sạc và ví vẫn hoạt động."))}`) + '<div class="mt"></div>' : ""}
      ${requestCard}
      ${ui.card({ title: t("Subscriptions of every customer", "Thuê bao của mọi khách hàng"), sub: "PAY-03 · PAY-04", flush: true, actions: canSell ? ui.btn(t("New subscription", "Tạo thuê bao"), { icon: "plus", kind: "primary", sm: true, action: "new-sub" }) : "", body: table })}`;
  }

  function myPlan() {
    const org = scope.org();
    const sub = db.subscriptions.find((s) => s.organization_id === org.organization_id);
    const head = ui.pageHead({
      title: t("My plan", "Gói dịch vụ của tôi"), sub: esc(org.name), features: ["PAY-03", "PAY-04", "ACC-14"],
      actions: `${G3.canSee("invoices") ? ui.btn(t("Invoices", "Hóa đơn"), { href: "#/invoices", icon: "receipt" }) : ""}${ui.btn(t("Request an upgrade", "Yêu cầu nâng cấp"), { kind: "primary", icon: "send", action: "upgrade" })}`,
    });
    if (!sub) return head + ui.card({ body: ui.empty(t("Your organization has no subscription yet. Contact G3 Network sales.", "Tổ chức của bạn chưa có thuê bao. Vui lòng liên hệ bộ phận kinh doanh G3 Network.")) });
    const plan = planOf(sub);
    const included = planFeatures(sub);
    const trucks = db.vehicles.filter((v) => v.organization_id === org.organization_id);
    const monthly = subscriptionMonthly(sub);
    const manager = db.user(org.account_manager_user_id);
    const nextInvoice = new Date(NOW); nextInvoice.setDate(1); nextInvoice.setMonth(nextInvoice.getMonth() + 1);
    const roles = scope.roles();
    const featureRows = ui.table({
      rows: db.catalog_features,
      columns: [
        { label: t("Feature", "Tính năng"), render: (f) => `<b>${esc(tx(f.name))}</b><span class="sub mono">${esc(f.code)}</span>` },
        { label: t("In the plan", "Trong gói"), render: (f) => (sub.features_locked && included.has(f.code) ? locked(t("Locked: invoice overdue", "Bị khóa: hóa đơn quá hạn")) : included.has(f.code) ? yes() : ADD_ON_CODES.includes(f.code) ? `<span class="tag">${esc(t("add-on", "mua thêm"))}</span>` : no()) },
        { label: t("In your roles", "Trong vai trò của bạn"), render: (f) => ((FEATURE_ROLES[f.code] || []).some((r) => roles.includes(r)) ? yes() : no(t("Not part of your roles", "Không thuộc vai trò của bạn"))) },
        { label: t("Available to you", "Bạn được dùng"), render: (f) => { const ok = included.has(f.code) && !sub.features_locked && (FEATURE_ROLES[f.code] || []).some((r) => roles.includes(r)); return ok ? ui.badge("ACTIVE", "ok", t("Available", "Được dùng")) : ui.badge("LOCKED", "muted", t("Not available", "Không có")); } },
        { label: "", render: (f) => { const id = FEATURE_SCREEN[f.code]; const p = G3.pages.find((x) => x.id === id); return p && G3.canSee(id) && included.has(f.code) && !sub.features_locked ? `<a href="#${p.path}">${esc(t("Open", "Mở"))} →</a>` : ""; } },
      ],
    });
    const sms = (sub.add_ons || []).find((a) => a.code === "SMS");
    const usage = sms
      ? `<div class="row between"><b>${esc(t("SMS alerts", "SMS cảnh báo"))}</b><span class="num">${fmt.num(sms.used)} / ${fmt.num(sms.quota)}</span></div>${ui.meter((sms.used / sms.quota) * 100, sms.used / sms.quota > 0.85 ? "danger" : sms.used / sms.quota > 0.65 ? "warn" : "")}<div class="small muted" style="margin-top:6px">${esc(t(`${sms.quantity} packs of 1,000 messages · resets on ${fmt.date(nextInvoice)} · at 100% alerts continue in the app and by e-mail only`, `${sms.quantity} gói 1.000 tin · làm mới ngày ${fmt.date(nextInvoice)} · khi hết hạn mức, cảnh báo chỉ gửi qua app và e-mail`))}</div>`
      : `<div class="muted small">${esc(t("No add-on yet. The in-cab ADAS package and SMS alert packs can be added to any plan.", "Chưa có tiện ích. Gói ADAS trong cabin và gói SMS cảnh báo có thể thêm vào mọi gói."))}</div>`;
    const comparePlans = db.plans.filter((p) => p.plan_id === plan.plan_id || p.visibility === "PUBLIC");
    const compare = ui.table({
      rows: db.catalog_features.filter((f) => !ADD_ON_CODES.includes(f.code) || comparePlans.some((p) => p.features.includes(f.code))),
      columns: [
        { label: t("Feature", "Tính năng"), render: (f) => esc(tx(f.name)) },
        ...comparePlans.map((p) => ({ label: `${p.name}${p.plan_id === plan.plan_id ? " ★" : ""}`, render: (f) => (p.features.includes(f.code) ? yes() : no()) })),
      ],
    });
    return `${head}
      ${sub.features_locked ? ui.callout("danger", `<b>${esc(t("Plan features are locked", "Tính năng của gói đang bị khóa"))}</b> — ${esc(sub.status_reason || "")}. ${esc(t("Pay the overdue invoice to unlock them; charging and the wallet keep working.", "Thanh toán hóa đơn quá hạn để mở khóa; sạc và ví vẫn hoạt động."))} ${G3.canSee("invoices") ? `<a href="#/invoices?status=OVERDUE">${esc(t("Open invoices", "Mở hóa đơn"))} →</a>` : ""}`, "lock") + '<div class="mt"></div>' : sub.status === "OVERDUE" ? ui.callout("warn", esc(t("An invoice is overdue. Features may be locked if it stays unpaid.", "Có hóa đơn quá hạn. Tính năng có thể bị khóa nếu chưa thanh toán."))) + '<div class="mt"></div>' : ""}
      <div class="grid cols-4">
        ${ui.kpi({ label: t("Plan", "Gói"), icon: "layers", value: esc(plan.name), foot: `${lab(plan.visibility)} · ${t("since", "từ")} ${fmt.date(sub.starts_on)}` })}
        ${ui.kpi({ label: t("Trucks billed", "Xe tính phí"), icon: "truck", value: trucks.length, foot: `${trucks.filter((v) => v.status === "ACTIVE").length} ${t("active", "hoạt động")} · ${trucks.filter((v) => v.status !== "ACTIVE").length} ${t("inactive", "ngừng")}` })}
        ${ui.kpi({ label: t("Monthly cost", "Chi phí hàng tháng"), icon: "receipt", value: fmt.vndShort(monthly), foot: `${t("excl. VAT 8%", "chưa gồm VAT 8%")} · ${fmt.vndShort(monthly * 1.08)} ${t("incl.", "gồm VAT")}` })}
        ${ui.kpi({ label: t("Renewal", "Gia hạn"), icon: "clock", value: sub.ends_on ? fmt.date(sub.ends_on) : t("Open-ended", "Không thời hạn"), tone: sub.ends_on && daysUntil(sub.ends_on) <= 30 ? "warn" : undefined, foot: sub.ends_on ? `${daysUntil(sub.ends_on)} ${t("days left", "ngày còn lại")}${daysUntil(sub.ends_on) <= 30 ? " · " + t("reminder sent", "đã gửi nhắc") : ""}` : "" })}
      </div>
      <div class="grid side mt">
        ${ui.card({ title: t("Features: included and locked", "Tính năng: có và bị khóa"), sub: t("you can use a feature when it is in your roles and in the plan (ACC-14)", "bạn dùng được khi tính năng có trong vai trò và trong gói (ACC-14)"), flush: true, body: featureRows })}
        <div class="stack">
          ${ui.card({
            title: t("Billing", "Thanh toán"),
            body: ui.kv([
              [t("Agreed price", "Giá thỏa thuận"), `${fmt.vnd(sub.agreed_price_vnd_truck_month)} <span class="muted small">${esc(lab("TRUCK_MONTH"))}</span>`],
              plan.price_vnd_truck_month && sub.agreed_price_vnd_truck_month !== plan.price_vnd_truck_month ? [t("Plan price", "Giá gói"), `<s class="muted">${fmt.vnd(plan.price_vnd_truck_month)}</s>`] : null,
              [t("Subscription", "Thuê bao"), `${trucks.length} × ${fmt.vnd(sub.agreed_price_vnd_truck_month)} = <b>${fmt.vnd(trucks.length * sub.agreed_price_vnd_truck_month)}</b>`],
              [t("Add-ons", "Tiện ích"), addOnMonthly(sub) ? fmt.vnd(addOnMonthly(sub)) : "—"],
              [t("Billing cycle", "Chu kỳ"), esc(lab(sub.billing_cycle))],
              [t("Period", "Thời hạn"), `${fmt.date(sub.starts_on)} → ${sub.ends_on ? fmt.date(sub.ends_on) : "…"}`],
              [t("Next invoice", "Hóa đơn kế tiếp"), fmt.date(nextInvoice)],
              [t("Account manager", "Người phụ trách"), manager ? `${esc(manager.full_name)}<span class="small muted"> · ${esc(manager.email || manager.phone_number)}</span>` : "—"],
            ]),
          })}
          ${ui.card({ title: t("Add-on usage", "Mức dùng tiện ích"), sub: "PAY-04", body: usage })}
        </div>
      </div>
      <div class="mt">${ui.card({ title: t("Compare plans", "So sánh các gói"), sub: t("★ your plan", "★ gói hiện tại"), flush: true, body: compare })}</div>`;
  }

  /* ---- plan actions ---- */
  function changePriceForm(code) {
    const f = feature(code);
    const current = currentVersion(f);
    ui.formModal({
      title: `${t("Change list price", "Đổi giá niêm yết")} · ${f.code}`,
      intro: `<p class="small muted">${esc(tx(f.name))} · ${esc(t("current", "hiện tại"))} <b>${fmt.vnd(current.price_vnd)}</b> ${esc(lab(f.unit))}. ${esc(t("Existing subscriptions keep their agreed price.", "Thuê bao hiện có giữ nguyên giá thỏa thuận."))}</p>`,
      fields: [
        { name: "price", label: t("New list price (₫)", "Giá niêm yết mới (₫)"), type: "number", required: true, value: current.price_vnd, attrs: 'min="1000" step="1000"' },
        { name: "effective_from", label: t("Effective from", "Hiệu lực từ"), type: "date", required: true, value: isoDay(Date.now() + 30 * DAY), hint: t("Today or later; a future date schedules the change.", "Hôm nay hoặc sau đó; ngày tương lai sẽ lên lịch thay đổi.") },
        { name: "reason", label: t("Reason", "Lý do"), type: "textarea", required: true, full: true },
      ],
      submitLabel: t("Save price", "Lưu giá"),
      onSubmit(values) {
        const price = Number(values.price);
        if (!Number.isInteger(price) || price <= 0) return t("The price must be a whole number of đồng above 0.", "Giá phải là số đồng nguyên lớn hơn 0.");
        if (values.effective_from < todayIso()) return t("The effective date cannot be in the past.", "Ngày hiệu lực không được ở quá khứ.");
        const versions = priceVersions(f);
        if (versions.some((v) => v.effective_from === values.effective_from)) return t("A price already starts on that date.", "Đã có giá bắt đầu vào ngày này.");
        versions.push({ price_vnd: price, effective_from: values.effective_from, reason: values.reason.slice(0, 200), user_id: scope.user().user_id });
        versions.sort((a, b) => (a.effective_from < b.effective_from ? -1 : 1));
        if (values.effective_from === todayIso()) f.list_price_vnd = price;
        G3.render();
        ui.toast(values.effective_from === todayIso() ? t(`${f.code} now costs ${fmt.vnd(price)}`, `${f.code} có giá mới ${fmt.vnd(price)}`) : t(`Price change scheduled for ${fmt.date(values.effective_from)}`, `Đã lên lịch đổi giá từ ${fmt.date(values.effective_from)}`));
      },
    });
  }

  function newPlanForm() {
    ui.formModal({
      title: t("New plan", "Tạo gói dịch vụ"), wide: true,
      fields: [
        { name: "name", label: t("Plan name", "Tên gói"), required: true, placeholder: "Pro 2027" },
        { name: "visibility", label: t("Visibility", "Phạm vi"), type: "select", options: ["PUBLIC", "PRIVATE", "DEFAULT"].map((v) => [v, lab(v)]), value: "PUBLIC" },
        { name: "organization_id", label: t("Customer (private plan)", "Khách hàng (gói riêng)"), type: "select", options: [["", "—"], ...customerOrgs().map((o) => [o.organization_id, o.short_name])], hint: t("Only for a private plan.", "Chỉ dùng cho gói riêng.") },
        { name: "price", label: t("Price per truck per month (₫)", "Giá mỗi xe mỗi tháng (₫)"), type: "number", required: true, value: 990000, attrs: 'min="0" step="1000"' },
        { name: "features", label: t("Features included", "Tính năng trong gói"), type: "checkboxes", full: true, options: db.catalog_features.map((f) => [f.code, `${f.code} · ${tx(f.name)}`]), value: ["MON", "FLT"] },
        { name: "description", label: t("Description", "Mô tả"), type: "textarea", full: true },
      ],
      submitLabel: t("Create plan", "Tạo gói"),
      onSubmit(values) {
        const price = Number(values.price);
        if (db.plans.some((p) => p.name.toLowerCase() === values.name.toLowerCase())) return t("A plan with this name already exists.", "Đã có gói trùng tên.");
        if (!Number.isFinite(price) || price < 0) return t("The price cannot be negative.", "Giá không được âm.");
        if (!values.features.length) return t("Pick at least one feature.", "Chọn ít nhất một tính năng.");
        if (values.visibility === "PRIVATE" && !values.organization_id) return t("A private plan needs its customer.", "Gói riêng cần chọn khách hàng.");
        if (values.visibility === "DEFAULT" && db.plans.some((p) => p.visibility === "DEFAULT")) return t("There is already a default plan; only one is allowed.", "Đã có gói mặc định; chỉ được có một gói.");
        if (values.visibility === "DEFAULT" && price !== 0) return t("The default plan is free.", "Gói mặc định là miễn phí.");
        db.plans.push({ plan_id: db.uid("plan"), name: values.name, visibility: values.visibility, private_organization_id: values.visibility === "PRIVATE" ? values.organization_id : undefined, features: values.features, price_vnd_truck_month: price, description: [values.description || "—", values.description || "—"] });
        G3.render();
        ui.toast(t(`Plan ${values.name} created`, `Đã tạo gói ${values.name}`));
      },
    });
  }

  function newSubscriptionForm() {
    const free = customerOrgs().filter((o) => !db.subscriptions.some((s) => s.organization_id === o.organization_id));
    if (!free.length) { ui.toast(t("Every customer already has a subscription. Use “Change plan” on its row.", "Mọi khách hàng đã có thuê bao. Dùng “Đổi gói” trên dòng tương ứng."), "", "info"); return; }
    ui.formModal({
      title: t("New subscription", "Tạo thuê bao"), wide: true,
      fields: [
        { name: "organization_id", label: t("Customer", "Khách hàng"), type: "select", options: free.map((o) => [o.organization_id, o.short_name]) },
        { name: "plan_id", label: t("Plan", "Gói"), type: "select", options: db.plans.map((p) => [p.plan_id, `${p.name} · ${fmt.vnd(p.price_vnd_truck_month)}`]) },
        { name: "price", label: t("Agreed price per truck/month (₫)", "Giá thỏa thuận mỗi xe/tháng (₫)"), type: "number", hint: t("Empty = the plan price.", "Để trống = giá của gói."), attrs: 'min="0" step="1000"' },
        { name: "cycle", label: t("Billing cycle", "Chu kỳ"), type: "select", options: ["MONTHLY", "QUARTERLY", "YEARLY"].map((v) => [v, lab(v)]) },
        { name: "starts_on", label: t("Starts on", "Bắt đầu"), type: "date", required: true, value: todayIso() },
        { name: "ends_on", label: t("Ends on", "Kết thúc"), type: "date", required: true, value: isoDay(Date.now() + 365 * DAY) },
      ],
      submitLabel: t("Create subscription", "Tạo thuê bao"),
      onSubmit(values) {
        if (values.ends_on <= values.starts_on) return t("The end date must be after the start date.", "Ngày kết thúc phải sau ngày bắt đầu.");
        const plan = db.find("plans", values.plan_id);
        db.subscriptions.push({ subscription_id: db.uid("sub"), organization_id: values.organization_id, plan_id: plan.plan_id, status: "ACTIVE", agreed_price_vnd_truck_month: values.price === "" ? plan.price_vnd_truck_month : Number(values.price), billing_cycle: values.cycle, starts_on: values.starts_on, ends_on: values.ends_on, add_ons: [] });
        G3.render();
        ui.toast(t("Subscription created; the agreed price is fixed for the contract", "Đã tạo thuê bao; giá thỏa thuận cố định theo hợp đồng"));
      },
    });
  }

  function changePlanForm(id) {
    const sub = db.find("subscriptions", id);
    ui.formModal({
      title: `${t("Change plan", "Đổi gói")} · ${db.org(sub.organization_id).short_name}`,
      fields: [
        { name: "plan_id", label: t("Plan", "Gói"), type: "select", value: sub.plan_id, options: db.plans.filter((p) => p.visibility !== "PRIVATE" || p.private_organization_id === sub.organization_id).map((p) => [p.plan_id, `${p.name} · ${fmt.vnd(p.price_vnd_truck_month)}`]) },
        { name: "price", label: t("Agreed price per truck/month (₫)", "Giá thỏa thuận mỗi xe/tháng (₫)"), type: "number", required: true, value: sub.agreed_price_vnd_truck_month, attrs: 'min="0" step="1000"' },
        { name: "reason", label: t("Reason", "Lý do"), type: "textarea", required: true, hint: t("Saved with the change history.", "Lưu vào lịch sử thay đổi.") },
      ],
      submitLabel: t("Save", "Lưu"),
      onSubmit(values) {
        const price = Number(values.price);
        if (!Number.isFinite(price) || price < 0) return t("The price cannot be negative.", "Giá không được âm.");
        sub.plan_id = values.plan_id;
        sub.agreed_price_vnd_truck_month = price;
        sub.change_reason = values.reason.slice(0, 200);
        G3.render();
        ui.toast(t("Plan changed; it applies from the next billing cycle", "Đã đổi gói; áp dụng từ chu kỳ thanh toán kế tiếp"));
      },
    });
  }

  function addOnsForm(id) {
    const sub = db.find("subscriptions", id);
    const sms = (sub.add_ons || []).find((a) => a.code === "SMS");
    const adas = (sub.add_ons || []).find((a) => a.code === "ADAS");
    const planHasAdas = planOf(sub).features.includes("ADAS");
    const trucks = trucksOf(sub.organization_id);
    ui.formModal({
      title: `${t("Add-ons", "Tiện ích mua thêm")} · ${db.org(sub.organization_id).short_name}`,
      fields: [
        { name: "sms", label: t("SMS alert packs per month", "Số gói SMS mỗi tháng"), type: "number", value: sms ? sms.quantity : 0, attrs: 'min="0" max="50" step="1"', hint: t(`1 pack = 1,000 SMS · list ${fmt.vnd(feature("SMS").list_price_vnd)}`, `1 gói = 1.000 tin · giá niêm yết ${fmt.vnd(feature("SMS").list_price_vnd)}`) },
        planHasAdas ? null : { name: "adas", label: t("In-cab ADAS package", "Gói ADAS trong cabin"), type: "checkbox", value: !!adas, checkLabel: t(`For all ${trucks} trucks`, `Cho cả ${trucks} xe`) },
        planHasAdas ? null : { name: "adas_price", label: t("ADAS agreed price per truck/month (₫)", "Giá ADAS thỏa thuận mỗi xe/tháng (₫)"), type: "number", value: adas ? adas.agreed_price_vnd : feature("ADAS").list_price_vnd, attrs: 'min="0" step="1000"' },
      ].filter(Boolean),
      submitLabel: t("Save add-ons", "Lưu tiện ích"),
      onSubmit(values) {
        const packs = Number(values.sms || 0);
        if (!Number.isInteger(packs) || packs < 0 || packs > 50) return t("SMS packs: a whole number from 0 to 50.", "Số gói SMS: số nguyên từ 0 đến 50.");
        const next = [];
        if (packs) next.push({ code: "SMS", quantity: packs, used: sms ? Math.min(sms.used, packs * 1000) : 0, quota: packs * 1000, agreed_price_vnd: feature("SMS").list_price_vnd });
        if (!planHasAdas && values.adas) {
          const price = Number(values.adas_price);
          if (!(price > 0)) return t("Enter the ADAS price.", "Nhập giá ADAS.");
          next.push({ code: "ADAS", quantity: trucks, agreed_price_vnd: price });
        }
        sub.add_ons = next;
        G3.render();
        ui.toast(t("Add-ons updated", "Đã cập nhật tiện ích"));
      },
    });
  }

  function lockSubscription(id, lock) {
    const sub = db.find("subscriptions", id);
    const org = db.org(sub.organization_id);
    ui.reasonModal({
      title: lock ? t("Lock features on overdue", "Khóa tính năng do quá hạn") : t("Unlock features", "Mở khóa tính năng"),
      danger: lock,
      intro: lock
        ? ui.callout("warn", esc(t(`${org.short_name} loses access to the plan's features (their menus hide them, ACC-14) until you unlock. Charging, the wallet and invoices keep working.`, `${org.short_name} sẽ mất quyền dùng các tính năng của gói (menu tự ẩn, ACC-14) cho đến khi mở khóa. Sạc, ví và hóa đơn vẫn hoạt động.`)))
        : `<p class="small muted">${esc(t("The plan's features become available again right away.", "Các tính năng của gói được dùng lại ngay."))}</p>`,
      submitLabel: lock ? t("Lock features", "Khóa tính năng") : t("Unlock", "Mở khóa"),
      onSubmit(reason) {
        sub.features_locked = lock;
        sub.status_reason = reason;
        G3.render();
        ui.toast(lock ? t(`Features locked for ${org.short_name}`, `Đã khóa tính năng của ${org.short_name}`) : t(`Features unlocked for ${org.short_name}`, `Đã mở khóa tính năng cho ${org.short_name}`), lock ? "alert" : "", lock ? "lock" : "unlock");
      },
    });
  }

  function upgradeRequestForm() {
    const org = scope.org();
    const sub = db.subscriptions.find((s) => s.organization_id === org.organization_id);
    const options = db.plans.filter((p) => p.visibility === "PUBLIC" && (!sub || p.plan_id !== sub.plan_id));
    const manager = db.user(org.account_manager_user_id);
    ui.formModal({
      title: t("Request an upgrade", "Yêu cầu nâng cấp gói"),
      intro: `<p class="small muted">${esc(t("Your account manager prepares the offer; nothing changes until you sign it.", "Người phụ trách sẽ chuẩn bị báo giá; không có gì thay đổi cho đến khi bạn ký."))}</p>`,
      fields: [
        { name: "plan_id", label: t("Plan you are interested in", "Gói bạn quan tâm"), type: "select", options: [...options.map((p) => [p.plan_id, `${p.name} · ${fmt.vnd(p.price_vnd_truck_month)} ${lab("TRUCK_MONTH")}`]), ["ADDON_ADAS", t("Add the in-cab ADAS package", "Thêm gói ADAS trong cabin")], ["ADDON_SMS", t("More SMS alerts", "Thêm SMS cảnh báo")]] },
        { name: "note", label: t("Note", "Ghi chú"), type: "textarea", placeholder: t("e.g. 6 more tractors from January", "VD: thêm 6 đầu kéo từ tháng 1") },
      ],
      submitLabel: t("Send request", "Gửi yêu cầu"),
      onSubmit(values) {
        const plan = db.find("plans", values.plan_id);
        UPGRADE_REQUESTS.unshift({ organization_id: org.organization_id, user_id: scope.user().user_id, plan_name: plan ? plan.name : values.plan_id === "ADDON_ADAS" ? "ADAS" : "SMS", note: values.note, created_at: new Date().toISOString() });
        ui.toast(t(`Request sent to ${manager ? manager.full_name : "sales"}. They will contact you within one working day.`, `Đã gửi yêu cầu đến ${manager ? manager.full_name : "bộ phận kinh doanh"}. Bạn sẽ được liên hệ trong một ngày làm việc.`), "", "send");
      },
    });
  }

  G3.page({
    id: "plans",
    path: "/plans",
    title: ["Plans & subscriptions", "Gói dịch vụ & thuê bao"],
    roles: ["SALES", "ACCOUNTANT", "ORG_ADMIN", "FLEET_MANAGER"],
    features: ["PAY-01", "PAY-02", "PAY-03", "PAY-04", "ACC-14"],
    nav: { group: "billing", order: 1, icon: "layers", label: ["Plans", "Gói dịch vụ"] },
    render({ query }) {
      return scope.isInternal() ? plansInternal(query) : myPlan();
    },
    mount(root) {
      bindCommon(root);
      root.querySelectorAll("[data-pick-feature]").forEach((tr) => tr.addEventListener("click", () => G3.setQuery({ feature: tr.dataset.pickFeature })));
      bindActions(root, {
        "change-price": changePriceForm,
        "new-plan": newPlanForm,
        "new-sub": newSubscriptionForm,
        "change-plan": changePlanForm,
        "edit-addons": addOnsForm,
        "lock-sub": (id) => lockSubscription(id, true),
        "unlock-sub": (id) => lockSubscription(id, false),
        upgrade: upgradeRequestForm,
      });
    },
  });

  /* =====================================================================
     WALLETS (PAY-07, PAY-08)
     ===================================================================== */
  const MIN_BALANCE_VND = 0; // BILLING_MIN_BALANCE_VND default: a negative wallet must top up first (BL-25)
  const DEPOSIT_VND = 300000; // deposit kept on a driver's own wallet (PAY-07 open question)
  const LOW_BALANCE_VND = 5000000;
  const TOPUP_MIN_VND = 10000;
  const TOPUP_MAX_VND = 50000000;
  const TOPUP_EXPIRY_MINUTES = 15;
  const BANK = { bin: "970436", short: "VCB", account_number: "1900 6868 0088", account_name: "G3 NETWORK" };
  const CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const TX_TONES = { TOP_UP: "ok", SESSION_BILL: "info", REFUND: "violet", ADJUSTMENT: "warn" };

  const walletTxs = (walletId) => db.wallet_transactions.filter((x) => x.wallet_id === walletId).sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  /** Transfer content the bank must carry back (topup_service: "G3NAP" + 5 characters). */
  function transferCode(key) {
    const r = seeded("topup-" + key);
    return "G3NAP" + Array.from({ length: 5 }, () => CODE_ALPHABET[Math.floor(r() * CODE_ALPHABET.length)]).join("");
  }

  /** A QR-looking SVG: finder, timing and alignment patterns plus modules drawn from the payload's hash. Not scannable. */
  function qrSvg(payload, px = 200) {
    const N = 29;
    const r = seeded(payload);
    const grid = Array.from({ length: N }, () => Array(N).fill(null));
    const finder = (r0, c0) => {
      for (let i = -1; i <= 7; i++) for (let j = -1; j <= 7; j++) {
        const rr = r0 + i, cc = c0 + j;
        if (rr < 0 || cc < 0 || rr >= N || cc >= N) continue;
        const ring = Math.max(Math.abs(i - 3), Math.abs(j - 3));
        grid[rr][cc] = ring === 3 || ring <= 1;
      }
    };
    finder(0, 0); finder(0, N - 7); finder(N - 7, 0);
    for (let i = 8; i < N - 8; i++) { grid[6][i] = i % 2 === 0; grid[i][6] = i % 2 === 0; }
    const a = N - 7;
    for (let i = -2; i <= 2; i++) for (let j = -2; j <= 2; j++) grid[a + i][a + j] = Math.max(Math.abs(i), Math.abs(j)) !== 1;
    const mid = (N - 1) / 2;
    for (let i = -3; i <= 3; i++) for (let j = -3; j <= 3; j++) grid[mid + i][mid + j] = false;
    let d = "";
    for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) {
      const on = grid[i][j] === null ? r() < 0.5 : grid[i][j];
      if (on) d += `M${j + 2} ${i + 2}h1v1h-1z`;
    }
    const s = N + 4;
    return `<svg width="${px}" height="${px}" viewBox="0 0 ${s} ${s}" shape-rendering="crispEdges" role="img" aria-label="VietQR"><rect width="${s}" height="${s}" fill="#fff" rx="1"/><path d="${d}" fill="#111"/><rect x="${mid - 0.5}" y="${mid - 0.5}" width="6" height="6" rx="1.4" fill="#0e9f6e" shape-rendering="geometricPrecision"/><text x="${mid + 2.5}" y="${mid + 3.6}" font-size="3" font-weight="700" fill="#fff" text-anchor="middle" font-family="Arial, sans-serif">G3</text></svg>`;
  }

  /** Post a ledger line and keep the balance and the BLOCKED status in step (BL-25). */
  function postTx(wallet, type, amount, extra) {
    const before = wallet.status;
    wallet.balance_vnd += amount;
    wallet.status = wallet.balance_vnd < MIN_BALANCE_VND ? "BLOCKED" : "ACTIVE";
    const row = { wallet_transaction_id: db.uid("wtx"), wallet_id: wallet.wallet_id, transaction_type: type, amount_vnd: amount, balance_after_vnd: wallet.balance_vnd, created_at: new Date().toISOString(), user_id: scope.user().user_id, ...extra };
    db.wallet_transactions.unshift(row);
    return { row, unblocked: before === "BLOCKED" && wallet.status === "ACTIVE" };
  }

  function txDetails(row) {
    if (row.transaction_type === "SESSION_BILL" || (row.transaction_type === "REFUND" && row.session_id)) {
      const s = db.find("charging_sessions", row.session_id);
      if (!s) return "—";
      const loc = db.location(s.location_id);
      const v = db.vehicle(s.vehicle_id);
      return `<b>${esc(loc ? loc.name : s.station_id)}</b>${v ? ` · ${esc(v.license_plate)}` : ""} · ${fmt.kwh(s.energy_kwh)}<span class="sub">${esc(t("Session", "Phiên"))} #${esc(s.ocpp_transaction_id)} · ${fmt.num(s.price_vnd_kwh)} ₫/kWh${row.reason ? " · " + esc(row.reason) : ""}</span>`;
    }
    if (row.transaction_type === "TOP_UP") return `<b class="mono">${esc(row.reference || "VietQR")}</b><span class="sub">${esc(t("Bank transfer · credited automatically", "Chuyển khoản · ghi có tự động"))}</span>`;
    return `${esc(row.reason || "—")}<span class="sub">${ui.userName(row.user_id)}</span>`;
  }

  function walletsPage(query) {
    const internal = scope.isInternal();
    const wallets = scope.filter(db.wallets);
    const head = (actions) => ui.pageHead({
      title: t("Wallets", "Ví trả trước"),
      sub: esc(internal ? t("Every prepaid wallet: balances, ledger, adjustments and refunds.", "Mọi ví trả trước: số dư, sổ giao dịch, điều chỉnh và hoàn tiền.") : t(`${scope.org().name} · the company wallet pays for its trucks' charging`, `${scope.org().name} · ví công ty thanh toán tiền sạc cho xe của công ty`)),
      features: ["PAY-07", "PAY-08"],
      actions,
    });
    if (!wallets.length) return head("") + ui.card({ body: ui.empty(t("No wallet in this scope", "Không có ví trong phạm vi này")) });
    const wallet = wallets.find((w) => w.wallet_id === query.wallet) || wallets[0];
    const txs = walletTxs(wallet.wallet_id);
    const since = (days) => Date.now() - days * DAY;
    const scopeTxs = (internal ? wallets.flatMap((w) => walletTxs(w.wallet_id)) : txs).filter((x) => new Date(x.created_at).getTime() >= since(30));
    const topups = scopeTxs.filter((x) => x.transaction_type === "TOP_UP");
    const bills = scopeTxs.filter((x) => x.transaction_type === "SESSION_BILL");
    const totalBalance = internal ? sum(wallets, (w) => w.balance_vnd) : wallet.balance_vnd;
    const blocked = wallets.filter((w) => w.status === "BLOCKED");

    const period = query.period || "30";
    const q = (query.q || "").toLowerCase();
    const rows = txs.filter((x) => {
      if (query.type && x.transaction_type !== query.type) return false;
      if (period !== "all" && new Date(x.created_at).getTime() < since(Number(period))) return false;
      if (q) {
        const s = x.session_id ? db.find("charging_sessions", x.session_id) : null;
        const text = [x.reference, x.reason, s && (db.location(s.location_id) || {}).name, s && (db.vehicle(s.vehicle_id) || {}).license_plate].filter(Boolean).join(" ").toLowerCase();
        if (!text.includes(q)) return false;
      }
      return true;
    });
    const shown = rows.slice(0, 40);
    const refunded = new Set(db.wallet_transactions.filter((x) => x.transaction_type === "REFUND" && x.session_id).map((x) => x.session_id));
    const txTable = ui.table({
      rows: shown,
      empty: t("No transaction matches", "Không có giao dịch phù hợp"),
      columns: [
        { label: t("Time", "Thời gian"), render: (x) => `${fmt.dateTime(x.created_at)}<span class="sub">${fmt.ago(x.created_at)}</span>` },
        { label: t("Type", "Loại"), render: (x) => ui.badge(x.transaction_type, TX_TONES[x.transaction_type]) },
        { label: t("Details", "Chi tiết"), render: txDetails },
        { label: t("Amount", "Số tiền"), align: "right", render: (x) => `<b>${money(x.amount_vnd)}</b>` },
        { label: t("Balance after", "Số dư sau"), align: "right", render: (x) => `<span style="${x.balance_after_vnd < 0 ? "color:var(--danger)" : ""}">${fmt.vnd(x.balance_after_vnd)}</span>` },
        internal ? { label: "", render: (x) => (x.transaction_type === "SESSION_BILL" && !refunded.has(x.session_id) ? actBtn(t("Refund", "Hoàn tiền"), "refund", x.wallet_transaction_id, { sm: true }) : x.transaction_type === "SESSION_BILL" ? `<span class="tag">${esc(t("Refunded", "Đã hoàn"))}</span>` : "") } : null,
      ].filter(Boolean),
    });

    const chartRows = txs.slice(0, 24).reverse();
    const balanceChart = chartRows.length > 1
      ? ui.lineChart({ labels: chartRows.map((x) => { const d = new Date(x.created_at); return `${d.getDate()}/${d.getMonth() + 1}`; }), series: [{ name: t("Balance", "Số dư"), values: chartRows.map((x) => x.balance_after_vnd), area: true, color: ui.PALETTE[1] }], height: 170, unit: " ₫" })
      : ui.empty();

    const isUserWallet = wallet.owner_kind === "USER";
    const available = wallet.balance_vnd - (isUserWallet ? DEPOSIT_VND : 0) - MIN_BALANCE_VND;
    const walletCard = ui.card({
      title: wallet.owner_name, sub: wallet.owner_kind === "USER" ? t("personal wallet", "ví cá nhân") : t("company wallet", "ví công ty"),
      actions: ui.badge(wallet.status),
      body: `<div style="font-size:30px;font-weight:700;letter-spacing:-0.02em;color:var(${wallet.balance_vnd < 0 ? "--danger" : "--text"})" class="num">${fmt.vnd(wallet.balance_vnd)}</div>
        <div class="small muted">${esc(t("Balance", "Số dư"))} · ${esc(t("updated", "cập nhật"))} ${txs[0] ? fmt.ago(txs[0].created_at) : "—"}</div>
        ${wallet.status === "BLOCKED" ? '<div class="mt"></div>' + ui.callout("danger", esc(t("The balance is below the minimum: new charging sessions are refused until the wallet is topped up (BL-25).", "Số dư dưới mức tối thiểu: phiên sạc mới bị từ chối cho đến khi nạp thêm tiền (BL-25)."))) : wallet.balance_vnd < LOW_BALANCE_VND ? '<div class="mt"></div>' + ui.callout("warn", esc(t(`Below ${fmt.vnd(LOW_BALANCE_VND)}: a low-balance alert was sent to the fleet manager and the accountant.`, `Dưới ${fmt.vnd(LOW_BALANCE_VND)}: đã gửi cảnh báo số dư thấp cho quản lý đội xe và kế toán.`))) : ""}
        <div class="divider"></div>
        ${ui.kv([
          [t("Minimum to start a charge", "Mức tối thiểu để bắt đầu sạc"), fmt.vnd(MIN_BALANCE_VND)],
          [t("Deposit held", "Tiền đặt cọc"), isUserWallet ? fmt.vnd(DEPOSIT_VND) : `<span class="muted">${esc(t("none for company wallets", "không áp dụng cho ví công ty"))}</span>`],
          [t("Available for charging", "Có thể dùng để sạc"), `<b style="color:var(${available < 0 ? "--danger" : "--ok"})">${fmt.vnd(Math.max(0, available))}</b>`],
          [t("Low-balance alert", "Cảnh báo số dư thấp"), `< ${fmt.vnd(LOW_BALANCE_VND)}`],
          [t("Organization", "Tổ chức"), ui.orgName(wallet.organization_id)],
          [t("Wallet ID", "Mã ví"), `<span class="mono">${esc(wallet.wallet_id)}</span>`],
        ])}
        <div class="row mt">${!internal ? actBtn(t("Top up", "Nạp tiền"), "topup", wallet.wallet_id, { kind: "primary", icon: "qr" }) : ""}${internal ? actBtn(t("Adjustment", "Điều chỉnh"), "adjust", wallet.wallet_id, { icon: "edit" }) + actBtn(t("Show top-up QR", "Xem mã nạp"), "topup", wallet.wallet_id, { icon: "qr" }) : ""}</div>
        <div class="small muted mt">${esc(t("Deposit rule (300,000 ₫ on driver wallets, what happens on withdrawal) and withdrawal limits are still open questions for PAY-07.", "Quy tắc đặt cọc (300.000 ₫ với ví tài xế, xử lý khi rút) và giới hạn rút tiền vẫn là câu hỏi mở của PAY-07."))}</div>`,
    });

    const allWallets = internal ? ui.card({
      title: t("All wallets", "Tất cả ví"), sub: `${wallets.length}`, flush: true,
      body: ui.table({
        rows: wallets.slice().sort((a, b) => a.balance_vnd - b.balance_vnd),
        selected: (w) => w.wallet_id === wallet.wallet_id,
        rowAttrs: (w) => `data-pick-wallet="${esc(w.wallet_id)}" style="cursor:pointer"`,
        columns: [
          { label: t("Owner", "Chủ ví"), render: (w) => `<b>${esc(w.owner_name)}</b><span class="sub">${esc(w.owner_kind === "USER" ? t("Personal", "Cá nhân") : t("Company", "Công ty"))} · ${ui.orgName(w.organization_id)}</span>` },
          { label: t("Balance", "Số dư"), align: "right", render: (w) => `<b style="color:var(${w.balance_vnd < 0 ? "--danger" : w.balance_vnd < LOW_BALANCE_VND ? "--warn" : "--text"})">${fmt.vnd(w.balance_vnd)}</b>` },
          { label: t("Last top-up", "Lần nạp gần nhất"), render: (w) => { const last = walletTxs(w.wallet_id).find((x) => x.transaction_type === "TOP_UP"); return last ? `${fmt.vnd(last.amount_vnd)}<span class="sub">${fmt.ago(last.created_at)}</span>` : "—"; } },
          { label: t("Charging (30 d)", "Tiền sạc (30 ngày)"), align: "right", render: (w) => fmt.vnd(-sum(walletTxs(w.wallet_id).filter((x) => x.transaction_type === "SESSION_BILL" && new Date(x.created_at).getTime() >= since(30)), (x) => x.amount_vnd)) },
          { label: t("Status", "Trạng thái"), render: (w) => ui.badge(w.status) },
        ],
      }),
    }) + '<div class="mt"></div>' : "";

    return `${head(ui.btn(t("Export", "Xuất"), { icon: "download", action: "export-tx" }))}
      <div class="grid cols-4">
        ${ui.kpi({ label: internal ? t("Balance of all wallets", "Tổng số dư các ví") : t("Balance", "Số dư"), icon: "wallet", value: fmt.vndShort(totalBalance), tone: totalBalance < 0 ? "danger" : undefined, foot: internal ? `${wallets.length} ${t("wallets", "ví")}` : G3.label(wallet.status) })}
        ${ui.kpi({ label: t("Blocked wallets", "Ví bị chặn"), icon: "lock", value: blocked.length, tone: blocked.length ? "danger" : "ok", foot: t("balance below the minimum", "số dư dưới mức tối thiểu") })}
        ${ui.kpi({ label: t("Top-ups (30 d)", "Nạp tiền (30 ngày)"), icon: "qr", value: fmt.vndShort(sum(topups, (x) => x.amount_vnd)), foot: `${topups.length} ${t("transfers", "lần chuyển khoản")}` })}
        ${ui.kpi({ label: t("Charging paid (30 d)", "Tiền sạc đã trả (30 ngày)"), icon: "plug", value: fmt.vndShort(-sum(bills, (x) => x.amount_vnd)), foot: `${bills.length} ${t("sessions", "phiên")}` })}
      </div>
      <div class="mt"></div>
      ${allWallets}
      <div class="grid side">
        ${ui.card({
          title: t("Transactions", "Giao dịch"), sub: esc(wallet.owner_name), flush: true,
          body: `<div class="toolbar">
              ${ui.select("type", [["", t("All types", "Mọi loại")], ...["TOP_UP", "SESSION_BILL", "REFUND", "ADJUSTMENT"].map((v) => [v, G3.label(v)])], query.type || "")}
              ${ui.segmented([["7", t("7 days", "7 ngày")], ["30", t("30 days", "30 ngày")], ["all", t("All", "Tất cả")]], period, "period")}
              ${ui.search("q", query.q, t("Station, plate, reference", "Trạm, biển số, mã"))}
              <span class="grow"></span><span class="small muted">${shown.length < rows.length ? t(`Showing ${shown.length} of ${rows.length}`, `Hiển thị ${shown.length}/${rows.length}`) : `${rows.length} ${t("rows", "dòng")}`}</span>
            </div>${txTable}`,
        })}
        <div class="stack">
          ${walletCard}
          ${ui.card({ title: t("Balance over time", "Số dư theo thời gian"), body: balanceChart })}
        </div>
      </div>`;
  }

  function openTopUp(walletId) {
    const wallet = db.find("wallets", walletId);
    const count = walletTxs(walletId).filter((x) => x.transaction_type === "TOP_UP").length;
    const code = transferCode(`${walletId}:${count}`);
    const expires = new Date(Date.now() + TOPUP_EXPIRY_MINUTES * 60000);
    const payload = (amount) => `VIETQR|${BANK.bin}|${BANK.account_number}|${amount}|${code}`;
    const start = 20000000;
    const body = `<div class="grid cols-2" style="align-items:start">
        <div class="stack" style="gap:12px">
          <div class="field"><label for="topup-amount">${esc(t("Amount (₫)", "Số tiền (₫)"))}</label><input class="input" id="topup-amount" type="number" min="${TOPUP_MIN_VND}" max="${TOPUP_MAX_VND}" step="10000" value="${start}"><span class="hint">${esc(t(`From ${fmt.vnd(TOPUP_MIN_VND)} to ${fmt.vnd(TOPUP_MAX_VND)} per transfer`, `Từ ${fmt.vnd(TOPUP_MIN_VND)} đến ${fmt.vnd(TOPUP_MAX_VND)} mỗi lần`))}</span></div>
          <div class="row" style="gap:6px">${[5e6, 10e6, 20e6, 50e6].map((p) => `<button class="btn sm" data-preset="${p}">${esc(fmt.vndShort(p))}</button>`).join("")}</div>
          ${ui.kv([
            [t("Bank", "Ngân hàng"), `${BANK.short} <span class="muted small">(BIN ${BANK.bin})</span>`],
            [t("Account number", "Số tài khoản"), `<b class="mono">${BANK.account_number}</b>`],
            [t("Account name", "Chủ tài khoản"), BANK.account_name],
            [t("Transfer content", "Nội dung chuyển khoản"), `<b class="mono" style="font-size:15px;color:var(--primary-strong)">${code}</b>`],
            [t("Amount", "Số tiền"), `<b data-amount-text>${fmt.vnd(start)}</b>`],
            [t("Code valid until", "Mã hiệu lực đến"), `${fmt.time(expires)} <span class="muted small">(${TOPUP_EXPIRY_MINUTES} ${esc(t("min", "phút"))})</span>`],
          ])}
          <div class="small" data-topup-error style="color:var(--danger);display:none"></div>
        </div>
        <div style="text-align:center">
          <div style="display:inline-block;padding:10px;border:1px solid var(--border);border-radius:12px;background:#fff" data-qr>${qrSvg(payload(start))}</div>
          <div class="small muted" style="margin-top:8px">${esc(t("Scan with any banking app (VietQR · NAPAS 247)", "Quét bằng app ngân hàng bất kỳ (VietQR · NAPAS 247)"))}</div>
          <div class="small muted">${esc(t("Keep the transfer content unchanged: it is how the payment finds this wallet.", "Giữ nguyên nội dung chuyển khoản: hệ thống dùng nó để tìm đúng ví."))}</div>
        </div>
      </div>
      <div class="mt">${ui.callout("info", esc(t(`${wallet.owner_name}'s wallet is credited as soon as the bank notifies us (usually under a minute). Prototype: “Simulate the transfer” plays the bank's notification.`, `Ví ${wallet.owner_name} được ghi có ngay khi ngân hàng báo về (thường dưới một phút). Bản mẫu: “Giả lập chuyển khoản” mô phỏng thông báo của ngân hàng.`)))}</div>`;
    ui.modal({
      title: t("Top up the wallet", "Nạp tiền vào ví"),
      wide: true,
      body,
      foot: `<button class="btn" data-close>${esc(t("Cancel", "Hủy"))}</button><button class="btn primary" data-simulate>${ui.icon("send", 16)}${esc(t("Simulate the transfer", "Giả lập chuyển khoản"))}</button>`,
      onMount(el, close) {
        const input = el.querySelector("#topup-amount");
        const refresh = () => {
          const amount = Math.round(Number(input.value) || 0);
          el.querySelector("[data-amount-text]").textContent = fmt.vnd(amount);
          el.querySelector("[data-qr]").innerHTML = qrSvg(payload(amount));
        };
        input.addEventListener("input", refresh);
        el.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => { input.value = b.dataset.preset; refresh(); }));
        el.querySelector("[data-simulate]").addEventListener("click", () => {
          const amount = Math.round(Number(input.value) || 0);
          const err = el.querySelector("[data-topup-error]");
          if (amount < TOPUP_MIN_VND || amount > TOPUP_MAX_VND) {
            err.textContent = t(`Enter an amount from ${fmt.vnd(TOPUP_MIN_VND)} to ${fmt.vnd(TOPUP_MAX_VND)}.`, `Nhập số tiền từ ${fmt.vnd(TOPUP_MIN_VND)} đến ${fmt.vnd(TOPUP_MAX_VND)}.`);
            err.style.display = "block";
            return;
          }
          const { unblocked } = postTx(wallet, "TOP_UP", amount, { reference: `VietQR ${code}`, payment_method: "BANK_TRANSFER" });
          const en = amount.toLocaleString("en-GB") + " ₫";
          const vi = amount.toLocaleString("vi-VN") + " ₫";
          db.notifications.unshift({ notification_id: Math.max(...db.notifications.map((n) => n.notification_id)) + 1, notification_type: "TOP_UP_RECEIVED", severity: "INFO", vehicle_id: null, organization_id: wallet.organization_id, title: [`Top-up ${en} received (VietQR ${code})`, `Đã nhận nạp ${vi} (VietQR ${code})`], created_at: new Date().toISOString(), read_at: null, payload: { amount_vnd: amount, transfer_code: code, wallet_id: wallet.wallet_id }, station_id: null });
          close();
          G3.render();
          ui.toast(`<b>${esc(G3.label("TOP_UP_RECEIVED"))}</b><br>${esc(t(`${fmt.vnd(amount)} credited to ${wallet.owner_name}.`, `Đã ghi có ${fmt.vnd(amount)} vào ví ${wallet.owner_name}.`))}${unblocked ? "<br>" + esc(t("The wallet is unblocked.", "Ví đã được mở chặn.")) : ""}`, "", "wallet");
        });
      },
    });
  }

  function adjustmentForm(walletId) {
    const wallet = db.find("wallets", walletId);
    ui.formModal({
      title: `${t("Wallet adjustment", "Điều chỉnh ví")} · ${wallet.owner_name}`,
      intro: ui.callout("info", esc(t("An adjustment is a ledger line, never an edit of the balance: it is kept with who made it and why.", "Điều chỉnh là một dòng giao dịch, không sửa trực tiếp số dư: lưu kèm người thực hiện và lý do."))),
      fields: [
        { name: "amount", label: t("Amount (₫, negative to debit)", "Số tiền (₫, số âm để trừ)"), type: "number", required: true, placeholder: "-150000", attrs: 'step="1000"' },
        { name: "reason", label: t("Reason", "Lý do"), type: "textarea", required: true, hint: t("e.g. compensation for SC-2305, goodwill credit", "VD: bồi hoàn theo SC-2305, hỗ trợ khách hàng") },
      ],
      submitLabel: t("Post adjustment", "Ghi điều chỉnh"),
      onSubmit(values) {
        const amount = Math.round(Number(values.amount));
        if (!amount) return t("Enter a non-zero amount.", "Nhập số tiền khác 0.");
        if (Math.abs(amount) > 100000000) return t("Adjustments above 100,000,000 ₫ need a top-up or a refund instead.", "Điều chỉnh trên 100.000.000 ₫ cần dùng nạp tiền hoặc hoàn tiền.");
        postTx(wallet, "ADJUSTMENT", amount, { reason: values.reason.slice(0, 200) });
        G3.render();
        ui.toast(t(`Adjustment ${fmt.vnd(amount)} posted`, `Đã ghi điều chỉnh ${fmt.vnd(amount)}`));
      },
    });
  }

  function refundForm(txId) {
    const bill = db.wallet_transactions.find((x) => x.wallet_transaction_id === txId);
    const wallet = db.find("wallets", bill.wallet_id);
    ui.reasonModal({
      title: t("Refund a charging bill", "Hoàn tiền phiên sạc"),
      danger: false,
      intro: `<p class="small muted">${esc(t(`${fmt.vnd(-bill.amount_vnd)} goes back to ${wallet.owner_name}'s wallet as a REFUND line.`, `${fmt.vnd(-bill.amount_vnd)} được hoàn vào ví ${wallet.owner_name} bằng một dòng hoàn tiền.`))}</p>`,
      submitLabel: t("Refund", "Hoàn tiền"),
      onSubmit(reason) {
        postTx(wallet, "REFUND", -bill.amount_vnd, { session_id: bill.session_id, reason });
        G3.render();
        ui.toast(t("Refund posted", "Đã hoàn tiền"));
      },
    });
  }

  G3.page({
    id: "wallets",
    path: "/wallets",
    title: ["Wallets", "Ví trả trước"],
    roles: ["ACCOUNTANT", "FLEET_MANAGER", "ORG_ADMIN"],
    features: ["PAY-07", "PAY-08"],
    nav: { group: "billing", order: 2, icon: "wallet" },
    render: ({ query }) => walletsPage(query),
    mount(root, { query }) {
      bindCommon(root);
      root.querySelectorAll("[data-pick-wallet]").forEach((tr) => tr.addEventListener("click", () => G3.setQuery({ wallet: tr.dataset.pickWallet })));
      bindActions(root, {
        topup: openTopUp,
        adjust: adjustmentForm,
        refund: refundForm,
        "export-tx"() {
          const wallets = scope.filter(db.wallets);
          const wallet = wallets.find((w) => w.wallet_id === query.wallet) || wallets[0];
          if (!wallet) return;
          ui.downloadCsv(`wallet-${wallet.wallet_id}.csv`, [
            { label: "created_at", key: "created_at" }, { label: "transaction_type", key: "transaction_type" }, { label: "amount_vnd", key: "amount_vnd" },
            { label: "balance_after_vnd", key: "balance_after_vnd" }, { label: "session_id", key: "session_id" }, { label: "reference", key: "reference" }, { label: "reason", key: "reason" },
          ], walletTxs(wallet.wallet_id));
        },
      });
    },
  });

  /* =====================================================================
     INVOICES (PAY-08, PAY-11, PAY-13, CHG-05)
     ===================================================================== */
  const VAT_RATE = 0.08;
  const DUE_DAYS = 15;
  const invoiceDue = (inv) => new Date(new Date(inv.issued_at).getTime() + DUE_DAYS * DAY).toISOString();
  const einvoiceSymbol = (period) => `1C${period.slice(-2)}TGN`;
  const einvoiceNo = (key) => String(hashStr("einv-" + key) % 10000000).padStart(7, "0");
  const lookupCode = (key) => (hashStr("lookup-" + key).toString(36).toUpperCase() + "X7Q2").slice(0, 8);

  /**
   * Per-truck lines of a monthly invoice. Weights come from the month's
   * completed sessions (or all sessions of the company when the month has
   * none in the mock data); amounts are scaled so the lines add up to the invoice.
   */
  function invoiceLines(inv) {
    const sessions = db.charging_sessions.filter((s) => s.organization_id === inv.organization_id && s.status === "COMPLETED" && s.vehicle_id);
    const inMonth = sessions.filter((s) => periodOf(s.started_at) === inv.period);
    const base = inMonth.length ? inMonth : sessions;
    const trucks = db.vehicles.filter((v) => v.organization_id === inv.organization_id);
    const weight = {};
    base.forEach((s) => { weight[s.vehicle_id] = (weight[s.vehicle_id] || { kwh: 0, n: 0 }); weight[s.vehicle_id].kwh += s.energy_kwh; weight[s.vehicle_id].n += 1; });
    const totalKwh = Object.values(weight).reduce((a, w) => a + w.kwh, 0) || 1;
    const avgSession = totalKwh / (base.length || 1);
    const sub = db.subscriptions.find((s) => s.organization_id === inv.organization_id);
    const perTruck = trucks.length ? inv.subscription_vnd / trucks.length : 0;
    let kwhLeft = inv.energy_kwh, vndLeft = inv.energy_vnd;
    const lines = trucks.map((v, i) => {
      const share = (weight[v.vehicle_id] ? weight[v.vehicle_id].kwh : 0) / totalKwh;
      const last = i === trucks.length - 1;
      const kwh = last ? kwhLeft : Math.round(inv.energy_kwh * share);
      const vnd = last ? vndLeft : Math.round(inv.energy_vnd * share);
      kwhLeft -= kwh; vndLeft -= vnd;
      return { vehicle: v, sessions: inMonth.length ? (weight[v.vehicle_id] || { n: 0 }).n : Math.round(kwh / avgSession), energy_kwh: kwh, energy_vnd: vnd, subscription_vnd: perTruck };
    });
    return { lines, sub };
  }

  function invoiceDetail(invoiceId) {
    const inv = db.find("invoices", invoiceId);
    const org = db.org(inv.organization_id);
    const { lines, sub } = invoiceLines(inv);
    const visible = scope.vehicleIds();
    const shownLines = lines.filter((l) => visible.has(l.vehicle.vehicle_id));
    const hidden = lines.length - shownLines.length;
    const canRecord = scope.isInternal() && scope.has("ACCOUNTANT", "HEAD_ADMIN");
    const plan = sub ? planOf(sub) : null;
    const body = `${inv.status === "OVERDUE" ? ui.callout("danger", esc(t(`Overdue since ${fmt.date(invoiceDue(inv))}. Features may be locked until it is paid (PAY-03).`, `Quá hạn từ ${fmt.date(invoiceDue(inv))}. Tính năng có thể bị khóa cho đến khi thanh toán (PAY-03).`))) + '<div class="mt"></div>' : ""}
      <div class="grid cols-2">
        ${ui.kv([
          [t("Customer", "Khách hàng"), `<b>${esc(org.name)}</b>`],
          [t("Tax code", "Mã số thuế"), `<span class="mono">${esc(org.tax_code)}</span>`],
          [t("Address", "Địa chỉ"), esc(org.address)],
          [t("Period", "Kỳ"), esc(inv.period)],
          [t("Issued · due", "Phát hành · hạn"), `${fmt.date(inv.issued_at)} · ${fmt.date(invoiceDue(inv))}`],
          [t("Status", "Trạng thái"), ui.badge(inv.status) + (inv.payment_reference ? ` <span class="small muted mono">${esc(inv.payment_reference)}</span>` : "")],
        ])}
        <div class="card" style="box-shadow:none;background:var(--surface-2);padding:12px 14px">
          <div class="row between mb" style="margin-bottom:8px"><b>${esc(t("Legal e-invoice", "Hóa đơn điện tử"))}</b>${ui.featureChips(["PAY-11"])}</div>
          ${ui.kv([
            [t("Symbol · number", "Ký hiệu · số"), `<span class="mono">${einvoiceSymbol(inv.period)} · ${einvoiceNo(inv.invoice_id)}</span>`],
            [t("Lookup code", "Mã tra cứu"), `<b class="mono">${esc(inv.lookup_code)}</b>`],
            [t("Provider", "Nhà cung cấp"), esc(t("Licensed e-invoice provider (to be chosen)", "Nhà cung cấp HĐĐT được cấp phép (chưa chọn)"))],
            [t("Tax authority code", "Mã của cơ quan thuế"), `<span class="mono">M1-${inv.period.slice(-2)}-${einvoiceNo("cqt" + inv.invoice_id)}</span>`],
            [t("Internal number", "Số nội bộ"), `<span class="mono">${esc(inv.invoice_number)}</span>`],
          ])}
        </div>
      </div>
      <h4 class="mt" style="margin-bottom:8px">${esc(t("Breakdown per truck", "Chi tiết theo từng xe"))} <span class="muted small" style="font-weight:400">· PAY-08 · CHG-05</span></h4>
      ${ui.table({
        rows: shownLines,
        columns: [
          { label: t("Truck", "Xe"), render: (l) => ui.vehicleCell(l.vehicle) },
          { label: t("Model", "Mẫu xe"), render: (l) => esc(db.model(l.vehicle.vehicle_model_id).code) },
          { label: t("Sessions", "Phiên"), align: "right", render: (l) => fmt.num(l.sessions) },
          { label: "kWh", align: "right", render: (l) => fmt.num(l.energy_kwh) },
          { label: t("Energy", "Tiền điện"), align: "right", render: (l) => fmt.vnd(l.energy_vnd) },
          { label: t("Subscription", "Thuê bao"), align: "right", render: (l) => fmt.vnd(l.subscription_vnd) },
          { label: t("Total excl. VAT", "Cộng chưa VAT"), align: "right", render: (l) => `<b>${fmt.vnd(l.energy_vnd + l.subscription_vnd)}</b>` },
        ],
      })}
      ${hidden ? `<p class="small muted">${esc(t(`${hidden} trucks outside your fleet limit are not listed (FLT-03); the totals are the whole invoice.`, `${hidden} xe ngoài phạm vi đội xe của bạn không hiển thị (FLT-03); tổng là của cả hóa đơn.`))}</p>` : ""}
      <div class="row mt" style="justify-content:flex-end"><div style="min-width:320px">${ui.kv([
        [t("Energy", "Tiền điện"), `${fmt.num(inv.energy_kwh)} kWh · ${fmt.vnd(inv.energy_vnd)}`],
        [t("Subscription", "Thuê bao"), `${lines.length} × ${fmt.vnd(lines.length ? inv.subscription_vnd / lines.length : 0)}${plan ? ` <span class="muted small">(${esc(plan.name)})</span>` : ""} = ${fmt.vnd(inv.subscription_vnd)}`],
        [t("Subtotal", "Cộng tiền hàng"), fmt.vnd(inv.energy_vnd + inv.subscription_vnd)],
        [t("VAT 8%", "Thuế GTGT 8%"), fmt.vnd(inv.vat_vnd)],
        [t("Total", "Tổng thanh toán"), `<b style="font-size:16px">${fmt.vnd(inv.total_vnd)}</b>`],
      ])}</div></div>`;
    ui.modal({
      title: `${t("Invoice", "Hóa đơn")} ${inv.invoice_number} · ${inv.period}`,
      wide: true,
      body,
      foot: `<button class="btn" data-close>${esc(t("Close", "Đóng"))}</button>
        <button class="btn" data-inv-csv>${ui.icon("download", 16)}${esc(t("Per-truck CSV", "CSV theo xe"))}</button>
        <button class="btn" data-inv-pdf>${ui.icon("file", 16)}${esc(t("Download PDF", "Tải PDF"))}</button>
        ${canRecord && inv.status === "OVERDUE" ? `<button class="btn" data-inv-remind>${ui.icon("send", 16)}${esc(t("Send reminder", "Gửi nhắc nợ"))}</button>` : ""}
        ${canRecord && inv.status !== "PAID" ? `<button class="btn primary" data-inv-pay>${ui.icon("check", 16)}${esc(t("Record payment", "Ghi nhận thanh toán"))}</button>` : ""}`,
      onMount(el, close) {
        el.querySelector("[data-inv-csv]").addEventListener("click", () => ui.downloadCsv(`invoice-${inv.invoice_number.replace(/\//g, "-")}.csv`, [
          { label: "license_plate", csv: (l) => l.vehicle.license_plate }, { label: "vin", csv: (l) => l.vehicle.vin }, { label: "sessions", key: "sessions" },
          { label: "energy_kwh", key: "energy_kwh" }, { label: "energy_vnd", key: "energy_vnd" }, { label: "subscription_vnd", key: "subscription_vnd" },
        ], shownLines));
        el.querySelector("[data-inv-pdf]").addEventListener("click", () => ui.toast(t("The PDF comes from the e-invoice provider (prototype: not generated)", "Bản PDF lấy từ nhà cung cấp HĐĐT (bản mẫu: không tạo file)"), "", "file"));
        const remind = el.querySelector("[data-inv-remind]");
        if (remind) remind.addEventListener("click", () => ui.toast(t(`Reminder sent to ${org.short_name}'s admin and accountant`, `Đã gửi nhắc nợ đến quản trị và kế toán của ${org.short_name}`), "", "send"));
        const pay = el.querySelector("[data-inv-pay]");
        if (pay) pay.addEventListener("click", () => ui.formModal({
          title: t("Record payment", "Ghi nhận thanh toán"),
          fields: [
            { name: "reference", label: t("Bank reference", "Mã giao dịch ngân hàng"), required: true, placeholder: "FT26290…" },
            { name: "paid_on", label: t("Paid on", "Ngày thanh toán"), type: "date", required: true, value: todayIso() },
          ],
          submitLabel: t("Mark as paid", "Đánh dấu đã thanh toán"),
          onSubmit(values) {
            if (values.paid_on > todayIso()) return t("The payment date cannot be in the future.", "Ngày thanh toán không được ở tương lai.");
            inv.status = "PAID";
            inv.payment_reference = values.reference;
            inv.paid_at = values.paid_on;
            close();
            G3.render();
            ui.toast(t(`Invoice ${inv.invoice_number} paid`, `Hóa đơn ${inv.invoice_number} đã thanh toán`));
          },
        }));
      },
    });
  }

  function monthlyTab(query, invoices) {
    const periods = [...new Set(invoices.map((i) => i.period))];
    const rows = invoices.filter((i) => (!query.status || i.status === query.status) && (!query.period || i.period === query.period));
    const internal = scope.isInternal();
    const chartPeriods = periods.slice().reverse();
    const chart = ui.barChart({
      labels: chartPeriods,
      stacked: true,
      series: [
        { name: t("Energy", "Tiền điện"), values: chartPeriods.map((p) => sum(invoices.filter((i) => i.period === p), (i) => i.energy_vnd)), color: ui.PALETTE[0] },
        { name: t("Subscription", "Thuê bao"), values: chartPeriods.map((p) => sum(invoices.filter((i) => i.period === p), (i) => i.subscription_vnd)), color: ui.PALETTE[1] },
        { name: "VAT", values: chartPeriods.map((p) => sum(invoices.filter((i) => i.period === p), (i) => i.vat_vnd)), color: "#94a3b8" },
      ],
      height: 200, unit: " ₫",
    });
    const statusCounts = ["ISSUED", "PAID", "OVERDUE"].map((s) => [s, invoices.filter((i) => i.status === s)]);
    return `<div class="grid side">
        ${ui.card({ title: t("Invoiced per month", "Giá trị hóa đơn theo tháng"), sub: "₫", actions: ui.legend([[t("Energy", "Tiền điện"), ui.PALETTE[0]], [t("Subscription", "Thuê bao"), ui.PALETTE[1]], ["VAT", "#94a3b8"]]), body: chart })}
        ${ui.card({
          title: t("By status", "Theo trạng thái"),
          body: `<div class="stack" style="gap:10px">${statusCounts.map(([s, list]) => `<div class="row between"><span>${ui.badge(s)} <span class="muted small">${list.length}</span></span><b class="num">${fmt.vnd(sum(list, (i) => i.total_vnd))}</b></div>`).join("")}</div>
            <div class="divider"></div>
            <div class="small muted">${esc(t(`One consolidated invoice per company and month: energy of every session its trucks charged, the subscription and VAT 8%. Due ${DUE_DAYS} days after issue. Issued through a licensed e-invoice provider with a lookup code (PAY-11).`, `Một hóa đơn gộp mỗi công ty mỗi tháng: điện của mọi phiên sạc của xe công ty, phí thuê bao và VAT 8%. Hạn thanh toán ${DUE_DAYS} ngày sau khi phát hành. Phát hành qua nhà cung cấp HĐĐT được cấp phép, có mã tra cứu (PAY-11).`))}</div>`,
        })}
      </div>
      <div class="mt">${ui.card({
        title: t("Monthly consolidated invoices", "Hóa đơn tổng hợp hàng tháng"), sub: "PAY-08 · PAY-11", flush: true,
        body: `<div class="toolbar">
            ${ui.select("status", [["", t("All statuses", "Mọi trạng thái")], ...["ISSUED", "PAID", "OVERDUE"].map((s) => [s, G3.label(s)])], query.status || "")}
            ${ui.select("period", [["", t("All periods", "Mọi kỳ")], ...periods.map((p) => [p, p])], query.period || "")}
            <span class="grow"></span>${ui.btn(t("Export", "Xuất"), { icon: "download", sm: true, action: "export-invoices" })}
          </div>
          ${ui.table({
            rows,
            empty: t("No invoice matches", "Không có hóa đơn phù hợp"),
            rowAttrs: (i) => `data-invoice="${esc(i.invoice_id)}" style="cursor:pointer"`,
            columns: [
              { label: t("Invoice", "Hóa đơn"), render: (i) => `<b class="mono">${esc(i.invoice_number)}</b><span class="sub mono">${einvoiceSymbol(i.period)} · ${einvoiceNo(i.invoice_id)}</span>` },
              internal ? { label: t("Customer", "Khách hàng"), render: (i) => `<b>${ui.orgName(i.organization_id)}</b>` } : null,
              { label: t("Period", "Kỳ"), render: (i) => esc(i.period) },
              { label: t("Energy", "Điện năng"), align: "right", render: (i) => `${fmt.num(i.energy_kwh)} kWh<span class="sub">${fmt.vnd(i.energy_vnd)}</span>` },
              { label: t("Subscription", "Thuê bao"), align: "right", render: (i) => fmt.vnd(i.subscription_vnd) },
              { label: "VAT 8%", align: "right", render: (i) => fmt.vnd(i.vat_vnd) },
              { label: t("Total", "Tổng"), align: "right", render: (i) => `<b>${fmt.vnd(i.total_vnd)}</b>` },
              { label: t("Status", "Trạng thái"), render: (i) => ui.badge(i.status) },
              { label: t("Due", "Hạn"), render: (i) => `<span style="${i.status === "OVERDUE" ? "color:var(--danger);font-weight:600" : ""}">${fmt.date(invoiceDue(i))}</span>` },
              { label: t("Lookup code", "Mã tra cứu"), render: (i) => `<span class="mono">${esc(i.lookup_code)}</span>` },
            ].filter(Boolean),
          })}`,
      })}</div>`;
  }

  function retailTab(query) {
    const internal = scope.isInternal();
    const since = Date.now() - 30 * DAY;
    const sessions = scope.byVehicle(db.charging_sessions).filter((s) => s.status === "COMPLETED" && new Date(s.started_at).getTime() >= since);
    const groupKey = internal ? (s) => s.organization_id : (s) => s.vehicle_id;
    const groups = {};
    sessions.forEach((s) => { const k = groupKey(s); groups[k] = groups[k] || { key: k, n: 0, kwh: 0, vnd: 0 }; groups[k].n += 1; groups[k].kwh += s.energy_kwh; groups[k].vnd += s.cost_vnd; });
    const groupRows = Object.values(groups).sort((a, b) => b.kwh - a.kwh);
    const totalKwh = sum(groupRows, (g) => g.kwh);
    const energyCard = ui.card({
      title: internal ? t("Energy per customer (30 d)", "Điện năng theo khách hàng (30 ngày)") : t("Energy per truck (30 d)", "Điện năng theo xe (30 ngày)"), sub: "CHG-05", flush: true,
      body: ui.table({
        rows: groupRows,
        columns: [
          { label: internal ? t("Customer", "Khách hàng") : t("Truck", "Xe"), render: (g) => (internal ? `<b>${ui.orgName(g.key)}</b>` : ui.vehicleCell(db.vehicle(g.key))) },
          { label: t("Sessions", "Phiên"), align: "right", render: (g) => fmt.num(g.n) },
          { label: "kWh", align: "right", render: (g) => `<b>${fmt.num(g.kwh)}</b>` },
          { label: t("Share", "Tỷ trọng"), render: (g) => `<div style="min-width:90px">${ui.meter((g.kwh / (totalKwh || 1)) * 100)}</div>` },
          { label: t("Amount", "Thành tiền"), align: "right", render: (g) => fmt.vnd(g.vnd) },
          { label: "₫/kWh", align: "right", render: (g) => fmt.num(g.vnd / (g.kwh || 1)) },
        ],
      }),
    });
    const locations = [...new Set(sessions.map((s) => s.location_id))].map((id) => db.location(id)).filter(Boolean);
    const q = (query.q || "").toLowerCase();
    const billed = sessions.filter((s) => s.bill_status !== "VOID" && (!query.location || s.location_id === query.location) && (!q || ((db.vehicle(s.vehicle_id) || {}).license_plate || "").toLowerCase().includes(q)));
    const shown = billed.slice(0, 50);
    const table = ui.table({
      rows: shown,
      empty: t("No session matches", "Không có phiên phù hợp"),
      columns: [
        { label: t("Session", "Phiên"), render: (s) => `${fmt.dateTime(s.started_at)}<span class="sub mono">#${esc(s.ocpp_transaction_id)}</span>` },
        { label: t("Station", "Trạm"), render: (s) => esc((db.location(s.location_id) || {}).name || s.station_id) },
        { label: t("Truck", "Xe"), render: (s) => `<b>${esc((db.vehicle(s.vehicle_id) || {}).license_plate || "—")}</b>` },
        { label: t("Buyer", "Người mua"), render: (s) => { const d = db.driver(s.driver_id); const o = db.org(s.organization_id); return `${d ? esc(d.full_name) : esc(o.short_name)}<span class="sub">${esc(o.short_name)} · ${esc(t("Tax code", "MST"))} ${esc(o.tax_code)}</span>`; } },
        { label: "kWh", align: "right", render: (s) => fmt.num(s.energy_kwh, 1) },
        { label: t("Amount incl. VAT", "Tiền gồm VAT"), align: "right", render: (s) => `<b>${fmt.vnd(s.cost_vnd)}</b><span class="sub">VAT ${fmt.vnd(s.cost_vnd - s.cost_vnd / (1 + VAT_RATE))}</span>` },
        { label: t("E-invoice", "HĐĐT"), render: (s) => (s.bill_status === "ON_HOLD" ? ui.badge("ON_HOLD") : `<span class="mono">${einvoiceSymbol(periodOf(s.started_at)).replace("TGN", "TGL")} · ${einvoiceNo(s.session_id)}</span><span class="sub mono">${esc(t("lookup", "tra cứu"))} ${lookupCode(s.session_id)}</span>`) },
        { label: t("Status", "Trạng thái"), render: (s) => (s.bill_status === "ON_HOLD" ? ui.badge("PENDING", "warn", t("Waiting for the bill", "Chờ chốt hóa đơn")) : ui.badge("ISSUED")) },
      ],
    });
    return `${energyCard}
      <div class="mt">${ui.card({
        title: t("Retail e-invoices per session", "Hóa đơn điện tử theo từng phiên"), sub: "PAY-11", flush: true,
        body: `<div class="toolbar">
            ${ui.select("location", [["", t("All stations", "Mọi trạm")], ...locations.map((l) => [l.location_id, l.name])], query.location || "")}
            ${ui.search("q", query.q, t("Plate", "Biển số"))}
            <span class="grow"></span><span class="small muted">${shown.length < billed.length ? t(`Showing ${shown.length} of ${billed.length}`, `Hiển thị ${shown.length}/${billed.length}`) : `${billed.length} ${t("sessions", "phiên")}`}</span>
          </div>${table}
          <div class="small muted" style="padding:10px 16px">${esc(t("Every billed session gets its own e-invoice right after the session ends; a bill on hold (energy mismatch, CHG-06) waits until staff settle it.", "Mỗi phiên đã tính tiền có hóa đơn điện tử riêng ngay khi kết thúc; phiên tạm giữ (lệch điện năng, CHG-06) chờ nhân viên xử lý."))}</div>`,
      })}</div>`;
  }

  /** Six completed months of revenue (PAY-13); months older than the mock invoices are extrapolated. */
  function revenueSeries() {
    const periods = monthsBack(6, false);
    const invs = scope.filter(db.invoices);
    const known = periods.map((p) => { const rows = invs.filter((i) => i.period === p); return rows.length ? { energy: sum(rows, (i) => i.energy_vnd), subs: sum(rows, (i) => i.subscription_vnd) } : null; });
    const first = known.find(Boolean) || { energy: 0, subs: 0 };
    return periods.map((p, i) => {
      const k = known[i] || { energy: first.energy * (0.78 + i * 0.07), subs: first.subs * (0.86 + i * 0.05) };
      return { period: p, fleet_energy_vnd: k.energy, retail_energy_vnd: k.energy * (0.2 + frac("retail-" + p) * 0.08), subscription_vnd: k.subs };
    });
  }

  function revenueTab() {
    const series = revenueSeries();
    const last = series[series.length - 1];
    const prev = series[series.length - 2];
    const total = (r) => r.fleet_energy_vnd + r.retail_energy_vnd + r.subscription_vnd;
    const growth = prev && total(prev) ? ((total(last) - total(prev)) / total(prev)) * 100 : 0;
    const subs = scope.filter(db.subscriptions);
    const byPlan = db.plans.map((p) => ({ label: p.name, value: Math.round(sum(subs.filter((s) => s.plan_id === p.plan_id), (s) => trucksOf(s.organization_id) * s.agreed_price_vnd_truck_month) / 1e6 * 10) / 10 })).filter((x) => x.value > 0);
    const orgs = scope.filter(customerOrgs());
    const recent = monthsBack(4, false);
    return `<div class="grid cols-4">
        ${ui.kpi({ label: `${t("Revenue", "Doanh thu")} ${last.period}`, icon: "chart", value: fmt.vndShort(total(last)), foot: `<span class="${growth >= 0 ? "trend-up" : "trend-down"}">${growth >= 0 ? "▲" : "▼"} ${fmt.pct(Math.abs(growth), 1)}</span> ${t("vs previous month", "so với tháng trước")}` })}
        ${ui.kpi({ label: "G3 Energy", icon: "bolt", value: fmt.vndShort(last.fleet_energy_vnd + last.retail_energy_vnd), foot: t("charging energy", "bán điện sạc") })}
        ${ui.kpi({ label: "G3 Network", icon: "layers", value: fmt.vndShort(last.subscription_vnd), foot: t("subscriptions & add-ons", "thuê bao & tiện ích") })}
        ${ui.kpi({ label: t("Retail share", "Tỷ trọng bán lẻ"), icon: "plug", value: fmt.pct((last.retail_energy_vnd / (last.fleet_energy_vnd + last.retail_energy_vnd || 1)) * 100), foot: t("charging paid in the app", "sạc thanh toán qua app") })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Energy vs subscription revenue", "Doanh thu điện và thuê bao"), sub: t("6 months, excl. VAT", "6 tháng, chưa VAT"),
          actions: ui.legend([[t("Charging · fleet invoices", "Sạc · hóa đơn đội xe"), ui.PALETTE[0]], [t("Charging · app/retail", "Sạc · app/bán lẻ"), "#5fd3a5"], [t("Subscriptions", "Thuê bao"), ui.PALETTE[1]]]),
          body: ui.barChart({ labels: series.map((r) => r.period), stacked: true, height: 240, unit: " ₫", series: [
            { name: t("Charging · fleet", "Sạc · đội xe"), values: series.map((r) => r.fleet_energy_vnd), color: ui.PALETTE[0] },
            { name: t("Charging · retail", "Sạc · bán lẻ"), values: series.map((r) => r.retail_energy_vnd), color: "#5fd3a5" },
            { name: t("Subscriptions", "Thuê bao"), values: series.map((r) => r.subscription_vnd), color: ui.PALETTE[1] },
          ] }),
        })}
        ${ui.card({ title: t("Subscription revenue by plan", "Doanh thu thuê bao theo gói"), sub: t("million ₫ / month", "triệu ₫ / tháng"), body: byPlan.length ? ui.donut({ segments: byPlan, center: fmt.num(sum(byPlan, (x) => x.value), 1), centerSub: t("M ₫ / month", "tr ₫ / tháng") }) : ui.empty() })}
      </div>
      <div class="grid cols-2 mt">
        ${ui.card({
          title: t("By company", "Theo pháp nhân"), sub: t("which entity books the revenue", "pháp nhân ghi nhận doanh thu"), flush: true,
          actions: ui.btn(t("Export", "Xuất"), { icon: "download", sm: true, action: "export-revenue" }),
          body: ui.table({
            rows: series.slice().reverse(),
            columns: [
              { label: t("Month", "Tháng"), render: (r) => `<b>${esc(r.period)}</b>` },
              { label: "G3 Energy", align: "right", render: (r) => fmt.vnd(r.fleet_energy_vnd + r.retail_energy_vnd) },
              { label: "G3 Network", align: "right", render: (r) => fmt.vnd(r.subscription_vnd) },
              { label: t("Total", "Tổng"), align: "right", render: (r) => `<b>${fmt.vnd(total(r))}</b>` },
            ],
          }) + `<div class="small muted" style="padding:10px 16px">${esc(t("Assumed split: charging revenue to G3 Energy, subscriptions to G3 Network. Who owns the tariff is still an open question (PAY-09).", "Giả định: doanh thu sạc thuộc G3 Energy, thuê bao thuộc G3 Network. Bên sở hữu biểu giá vẫn là câu hỏi mở (PAY-09)."))}</div>`,
        })}
        ${ui.card({
          title: t("By customer", "Theo khách hàng"), sub: t(`last 4 months · ${recent[0]}–${recent[3]}`, `4 tháng gần nhất · ${recent[0]}–${recent[3]}`), flush: true,
          body: ui.table({
            rows: orgs,
            columns: [
              { label: t("Customer", "Khách hàng"), render: (o) => { const s = db.subscriptions.find((x) => x.organization_id === o.organization_id); return `<b>${esc(o.short_name)}</b><span class="sub">${s ? esc(planOf(s).name) : "—"}</span>`; } },
              { label: t("Trucks", "Xe"), align: "right", render: (o) => trucksOf(o.organization_id) },
              { label: t("Energy", "Điện"), align: "right", render: (o) => fmt.vndShort(sum(db.invoices.filter((i) => i.organization_id === o.organization_id && recent.includes(i.period)), (i) => i.energy_vnd)) },
              { label: t("Subscription", "Thuê bao"), align: "right", render: (o) => fmt.vndShort(sum(db.invoices.filter((i) => i.organization_id === o.organization_id && recent.includes(i.period)), (i) => i.subscription_vnd)) },
              { label: "MRR", align: "right", render: (o) => { const s = db.subscriptions.find((x) => x.organization_id === o.organization_id); return s ? `<b>${fmt.vndShort(subscriptionMonthly(s))}</b>` : "—"; } },
            ],
          }),
        })}
      </div>`;
  }

  G3.page({
    id: "invoices",
    path: "/invoices",
    title: ["Invoices", "Hóa đơn"],
    roles: ["ACCOUNTANT", "ORG_ADMIN", "FLEET_MANAGER", "HEAD_ADMIN"],
    features: ["PAY-08", "PAY-11", "PAY-13", "CHG-05"],
    nav: { group: "billing", order: 3, icon: "receipt" },
    render({ query }) {
      const internal = scope.isInternal();
      const canRevenue = internal && scope.has("HEAD_ADMIN", "ACCOUNTANT");
      const tabs = [["monthly", t("Monthly invoices", "Hóa đơn hàng tháng")], ["retail", t("Per-session e-invoices", "HĐĐT theo phiên")]];
      if (canRevenue) tabs.push(["revenue", t("Revenue", "Doanh thu")]);
      const tab = tabs.some(([k]) => k === query.tab) ? query.tab : "monthly";
      const invoices = scope.filter(db.invoices).slice().sort((a, b) => (a.issued_at < b.issued_at ? 1 : -1));
      const lastPeriod = invoices.length ? invoices[0].period : null;
      const lastRows = invoices.filter((i) => i.period === lastPeriod);
      const open = invoices.filter((i) => i.status !== "PAID");
      const overdue = invoices.filter((i) => i.status === "OVERDUE");
      const lastKwh = sum(lastRows, (i) => i.energy_kwh);
      const body = tab === "revenue" ? revenueTab() : tab === "retail" ? retailTab(query) : monthlyTab(query, invoices);
      return `${ui.pageHead({
          title: t("Invoices", "Hóa đơn"),
          sub: esc(internal ? t("Monthly consolidated invoices per company, retail e-invoices per session and platform revenue.", "Hóa đơn gộp hàng tháng theo công ty, hóa đơn điện tử bán lẻ theo phiên và doanh thu nền tảng.") : t(`${scope.org().name} · one invoice a month with every truck's charging and the subscription`, `${scope.org().name} · mỗi tháng một hóa đơn gồm tiền sạc của từng xe và phí thuê bao`)),
          features: canRevenue ? ["PAY-08", "PAY-11", "PAY-13", "CHG-05"] : ["PAY-08", "PAY-11", "CHG-05"],
        })}
        <div class="grid cols-4">
          ${ui.kpi({ label: lastPeriod ? `${t("Invoiced", "Đã lập hóa đơn")} ${lastPeriod}` : t("Invoiced", "Đã lập hóa đơn"), icon: "receipt", value: fmt.vndShort(sum(lastRows, (i) => i.total_vnd)), foot: `${lastRows.length} ${t("invoices, incl. VAT", "hóa đơn, gồm VAT")}` })}
          ${ui.kpi({ label: t("Outstanding", "Chưa thanh toán"), icon: "clock", value: fmt.vndShort(sum(open, (i) => i.total_vnd)), foot: `${open.length} ${t("invoices", "hóa đơn")}` })}
          ${ui.kpi({ label: t("Overdue", "Quá hạn"), icon: "alert", value: overdue.length, tone: overdue.length ? "danger" : "ok", foot: overdue.length ? fmt.vnd(sum(overdue, (i) => i.total_vnd)) : t("nothing overdue", "không có hóa đơn quá hạn") })}
          ${ui.kpi({ label: lastPeriod ? `${t("Energy", "Điện năng")} ${lastPeriod}` : t("Energy", "Điện năng"), icon: "bolt", value: fmt.num(lastKwh / 1000, 1), unit: "MWh", foot: `${fmt.num(sum(lastRows, (i) => i.energy_vnd) / (lastKwh || 1))} ₫/kWh ${t("average", "trung bình")}` })}
        </div>
        <div class="mt">${ui.tabs(tabs, tab)}</div>
        ${body}`;
    },
    mount(root, { query }) {
      bindCommon(root);
      root.querySelectorAll("[data-invoice]").forEach((tr) => tr.addEventListener("click", () => invoiceDetail(tr.dataset.invoice)));
      bindActions(root, {
        "export-invoices"() {
          const rows = scope.filter(db.invoices).filter((i) => (!query.status || i.status === query.status) && (!query.period || i.period === query.period));
          ui.downloadCsv("invoices.csv", [
            { label: "invoice_number", key: "invoice_number" }, { label: "organization", csv: (i) => db.org(i.organization_id).name }, { label: "tax_code", csv: (i) => db.org(i.organization_id).tax_code },
            { label: "period", key: "period" }, { label: "energy_kwh", key: "energy_kwh" }, { label: "energy_vnd", key: "energy_vnd" }, { label: "subscription_vnd", key: "subscription_vnd" },
            { label: "vat_vnd", key: "vat_vnd" }, { label: "total_vnd", key: "total_vnd" }, { label: "status", key: "status" }, { label: "einvoice", csv: (i) => `${einvoiceSymbol(i.period)}-${einvoiceNo(i.invoice_id)}` }, { label: "lookup_code", key: "lookup_code" },
          ], rows);
        },
        "export-revenue"() {
          ui.downloadCsv("platform-revenue.csv", [
            { label: "period", key: "period" }, { label: "g3_energy_fleet_vnd", csv: (r) => Math.round(r.fleet_energy_vnd) }, { label: "g3_energy_retail_vnd", csv: (r) => Math.round(r.retail_energy_vnd) }, { label: "g3_network_subscription_vnd", csv: (r) => Math.round(r.subscription_vnd) },
          ], revenueSeries());
        },
      });
    },
  });

  /* =====================================================================
     PROMOTIONS (PAY-12)
     ===================================================================== */
  const RETAIL_APP_USERS = 1860; // drivers registered in the public app only (no fleet membership)
  const RETAIL_OPT_IN = 0.57;
  let CAMPAIGNS = null;
  function campaigns() {
    if (CAMPAIGNS) return CAMPAIGNS;
    const d = (days) => isoDay(NOW + days * DAY);
    const sales = db.people.sales.user.user_id;
    const sales2 = db.people.sales2.user.user_id;
    CAMPAIGNS = [
      { campaign_id: "cmp-101", name: ["Night charging −20%", "Sạc đêm giảm 20%"], offer_kind: "CHARGING_DISCOUNT", discount_percent: 20, window_from: "22:00", window_to: "04:00", location_ids: [], audience: "ALL_DRIVERS", organization_id: null, channels: ["APP", "SMS"], starts_on: d(-18), ends_on: d(42), status: "ACTIVE", created_by: sales, description: ["Move charging to the off-peak hours of every public station.", "Dời giờ sạc sang thấp điểm tại mọi trạm công cộng."] },
      { campaign_id: "cmp-102", name: ["10,000 km service package", "Gói bảo dưỡng mốc 10.000 km"], offer_kind: "MAINTENANCE_DEAL", deal_text: ["15% off the 10,000 km service at Xưởng Cái Mép", "Giảm 15% bảo dưỡng mốc 10.000 km tại Xưởng Cái Mép"], location_ids: [], audience: "FLEET_DECISION_MAKERS", organization_id: null, channels: ["APP", "EMAIL"], starts_on: d(-9), ends_on: d(51), status: "ACTIVE", created_by: sales2, description: ["With our partner workshop; booked through support.", "Cùng xưởng đối tác; đặt lịch qua bộ phận hỗ trợ."] },
      { campaign_id: "cmp-103", name: ["Solar hours at Cái Mép", "Giờ nắng tại Cái Mép"], offer_kind: "CHARGING_DISCOUNT", discount_percent: 12, window_from: "10:00", window_to: "15:00", location_ids: ["loc-caimep"], audience: "ORGANIZATION", organization_id: "org-tcx", channels: ["APP"], starts_on: d(-3), ends_on: d(27), status: "ACTIVE", created_by: sales, description: ["For Tân Cảng Xanh's tractors that wait at the port at midday.", "Cho đầu kéo Tân Cảng Xanh chờ hàng tại cảng buổi trưa."] },
      { campaign_id: "cmp-104", name: ["G3 Phú Mỹ opening: −30% at midday", "Khai trương G3 Phú Mỹ: giảm 30% buổi trưa"], offer_kind: "CHARGING_DISCOUNT", discount_percent: 30, window_from: "10:00", window_to: "14:00", location_ids: ["loc-phumy"], audience: "RETAIL_DRIVERS", organization_id: null, channels: ["APP", "SMS"], starts_on: d(-75), ends_on: d(-45), status: "ENDED", status_reason: "Hết thời gian chiến dịch", created_by: sales2, description: ["Opening offer for the new station on QL51.", "Ưu đãi khai trương trạm mới trên QL51."] },
      { campaign_id: "cmp-105", name: ["Tết 2027: top-up bonus", "Tết 2027: thưởng nạp ví"], offer_kind: "TOP_UP_BONUS", discount_percent: 5, location_ids: [], audience: "RETAIL_DRIVERS", organization_id: null, channels: ["APP", "SMS", "EMAIL"], starts_on: d(70), ends_on: d(100), status: "DRAFT", created_by: sales, description: ["5% extra credit on wallet top-ups from 2,000,000 ₫.", "Cộng thêm 5% khi nạp ví từ 2.000.000 ₫."] },
    ];
    return CAMPAIGNS;
  }
  const hasConsent = (userId) => frac("marketing-" + userId) < 0.6;
  function customerMembers(match) {
    const ids = new Set();
    db.memberships.forEach((m) => { const o = db.org(m.organization_id); if (o && o.kind === "CUSTOMER" && m.status === "ACTIVE" && match(m)) ids.add(m.user_id); });
    return [...ids];
  }
  function audienceStats(c) {
    let users = [];
    let retail = 0;
    if (c.audience === "ALL_DRIVERS") { users = customerMembers((m) => m.roles.includes("DRIVER")); retail = RETAIL_APP_USERS; }
    else if (c.audience === "RETAIL_DRIVERS") retail = RETAIL_APP_USERS;
    else if (c.audience === "FLEET_DECISION_MAKERS") users = customerMembers((m) => m.roles.some((r) => ["ORG_ADMIN", "FLEET_MANAGER", "ACCOUNTANT"].includes(r)));
    else users = customerMembers((m) => m.organization_id === c.organization_id);
    const opted = users.filter(hasConsent).length + Math.round(retail * RETAIL_OPT_IN);
    return { total: users.length + retail, opted };
  }
  function campaignResults(c) {
    if (c.status === "DRAFT" || c.status === "SCHEDULED") return null;
    const { total, opted } = audienceStats(c);
    const start = new Date(c.starts_on).getTime(), end = new Date(c.ends_on).getTime();
    const elapsed = c.status === "ENDED" ? 1 : Math.max(0.05, Math.min(1, (Date.now() - start) / (end - start || 1)));
    const r = seeded("results-" + c.campaign_id);
    const delivered = Math.round(opted * (0.95 + r() * 0.04));
    const opened = Math.round(delivered * (0.48 + r() * 0.2));
    const redeemed = Math.round(delivered * (0.07 + r() * 0.1) * elapsed);
    const ticket = c.offer_kind === "MAINTENANCE_DEAL" ? 4500000 : c.offer_kind === "TOP_UP_BONUS" ? 3000000 : 140 * 4100 * (1 - c.discount_percent / 100);
    return { total, opted, delivered, opened, redeemed, revenue: redeemed * ticket * (c.offer_kind === "CHARGING_DISCOUNT" ? 2.4 : 1) };
  }
  function offerText(c) {
    if (c.offer_kind === "CHARGING_DISCOUNT") return t(`${c.discount_percent}% off charging ${c.window_from}–${c.window_to}`, `Giảm ${c.discount_percent}% tiền sạc ${c.window_from}–${c.window_to}`);
    if (c.offer_kind === "TOP_UP_BONUS") return t(`+${c.discount_percent}% credit on wallet top-ups`, `Cộng ${c.discount_percent}% khi nạp ví`);
    return tx(c.deal_text);
  }
  const stationsText = (c) => (c.location_ids && c.location_ids.length ? c.location_ids.map((id) => db.location(id).name).join(", ") : t("Every public station", "Mọi trạm công cộng"));
  const audienceText = (c) => (c.audience === "ORGANIZATION" ? `${lab("ORGANIZATION")}: ${db.org(c.organization_id).short_name}` : lab(c.audience));
  const campaignTone = (s) => (s === "ACTIVE" ? "ok" : s === "SCHEDULED" ? "info" : "muted");

  function promotionsPage(query) {
    const list = campaigns();
    const status = query.status || "";
    const rows = list.filter((c) => !status || c.status === status);
    const selected = list.find((c) => c.campaign_id === query.c) || rows[0] || list[0];
    const results = list.map((c) => campaignResults(c)).filter(Boolean);
    const driverUsers = customerMembers((m) => m.roles.includes("DRIVER"));
    const managerUsers = customerMembers((m) => m.roles.some((r) => ["ORG_ADMIN", "FLEET_MANAGER", "ACCOUNTANT"].includes(r)));
    const consentGroups = [
      [t("Customer fleet drivers", "Tài xế của khách hàng"), driverUsers.length, driverUsers.filter(hasConsent).length],
      [t("Owners, managers, accountants", "Chủ, quản lý, kế toán"), managerUsers.length, managerUsers.filter(hasConsent).length],
      [t("Public app drivers", "Tài xế app công cộng"), RETAIL_APP_USERS, Math.round(RETAIL_APP_USERS * RETAIL_OPT_IN)],
    ];
    const allTotal = sum(consentGroups, (g) => g[1]);
    const allOpted = sum(consentGroups, (g) => g[2]);

    const table = ui.table({
      rows,
      empty: t("No campaign", "Không có chiến dịch"),
      selected: (c) => selected && c.campaign_id === selected.campaign_id,
      rowAttrs: (c) => `data-pick-campaign="${esc(c.campaign_id)}" style="cursor:pointer"`,
      columns: [
        { label: t("Campaign", "Chiến dịch"), render: (c) => `<b>${esc(tx(c.name))}</b><span class="sub">${esc(offerText(c))}</span>` },
        { label: t("Audience", "Đối tượng"), render: (c) => `${esc(audienceText(c))}<span class="sub">${c.channels.map((ch) => lab(ch)).join(" · ")}</span>` },
        { label: t("Period", "Thời gian"), render: (c) => `${fmt.date(c.starts_on)}<span class="sub">→ ${fmt.date(c.ends_on)}</span>` },
        { label: t("Opted in / audience", "Đồng ý / đối tượng"), align: "right", render: (c) => { const s = audienceStats(c); return `${fmt.num(s.opted)}<span class="sub">/ ${fmt.num(s.total)}</span>`; } },
        { label: t("Redeemed", "Đã dùng"), align: "right", render: (c) => { const r = campaignResults(c); return r ? `<b>${fmt.num(r.redeemed)}</b><span class="sub">${fmt.pct((r.redeemed / (r.delivered || 1)) * 100, 1)}</span>` : '<span class="muted">—</span>'; } },
        { label: t("Status", "Trạng thái"), render: (c) => pill(c.status, campaignTone(c.status)) },
      ],
    });

    let detail = ui.card({ body: ui.empty() });
    if (selected) {
      const s = audienceStats(selected);
      const r = campaignResults(selected);
      const funnel = r
        ? ui.hBars({ labels: [t("Audience", "Đối tượng"), t("Marketing consent", "Đồng ý nhận tiếp thị"), t("Delivered", "Đã gửi tới"), t("Opened", "Đã mở"), t("Redeemed", "Đã sử dụng")], series: [{ values: [r.total, r.opted, r.delivered, r.opened, r.redeemed], color: ui.PALETTE[3] }] })
        : `<div class="small muted">${esc(t(`Not sent yet: ${fmt.num(s.opted)} of ${fmt.num(s.total)} people in the audience have agreed to receive marketing.`, `Chưa gửi: ${fmt.num(s.opted)}/${fmt.num(s.total)} người trong nhóm đối tượng đã đồng ý nhận tiếp thị.`))}</div>`;
      const actions = selected.status === "DRAFT"
        ? actBtn(t("Send campaign", "Gửi chiến dịch"), "activate-campaign", selected.campaign_id, { kind: "primary", icon: "send", sm: true })
        : selected.status === "ACTIVE" || selected.status === "SCHEDULED"
          ? actBtn(t("End campaign", "Kết thúc"), "end-campaign", selected.campaign_id, { kind: "danger", sm: true })
          : actBtn(t("Duplicate as draft", "Nhân bản thành nháp"), "duplicate-campaign", selected.campaign_id, { sm: true });
      detail = ui.card({
        title: tx(selected.name), actions: pill(selected.status, campaignTone(selected.status)),
        body: `<p class="small muted" style="margin-top:0">${esc(tx(selected.description))}</p>
          ${ui.kv([
            [t("Offer", "Ưu đãi"), `${pill(selected.offer_kind, "accent")} ${esc(offerText(selected))}`],
            selected.offer_kind === "CHARGING_DISCOUNT" ? [t("Stations", "Trạm"), esc(stationsText(selected))] : null,
            [t("Audience", "Đối tượng"), esc(audienceText(selected))],
            [t("Channels", "Kênh"), selected.channels.map((ch) => ui.tag(lab(ch))).join(" ")],
            [t("Period", "Thời gian"), `${fmt.date(selected.starts_on)} → ${fmt.date(selected.ends_on)}`],
            [t("Created by", "Người tạo"), ui.userName(selected.created_by)],
            selected.status_reason ? [t("Reason", "Lý do"), esc(selected.status_reason)] : null,
            r ? [t("Revenue influenced", "Doanh thu tác động"), `<b>${fmt.vnd(r.revenue)}</b>`] : null,
          ])}
          <div class="divider"></div>
          <h4 style="margin-bottom:10px">${esc(t("Results", "Kết quả"))}</h4>
          ${funnel}
          <div class="row mt">${actions}${r ? actBtn(t("Export results", "Xuất kết quả"), "export-campaign", selected.campaign_id, { sm: true, icon: "download" }) : ""}</div>`,
      });
    }

    return `${ui.pageHead({
        title: t("Promotions", "Chiến dịch ưu đãi"),
        sub: esc(t("Offers on charging and services, sent only to people who agreed to receive marketing.", "Ưu đãi sạc điện và dịch vụ, chỉ gửi cho người đã đồng ý nhận thông tin tiếp thị.")),
        features: ["PAY-12"],
        actions: ui.btn(t("New campaign", "Tạo chiến dịch"), { icon: "plus", kind: "primary", action: "new-campaign" }),
      })}
      <div class="grid cols-5">
        ${ui.kpi({ label: t("Marketing consent", "Đồng ý nhận tiếp thị"), icon: "check", value: fmt.pct((allOpted / (allTotal || 1)) * 100), foot: `${fmt.num(allOpted)} / ${fmt.num(allTotal)} ${t("people", "người")}` })}
        ${ui.kpi({ label: t("Active campaigns", "Chiến dịch đang chạy"), icon: "send", value: list.filter((c) => c.status === "ACTIVE").length, foot: `${list.filter((c) => c.status === "DRAFT").length} ${t("drafts", "bản nháp")}` })}
        ${ui.kpi({ label: t("Delivered", "Đã gửi tới"), icon: "bell", value: fmt.num(sum(results, (r) => r.delivered)), foot: t("consenting recipients only", "chỉ người đã đồng ý") })}
        ${ui.kpi({ label: t("Redemptions", "Lượt sử dụng"), icon: "tag", value: fmt.num(sum(results, (r) => r.redeemed)), foot: `${fmt.pct((sum(results, (r) => r.redeemed) / (sum(results, (r) => r.delivered) || 1)) * 100, 1)} ${t("conversion", "chuyển đổi")}` })}
        ${ui.kpi({ label: t("Revenue influenced", "Doanh thu tác động"), icon: "chart", value: fmt.vndShort(sum(results, (r) => r.revenue)), foot: t("sessions & services with a code", "phiên sạc & dịch vụ có dùng mã") })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Campaigns", "Danh sách chiến dịch"), flush: true,
          body: `<div class="toolbar">${ui.segmented([["", t("All", "Tất cả")], ["DRAFT", G3.label("DRAFT")], ["SCHEDULED", lab("SCHEDULED")], ["ACTIVE", G3.label("ACTIVE")], ["ENDED", lab("ENDED")]], status, "status")}</div>${table}`,
        })}
        <div class="stack">
          ${detail}
          ${ui.card({
            title: t("Marketing consent", "Đồng ý nhận tiếp thị"), sub: "opt-in",
            body: `<div class="stack" style="gap:10px">${consentGroups.map(([label, total, opted]) => `<div><div class="row between small"><span>${esc(label)}</span><b class="num">${fmt.num(opted)} / ${fmt.num(total)}</b></div>${ui.meter((opted / (total || 1)) * 100)}</div>`).join("")}</div>
              <div class="divider"></div>
              <div class="small muted">${esc(t("Consent is asked in the app with the legal terms (ACC-17) and can be withdrawn from any message. People without it are never counted in a send, whatever the audience.", "Sự đồng ý được hỏi trong app cùng điều khoản pháp lý (ACC-17) và có thể rút lại từ bất kỳ tin nào. Người chưa đồng ý không bao giờ nhận chiến dịch, dù thuộc đối tượng nào."))}</div>`,
          })}
        </div>
      </div>`;
  }

  function newCampaignForm() {
    ui.formModal({
      title: t("New campaign", "Tạo chiến dịch"), wide: true,
      intro: `<p class="small muted">${esc(t("Saved as a draft. Nothing is sent until you press “Send campaign”.", "Lưu dưới dạng nháp. Chưa gửi cho đến khi bạn bấm “Gửi chiến dịch”."))}</p>`,
      fields: [
        { name: "name", label: t("Name", "Tên chiến dịch"), required: true, full: true, placeholder: t("Weekend charging −15%", "Sạc cuối tuần giảm 15%") },
        { name: "audience", label: t("Audience", "Đối tượng"), type: "select", options: ["ALL_DRIVERS", "RETAIL_DRIVERS", "FLEET_DECISION_MAKERS", "ORGANIZATION"].map((v) => [v, lab(v)]) },
        { name: "organization_id", label: t("Customer (one customer)", "Khách hàng (một khách hàng)"), type: "select", options: [["", "—"], ...customerOrgs().map((o) => [o.organization_id, o.short_name])] },
        { name: "offer_kind", label: t("Offer", "Loại ưu đãi"), type: "select", options: ["CHARGING_DISCOUNT", "MAINTENANCE_DEAL", "TOP_UP_BONUS"].map((v) => [v, lab(v)]) },
        { name: "percent", label: t("Discount or bonus (%)", "Mức giảm hoặc thưởng (%)"), type: "number", value: 15, attrs: 'min="1" max="50"' },
        { name: "window_from", label: t("Charging window from", "Khung giờ sạc từ"), type: "time", value: "22:00" },
        { name: "window_to", label: t("Charging window to", "Khung giờ sạc đến"), type: "time", value: "04:00" },
        { name: "location_id", label: t("Station", "Trạm"), type: "select", options: [["", t("Every public station", "Mọi trạm công cộng")], ...db.charging_locations.filter((l) => l.is_public && l.status === "ACTIVE").map((l) => [l.location_id, l.name])] },
        { name: "deal_text", label: t("Deal text (maintenance)", "Nội dung ưu đãi (bảo dưỡng)"), placeholder: t("10% off brake service", "Giảm 10% bảo dưỡng phanh") },
        { name: "starts_on", label: t("Starts on", "Bắt đầu"), type: "date", required: true, value: isoDay(Date.now() + 3 * DAY) },
        { name: "ends_on", label: t("Ends on", "Kết thúc"), type: "date", required: true, value: isoDay(Date.now() + 33 * DAY) },
        { name: "channels", label: t("Channels", "Kênh gửi"), type: "checkboxes", options: [["APP", G3.label("APP")], ["EMAIL", lab("EMAIL")], ["SMS", lab("SMS")]], value: ["APP"], required: true },
        { name: "description", label: t("Description", "Mô tả"), type: "textarea", full: true },
      ],
      submitLabel: t("Save draft", "Lưu nháp"),
      onSubmit(values) {
        const percent = Number(values.percent);
        if (values.ends_on <= values.starts_on) return t("The end date must be after the start date.", "Ngày kết thúc phải sau ngày bắt đầu.");
        if (values.audience === "ORGANIZATION" && !values.organization_id) return t("Pick the customer.", "Chọn khách hàng.");
        if (values.offer_kind !== "MAINTENANCE_DEAL" && !(percent >= 1 && percent <= 50)) return t("The discount or bonus must be between 1% and 50%.", "Mức giảm hoặc thưởng phải từ 1% đến 50%.");
        if (values.offer_kind === "CHARGING_DISCOUNT" && (!values.window_from || !values.window_to || values.window_from === values.window_to)) return t("Set a charging window.", "Chọn khung giờ sạc.");
        if (values.offer_kind === "MAINTENANCE_DEAL" && !values.deal_text) return t("Describe the maintenance deal.", "Nhập nội dung ưu đãi bảo dưỡng.");
        const c = {
          campaign_id: db.uid("cmp"), name: [values.name, values.name], offer_kind: values.offer_kind, discount_percent: percent, window_from: values.window_from, window_to: values.window_to,
          location_ids: values.location_id ? [values.location_id] : [], deal_text: [values.deal_text, values.deal_text], audience: values.audience, organization_id: values.audience === "ORGANIZATION" ? values.organization_id : null,
          channels: values.channels, starts_on: values.starts_on, ends_on: values.ends_on, status: "DRAFT", created_by: scope.user().user_id, description: [values.description || "—", values.description || "—"],
        };
        campaigns().unshift(c);
        G3.setQuery({ c: c.campaign_id, status: "" });
        ui.toast(t("Draft saved", "Đã lưu nháp"));
      },
    });
  }

  function activateCampaign(id) {
    const c = campaigns().find((x) => x.campaign_id === id);
    const s = audienceStats(c);
    const future = c.starts_on > todayIso();
    ui.modal({
      title: `${t("Send campaign", "Gửi chiến dịch")} · ${tx(c.name)}`,
      body: `${ui.kv([
          [t("Audience", "Đối tượng"), `${esc(audienceText(c))} · ${fmt.num(s.total)} ${esc(t("people", "người"))}`],
          [t("With marketing consent", "Đã đồng ý nhận tiếp thị"), `<b>${fmt.num(s.opted)}</b>`],
          [t("Excluded (no consent)", "Loại trừ (chưa đồng ý)"), `${fmt.num(s.total - s.opted)}`],
          [t("Channels", "Kênh"), c.channels.map((ch) => ui.tag(lab(ch))).join(" ")],
          [t("Send", "Gửi"), future ? t(`on ${fmt.date(c.starts_on)}, when the campaign starts`, `ngày ${fmt.date(c.starts_on)}, khi chiến dịch bắt đầu`) : t("now", "ngay bây giờ")],
        ])}
        <div class="mt">${ui.callout("info", esc(t("Each message carries an opt-out link; an opt-out stops every later campaign for that person.", "Mỗi tin có liên kết từ chối; người đã từ chối sẽ không nhận các chiến dịch sau.")))}</div>`,
      foot: `<button class="btn" data-close>${esc(t("Cancel", "Hủy"))}</button><button class="btn primary" data-send>${ui.icon("send", 16)}${esc(future ? t("Schedule", "Lên lịch") : t("Send now", "Gửi ngay"))}</button>`,
      onMount(el, close) {
        el.querySelector("[data-send]").addEventListener("click", () => {
          c.status = future ? "SCHEDULED" : "ACTIVE";
          c.sent_at = new Date().toISOString();
          close();
          G3.render();
          ui.toast(future ? t(`Scheduled for ${fmt.date(c.starts_on)}`, `Đã lên lịch ngày ${fmt.date(c.starts_on)}`) : t(`Sent to ${fmt.num(s.opted)} people with marketing consent`, `Đã gửi tới ${fmt.num(s.opted)} người đồng ý nhận tiếp thị`), "", "send");
        });
      },
    });
  }

  function endCampaign(id) {
    const c = campaigns().find((x) => x.campaign_id === id);
    ui.reasonModal({
      title: `${t("End campaign", "Kết thúc chiến dịch")} · ${tx(c.name)}`,
      intro: `<p class="small muted">${esc(t("Codes stop working right away; sessions already discounted keep their price.", "Mã ưu đãi hết hiệu lực ngay; các phiên đã giảm giá giữ nguyên giá."))}</p>`,
      submitLabel: t("End campaign", "Kết thúc"),
      onSubmit(reason) {
        c.status = "ENDED";
        c.status_reason = reason;
        if (c.ends_on > todayIso()) c.ends_on = todayIso();
        G3.render();
        ui.toast(t("Campaign ended", "Đã kết thúc chiến dịch"));
      },
    });
  }

  G3.page({
    id: "promotions",
    path: "/promotions",
    title: ["Promotions", "Chiến dịch ưu đãi"],
    roles: ["SALES"],
    features: ["PAY-12"],
    nav: { group: "billing", order: 4, icon: "tag" },
    render: ({ query }) => promotionsPage(query),
    mount(root) {
      bindCommon(root);
      root.querySelectorAll("[data-pick-campaign]").forEach((tr) => tr.addEventListener("click", () => G3.setQuery({ c: tr.dataset.pickCampaign })));
      bindActions(root, {
        "new-campaign": newCampaignForm,
        "activate-campaign": activateCampaign,
        "end-campaign": endCampaign,
        "duplicate-campaign"(id) {
          const c = campaigns().find((x) => x.campaign_id === id);
          const copy = { ...c, campaign_id: db.uid("cmp"), name: [c.name[0] + " (copy)", c.name[1] + " (bản sao)"], status: "DRAFT", status_reason: null, starts_on: isoDay(Date.now() + 7 * DAY), ends_on: isoDay(Date.now() + 37 * DAY), created_by: scope.user().user_id };
          campaigns().unshift(copy);
          G3.setQuery({ c: copy.campaign_id, status: "" });
          ui.toast(t("Draft created from the campaign", "Đã tạo bản nháp từ chiến dịch"));
        },
        "export-campaign"(id) {
          const c = campaigns().find((x) => x.campaign_id === id);
          const r = campaignResults(c);
          ui.downloadCsv(`campaign-${id}.csv`, [{ label: "metric", key: "metric" }, { label: "value", key: "value" }], [
            { metric: "audience", value: r.total }, { metric: "marketing_consent", value: r.opted }, { metric: "delivered", value: r.delivered },
            { metric: "opened", value: r.opened }, { metric: "redeemed", value: r.redeemed }, { metric: "revenue_influenced_vnd", value: Math.round(r.revenue) },
          ]);
        },
      });
    },
  });

  /* =====================================================================
     CARBON (CRB-01, CRB-04, CRB-07, CRB-08, CRB-09, CRB-10, CRB-11)
     ===================================================================== */
  const DIESEL_KG_CO2_PER_L = 2.68;
  const RENEWABLE_T_PER_MWH = 0.041; // life-cycle factor of solar PV used by db.carbonMonthly
  const PAYLOAD_T = { "vm-262": 9, "vm-400": 24, "vm-825": 30 };

  /* Emission factor registry (CRB-01): values illustrative; IPCC AR5 medians for the life-cycle factors. */
  const FACTORS = [
    { factor_id: "EF-GRID-2022", source: "GRID", value: 0.6766, unit: "tCO₂e/MWh", year: 2022, reference: ["MONRE notice of the 2022 grid emission factor (illustrative reference)", "Văn bản công bố hệ số phát thải lưới điện 2022 của Bộ TN&MT (tham chiếu minh họa)"], effective_from: "2024-02-01", status: "SUPERSEDED", version: 1 },
    { factor_id: "EF-GRID-2023", source: "GRID", value: db.GRID_FACTOR_T_PER_MWH, unit: "tCO₂e/MWh", year: 2023, reference: ["MONRE notice of the 2023 grid emission factor (illustrative reference)", "Văn bản công bố hệ số phát thải lưới điện 2023 của Bộ TN&MT (tham chiếu minh họa)"], effective_from: "2025-01-15", status: "ACTIVE", version: 2 },
    { factor_id: "LCA-SOLAR", source: "SOLAR", value: RENEWABLE_T_PER_MWH, unit: "tCO₂e/MWh", year: 2014, reference: ["IPCC AR5 WG III Annex III, life-cycle median", "IPCC AR5 WG III Phụ lục III, trung vị vòng đời"], effective_from: "2025-01-15", status: "ACTIVE", version: 1 },
    { factor_id: "LCA-WIND", source: "WIND", value: 0.011, unit: "tCO₂e/MWh", year: 2014, reference: ["IPCC AR5 WG III Annex III, life-cycle median", "IPCC AR5 WG III Phụ lục III, trung vị vòng đời"], effective_from: "2025-01-15", status: "ACTIVE", version: 1 },
    { factor_id: "LCA-BIOMASS", source: "BIOMASS", value: 0.23, unit: "tCO₂e/MWh", year: 2014, reference: ["IPCC AR5 WG III Annex III, dedicated biomass median", "IPCC AR5 WG III Phụ lục III, trung vị sinh khối"], effective_from: "2025-01-15", status: "ACTIVE", version: 1 },
    { factor_id: "LCA-STORAGE", source: "STORAGE", value: 0.05, unit: "tCO₂e/MWh", year: 2024, reference: ["Round-trip losses + embodied emissions of LFP storage (internal estimate)", "Tổn thất nạp-xả + phát thải hàm chứa của pin LFP (ước tính nội bộ)"], effective_from: "2025-06-01", status: "ACTIVE", version: 1 },
    { factor_id: "EF-DIESEL", source: "DIESEL", value: DIESEL_KG_CO2_PER_L, unit: "kgCO₂e/L", year: 2006, reference: ["IPCC 2006 Guidelines, Vol. 2 ch. 3, default for diesel", "Hướng dẫn IPCC 2006, Tập 2 chương 3, giá trị mặc định diesel"], effective_from: "2025-01-15", status: "ACTIVE", version: 1 },
  ];
  /* Diesel baseline per model and load (CRB-04): an equivalent diesel truck's litres per 100 km. */
  const BASELINE = [
    { vehicle_model_id: "vm-262", equivalent: ["Diesel 4x2 rigid, 18 t", "Xe tải diesel 4x2, 18 tấn"], empty_l_100km: 24, loaded_l_100km: 31, source: ["Fleet fuel logs 2024, 3 customers", "Nhật ký nhiên liệu 2024, 3 khách hàng"], version: 2, updated_at: "2026-03-10" },
    { vehicle_model_id: "vm-400", equivalent: ["Diesel 4x2 tractor, 40 t", "Đầu kéo diesel 4x2, 40 tấn"], empty_l_100km: 29, loaded_l_100km: 37, source: ["Fleet fuel logs 2024, 3 customers", "Nhật ký nhiên liệu 2024, 3 khách hàng"], version: 2, updated_at: "2026-03-10" },
    { vehicle_model_id: "vm-825", equivalent: ["Diesel 6x4 tractor, 49 t", "Đầu kéo diesel 6x4, 49 tấn"], empty_l_100km: 32, loaded_l_100km: 42, source: ["OEM data sheet + logs", "Thông số nhà sản xuất + nhật ký"], version: 1, updated_at: "2025-11-02" },
  ];
  /* REC / I-REC certificates (CRB-10). */
  const CERTIFICATES = [
    { code: "IREC-VN-2026-000412", standard: "I-REC(E)", issuer: ["I-REC registry (illustrative)", "Sổ đăng ký I-REC (minh họa)"], location_id: "loc-caimep", source: "SOLAR", volume_mwh: 180, matched_mwh: 180, vintage_from: "2026-01-01", vintage_to: "2026-03-31", valid_until: "2027-03-31", status: "RETIRED" },
    { code: "IREC-VN-2026-000588", standard: "I-REC(E)", issuer: ["I-REC registry (illustrative)", "Sổ đăng ký I-REC (minh họa)"], location_id: "loc-caimep", source: "SOLAR", volume_mwh: 205, matched_mwh: 141, vintage_from: "2026-04-01", vintage_to: "2026-06-30", valid_until: "2027-06-30", status: "ACTIVE" },
    { code: "IREC-VN-2026-000603", standard: "I-REC(E)", issuer: ["I-REC registry (illustrative)", "Sổ đăng ký I-REC (minh họa)"], location_id: "loc-catlai", source: "SOLAR", volume_mwh: 118, matched_mwh: 64, vintage_from: "2026-04-01", vintage_to: "2026-06-30", valid_until: "2027-06-30", status: "ACTIVE" },
    { code: "REC-WIND-VN-25-0091", standard: "I-REC(E)", issuer: ["I-REC registry (illustrative)", "Sổ đăng ký I-REC (minh họa)"], location_id: null, source: "WIND", volume_mwh: 300, matched_mwh: 210, vintage_from: "2025-07-01", vintage_to: "2025-12-31", valid_until: "2026-09-30", status: "EXPIRED" },
  ];
  const MRV_VERIFIER = {};

  /* db.carbonMonthly draws from the shared generator on every call, so each scope is computed once. */
  const carbonCache = {};
  function carbonRows(orgId) {
    const key = orgId || "all";
    if (!carbonCache[key]) carbonCache[key] = db.carbonMonthly(orgId, 12);
    let scale = 1;
    if (orgId && scope.fleetLimit()) {
      const all = db.vehicles.filter((v) => v.organization_id === orgId && v.live).length;
      scale = all ? scope.vehicles().filter((v) => v.live).length / all : 0;
    }
    const fields = ["distance_km", "energy_kwh", "scope2_grid_t", "scope2_renewable_t", "diesel_baseline_t", "avoided_t"];
    return carbonCache[key].map((r) => { const out = { ...r }; fields.forEach((f) => { out[f] = r[f] * scale; }); return out; });
  }
  function aggregateCarbon(rows, mode) {
    const current = periodOf(Date.now());
    if (mode === "month") return rows.map((r) => ({ ...r, label: shortPeriod(r.month), has_current: r.month === current }));
    const groups = [];
    rows.forEach((r) => {
      const [mm, yyyy] = r.month.split("/");
      const label = mode === "year" ? yyyy : `Q${Math.ceil(Number(mm) / 3)}/${yyyy.slice(-2)}`;
      let g = groups.find((x) => x.label === label);
      if (!g) { g = { label, month: label, has_current: false, distance_km: 0, energy_kwh: 0, renewable_kwh: 0, scope2_grid_t: 0, scope2_renewable_t: 0, diesel_baseline_t: 0, avoided_t: 0 }; groups.push(g); }
      g.has_current = g.has_current || r.month === current;
      ["distance_km", "energy_kwh", "scope2_grid_t", "scope2_renewable_t", "diesel_baseline_t", "avoided_t"].forEach((f) => { g[f] += r[f]; });
      g.renewable_kwh += r.energy_kwh * r.renewable_share;
    });
    groups.forEach((g) => { g.renewable_share = g.renewable_kwh / (g.energy_kwh || 1); });
    return groups;
  }
  const scope2 = (r) => r.scope2_grid_t + r.scope2_renewable_t;
  const gPerKm = (r) => (r.distance_km ? (scope2(r) * 1e6) / r.distance_km : 0);

  function niceCeil(value) {
    if (value <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(value)));
    const n = value / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }
  const CARBON_COLORS = { grid: "#d97706", renewable: "#0e9f6e", diesel: "#dc2626" };
  /** Stacked Scope 2 bars (grid + renewable) with the diesel baseline as a dashed line. */
  function carbonChart(rows, height = 250) {
    const W = 640, H = height, L = 44, R = 12, T = 12, B = 26;
    const n = rows.length || 1;
    const hi = niceCeil(Math.max(1, ...rows.map((r) => Math.max(r.diesel_baseline_t, scope2(r)))));
    const y = (v) => T + (1 - v / hi) * (H - T - B);
    const band = (W - L - R) / n;
    const bw = band * 0.52;
    let out = `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px">`;
    for (let k = 0; k <= 4; k++) {
      const v = (hi * k) / 4;
      out += `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${esc(fmt.num(v, hi < 10 ? 1 : 0))}</text>`;
    }
    rows.forEach((r, i) => {
      const x0 = L + band * i + (band - bw) / 2;
      out += `<rect x="${x0}" y="${y(r.scope2_grid_t)}" width="${bw}" height="${Math.max(0, y(0) - y(r.scope2_grid_t))}" fill="${CARBON_COLORS.grid}"/>`;
      out += `<rect x="${x0}" y="${y(scope2(r))}" width="${bw}" height="${Math.max(0, y(r.scope2_grid_t) - y(scope2(r)))}" fill="${CARBON_COLORS.renewable}"/>`;
      out += `<text x="${L + band * i + band / 2}" y="${H - 7}" text-anchor="middle">${esc(r.label)}</text>`;
    });
    const pts = rows.map((r, i) => [L + band * i + band / 2, y(r.diesel_baseline_t)]);
    out += `<path d="${pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("")}" fill="none" stroke="${CARBON_COLORS.diesel}" stroke-width="2.2" stroke-dasharray="6 4" vector-effect="non-scaling-stroke"/>`;
    pts.forEach((p) => { out += `<circle cx="${p[0]}" cy="${p[1]}" r="3" fill="${CARBON_COLORS.diesel}"/>`; });
    rows.forEach((r, i) => {
      const tip = `${r.label}: ${t("grid", "lưới")} ${fmt.num(r.scope2_grid_t, 1)} t · ${t("renewable", "tái tạo")} ${fmt.num(r.scope2_renewable_t, 1)} t · ${t("diesel baseline", "đường cơ sở diesel")} ${fmt.num(r.diesel_baseline_t, 1)} t · ${t("avoided", "giảm được")} ${fmt.num(r.avoided_t, 1)} tCO₂e`;
      out += `<rect x="${L + band * i}" y="${T}" width="${band}" height="${H - T - B}" fill="transparent" data-tip="${esc(tip)}"/>`;
    });
    return out + "</svg>";
  }

  /** Per-truck intensity over 30 days (CRB-08): Scope 2 from kWh and the truck's renewable share, tonne-km from the trips' load. */
  function truckIntensity(vehicles) {
    return vehicles.filter((v) => v.live && v.organization_id !== "org-g3").map((v) => {
      const days = db.dailyReport(v.vehicle_id, 30);
      const km = sum(days, (d) => d.distance_km);
      const kwh = sum(days, (d) => d.energy_kwh);
      const ren = 0.12 + frac("renewable-" + v.vehicle_id) * 0.25;
      const kg = kwh * (1 - ren) * db.GRID_FACTOR_T_PER_MWH + kwh * ren * RENEWABLE_T_PER_MWH;
      const trips = db.trips.filter((x) => x.vehicle_id === v.vehicle_id && x.status === "COMPLETED");
      const loaded = trips.length ? trips.filter((x) => x.declared_load === "LOADED").length / trips.length : 0.6;
      const tkm = km * (PAYLOAD_T[v.vehicle_model_id] || 10) * loaded;
      const base = BASELINE.find((b) => b.vehicle_model_id === v.vehicle_model_id);
      const dieselKgKm = base ? ((base.empty_l_100km * (1 - loaded) + base.loaded_l_100km * loaded) / 100) * DIESEL_KG_CO2_PER_L : db.DIESEL_KG_PER_KM;
      return { vehicle: v, km, kwh, ren, kg, loaded, g_km: km ? (kg * 1000) / km : 0, g_tkm: tkm ? (kg * 1000) / tkm : 0, diesel_g_km: dieselKgKm * 1000, avoided_t: (dieselKgKm * km - kg) / 1000 };
    }).sort((a, b) => b.avoided_t - a.avoided_t);
  }

  function carbonInventory(query, orgId, rows) {
    const mode = ["month", "quarter", "year"].includes(query.period) ? query.period : "month";
    const agg = aggregateCarbon(rows, mode);
    const total = { km: sum(rows, (r) => r.distance_km), kwh: sum(rows, (r) => r.energy_kwh), grid: sum(rows, (r) => r.scope2_grid_t), ren: sum(rows, (r) => r.scope2_renewable_t), diesel: sum(rows, (r) => r.diesel_baseline_t), avoided: sum(rows, (r) => r.avoided_t) };
    const renShare = sum(rows, (r) => r.energy_kwh * r.renewable_share) / (total.kwh || 1);
    const org = orgId ? db.org(orgId) : null;
    const canEdit = scope.has("ORG_ADMIN", "OPERATIONS", "HEAD_ADMIN");
    return `<div class="grid cols-4">
        ${ui.kpi({ label: t("CO₂e avoided vs diesel", "CO₂e giảm so với diesel"), icon: "leaf", value: fmt.num(total.avoided, 1), unit: "t", tone: "ok", foot: `${t("12 months", "12 tháng")} · ${fmt.pct((total.avoided / (total.diesel || 1)) * 100)} ${t("of the baseline", "so với đường cơ sở")}` })}
        ${ui.kpi({ label: t("Scope 2 total", "Tổng Scope 2"), icon: "bolt", value: fmt.num(total.grid + total.ren, 1), unit: "tCO₂e", foot: `${t("Scope 1", "Scope 1")}: 0 t · ${fmt.num(total.kwh / 1000, 0)} MWh` })}
        ${ui.kpi({ label: t("Renewable share", "Tỷ lệ điện tái tạo"), icon: "sun", value: fmt.pct(renShare * 100, 1), foot: t("solar at stations and depots", "điện mặt trời tại trạm và depot") })}
        ${ui.kpi({ label: t("Emission intensity", "Cường độ phát thải"), icon: "gauge", value: fmt.num(((total.grid + total.ren) * 1e6) / (total.km || 1), 0), unit: "gCO₂e/km", foot: `${t("diesel", "diesel")} ${fmt.num((total.diesel * 1e6) / (total.km || 1), 0)} g/km` })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Scope 2 emissions vs diesel baseline", "Phát thải Scope 2 so với đường cơ sở diesel"), sub: "tCO₂e",
          actions: ui.segmented([["month", t("Month", "Tháng")], ["quarter", t("Quarter", "Quý")], ["year", t("Year", "Năm")]], mode, "period"),
          body: `${carbonChart(agg)}<div class="mt">${ui.legend([[t("Scope 2 · grid", "Scope 2 · lưới điện"), CARBON_COLORS.grid], [t("Scope 2 · renewable", "Scope 2 · tái tạo"), CARBON_COLORS.renewable], [t("Diesel baseline (dashed)", "Đường cơ sở diesel (nét đứt)"), CARBON_COLORS.diesel]])}</div>`,
        })}
        ${ui.card({
          title: t("Inventory boundaries & methodology", "Ranh giới kiểm kê & phương pháp"), sub: "CRB-07",
          actions: canEdit ? ui.btn(t("Edit", "Sửa"), { sm: true, icon: "edit", action: "edit-boundaries" }) : "",
          body: ui.kv([
            [t("Organization", "Tổ chức"), org ? esc(org.name) : esc(t("Every customer (internal view)", "Mọi khách hàng (góc nhìn nội bộ)"))],
            [t("Consolidation", "Hợp nhất"), esc(t("Operational control: trucks owned or leased", "Kiểm soát vận hành: xe sở hữu hoặc thuê"))],
            [t("Scope 1", "Scope 1"), esc(t("None: battery-electric trucks burn no fuel", "Không có: xe điện không đốt nhiên liệu"))],
            [t("Scope 2", "Scope 2"), esc(t("Electricity charged at G3 stations and depots, location-based", "Điện sạc tại trạm G3 và depot, theo vị trí (location-based)"))],
            [t("Grid factor", "Hệ số lưới"), `<b>${fmt.num(db.GRID_FACTOR_T_PER_MWH, 4)}</b> tCO₂e/MWh <span class="tag">EF-GRID-2023 v2</span>`],
            [t("Renewable factor", "Hệ số tái tạo"), `${fmt.num(RENEWABLE_T_PER_MWH, 3)} tCO₂e/MWh <span class="muted small">(${esc(t("life-cycle, solar", "vòng đời, mặt trời"))})</span>`],
            [t("Energy measured", "Đo điện năng"), esc(t("Charger meter (OCPP), losses included", "Công tơ trụ sạc (OCPP), đã gồm tổn thất"))],
            [t("Standard", "Chuẩn"), "GHG Protocol · ISO 14064-1"],
            [t("Base year", "Năm cơ sở"), "2026"],
          ]),
        })}
      </div>
      <div class="mt">${ui.card({
        title: t("Inventory by period", "Kiểm kê theo kỳ"), sub: t("Scope 1 & 2, tCO₂e", "Scope 1 & 2, tCO₂e"), flush: true,
        actions: ui.btn(t("Export GHG dataset", "Xuất bộ dữ liệu KNK"), { icon: "download", sm: true, kind: "primary", action: "export-ghg" }),
        body: ui.table({
          rows: agg.slice().reverse(),
          columns: [
            { label: t("Period", "Kỳ"), render: (r) => `<b>${esc(mode === "month" ? r.month : r.label)}</b>` },
            { label: t("Distance", "Quãng đường"), align: "right", render: (r) => fmt.km(r.distance_km) },
            { label: t("Energy", "Điện năng"), align: "right", render: (r) => `${fmt.num(r.energy_kwh / 1000, 1)} MWh` },
            { label: t("Renewable", "Tái tạo"), align: "right", render: (r) => fmt.pct(r.renewable_share * 100, 1) },
            { label: "Scope 1", align: "right", render: () => fmt.num(0, 1) },
            { label: t("Scope 2 grid", "Scope 2 lưới"), align: "right", render: (r) => fmt.num(r.scope2_grid_t, 1) },
            { label: t("Scope 2 renewable", "Scope 2 tái tạo"), align: "right", render: (r) => fmt.num(r.scope2_renewable_t, 1) },
            { label: t("Diesel baseline", "Đường cơ sở diesel"), align: "right", render: (r) => fmt.num(r.diesel_baseline_t, 1) },
            { label: t("Avoided", "Giảm được"), align: "right", render: (r) => `<b style="color:var(--ok)">${fmt.num(r.avoided_t, 1)}</b>` },
            { label: "gCO₂e/km", align: "right", render: (r) => fmt.num(gPerKm(r), 0) },
            { label: t("Status", "Trạng thái"), render: (r) => (r.has_current ? pill("PROVISIONAL", "warn") : pill("FINAL", "ok")) },
          ],
        }) + `<div class="small muted" style="padding:10px 16px">${esc(t("The export follows the enterprise GHG inventory submission layout (activity data, emission factor and its legal reference, scope, tCO₂e per period) — CRB-09.", "Bản xuất theo mẫu nộp kiểm kê khí nhà kính cấp doanh nghiệp (dữ liệu hoạt động, hệ số phát thải và căn cứ pháp lý, phạm vi, tCO₂e theo kỳ) — CRB-09."))}</div>`,
      })}</div>`;
  }

  function carbonIntensity() {
    const rows = truckIntensity(scope.vehicles());
    const totalAvoided = sum(rows, (r) => r.avoided_t);
    const maxAvoided = Math.max(0.1, ...rows.map((r) => r.avoided_t));
    const visible = scope.vehicleIds();
    const trips = db.trips.filter((x) => visible.has(x.vehicle_id) && x.status === "COMPLETED" && x.distance_km && x.energy_kwh);
    const routes = {};
    trips.forEach((x) => {
      const k = `${x.origin} → ${x.destination}`;
      routes[k] = routes[k] || { route: k, n: 0, km: 0, kwh: 0, loaded: 0, tkm: 0 };
      const r = routes[k];
      r.n += 1; r.km += x.distance_km; r.kwh += x.energy_kwh;
      if (x.declared_load === "LOADED") { r.loaded += 1; r.tkm += x.distance_km * (PAYLOAD_T[db.vehicle(x.vehicle_id).vehicle_model_id] || 10); }
    });
    const avgFactor = 0.8 * db.GRID_FACTOR_T_PER_MWH + 0.2 * RENEWABLE_T_PER_MWH;
    const routeRows = Object.values(routes).sort((a, b) => b.n - a.n).slice(0, 8);
    return `${ui.callout("info", esc(t(`Last 30 days. Each truck's Scope 2 uses its own renewable share (where and when it charged); tonne-km uses the declared load of its trips and the model's typical payload. Total avoided: ${fmt.num(totalAvoided, 1)} tCO₂e.`, `30 ngày gần nhất. Scope 2 của từng xe dùng tỷ lệ tái tạo riêng (nơi và giờ sạc); tấn-km dùng tải khai báo của các chuyến và tải trọng điển hình của mẫu xe. Tổng giảm được: ${fmt.num(totalAvoided, 1)} tCO₂e.`)))}
      <div class="mt">${ui.card({
        title: t("Intensity per truck", "Cường độ phát thải theo xe"), sub: "CRB-08", flush: true,
        actions: ui.btn(t("Export", "Xuất"), { icon: "download", sm: true, action: "export-intensity" }),
        body: ui.table({
          rows,
          empty: t("No truck with data", "Không có xe có dữ liệu"),
          href: G3.canSee("vehicle-detail") ? (r) => `/vehicles/${r.vehicle.vehicle_id}` : null,
          columns: [
            { label: t("Truck", "Xe"), render: (r) => ui.vehicleCell(r.vehicle) },
            { label: t("Model", "Mẫu xe"), render: (r) => esc(db.model(r.vehicle.vehicle_model_id).code) },
            { label: t("Distance", "Quãng đường"), align: "right", render: (r) => fmt.km(r.km) },
            { label: "kWh/km", align: "right", render: (r) => fmt.num(r.km ? r.kwh / r.km : 0, 2) },
            { label: t("Renewable", "Tái tạo"), align: "right", render: (r) => fmt.pct(r.ren * 100) },
            { label: t("Loaded trips", "Chuyến có hàng"), align: "right", render: (r) => fmt.pct(r.loaded * 100) },
            { label: "gCO₂e/km", align: "right", render: (r) => `<b>${fmt.num(r.g_km, 0)}</b><span class="sub">diesel ${fmt.num(r.diesel_g_km, 0)}</span>` },
            { label: "gCO₂e/t·km", align: "right", render: (r) => fmt.num(r.g_tkm, 1) },
            { label: t("Avoided (t)", "Giảm được (t)"), render: (r) => `<div style="min-width:120px"><div class="row between small"><span></span><b class="num" style="color:var(--ok)">${fmt.num(r.avoided_t, 2)}</b></div>${ui.meter((r.avoided_t / maxAvoided) * 100)}</div>` },
          ],
        }),
      })}</div>
      <div class="mt">${ui.card({
        title: t("Intensity per route", "Cường độ phát thải theo tuyến"), sub: t("completed trips", "chuyến đã hoàn tất"), flush: true,
        body: ui.table({
          rows: routeRows,
          empty: t("No completed trip", "Chưa có chuyến hoàn tất"),
          columns: [
            { label: t("Route", "Tuyến"), render: (r) => `<b>${esc(r.route)}</b>` },
            { label: t("Trips", "Chuyến"), align: "right", render: (r) => r.n },
            { label: t("Distance", "Quãng đường"), align: "right", render: (r) => fmt.km(r.km) },
            { label: "kWh/km", align: "right", render: (r) => fmt.num(r.kwh / r.km, 2) },
            { label: t("Loaded", "Có hàng"), align: "right", render: (r) => fmt.pct((r.loaded / r.n) * 100) },
            { label: "gCO₂e/km", align: "right", render: (r) => `<b>${fmt.num((r.kwh * avgFactor * 1000) / r.km, 0)}</b>` },
            { label: "gCO₂e/t·km", align: "right", render: (r) => (r.tkm ? fmt.num((r.kwh * avgFactor * 1000) / r.tkm, 1) : "—") },
            { label: t("Avoided", "Giảm được"), align: "right", render: (r) => `${fmt.num((r.km * db.DIESEL_KG_PER_KM - r.kwh * avgFactor) / 1000, 2)} t` },
          ],
        }),
      })}</div>`;
  }

  function carbonFactors() {
    const log = carbonRows(null).slice().reverse();
    const current = periodOf(Date.now());
    return `<div class="grid side">
        ${ui.card({
          title: t("Emission factor registry", "Danh mục hệ số phát thải"), sub: "CRB-01", flush: true,
          actions: ui.btn(t("New version", "Thêm phiên bản"), { icon: "plus", sm: true, kind: "primary", action: "new-factor" }),
          body: ui.table({
            rows: FACTORS,
            columns: [
              { label: t("Factor", "Hệ số"), render: (f) => `<b>${esc(lab(f.source))}</b><span class="sub mono">${esc(f.factor_id)} · v${f.version}</span>` },
              { label: t("Value", "Giá trị"), align: "right", render: (f) => `<b>${fmt.num(f.value, f.value < 1 ? 4 : 2)}</b><span class="sub">${esc(f.unit)}</span>` },
              { label: t("Data year", "Năm dữ liệu"), render: (f) => f.year },
              { label: t("Legal / scientific reference", "Căn cứ pháp lý / khoa học"), render: (f) => `<span class="small">${esc(tx(f.reference))}</span>` },
              { label: t("Effective", "Hiệu lực"), render: (f) => fmt.date(f.effective_from) },
              { label: t("Status", "Trạng thái"), render: (f) => pill(f.status, f.status === "ACTIVE" ? "ok" : "muted") },
            ],
          }),
        })}
        ${ui.card({
          title: t("Versions applied per period", "Phiên bản áp dụng theo kỳ"), flush: true,
          body: ui.table({
            rows: log,
            columns: [
              { label: t("Period", "Kỳ"), render: (r) => `<b>${esc(r.month)}</b>` },
              { label: t("Grid", "Lưới"), render: () => '<span class="mono small">EF-GRID-2023 v2</span>' },
              { label: t("Life-cycle", "Vòng đời"), render: () => '<span class="mono small">LCA-SOLAR v1</span>' },
              { label: t("Run", "Tính lúc"), render: (r) => (r.month === current ? pill("PROVISIONAL", "warn") : pill("FINAL", "ok")) },
            ],
          }) + `<div class="small muted" style="padding:10px 16px">${esc(t("A new factor version never rewrites a final period silently: recalculation is a new run, logged next to the old one.", "Phiên bản hệ số mới không âm thầm ghi đè kỳ đã chốt: tính lại là một lần chạy mới, lưu cạnh lần cũ."))}</div>`,
        })}
      </div>`;
  }

  function carbonBaseline() {
    return `${ui.card({
        title: t("Diesel baseline per model and load", "Đường cơ sở diesel theo mẫu xe và tải"), sub: "CRB-04", flush: true,
        body: ui.table({
          rows: BASELINE,
          columns: [
            { label: t("Electric model", "Mẫu xe điện"), render: (b) => `<b>${esc(db.model(b.vehicle_model_id).name)}</b><span class="sub">${esc(db.model(b.vehicle_model_id).kind)}</span>` },
            { label: t("Diesel equivalent", "Xe diesel tương đương"), render: (b) => esc(tx(b.equivalent)) },
            { label: t("Empty", "Xe rỗng"), align: "right", render: (b) => `${fmt.num(b.empty_l_100km)} L/100 km<span class="sub">${fmt.num((b.empty_l_100km / 100) * DIESEL_KG_CO2_PER_L * 1000, 0)} gCO₂e/km</span>` },
            { label: t("Loaded", "Có hàng"), align: "right", render: (b) => `${fmt.num(b.loaded_l_100km)} L/100 km<span class="sub">${fmt.num((b.loaded_l_100km / 100) * DIESEL_KG_CO2_PER_L * 1000, 0)} gCO₂e/km</span>` },
            { label: t("Source", "Nguồn"), render: (b) => `<span class="small">${esc(tx(b.source))}</span><span class="sub">v${b.version} · ${fmt.date(b.updated_at)}</span>` },
            { label: "", render: (b) => actBtn(t("Edit", "Sửa"), "edit-baseline", b.vehicle_model_id, { sm: true, icon: "edit" }) },
          ],
        }),
      })}
      <div class="grid cols-2 mt">
        ${ui.card({ title: t("Diesel emission factor", "Hệ số phát thải diesel"), body: ui.kv([[t("Value", "Giá trị"), `<b>${fmt.num(DIESEL_KG_CO2_PER_L, 2)}</b> kgCO₂e/L`], [t("Reference", "Căn cứ"), esc(tx(FACTORS.find((f) => f.source === "DIESEL").reference))], [t("Fleet average used in reports", "Trung bình dùng trong báo cáo"), `${fmt.num(db.DIESEL_KG_PER_KM, 2)} kgCO₂e/km`]]) })}
        ${ui.card({ title: t("How the baseline is used", "Cách dùng đường cơ sở"), body: `<div class="small">${esc(t("CO₂ avoided = what an equivalent diesel truck would have emitted on the same distance and load − the electric truck's Scope 2. The load comes from the trip (declared, checked against the inferred load).", "CO₂ giảm được = lượng xe diesel tương đương sẽ phát thải trên cùng quãng đường và tải − Scope 2 của xe điện. Tải lấy từ chuyến (khai báo, đối chiếu với tải suy luận)."))}</div>` })}
      </div>`;
  }

  function carbonCertificates() {
    const kwhAll = sum(carbonRows(null), (r) => r.energy_kwh * r.renewable_share) / 1000;
    const matched = sum(CERTIFICATES, (c) => c.matched_mwh);
    return `<div class="grid cols-3">
        ${ui.kpi({ label: t("Certificates", "Chứng chỉ"), icon: "file", value: CERTIFICATES.length, foot: `${CERTIFICATES.filter((c) => c.status === "ACTIVE").length} ${t("active", "còn hiệu lực")}` })}
        ${ui.kpi({ label: t("Volume matched", "Sản lượng đã khớp"), icon: "check", value: fmt.num(matched), unit: "MWh", foot: `${t("of", "trên")} ${fmt.num(sum(CERTIFICATES, (c) => c.volume_mwh))} MWh` })}
        ${ui.kpi({ label: t("Renewable charged (12 mo)", "Điện tái tạo đã sạc (12 tháng)"), icon: "sun", value: fmt.num(kwhAll), unit: "MWh", foot: t("claimed only with a certificate", "chỉ công bố khi có chứng chỉ") })}
      </div>
      <div class="mt">${ui.card({
        title: t("REC / I-REC certificates", "Chứng chỉ REC / I-REC"), sub: "CRB-10", flush: true,
        actions: ui.btn(t("Record certificate", "Thêm chứng chỉ"), { icon: "plus", sm: true, kind: "primary", action: "new-certificate" }),
        body: ui.table({
          rows: CERTIFICATES,
          columns: [
            { label: t("Code", "Mã"), render: (c) => `<b class="mono">${esc(c.code)}</b><span class="sub">${esc(c.standard)} · ${esc(tx(c.issuer))}</span>` },
            { label: t("Generation", "Nguồn phát"), render: (c) => `${esc(lab(c.source))}<span class="sub">${c.location_id ? esc(db.location(c.location_id).name) : esc(t("Purchased (unbundled)", "Mua riêng (không kèm điện)"))}</span>` },
            { label: t("Vintage", "Kỳ phát điện"), render: (c) => `${fmt.date(c.vintage_from)} → ${fmt.date(c.vintage_to)}` },
            { label: t("Matched / volume", "Đã khớp / sản lượng"), render: (c) => `<div style="min-width:150px"><div class="row between small"><span class="num">${fmt.num(c.matched_mwh)} / ${fmt.num(c.volume_mwh)} MWh</span></div>${ui.meter((c.matched_mwh / c.volume_mwh) * 100)}</div>` },
            { label: t("Valid until", "Hiệu lực đến"), render: (c) => fmt.date(c.valid_until) },
            { label: t("Status", "Trạng thái"), render: (c) => pill(c.status, c.status === "ACTIVE" ? "ok" : c.status === "EXPIRED" ? "danger" : "muted") },
          ],
        }),
      })}</div>`;
  }

  function carbonMrv(query) {
    const year = new Date(NOW).getFullYear();
    const periods = [["P1", `01/${year}`, `06/${year}`, [1, 2, 3, 4, 5, 6]], ["P2", `07/${year}`, `12/${year}`, [7, 8, 9, 10, 11, 12]]];
    const pick = periods.find((p) => p[0] === query.mp) || periods[0];
    const rows = carbonRows(null).filter((r) => r.month.endsWith(String(year)) && pick[3].includes(Number(r.month.slice(0, 2))));
    const complete = rows.length === 6;
    const er = sum(rows, (r) => r.avoided_t);
    const baseline = sum(rows, (r) => r.diesel_baseline_t);
    const project = sum(rows, (r) => scope2(r));
    const verifier = MRV_VERIFIER[pick[0]];
    const sections = [
      [t("Project description & boundary", "Mô tả dự án & ranh giới"), "COMPLETED", t("Electric trucks of every customer fleet charged on the G3 network", "Xe tải điện của mọi đội xe khách hàng sạc trên mạng lưới G3")],
      [t("Activity data: km per truck", "Dữ liệu hoạt động: km theo xe"), complete ? "COMPLETED" : "IN_PROGRESS", t("T-Box odometer, daily, every truck", "Odometer T-Box, hằng ngày, mọi xe")],
      [t("Energy per session", "Điện năng theo phiên"), complete ? "COMPLETED" : "IN_PROGRESS", t("Charger meter values (OCPP), CHG-07", "Chỉ số công tơ trụ sạc (OCPP), CHG-07")],
      [t("Baseline emissions", "Phát thải đường cơ sở"), "COMPLETED", t("Diesel baseline per model and load (CRB-04)", "Đường cơ sở diesel theo mẫu xe và tải (CRB-04)")],
      [t("Project emissions", "Phát thải dự án"), "COMPLETED", t("Scope 2 from the inventory (CRB-07)", "Scope 2 từ kiểm kê (CRB-07)")],
      [t("Leakage", "Rò rỉ"), "COMPLETED", t("None identified (diesel trucks scrapped, not resold)", "Không có (xe diesel thay thế đã thanh lý, không bán lại)")],
      [t("Meter calibration certificates", "Chứng nhận kiểm định công tơ"), pick[0] === "P1" ? "COMPLETED" : "PENDING", t("18 of 21 chargers uploaded", "Đã tải 18/21 trụ sạc")],
      [t("Double-counting check", "Kiểm tra tính trùng"), "PENDING", t("Against customers' own claims and I-REC retirements (CRB-10)", "Đối chiếu với công bố của khách hàng và I-REC đã sử dụng (CRB-10)")],
      [t("Verification by an accredited verifier", "Thẩm định bởi đơn vị được công nhận"), verifier ? "IN_PROGRESS" : "PENDING", verifier ? `${verifier.name} · ${fmt.date(verifier.sent_at)}` : t("Not sent", "Chưa gửi")],
    ];
    const done = sections.filter((s) => s[1] === "COMPLETED").length;
    return `<div class="grid cols-4">
        ${ui.kpi({ label: t("Emission reductions", "Lượng giảm phát thải"), icon: "leaf", value: fmt.num(er, 1), unit: "tCO₂e", tone: "ok", foot: `${pick[1]} – ${pick[2]}${complete ? "" : " · " + t("in progress", "đang diễn ra")}` })}
        ${ui.kpi({ label: t("Baseline emissions", "Phát thải đường cơ sở"), icon: "truck", value: fmt.num(baseline, 1), unit: "t", foot: "CRB-04" })}
        ${ui.kpi({ label: t("Project emissions", "Phát thải dự án"), icon: "bolt", value: fmt.num(project, 1), unit: "t", foot: "CRB-07 Scope 2" })}
        ${ui.kpi({ label: t("Dossier", "Hồ sơ"), icon: "file", value: `${done}<small>/ ${sections.length}</small>`, foot: t("sections complete", "phần đã hoàn tất") })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("MRV dossier", "Hồ sơ MRV"), sub: t("monitoring period", "kỳ giám sát"), flush: true,
          actions: ui.segmented(periods.map((p) => [p[0], `${p[0]} · ${p[1]}–${p[2]}`]), pick[0], "mp"),
          body: `<ul class="list">${sections.map(([title, status, note]) => `<li><div class="sev-icon ${status === "COMPLETED" ? "INFO" : "WARNING"}">${ui.icon(status === "COMPLETED" ? "check" : "clock", 15)}</div><div class="grow"><div class="title">${esc(title)}</div><div class="meta">${esc(note)}</div></div>${status === "IN_PROGRESS" ? ui.badge(status, "info", t("In progress", "Đang thực hiện")) : ui.badge(status)}</li>`).join("")}</ul>`,
        })}
        ${ui.card({
          title: t("Project", "Dự án"), sub: "CRB-11",
          body: `${ui.kv([
              [t("Name", "Tên"), esc(t("Electric truck switch — southern Vietnam corridor", "Chuyển đổi xe tải điện — hành lang phía Nam"))],
              [t("Methodology", "Phương pháp luận"), "CDM AMS-III.C"],
              [t("Proponent", "Chủ dự án"), "G3 Network"],
              [t("Trucks", "Số xe"), fmt.num(db.vehicles.filter((v) => v.live && v.organization_id !== "org-g3").length)],
              [t("Distance", "Quãng đường"), fmt.km(sum(rows, (r) => r.distance_km))],
              [t("Energy", "Điện năng"), `${fmt.num(sum(rows, (r) => r.energy_kwh) / 1000, 1)} MWh`],
              [t("Factor versions", "Phiên bản hệ số"), '<span class="mono small">EF-GRID-2023 v2 · LCA-SOLAR v1</span>'],
            ])}
            <div class="row mt">${ui.btn(t("Assemble dossier", "Tổng hợp hồ sơ"), { icon: "download", kind: "primary", sm: true, action: "assemble-mrv", attrs: `data-id="${pick[0]}"` })}${ui.btn(t("Send to verifier", "Gửi thẩm định"), { icon: "send", sm: true, action: "send-mrv", attrs: `data-id="${pick[0]}"` })}</div>
            <div class="small muted mt">${esc(t("Customers' own inventories (CRB-07) and the project use the same data; the double-counting check makes sure a tonne is claimed only once.", "Kiểm kê của khách hàng (CRB-07) và dự án dùng cùng dữ liệu; kiểm tra tính trùng bảo đảm mỗi tấn chỉ được công bố một lần."))}</div>`,
        })}
      </div>`;
  }

  function carbonPage(query) {
    const internal = scope.isInternal();
    const staff = internal && scope.has("OPERATIONS", "HEAD_ADMIN");
    const tabs = [["inventory", t("Scope 1 & 2 inventory", "Kiểm kê Scope 1 & 2")], ["intensity", t("Intensity & EV vs diesel", "Cường độ & so sánh diesel")]];
    if (staff) tabs.push(["factors", t("Emission factors", "Hệ số phát thải")], ["baseline", t("Diesel baseline", "Đường cơ sở diesel")], ["certificates", t("Certificates", "Chứng chỉ REC")], ["mrv", t("MRV dossier", "Hồ sơ MRV")]);
    const tab = tabs.some(([k]) => k === query.tab) ? query.tab : "inventory";
    const orgId = internal ? scope.orgFilter() : scope.org().organization_id;
    const rows = carbonRows(orgId);
    let body;
    if (tab === "intensity") body = carbonIntensity();
    else if (tab === "factors") body = carbonFactors();
    else if (tab === "baseline") body = carbonBaseline();
    else if (tab === "certificates") body = carbonCertificates();
    else if (tab === "mrv") body = carbonMrv(query);
    else body = carbonInventory(query, orgId, rows);
    const limit = scope.fleetLimit();
    return `${ui.pageHead({
        title: t("Carbon & emissions", "Carbon & phát thải"),
        sub: esc(limit ? t("Limited to the trucks of your fleets (FLT-03).", "Giới hạn trong các xe thuộc đội xe của bạn (FLT-03).") : internal ? (orgId ? t(`Filtered to ${db.org(orgId).name}`, `Đang lọc theo ${db.org(orgId).name}`) : t("Every customer fleet · internal view", "Mọi đội xe khách hàng · góc nhìn nội bộ")) : t(`${scope.org().name} · greenhouse-gas inventory of the fleet`, `${scope.org().name} · kiểm kê khí nhà kính của đội xe`)),
        features: staff ? ["CRB-07", "CRB-08", "CRB-09", "CRB-01", "CRB-04", "CRB-10", "CRB-11"] : ["CRB-07", "CRB-08", "CRB-09"],
      })}
      ${ui.tabs(tabs, tab)}
      ${body}`;
  }

  function exportGhg(orgId) {
    const rows = carbonRows(orgId);
    const org = orgId ? db.org(orgId) : null;
    const out = [];
    rows.forEach((r) => {
      const base = { organization: org ? org.name : "ALL CUSTOMERS", tax_code: org ? org.tax_code : "", period: r.month };
      out.push({ ...base, scope: "1", category: "Mobile combustion", activity_data: 0, unit: "L diesel", emission_factor: DIESEL_KG_CO2_PER_L, factor_unit: "kgCO2e/L", factor_reference: "EF-DIESEL v1", tco2e: 0 });
      out.push({ ...base, scope: "2", category: "Purchased electricity - grid", activity_data: Math.round(r.energy_kwh * (1 - r.renewable_share)) / 1000, unit: "MWh", emission_factor: db.GRID_FACTOR_T_PER_MWH, factor_unit: "tCO2e/MWh", factor_reference: "EF-GRID-2023 v2", tco2e: Math.round(r.scope2_grid_t * 10) / 10 });
      out.push({ ...base, scope: "2", category: "Electricity - on-site solar", activity_data: Math.round(r.energy_kwh * r.renewable_share) / 1000, unit: "MWh", emission_factor: RENEWABLE_T_PER_MWH, factor_unit: "tCO2e/MWh", factor_reference: "LCA-SOLAR v1", tco2e: Math.round(r.scope2_renewable_t * 10) / 10 });
    });
    ui.downloadCsv(`ghg-inventory-${org ? org.tax_code : "all"}.csv`, ["organization", "tax_code", "period", "scope", "category", "activity_data", "unit", "emission_factor", "factor_unit", "factor_reference", "tco2e"].map((k) => ({ label: k, key: k })), out);
  }

  G3.page({
    id: "carbon",
    path: "/carbon",
    title: ["Carbon & emissions", "Carbon & phát thải"],
    roles: ["FLEET_MANAGER", "ORG_ADMIN", "OPERATIONS", "HEAD_ADMIN"],
    features: ["CRB-01", "CRB-04", "CRB-07", "CRB-08", "CRB-09", "CRB-10", "CRB-11"],
    nav: { group: "sustainability", order: 1, icon: "leaf", label: ["Carbon", "Carbon"] },
    render: ({ query }) => carbonPage(query),
    mount(root) {
      bindCommon(root);
      const orgId = scope.isInternal() ? scope.orgFilter() : scope.org().organization_id;
      bindActions(root, {
        "export-ghg": () => exportGhg(orgId),
        "export-intensity"() {
          ui.downloadCsv("emission-intensity.csv", [
            { label: "license_plate", csv: (r) => r.vehicle.license_plate }, { label: "vin", csv: (r) => r.vehicle.vin }, { label: "distance_km", csv: (r) => Math.round(r.km) },
            { label: "energy_kwh", csv: (r) => Math.round(r.kwh) }, { label: "renewable_share", csv: (r) => r.ren.toFixed(3) }, { label: "gco2e_per_km", csv: (r) => Math.round(r.g_km) },
            { label: "gco2e_per_tkm", csv: (r) => r.g_tkm.toFixed(1) }, { label: "avoided_t", csv: (r) => r.avoided_t.toFixed(3) },
          ], truckIntensity(scope.vehicles()));
        },
        "edit-boundaries"() {
          ui.formModal({
            title: t("Inventory boundaries", "Ranh giới kiểm kê"),
            fields: [
              { name: "approach", label: t("Consolidation approach", "Cách hợp nhất"), type: "select", options: [["OPERATIONAL", t("Operational control", "Kiểm soát vận hành")], ["FINANCIAL", t("Financial control", "Kiểm soát tài chính")], ["EQUITY", t("Equity share", "Tỷ lệ vốn góp")]] },
              { name: "base_year", label: t("Base year", "Năm cơ sở"), type: "number", value: 2026, attrs: 'min="2020" max="2030"' },
              { name: "depots", label: t("Depot chargers", "Trụ sạc tại depot"), type: "checkbox", value: true, checkLabel: t("Include energy charged at our own depot", "Gồm điện sạc tại depot của chúng tôi") },
              { name: "reason", label: t("Reason", "Lý do"), type: "textarea", required: true },
            ],
            submitLabel: t("Save", "Lưu"),
            onSubmit(values) {
              const year = Number(values.base_year);
              if (!(year >= 2020 && year <= 2030)) return t("The base year must be between 2020 and 2030.", "Năm cơ sở phải từ 2020 đến 2030.");
              ui.toast(t("Boundaries saved; the next run uses them", "Đã lưu ranh giới; lần tính tiếp theo sẽ áp dụng"));
            },
          });
        },
        "new-factor"() {
          ui.formModal({
            title: t("New emission factor version", "Thêm phiên bản hệ số phát thải"), wide: true,
            fields: [
              { name: "source", label: t("Factor", "Hệ số"), type: "select", options: ["GRID", "SOLAR", "WIND", "BIOMASS", "STORAGE", "DIESEL"].map((s) => [s, lab(s)]) },
              { name: "value", label: t("Value", "Giá trị"), type: "number", required: true, attrs: 'step="0.0001" min="0"', hint: t("tCO₂e/MWh (kgCO₂e/L for diesel)", "tCO₂e/MWh (kgCO₂e/L với diesel)") },
              { name: "year", label: t("Data year", "Năm dữ liệu"), type: "number", required: true, value: 2024 },
              { name: "effective_from", label: t("Effective from", "Hiệu lực từ"), type: "date", required: true, value: todayIso() },
              { name: "reference", label: t("Legal document / reference", "Văn bản pháp lý / căn cứ"), required: true, full: true },
            ],
            submitLabel: t("Publish version", "Ban hành phiên bản"),
            onSubmit(values) {
              const value = Number(values.value);
              if (!(value >= 0) || values.value === "") return t("Enter a value of 0 or more.", "Nhập giá trị từ 0 trở lên.");
              const previous = FACTORS.filter((f) => f.source === values.source && f.status === "ACTIVE");
              previous.forEach((f) => { f.status = "SUPERSEDED"; });
              const version = Math.max(0, ...FACTORS.filter((f) => f.source === values.source).map((f) => f.version)) + 1;
              FACTORS.unshift({ factor_id: `${values.source === "GRID" ? "EF-GRID" : values.source === "DIESEL" ? "EF-DIESEL" : "LCA-" + values.source}-${values.year}`, source: values.source, value, unit: values.source === "DIESEL" ? "kgCO₂e/L" : "tCO₂e/MWh", year: Number(values.year), reference: values.reference, effective_from: values.effective_from, status: "ACTIVE", version });
              G3.render();
              const affected = carbonRows(null).filter((r) => { const [mm, yyyy] = r.month.split("/"); return `${yyyy}-${mm}` >= values.effective_from.slice(0, 7); }).length;
              ui.toast(t(`Version ${version} published · ${affected} periods queued for recalculation`, `Đã ban hành phiên bản ${version} · ${affected} kỳ chờ tính lại`));
            },
          });
        },
        "edit-baseline"(modelId) {
          const b = BASELINE.find((x) => x.vehicle_model_id === modelId);
          ui.formModal({
            title: `${t("Diesel baseline", "Đường cơ sở diesel")} · ${db.model(modelId).code}`,
            fields: [
              { name: "empty", label: t("Empty (L/100 km)", "Xe rỗng (L/100 km)"), type: "number", required: true, value: b.empty_l_100km, attrs: 'step="0.1" min="5" max="80"' },
              { name: "loaded", label: t("Loaded (L/100 km)", "Có hàng (L/100 km)"), type: "number", required: true, value: b.loaded_l_100km, attrs: 'step="0.1" min="5" max="80"' },
              { name: "source", label: t("Source", "Nguồn"), required: true, value: tx(b.source), full: true },
            ],
            submitLabel: t("Save new version", "Lưu phiên bản mới"),
            onSubmit(values) {
              const empty = Number(values.empty), loaded = Number(values.loaded);
              if (!(empty >= 5 && empty <= 80 && loaded >= 5 && loaded <= 80)) return t("Consumption must be between 5 and 80 L/100 km.", "Mức tiêu hao phải từ 5 đến 80 L/100 km.");
              if (loaded < empty) return t("Loaded consumption cannot be below empty.", "Tiêu hao khi có hàng không thể thấp hơn xe rỗng.");
              Object.assign(b, { empty_l_100km: empty, loaded_l_100km: loaded, source: values.source, version: b.version + 1, updated_at: todayIso() });
              G3.render();
              ui.toast(t(`Baseline v${b.version} saved`, `Đã lưu đường cơ sở v${b.version}`));
            },
          });
        },
        "new-certificate"() {
          ui.formModal({
            title: t("Record a certificate", "Thêm chứng chỉ"), wide: true,
            fields: [
              { name: "code", label: t("Certificate code", "Mã chứng chỉ"), required: true, placeholder: "IREC-VN-2026-000700" },
              { name: "standard", label: t("Standard", "Tiêu chuẩn"), type: "select", options: [["I-REC(E)", "I-REC(E)"], ["TIGR", "TIGR"]] },
              { name: "location_id", label: t("Generation site", "Nơi phát điện"), type: "select", options: [["", t("Purchased (unbundled)", "Mua riêng (không kèm điện)")], ...db.charging_locations.filter((l) => l.solar_kw).map((l) => [l.location_id, `${l.name} · ${l.solar_kw} kWp`])] },
              { name: "volume", label: t("Volume (MWh)", "Sản lượng (MWh)"), type: "number", required: true, attrs: 'min="1"' },
              { name: "vintage_from", label: t("Vintage from", "Phát điện từ"), type: "date", required: true },
              { name: "vintage_to", label: t("Vintage to", "Phát điện đến"), type: "date", required: true },
              { name: "valid_until", label: t("Valid until", "Hiệu lực đến"), type: "date", required: true },
            ],
            submitLabel: t("Record", "Lưu"),
            onSubmit(values) {
              if (CERTIFICATES.some((c) => c.code === values.code)) return t("This code is already recorded.", "Mã này đã được ghi nhận.");
              if (!(Number(values.volume) > 0)) return t("The volume must be above 0.", "Sản lượng phải lớn hơn 0.");
              if (values.vintage_to < values.vintage_from) return t("The vintage end is before its start.", "Ngày kết thúc kỳ phát điện trước ngày bắt đầu.");
              if (values.valid_until < values.vintage_to) return t("The validity must end after the vintage.", "Hiệu lực phải kết thúc sau kỳ phát điện.");
              CERTIFICATES.unshift({ code: values.code, standard: values.standard, issuer: ["I-REC registry (illustrative)", "Sổ đăng ký I-REC (minh họa)"], location_id: values.location_id || null, source: "SOLAR", volume_mwh: Number(values.volume), matched_mwh: 0, vintage_from: values.vintage_from, vintage_to: values.vintage_to, valid_until: values.valid_until, status: values.valid_until < todayIso() ? "EXPIRED" : "ACTIVE" });
              G3.render();
              ui.toast(t("Certificate recorded; matching starts with the next run", "Đã ghi nhận chứng chỉ; khớp sản lượng ở lần tính tiếp theo"));
            },
          });
        },
        "assemble-mrv"(periodKey) {
          const year = new Date(NOW).getFullYear();
          const months = periodKey === "P2" ? [7, 8, 9, 10, 11, 12] : [1, 2, 3, 4, 5, 6];
          const rows = carbonRows(null).filter((r) => r.month.endsWith(String(year)) && months.includes(Number(r.month.slice(0, 2))));
          ui.downloadCsv(`mrv-${periodKey}-${year}.csv`, [
            { label: "period", key: "month" }, { label: "distance_km", csv: (r) => Math.round(r.distance_km) }, { label: "energy_kwh", csv: (r) => Math.round(r.energy_kwh) },
            { label: "renewable_share", csv: (r) => r.renewable_share.toFixed(3) }, { label: "baseline_tco2e", key: "diesel_baseline_t" }, { label: "project_tco2e", csv: (r) => (r.scope2_grid_t + r.scope2_renewable_t).toFixed(1) },
            { label: "emission_reduction_tco2e", key: "avoided_t" }, { label: "grid_factor_version", csv: () => "EF-GRID-2023 v2" },
          ], rows);
        },
        "send-mrv"(periodKey) {
          ui.formModal({
            title: t("Send the dossier to a verifier", "Gửi hồ sơ cho đơn vị thẩm định"),
            fields: [
              { name: "name", label: t("Verifier", "Đơn vị thẩm định"), required: true, placeholder: t("Accredited validation/verification body", "Tổ chức thẩm định được công nhận") },
              { name: "email", label: "E-mail", required: true, placeholder: "mrv@verifier.example" },
            ],
            submitLabel: t("Send", "Gửi"),
            onSubmit(values) {
              if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(values.email)) return t("Enter a valid e-mail address.", "Nhập địa chỉ e-mail hợp lệ.");
              MRV_VERIFIER[periodKey] = { name: values.name, email: values.email, sent_at: new Date().toISOString() };
              G3.render();
              ui.toast(t(`Dossier ${periodKey} sent to ${values.name}`, `Đã gửi hồ sơ ${periodKey} cho ${values.name}`), "", "send");
            },
          });
        },
      });
    },
  });
})();
