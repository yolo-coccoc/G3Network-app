/* Administration screens:
   - organizations (/organizations): registry, account managers, onboarding wizard (ACC-01, ACC-02, ACC-08)
   - organization-detail (/organizations/:id): profile, status, account manager, change history (ACC-01, ACC-02, ACC-12, ACC-22)
   - members (/members): members, invitations, roles, ORG_ADMIN handover, fleet limits, bulk import (ACC-09, ACC-11, ACC-12, ACC-13, FLT-03)
   - users (/users): every account, lock/unlock, login history, password reset check (ACC-03, ACC-04, ACC-06)
   - audit (/audit): access audit log with security events, change history feed (ACC-18, ACC-22)
   - settings (/settings): channels, organization settings, thresholds, legal texts, API keys, device interval (NTF-05, MON-17, ACC-17, ACC-21, DEV-07)
   - account (/account): my profile, password, phone, sessions, push, data requests (ACC-04, ACC-06, ACC-16, ACC-19) */

(function () {
  const { ui, db, fmt, scope } = G3;
  const esc = G3.esc;
  const { NOW, MIN, HOUR, DAY } = db;
  const P = db.people;

  /* ---------- Small helpers ---------- */
  function hashOf(text) {
    let h = 2166136261;
    for (const ch of String(text)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
    return h >>> 0;
  }
  /** Deterministic generator per key (mock rows must not change between reloads). */
  function seeded(key) {
    let h = hashOf(key) || 1;
    return () => { h ^= h << 13; h ^= h >>> 17; h ^= h << 5; return ((h >>> 0) % 100000) / 100000; };
  }
  const isoAgo = (ms) => new Date(NOW - ms).toISOString();
  const nowIso = () => new Date().toISOString();
  const me = () => scope.user();
  const uidOf = (key) => (P[key] ? P[key].user.user_id : null);
  const orgOf = (m) => db.org(m.organization_id);
  const isLeft = (m) => !!m.left_at;
  const PHONE_RE = /^0\d{9}$/;

  /** Labels for values the core has no label for. */
  const LOCAL_LABELS = {
    LEFT: ["Left", "Đã rời"],
    TERMS_OF_SERVICE: ["Terms of service", "Điều khoản sử dụng"],
    PRIVACY_POLICY: ["Privacy policy", "Chính sách quyền riêng tư"],
    DATA_PROCESSING_AGREEMENT: ["Data processing agreement", "Thỏa thuận xử lý dữ liệu"],
    PRIVACY_NOTICE: ["Driver privacy notice", "Thông báo xử lý dữ liệu tài xế"],
    LOCATION_TRACKING: ["Location tracking consent", "Đồng ý theo dõi vị trí"],
    LOCATION_HISTORY: ["Location history", "Lịch sử vị trí"],
    LIVE_POSITION: ["Live position", "Vị trí trực tuyến"],
    DRIVER_PROFILE: ["Driver profile", "Hồ sơ tài xế"],
    DRIVING_SESSIONS: ["Driving sessions", "Phiên lái xe"],
    CAMERA_FOOTAGE: ["Camera footage", "Video camera"],
    ACCOUNT: ["User account", "Tài khoản"],
    USER_ACCOUNTS: ["User accounts", "Danh sách tài khoản"],
    CITIZEN_ID: ["Citizen ID", "Số CCCD"],
    ACCESS_AUDIT_LOG: ["Access audit log", "Nhật ký truy cập"],
    OWN_DATA: ["Own personal data", "Dữ liệu cá nhân của tôi"],
    MEMBERS: ["Member list", "Danh sách thành viên"],
    WEB: ["Web", "Web"],
    ANDROID: ["Android", "Android"],
    IOS: ["iOS", "iOS"],
    EXPORT_REQUEST: ["Export my data", "Xuất dữ liệu của tôi"],
    CORRECTION: ["Correction", "Chỉnh sửa"],
    DELETION: ["Deletion", "Xóa dữ liệu"],
    RECEIVED: ["Received", "Đã tiếp nhận"],
    REVOKED: ["Revoked", "Đã thu hồi"],
    ORGANIZATION: ["Organization", "Tổ chức"],
    USER: ["User", "Người dùng"],
    MEMBERSHIP: ["Membership", "Thành viên"],
    ROLE: ["Role", "Vai trò"],
    SETTING: ["Setting", "Cấu hình"],
    MONTHLY: ["Monthly", "Hằng tháng"],
    QUARTERLY: ["Quarterly", "Hằng quý"],
    MODEL: ["Vehicle model", "Dòng xe"],
    FLEET: ["Fleet", "Đội xe"],
    APPLIED: ["Applied", "Đã áp dụng"],
    MISMATCH: ["Not applied", "Chưa áp dụng"],
    NO_ANSWER: ["No answer", "Không phản hồi"],
  };
  const lbl = (v) => (LOCAL_LABELS[v] ? t(LOCAL_LABELS[v][0], LOCAL_LABELS[v][1]) : G3.label(v));
  const badge = (v, tone) => ui.badge(v, tone, lbl(v));

  const ACTION_TONES = { VIEW: "info", EXPORT: "warn", LOGIN_SUCCESS: "ok", LOGIN_FAILED: "danger", LOGIN_LOCKED: "danger", LOGOUT: "muted", PASSWORD_CHANGED: "violet", PHONE_CHANGED: "violet" };
  const KIND_TONES = { INTERNAL: "violet", CUSTOMER: "info", PARTNER: "accent" };
  const ADMIN_ROLES = ["HEAD_ADMIN", "CO_ADMIN", "ORG_ADMIN"];

  function roleTags(roles) {
    if (!roles || !roles.length) return '<span class="muted">—</span>';
    return `<span class="row" style="gap:4px">${roles.map((r) => (ADMIN_ROLES.includes(r) ? `<span class="badge violet plain">${esc(G3.roleLabel(r))}</span>` : ui.tag(G3.roleLabel(r)))).join("")}</span>`;
  }
  function personCell(user, sub) {
    if (!user) return '<span class="muted">—</span>';
    return `<b>${esc(user.full_name)}</b><span class="sub">${esc(sub !== undefined ? sub : user.email || user.phone_number)}</span>`;
  }
  const maskId = (value) => (value ? value.slice(0, 3) + "•••••" + value.slice(-3) : "—");
  const orgMemberships = (orgId) => db.memberships.filter((m) => m.organization_id === orgId);
  const liveMemberships = (orgId) => orgMemberships(orgId).filter((m) => !isLeft(m));
  const orgVehicles = (orgId) => db.vehicles.filter((v) => v.organization_id === orgId);
  const subOf = (orgId) => db.subscriptions.find((s) => s.organization_id === orgId);
  const salesUsers = () => db.memberships.filter((m) => m.organization_id === "org-g3" && m.status === "ACTIVE" && m.roles.includes("SALES")).map((m) => db.user(m.user_id));
  function orgAdminOf(orgId) {
    const m = orgMemberships(orgId).find((x) => !isLeft(x) && x.roles.includes("ORG_ADMIN"));
    return m || null;
  }

  /** One page-open entry in the access log, not one per re-render (ACC-18). */
  const lastViewLogged = {};
  function auditViewOnce(pageId, dataType) {
    if (lastViewLogged[pageId] && Date.now() - lastViewLogged[pageId] < 10 * MIN) return;
    lastViewLogged[pageId] = Date.now();
    G3.audit("VIEW", dataType, null, null);
  }

  /** Click delegation on the content root; removed when the page is left (the root survives re-renders). */
  function delegate(root, handlers) {
    const onClick = (e) => {
      const el = e.target.closest("[data-action]");
      if (el && root.contains(el) && handlers[el.dataset.action]) { e.preventDefault(); handlers[el.dataset.action](el, e); return; }
      if (handlers._click) handlers._click(e);
    };
    root.addEventListener("click", onClick);
    G3.onLeave(() => root.removeEventListener("click", onClick));
  }
  function bindQuery(root) {
    root.querySelectorAll("[data-filter]").forEach((el) => el.addEventListener("change", () => G3.setQuery({ [el.dataset.filter]: el.value })));
    root.querySelectorAll("[data-tabs] [data-tab]").forEach((b) => b.addEventListener("click", () => G3.setQuery({ [b.closest("[data-tabs]").dataset.tabs]: b.dataset.tab })));
    root.querySelectorAll("[data-seg] [data-value]").forEach((b) => b.addEventListener("click", () => G3.setQuery({ [b.closest("[data-seg]").dataset.seg]: b.dataset.value })));
  }
  function done(message) { G3.render(); ui.toast(message); }
  /** A select that is part of a form, not a URL filter (ui.select binds to the query). */
  function plainSelect(name, options, value, disabled) {
    return `<select class="input" name="${name}" ${disabled ? "disabled" : ""}>${options.map(([k, label]) => `<option value="${esc(k)}" ${String(k) === String(value) ? "selected" : ""}>${esc(label)}</option>`).join("")}</select>`;
  }

  /* ---------- Who created each account (users.created_by, ACC-03) ---------- */
  const createdByMap = new Map();
  function createdBy(user) {
    if (createdByMap.has(user.user_id)) return createdByMap.get(user.user_id);
    const first = db.memberships.find((m) => m.user_id === user.user_id);
    let by = null;
    if (first) {
      if (first.organization_id === "org-g3") by = user.user_id === uidOf("head") ? null : uidOf("head");
      else if (first.roles.includes("ORG_ADMIN")) by = (db.org(first.organization_id) || {}).account_manager_user_id || uidOf("coadmin");
      else { const admin = orgAdminOf(first.organization_id); by = admin ? admin.user_id : uidOf("ops"); }
    }
    createdByMap.set(user.user_id, by);
    return by;
  }

  /* Two drivers who have not logged in for months: the password reset needs an extra check (ACC-06). */
  const lastLoginOverride = {};
  (function () {
    const dnDrivers = db.drivers.filter((d) => d.organization_id === "org-dn");
    const mnDrivers = db.drivers.filter((d) => d.organization_id === "org-mn");
    if (dnDrivers.length) lastLoginOverride[dnDrivers[dnDrivers.length - 1].user_id] = isoAgo(214 * DAY);
    if (mnDrivers.length > 2) lastLoginOverride[mnDrivers[2].user_id] = isoAgo(133 * DAY);
  })();
  const lastLogin = (user) => (lastLoginOverride[user.user_id] !== undefined ? lastLoginOverride[user.user_id] : user.last_login_at);
  const isLongInactive = (user) => { const l = lastLogin(user); return l ? NOW - new Date(l).getTime() > 90 * DAY : NOW - new Date(user.created_at).getTime() > 90 * DAY; };

  /* ---------- Login history (ACC-04) ---------- */
  const DEVICES = ["Chrome 129 / Windows 11", "Edge 129 / Windows 10", "Safari 18 / macOS", "G3 Fleet 1.4 / Android 14", "G3 Driver 1.4 / Android 13", "G3 Fleet 1.4 / iOS 18"];
  function loginHistory(user) {
    const r = seeded("login" + user.user_id);
    const rows = [];
    const last = lastLogin(user);
    if (!last) return [];
    const base = new Date(last).getTime();
    const usual = DEVICES[Math.floor(r() * DEVICES.length)];
    for (let k = 0; k < 12; k++) {
      const at = base - k * (r() * 3 + 0.4) * DAY;
      const roll = r();
      const action = k === 0 ? "LOGIN_SUCCESS" : roll < 0.12 ? "LOGIN_FAILED" : roll < 0.3 ? "LOGOUT" : "LOGIN_SUCCESS";
      const newDevice = k === 4 && action === "LOGIN_SUCCESS";
      rows.push({ action, occurred_at: new Date(at).toISOString(), ip_address: `${newDevice ? "171.244" : "113.161"}.${Math.floor(r() * 250) + 1}.${Math.floor(r() * 250) + 1}`, device: newDevice ? DEVICES[(DEVICES.indexOf(usual) + 2) % DEVICES.length] : usual, is_new_device: newDevice });
    }
    db.access_audit_logs.filter((a) => a.user_id === user.user_id && a.data_type === "ACCOUNT").forEach((a) => rows.push({ action: a.action, occurred_at: a.occurred_at, ip_address: a.ip_address, device: a.device, is_new_device: false }));
    return rows.sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : -1));
  }
  function loginHistoryTable(user, limit = 10) {
    return ui.table({
      rows: loginHistory(user).slice(0, limit),
      empty: t("Never logged in", "Chưa đăng nhập lần nào"),
      columns: [
        { label: t("When", "Thời điểm"), render: (r) => `${fmt.dateTime(r.occurred_at)}<span class="sub">${fmt.ago(r.occurred_at)}</span>` },
        { label: t("Event", "Sự kiện"), render: (r) => ui.badge(r.action, ACTION_TONES[r.action]) + (r.is_new_device ? ` <span class="badge warn plain">${esc(t("New device", "Thiết bị mới"))}</span>` : "") },
        { label: "IP", render: (r) => `<span class="mono">${esc(r.ip_address)}</span>` },
        { label: t("Device", "Thiết bị"), render: (r) => esc(r.device) },
      ],
    });
  }

  /* ---------- Organization change history (organization_history, ACC-22) ---------- */
  const ORG_FIELDS = [
    ["name", ["Legal name", "Tên pháp lý"]],
    ["short_name", ["Display name", "Tên hiển thị"]],
    ["legal_form", ["Legal form", "Loại hình"]],
    ["tax_code", ["Tax code", "Mã số thuế"]],
    ["address", ["Address", "Địa chỉ"]],
    ["status", ["Status", "Trạng thái"]],
    ["status_reason", ["Status reason", "Lý do trạng thái"]],
    ["account_manager_user_id", ["Account manager", "Người phụ trách"]],
    ["is_internal", ["Internal organization", "Tổ chức nội bộ"]],
  ];
  const snapshot = (o) => ({ name: o.name, short_name: o.short_name, legal_form: o.legal_form, tax_code: o.tax_code, address: o.address, status: o.status, status_reason: o.status_reason, account_manager_user_id: o.account_manager_user_id || null, is_internal: o.is_internal });
  /* Seeded edits, newest first: `old` holds the values before the edit. */
  const SEED_ORG_CHANGES = {
    "org-g3": [{ days: 200, by: "head", reason: ["Moved office", "Chuyển văn phòng"], old: { address: "Tầng 5, 2 Hải Triều, Bến Nghé, Quận 1, TP. HCM" } }],
    "org-tcx": [
      { days: 35, by: "coadmin", reason: ["Accounts reassigned by region", "Phân công lại người phụ trách theo khu vực"], old: { account_manager_user_id: "sales2" } },
      { days: 120, by: "sales", reason: ["New head office on the business registration", "Trụ sở mới theo ĐKKD"], old: { address: "Lô B2, KCN Nhơn Trạch 2, Đồng Nai" } },
    ],
    "org-mn": [
      { days: 40, by: "coadmin", reason: ["Renamed in the amended business licence", "Đổi tên theo giấy phép kinh doanh sửa đổi"], old: { name: "Công ty TNHH Vận tải Miền Nam", short_name: "Vận tải Miền Nam" } },
      { days: 100, by: "coadmin", reason: ["First account manager assigned", "Giao người phụ trách đầu tiên"], old: { account_manager_user_id: null } },
    ],
    "org-dn": [
      { days: 6, by: null, reason: ["Automatic suspension: invoice 30 days overdue", "Tự động tạm ngưng: hóa đơn quá hạn 30 ngày"], old: { status: "ACTIVE", status_reason: null } },
      { days: 80, by: "sales2", reason: ["New name on the business registration", "Đổi tên theo ĐKKD mới"], old: { name: "HTX Vận tải Đồng Nai", short_name: "Vận tải Đồng Nai" } },
    ],
    "org-ind": [{ days: 60, by: "sales2", reason: ["Full address added", "Bổ sung địa chỉ đầy đủ"], old: { address: "Phú Mỹ" } }],
  };
  const versionStore = {};
  /** Versions of an organization, oldest first; the last one is the current row. */
  function versionsOf(org) {
    if (versionStore[org.organization_id]) return versionStore[org.organization_id];
    const seeds = SEED_ORG_CHANGES[org.organization_id] || [];
    let row = snapshot(org);
    const versions = [];
    seeds.forEach((c) => {
      versions.unshift({ at: isoAgo(c.days * DAY), by: c.by ? uidOf(c.by) : null, reason: t(c.reason[0], c.reason[1]), row });
      const old = { ...c.old };
      if ("account_manager_user_id" in old && old.account_manager_user_id) old.account_manager_user_id = uidOf(old.account_manager_user_id);
      row = { ...row, ...old };
    });
    const creator = org.is_internal ? null : org.kind === "PARTNER" ? uidOf("coadmin") : row.account_manager_user_id || uidOf("sales");
    versions.unshift({ at: org.created_at, by: creator, reason: t("Organization created", "Tạo tổ chức"), row, created: true });
    versionStore[org.organization_id] = versions;
    return versions;
  }
  function recordOrgChange(org, reason) {
    versionsOf(org).push({ at: nowIso(), by: me().user_id, reason, row: snapshot(org) });
  }
  function diffRows(a, b) {
    return ORG_FIELDS.filter(([key]) => (a ? a[key] : null) !== b[key]).map(([key]) => key);
  }
  function orgFieldValue(key, value) {
    if (value === null || value === undefined || value === "") return '<span class="muted">—</span>';
    if (key === "account_manager_user_id") return ui.userName(value);
    if (key === "legal_form" || key === "status") return esc(G3.label(value));
    if (key === "is_internal") return esc(value ? t("Yes", "Có") : t("No", "Không"));
    return esc(value);
  }
  const fieldName = (key) => { const f = ORG_FIELDS.find(([k]) => k === key); return f ? t(f[1][0], f[1][1]) : key; };

  /* ---------- Role assignments with history (user_role_assignments, ACC-13) ---------- */
  const roleEvents = [];
  db.memberships.forEach((m) => {
    const org = orgOf(m);
    const admin = orgAdminOf(m.organization_id);
    m.roles.forEach((role) => {
      const by = org.is_internal ? uidOf("head") : role === "ORG_ADMIN" ? org.account_manager_user_id || uidOf("coadmin") : admin ? admin.user_id : uidOf("ops");
      roleEvents.push({ membership_id: m.membership_id, organization_id: m.organization_id, role, granted_at: m.joined_at || m.invited_at, granted_by: by === m.user_id ? uidOf("head") : by, revoked_at: null, revoked_by: null });
    });
  });
  // Past grants that were revoked: they show that history is kept.
  [[P.tcxFmLimited, "ACCOUNTANT", 90, 41], [P.lockedOne, "FLEET_MANAGER", 150, 70], [P.tcxFm, "DISPATCHER", 180, 120]].forEach(([person, role, grantedDays, revokedDays]) => {
    roleEvents.push({ membership_id: person.membership.membership_id, organization_id: person.membership.organization_id, role, granted_at: isoAgo(grantedDays * DAY), granted_by: uidOf("tcxAdmin"), revoked_at: isoAgo(revokedDays * DAY), revoked_by: uidOf("tcxAdmin") });
  });
  function grantRole(m, role) {
    if (m.roles.includes(role)) return;
    m.roles.push(role);
    roleEvents.push({ membership_id: m.membership_id, organization_id: m.organization_id, role, granted_at: nowIso(), granted_by: me().user_id, revoked_at: null, revoked_by: null });
  }
  function revokeRole(m, role) {
    m.roles = m.roles.filter((r) => r !== role);
    const open = roleEvents.find((e) => e.membership_id === m.membership_id && e.role === role && !e.revoked_at);
    if (open) { open.revoked_at = nowIso(); open.revoked_by = me().user_id; }
  }

  /* ---------- Membership / user / setting changes (history tables, ACC-22) ---------- */
  const memberChanges = [
    { at: isoAgo(9 * DAY), by: uidOf("tcxAdmin"), membership_id: P.lockedOne.membership.membership_id, fields: [["status", "ACTIVE", "LOCKED"]], reason: "Nghỉ việc, chờ bàn giao" },
  ];
  const userChanges = [
    { at: isoAgo(26 * DAY), by: P.tcxFm.user.user_id, user_id: P.tcxFm.user.user_id, fields: [["phone_number", "0903112233", P.tcxFm.user.phone_number]], reason: t("Phone number change confirmed by OTP", "Đổi số điện thoại, xác nhận bằng OTP") },
  ];
  const settingChanges = [];
  const fleetLimitHistory = [
    { at: isoAgo(44 * DAY), by: uidOf("tcxAdmin"), membership_id: P.tcxFmLimited.membership.membership_id, fleet_ids: ["flt-tcx-caimep"] },
  ];
  const inviteMeta = {};
  const cancelledInvites = [];

  /* ---------- Shared org context (members, settings) ---------- */
  function contextOrg(query) {
    if (!scope.isInternal()) return scope.org();
    const id = query.org || scope.orgFilter();
    return id ? db.org(id) || null : null;
  }
  function orgPicker(title, query, includeInternal) {
    const orgs = db.organizations.filter((o) => includeInternal || !o.is_internal);
    return ui.card({
      title, sub: t("Internal staff: pick an organization (or use the top-bar filter)", "Nhân viên nội bộ: chọn tổ chức (hoặc dùng bộ lọc trên thanh trên cùng)"), flush: true,
      body: ui.table({
        rows: orgs, rowAttrs: (o) => `data-pick-org="${o.organization_id}" style="cursor:pointer"`,
        columns: [
          { label: t("Organization", "Tổ chức"), render: (o) => `<b>${esc(o.short_name)}</b><span class="sub">${esc(o.name)}</span>` },
          { label: t("Kind", "Phân loại"), render: (o) => ui.badge(o.kind, KIND_TONES[o.kind]) },
          { label: t("Members", "Thành viên"), align: "right", render: (o) => liveMemberships(o.organization_id).length },
          { label: t("Status", "Trạng thái"), render: (o) => ui.badge(o.status) },
          { label: "", align: "right", render: () => ui.icon("chevron", 16) },
        ],
      }),
    });
  }
  function bindOrgPicker(root) {
    root.querySelectorAll("[data-pick-org]").forEach((tr) => tr.addEventListener("click", () => G3.setQuery({ org: tr.dataset.pickOrg })));
  }
  function orgSwitchSelect(org) {
    if (!scope.isInternal()) return "";
    return ui.select("org", db.organizations.map((o) => [o.organization_id, o.short_name + (o.is_internal ? ` (${t("internal", "nội bộ")})` : "")]), org ? org.organization_id : "");
  }

  /* =====================================================================
     1. Organizations (ACC-01, ACC-02, ACC-08)
     ===================================================================== */
  function taxCodeError(legalForm, taxCode, exceptOrgId) {
    if (legalForm === "COMPANY" && !/^\d{10}(\d{3})?$/.test(taxCode)) return t("A company tax code has 10 digits (13 for a branch).", "Mã số thuế doanh nghiệp gồm 10 chữ số (13 với chi nhánh).");
    if (legalForm === "INDIVIDUAL" && !/^\d{12}$/.test(taxCode)) return t("An individual uses the 12-digit citizen ID as tax code.", "Cá nhân dùng số CCCD 12 chữ số làm mã số thuế.");
    const dup = db.organizations.find((o) => o.tax_code === taxCode && o.organization_id !== exceptOrgId && o.status !== "CLOSED");
    if (dup) return t(`Tax code already used by ${dup.short_name}.`, `Mã số thuế đã được dùng cho ${dup.short_name}.`);
    return null;
  }

  function onboardWizard() {
    const defaultAm = scope.has("SALES") ? me().user_id : (salesUsers()[0] || {}).user_id;
    const st = { step: 1, legal_form: "COMPANY", name: "", short_name: "", tax_code: "", address: "", plan_id: "plan-basic", price: "790000", billing_cycle: "MONTHLY", months: "24", admin_name: "", admin_phone: "", admin_email: "", am: defaultAm, existing: null };
    const STEPS = [t("Organization", "Tổ chức"), t("Subscription", "Gói dịch vụ"), t("First administrator", "Quản trị viên đầu tiên"), t("Account manager", "Người phụ trách")];
    const plans = db.plans.filter((p) => p.visibility !== "PRIVATE");
    const input = (name, label, attrs = "", hint = "") => `<div class="field"><label>${esc(label)}</label><input class="input" name="${name}" value="${esc(st[name])}" ${attrs}>${hint ? `<span class="hint">${esc(hint)}</span>` : ""}</div>`;

    function stepHtml() {
      const stepper = `<div class="row" style="gap:6px;margin-bottom:16px">${STEPS.map((s, i) => `<span class="badge ${i + 1 === st.step ? "info" : i + 1 < st.step ? "ok" : "muted"} plain">${i + 1}. ${esc(s)}</span>`).join(`<span class="muted">${ui.icon("chevron", 14)}</span>`)}</div>`;
      let body = "";
      if (st.step === 1) {
        body = `<div class="form cols-2">
          <div class="field"><label>${esc(t("Legal form", "Loại hình pháp lý"))}</label><select class="input" name="legal_form">${["COMPANY", "INDIVIDUAL"].map((v) => `<option value="${v}" ${st.legal_form === v ? "selected" : ""}>${esc(G3.label(v))}</option>`).join("")}</select></div>
          ${input("tax_code", st.legal_form === "INDIVIDUAL" ? t("Citizen ID (tax code)", "Số CCCD (mã số thuế)") : t("Tax code", "Mã số thuế"), 'inputmode="numeric"', st.legal_form === "INDIVIDUAL" ? t("12 digits; personal data, masked on screen", "12 chữ số; dữ liệu cá nhân, được che khi hiển thị") : t("10 digits, or 13 for a branch", "10 chữ số, hoặc 13 với chi nhánh"))}
          ${input("name", st.legal_form === "INDIVIDUAL" ? t("Full name", "Họ và tên") : t("Registered name", "Tên đăng ký"), 'placeholder="Công ty CP Vận tải …"')}
          ${input("short_name", t("Display name", "Tên hiển thị"))}
          <div class="field full"><label>${esc(t("Registered address", "Địa chỉ đăng ký"))}</label><input class="input" name="address" value="${esc(st.address)}"></div>
        </div>`;
      } else if (st.step === 2) {
        body = `<div class="stack" style="gap:8px">${plans.map((p) => `<label class="persona-card" style="display:flex;gap:10px;align-items:flex-start"><input type="radio" name="plan_id" value="${p.plan_id}" ${st.plan_id === p.plan_id ? "checked" : ""} style="margin-top:3px"><div><b>${esc(p.name)} · ${esc(fmt.vnd(p.price_vnd_truck_month))} / ${esc(t("truck / month", "xe / tháng"))}</b><span>${esc(t(p.description[0], p.description[1]))} · ${esc(p.features.join(", "))}</span></div></label>`).join("")}</div>
          <div class="form cols-2 mt">
            ${input("price", t("Agreed price (₫ / truck / month)", "Giá thỏa thuận (₫ / xe / tháng)"), 'inputmode="numeric"', t("May differ from the list price; printed on invoices", "Có thể khác giá niêm yết; in trên hóa đơn"))}
            <div class="field"><label>${esc(t("Billing cycle", "Chu kỳ thanh toán"))}</label><select class="input" name="billing_cycle">${["MONTHLY", "QUARTERLY"].map((v) => `<option value="${v}" ${st.billing_cycle === v ? "selected" : ""}>${esc(lbl(v))}</option>`).join("")}</select></div>
            <div class="field"><label>${esc(t("Contract length", "Thời hạn hợp đồng"))}</label><select class="input" name="months">${["12", "24", "36"].map((v) => `<option value="${v}" ${st.months === v ? "selected" : ""}>${v} ${esc(t("months", "tháng"))}</option>`).join("")}</select></div>
          </div>`;
      } else if (st.step === 3) {
        body = `${ui.callout("info", esc(t("The first administrator receives an invitation SMS, sets a password with the OTP and accepts the data processing agreement for the company at first login (ACC-17). An existing account just gains this organization.", "Quản trị viên đầu tiên nhận SMS mời, đặt mật khẩu bằng OTP và chấp nhận thỏa thuận xử lý dữ liệu thay mặt công ty ở lần đăng nhập đầu (ACC-17). Nếu số điện thoại đã có tài khoản, người đó chỉ được thêm vào tổ chức này.")))}
          <div class="form cols-2 mt">
            ${input("admin_phone", t("Phone number (login ID)", "Số điện thoại (tên đăng nhập)"), 'type="tel" placeholder="09xx xxx xxx"')}
            ${input("admin_name", t("Full name", "Họ và tên"))}
            ${input("admin_email", t("E-mail (optional)", "E-mail (không bắt buộc)"), 'type="email"')}
            <div class="field"><label>${esc(t("Role", "Vai trò"))}</label><div>${roleTags(["ORG_ADMIN"])}</div><span class="hint">${esc(t("Exactly one per organization; handed over later from the Members screen.", "Mỗi tổ chức đúng một người; bàn giao sau trên màn hình Thành viên."))}</span></div>
          </div>${st.existing ? `<div class="mt">${ui.callout("warn", esc(t(`Existing account: ${st.existing.full_name}. They gain this organization after accepting.`, `Đã có tài khoản: ${st.existing.full_name}. Người này được thêm vào tổ chức sau khi chấp nhận lời mời.`)))}</div>` : ""}`;
      } else {
        const plan = db.find("plans", st.plan_id);
        body = `<div class="form cols-2"><div class="field full"><label>${esc(t("Account manager (sales)", "Người phụ trách (kinh doanh)"))}</label><select class="input" name="am">${salesUsers().map((u) => `<option value="${u.user_id}" ${st.am === u.user_id ? "selected" : ""}>${esc(u.full_name)} · ${db.organizations.filter((o) => o.account_manager_user_id === u.user_id).length} ${esc(t("customers", "khách hàng"))}</option>`).join("")}</select></div></div>
          <div class="divider"></div><h4 class="mb">${esc(t("Summary", "Tóm tắt"))}</h4>
          ${ui.kv([
            [t("Organization", "Tổ chức"), `<b>${esc(st.name)}</b> (${esc(st.short_name)})`],
            [t("Legal form · tax code", "Loại hình · mã số thuế"), `${esc(G3.label(st.legal_form))} · <span class="mono">${esc(st.legal_form === "INDIVIDUAL" ? maskId(st.tax_code) : st.tax_code)}</span>`],
            [t("Plan", "Gói"), `${esc(plan.name)} · ${esc(fmt.vnd(Number(st.price)))} / ${esc(t("truck / month", "xe / tháng"))} · ${esc(lbl(st.billing_cycle))} · ${esc(st.months)} ${esc(t("months", "tháng"))}`],
            [t("First administrator", "Quản trị viên đầu tiên"), `${esc(st.admin_name)} · <span class="mono">${esc(st.admin_phone)}</span>`],
          ])}`;
      }
      return stepper + body + '<div class="small wiz-error" style="color:var(--danger);display:none;margin-top:12px"></div>';
    }

    ui.modal({
      title: t("Onboard a company", "Khởi tạo khách hàng doanh nghiệp"),
      wide: true,
      body: '<div data-wiz></div>',
      foot: `<button class="btn" data-wiz-back>${esc(t("Back", "Quay lại"))}</button><button class="btn primary" data-wiz-next></button>`,
      onMount(el, close) {
        const host = el.querySelector("[data-wiz]");
        const back = el.querySelector("[data-wiz-back]");
        const next = el.querySelector("[data-wiz-next]");
        const paint = () => {
          host.innerHTML = stepHtml();
          back.style.visibility = st.step === 1 ? "hidden" : "visible";
          next.textContent = st.step === 4 ? t("Create and send invitation", "Tạo và gửi lời mời") : t("Next", "Tiếp tục");
          const legal = host.querySelector("[name=legal_form]");
          if (legal) legal.addEventListener("change", () => { collect(); paint(); });
          host.querySelectorAll("[name=plan_id]").forEach((r) => r.addEventListener("change", () => { const p = db.find("plans", r.value); host.querySelector("[name=price]").value = p.price_vnd_truck_month; }));
          const first = host.querySelector("input:not([type=radio]), select");
          if (first) first.focus();
        };
        const collect = () => {
          host.querySelectorAll("[name]").forEach((f) => { if (f.type === "radio") { if (f.checked) st[f.name] = f.value; } else st[f.name] = f.value.trim(); });
        };
        const fail = (msg) => { const err = host.querySelector(".wiz-error"); err.textContent = msg; err.style.display = "block"; };
        const validate = () => {
          if (st.step === 1) {
            if (!st.name || !st.short_name) return t("Enter the registered name and a display name.", "Nhập tên đăng ký và tên hiển thị.");
            return taxCodeError(st.legal_form, st.tax_code);
          }
          if (st.step === 2) return /^\d+$/.test(st.price) ? null : t("The agreed price must be a whole number of đồng.", "Giá thỏa thuận phải là số đồng nguyên.");
          if (st.step === 3) {
            if (!PHONE_RE.test(st.admin_phone)) return t("The phone number has 10 digits and starts with 0.", "Số điện thoại gồm 10 chữ số, bắt đầu bằng 0.");
            st.existing = db.users.find((u) => u.phone_number === st.admin_phone) || null;
            if (st.existing && !st.admin_name) st.admin_name = st.existing.full_name;
            if (!st.admin_name) return t("Enter the administrator's full name.", "Nhập họ tên quản trị viên.");
            if (st.admin_email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(st.admin_email)) return t("The e-mail address is not valid.", "Địa chỉ e-mail không hợp lệ.");
          }
          return null;
        };
        back.addEventListener("click", () => { collect(); st.step = Math.max(1, st.step - 1); paint(); });
        next.addEventListener("click", () => {
          collect();
          const error = validate();
          if (error) return fail(error);
          if (st.step < 4) { st.step++; paint(); return; }
          finish();
          close();
        });
        paint();
      },
    });

    function finish() {
      const now = nowIso();
      const org = { organization_id: db.uid("org"), name: st.name, short_name: st.short_name, legal_form: st.legal_form, tax_code: st.tax_code, kind: "CUSTOMER", is_internal: false, status: "ACTIVE", status_reason: null, address: st.address || null, created_at: now, account_manager_user_id: st.am };
      db.organizations.push(org);
      versionStore[org.organization_id] = [{ at: now, by: me().user_id, reason: t("Organization created (onboarding)", "Tạo tổ chức (khởi tạo khách hàng)"), row: snapshot(org), created: true }];
      const start = new Date();
      const end = new Date(start); end.setMonth(end.getMonth() + Number(st.months));
      db.subscriptions.push({ subscription_id: db.uid("sub"), organization_id: org.organization_id, plan_id: st.plan_id, status: "ACTIVE", agreed_price_vnd_truck_month: Number(st.price), billing_cycle: st.billing_cycle, starts_on: start.toISOString().slice(0, 10), ends_on: end.toISOString().slice(0, 10), add_ons: [] });
      let user = st.existing;
      if (!user) {
        user = { user_id: db.uid("usr"), full_name: st.admin_name, phone_number: st.admin_phone, email: st.admin_email || null, status: "INVITED", status_reason: null, created_at: now, last_login_at: null };
        db.users.push(user);
        createdByMap.set(user.user_id, me().user_id);
      }
      const m = { membership_id: db.uid("mbr"), user_id: user.user_id, organization_id: org.organization_id, status: "INVITED", status_reason: null, roles: [], fleet_ids: [], invited_at: now, joined_at: null, left_at: null };
      db.memberships.push(m);
      grantRole(m, "ORG_ADMIN");
      inviteMeta[m.membership_id] = { sent: 1, last_sent_at: now, invited_by: me().user_id };
      G3.navigate(`/organizations/${org.organization_id}`);
      ui.toast(esc(t(`${org.short_name} created. Invitation SMS sent to ${user.phone_number}.`, `Đã tạo ${org.short_name}. Đã gửi SMS mời tới ${user.phone_number}.`)), "", "send");
    }
  }

  G3.page({
    id: "organizations",
    path: "/organizations",
    title: ["Organizations", "Tổ chức"],
    roles: ["SALES", "CO_ADMIN", "HEAD_ADMIN"],
    features: ["ACC-01", "ACC-02", "ACC-08"],
    nav: { group: "admin", order: 1, icon: "building" },
    render({ query }) {
      if (!scope.isInternal()) return ui.pageHead({ title: t("Organizations", "Tổ chức") }) + ui.callout("warn", esc(t("Internal screen.", "Màn hình nội bộ.")), "lock");
      const q = (query.q || "").toLowerCase();
      const all = scope.filter(db.organizations);
      const rows = all.filter((o) => (!query.kind || o.kind === query.kind) && (!query.status || o.status === query.status) && (!query.am || o.account_manager_user_id === query.am) && (!query.form || o.legal_form === query.form) && (!q || o.name.toLowerCase().includes(q) || o.short_name.toLowerCase().includes(q) || (o.tax_code || "").includes(q)));
      const customers = all.filter((o) => o.kind === "CUSTOMER");
      const trucks = customers.reduce((a, o) => a + orgVehicles(o.organization_id).length, 0);
      const recent = customers.filter((o) => NOW - new Date(o.created_at).getTime() < 90 * DAY).length;
      const mine = scope.has("SALES") ? customers.filter((o) => o.account_manager_user_id === me().user_id) : null;
      const unassigned = customers.filter((o) => !o.account_manager_user_id);
      const bySales = salesUsers().map((u) => ({ u, orgs: customers.filter((o) => o.account_manager_user_id === u.user_id) }));
      return `${ui.pageHead({
        title: t("Organizations", "Tổ chức"),
        sub: esc(t("Every party using the system: customers, owner-drivers, our own organization and partners.", "Mọi bên dùng hệ thống: khách hàng, chủ xe cá nhân, tổ chức nội bộ và đối tác.")),
        features: ["ACC-01", "ACC-02", "ACC-08"],
        actions: `${ui.btn(t("Export", "Xuất"), { icon: "download", action: "export" })}${ui.btn(t("Onboard a company", "Khởi tạo khách hàng"), { icon: "plus", kind: "primary", action: "onboard" })}`,
      })}
      <div class="grid cols-5">
        ${ui.kpi({ label: t("Customers", "Khách hàng"), icon: "building", value: customers.length, foot: `${customers.filter((o) => o.legal_form === "INDIVIDUAL").length} ${t("individuals", "cá nhân")}` })}
        ${ui.kpi({ label: t("Active", "Đang hoạt động"), icon: "check", value: customers.filter((o) => o.status === "ACTIVE").length, tone: "ok", foot: `${customers.filter((o) => o.status === "SUSPENDED").length} ${t("suspended", "tạm ngưng")} · ${customers.filter((o) => o.status === "CLOSED").length} ${t("closed", "đã đóng")}` })}
        ${ui.kpi({ label: t("Trucks under contract", "Xe theo hợp đồng"), icon: "truck", value: trucks, foot: `${fmt.num(trucks / (customers.length || 1), 1)} ${t("per customer", "xe / khách hàng")}` })}
        ${ui.kpi({ label: t("New in 90 days", "Mới trong 90 ngày"), icon: "plus", value: recent, foot: t("onboarded by sales", "do kinh doanh khởi tạo") })}
        ${ui.kpi({ label: mine ? t("My customers", "Khách hàng của tôi") : t("Without account manager", "Chưa có người phụ trách"), icon: "user", value: mine ? mine.length : unassigned.length, tone: !mine && unassigned.length ? "warn" : undefined, foot: `${all.filter((o) => o.kind === "PARTNER").length} ${t("partners", "đối tác")}` })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Organization registry", "Danh mục tổ chức"), sub: `${rows.length} / ${all.length}`, flush: true,
          body: `<div class="toolbar">${ui.search("q", query.q, t("Name or tax code", "Tên hoặc mã số thuế"))}
            ${ui.select("kind", [["", t("All kinds", "Mọi phân loại")], ...["CUSTOMER", "PARTNER", "INTERNAL"].map((k) => [k, G3.label(k)])], query.kind || "")}
            ${ui.select("status", [["", t("All statuses", "Mọi trạng thái")], ...["ACTIVE", "SUSPENDED", "CLOSED"].map((k) => [k, G3.label(k)])], query.status || "")}
            ${ui.select("form", [["", t("All legal forms", "Mọi loại hình")], ["COMPANY", G3.label("COMPANY")], ["INDIVIDUAL", G3.label("INDIVIDUAL")]], query.form || "")}
            ${ui.select("am", [["", t("Any account manager", "Mọi người phụ trách")], ...salesUsers().map((u) => [u.user_id, u.full_name])], query.am || "")}</div>
            ${ui.table({
              rows, href: (o) => `/organizations/${o.organization_id}`, empty: t("No organization matches", "Không có tổ chức phù hợp"),
              columns: [
                { label: t("Organization", "Tổ chức"), render: (o) => `<b>${esc(o.short_name)}</b><span class="sub">${esc(o.name)}</span>` },
                { label: t("Legal form", "Loại hình"), render: (o) => esc(G3.label(o.legal_form)) },
                { label: t("Tax code", "Mã số thuế"), render: (o) => `<span class="mono" ${o.legal_form === "INDIVIDUAL" ? `title="${esc(t("Citizen ID: personal data, masked", "Số CCCD: dữ liệu cá nhân, được che"))}"` : ""}>${esc(o.legal_form === "INDIVIDUAL" ? maskId(o.tax_code) : o.tax_code || "—")}</span>` },
                { label: t("Kind", "Phân loại"), render: (o) => ui.badge(o.kind, KIND_TONES[o.kind]) },
                { label: t("Status", "Trạng thái"), render: (o) => `${ui.badge(o.status)}${o.status_reason ? `<span class="sub">${esc(o.status_reason)}</span>` : ""}` },
                { label: t("Account manager", "Người phụ trách"), render: (o) => (o.kind === "CUSTOMER" ? (o.account_manager_user_id ? ui.userName(o.account_manager_user_id) : `<span class="badge warn plain">${esc(t("None", "Chưa có"))}</span>`) : '<span class="muted">—</span>') },
                { label: t("Trucks", "Xe"), align: "right", render: (o) => orgVehicles(o.organization_id).length },
                { label: t("Members", "Thành viên"), align: "right", render: (o) => liveMemberships(o.organization_id).length },
                { label: t("Created", "Ngày tạo"), render: (o) => fmt.date(o.created_at) },
              ],
            })}`,
        })}
        <div class="stack">
          ${ui.card({
            title: t("Customers per account manager", "Khách hàng theo người phụ trách"), sub: "ACC-02",
            body: bySales.map(({ u, orgs }) => `<div class="mb"><div class="row between"><b>${esc(u.full_name)}</b><span class="pill-count">${orgs.length} · ${orgs.reduce((a, o) => a + orgVehicles(o.organization_id).length, 0)} ${esc(t("trucks", "xe"))}</span></div><div class="small muted" style="margin-top:4px">${orgs.map((o) => `<a href="#/organizations/${o.organization_id}">${esc(o.short_name)}</a>`).join(" · ") || esc(t("No customer yet", "Chưa có khách hàng"))}</div></div>`).join("") + (unassigned.length ? ui.callout("warn", esc(t(`${unassigned.length} customer(s) without an account manager.`, `${unassigned.length} khách hàng chưa có người phụ trách.`))) : ""),
          })}
          ${ui.card({
            title: t("Onboarding a company", "Khởi tạo khách hàng"), sub: "ACC-08",
            body: `<ol class="small" style="margin:0;padding-left:18px;line-height:1.8">
              <li>${esc(t("Organization: legal form and tax code check", "Tổ chức: loại hình và kiểm tra mã số thuế"))}</li>
              <li>${esc(t("Subscription: plan and agreed price", "Gói dịch vụ: gói và giá thỏa thuận"))}</li>
              <li>${esc(t("First administrator invited by phone", "Mời quản trị viên đầu tiên qua số điện thoại"))}</li>
              <li>${esc(t("Account manager assigned", "Giao người phụ trách"))}</li></ol>
              <div class="divider"></div>${ui.btn(t("Start onboarding", "Bắt đầu khởi tạo"), { icon: "plus", kind: "primary", action: "onboard", sm: true })}`,
          })}
        </div>
      </div>`;
    },
    mount(root, { query }) {
      bindQuery(root);
      delegate(root, {
        onboard: () => onboardWizard(),
        export: () => {
          const q = (query.q || "").toLowerCase();
          const rows = scope.filter(db.organizations).filter((o) => !q || o.name.toLowerCase().includes(q));
          ui.downloadCsv("organizations.csv", [
            { label: "organization_id", key: "organization_id" }, { label: "legal_name", key: "name" }, { label: "display_name", key: "short_name" },
            { label: "legal_form", key: "legal_form" }, { label: "tax_code", csv: (o) => (o.legal_form === "INDIVIDUAL" ? maskId(o.tax_code) : o.tax_code) },
            { label: "kind", key: "kind" }, { label: "status", key: "status" }, { label: "status_reason", key: "status_reason" },
            { label: "account_manager", csv: (o) => (db.user(o.account_manager_user_id) || {}).full_name || "" }, { label: "trucks", csv: (o) => orgVehicles(o.organization_id).length },
          ], rows);
        },
      });
    },
  });

  /* =====================================================================
     2. Organization detail (ACC-01, ACC-02, ACC-12, ACC-22)
     ===================================================================== */
  function editOrgProfile(org) {
    ui.formModal({
      title: t("Edit organization", "Sửa thông tin tổ chức"), wide: true,
      fields: [
        { name: "name", label: t("Legal name", "Tên pháp lý"), value: org.name, required: true, full: true },
        { name: "short_name", label: t("Display name", "Tên hiển thị"), value: org.short_name, required: true },
        { name: "legal_form", label: t("Legal form", "Loại hình"), type: "select", value: org.legal_form, options: [["COMPANY", G3.label("COMPANY")], ["INDIVIDUAL", G3.label("INDIVIDUAL")]] },
        { name: "tax_code", label: t("Tax code", "Mã số thuế"), value: org.tax_code, required: true },
        { name: "address", label: t("Address", "Địa chỉ"), value: org.address, full: true },
        { name: "reason", label: t("Reason for the change", "Lý do thay đổi"), type: "textarea", required: true, full: true, hint: t("Saved in the change history (organization_history).", "Lưu vào lịch sử thay đổi (organization_history).") },
      ],
      onSubmit(v) {
        if (org.is_internal && v.legal_form !== "COMPANY") return t("An internal organization is always a company.", "Tổ chức nội bộ luôn là doanh nghiệp.");
        const err = taxCodeError(v.legal_form, v.tax_code, org.organization_id);
        if (err) return err;
        const before = snapshot(org);
        Object.assign(org, { name: v.name, short_name: v.short_name, legal_form: v.legal_form, tax_code: v.tax_code, address: v.address || null });
        if (!diffRows(before, snapshot(org)).length) return t("Nothing changed.", "Không có thay đổi.");
        recordOrgChange(org, v.reason.slice(0, 200));
        done(t("Organization updated", "Đã cập nhật tổ chức"));
      },
    });
  }
  function changeOrgStatus(org) {
    ui.formModal({
      title: t(`Status of ${org.short_name}`, `Trạng thái của ${org.short_name}`),
      intro: ui.callout("info", esc(t("SUSPENDED blocks logins but keeps collecting vehicle data. CLOSED blocks logins and new data; the data is kept for the legal retention period and the organization is soft-deleted.", "SUSPENDED chặn đăng nhập nhưng vẫn thu dữ liệu xe. CLOSED chặn đăng nhập và dữ liệu mới; dữ liệu được giữ theo thời hạn luật định và tổ chức bị xóa mềm."))),
      fields: [
        { name: "status", label: t("New status", "Trạng thái mới"), type: "select", value: org.status, options: ["ACTIVE", "SUSPENDED", "CLOSED"].map((s) => [s, G3.label(s)]) },
        { name: "reason", label: t("Reason", "Lý do"), type: "textarea", required: true, hint: t("Stored as status_reason (max 200 characters).", "Lưu vào status_reason (tối đa 200 ký tự).") },
      ],
      danger: true,
      submitLabel: t("Change status", "Đổi trạng thái"),
      onSubmit(v) {
        if (v.status === org.status) return t("Pick a different status.", "Chọn trạng thái khác.");
        org.status = v.status;
        org.status_reason = v.status === "ACTIVE" ? null : v.reason.slice(0, 200);
        recordOrgChange(org, v.reason.slice(0, 200));
        done(t(`Status changed to ${G3.label(v.status)}`, `Đã đổi trạng thái sang ${G3.label(v.status)}`));
      },
    });
  }
  function reassignAm(org) {
    ui.formModal({
      title: t("Assign account manager", "Giao người phụ trách"),
      fields: [
        { name: "am", label: t("Account manager", "Người phụ trách"), type: "select", value: org.account_manager_user_id || "", options: salesUsers().map((u) => [u.user_id, u.full_name]) },
        { name: "reason", label: t("Reason", "Lý do"), type: "textarea", required: true },
      ],
      onSubmit(v) {
        if (v.am === org.account_manager_user_id) return t("This person already manages the account.", "Người này đang phụ trách khách hàng.");
        org.account_manager_user_id = v.am;
        recordOrgChange(org, v.reason.slice(0, 200));
        done(t("Account manager changed", "Đã đổi người phụ trách"));
      },
    });
  }

  function historyTab(org, query) {
    const versions = versionsOf(org);
    const current = versions[versions.length - 1].row;
    const selIndex = query.ver !== undefined && versions[Number(query.ver)] ? Number(query.ver) : Math.max(0, versions.length - 2);
    const sel = versions[selIndex];
    const timeline = `<ul class="timeline">${versions.map((ver, i) => ({ ver, i })).reverse().map(({ ver, i }) => {
      const changed = ver.created ? [] : diffRows(versions[i - 1].row, ver.row);
      return `<li data-action="pick-ver" data-ver="${i}" style="cursor:pointer;${i === selIndex ? "background:var(--primary-soft);border-radius:6px;" : ""}padding-right:8px">
        <div class="row between"><b>${esc(ver.created ? t("Created", "Tạo mới") : changed.map(fieldName).join(", "))}</b>${i === versions.length - 1 ? `<span class="badge ok plain">${esc(t("Current", "Hiện tại"))}</span>` : ""}</div>
        <div class="small">${esc(ver.reason)}</div>
        <div class="when">${fmt.dateTime(ver.at)} · ${ui.userName(ver.by)}</div></li>`;
    }).join("")}</ul>`;
    const compare = ui.table({
      rows: ORG_FIELDS.map(([key]) => key),
      rowAttrs: (key) => (sel.row[key] !== current[key] ? 'style="background:var(--warn-soft)"' : ""),
      columns: [
        { label: t("Field", "Trường"), render: (key) => `<b>${esc(fieldName(key))}</b>` },
        { label: `${t("Version of", "Phiên bản ngày")} ${fmt.date(sel.at)}`, render: (key) => orgFieldValue(key, sel.row[key]) },
        { label: t("Current", "Hiện tại"), render: (key) => orgFieldValue(key, current[key]) },
        { label: "", render: (key) => (sel.row[key] !== current[key] ? `<span class="badge warn plain">${esc(t("changed", "đã đổi"))}</span>` : "") },
      ],
    });
    return `<div class="grid side-l">
      ${ui.card({ title: t("Timeline", "Dòng thời gian"), sub: `${versions.length} ${t("versions", "phiên bản")}`, body: timeline })}
      ${ui.card({ title: t("Compare with the current version", "So sánh với phiên bản hiện tại"), sub: "ACC-22", flush: true, body: compare + `<div class="card-body small muted">${esc(t("Each change keeps a copy of the whole old row with who changed it, when and why (change history). Pick a version on the left.", "Mỗi thay đổi lưu bản sao toàn bộ dòng cũ, kèm người sửa, thời điểm và lý do (lịch sử thay đổi). Chọn một phiên bản bên trái."))}</div>` })}
    </div>`;
  }

  G3.page({
    id: "organization-detail",
    path: "/organizations/:id",
    title: ["Organization", "Tổ chức"],
    roles: ["SALES", "CO_ADMIN", "HEAD_ADMIN"],
    features: ["ACC-01", "ACC-02", "ACC-12", "ACC-22"],
    navParent: "organizations",
    render({ params, query }) {
      const org = db.org(params.id);
      if (!org || !scope.isInternal()) return ui.pageHead({ title: t("Organization", "Tổ chức"), crumbs: [{ label: t("Organizations", "Tổ chức"), href: "#/organizations" }] }) + ui.empty(t("Organization not found", "Không tìm thấy tổ chức"));
      const tab = query.tab || "overview";
      const members = liveMemberships(org.organization_id);
      const staff = members.filter((m) => !(m.roles.length === 1 && m.roles[0] === "DRIVER"));
      const vehicles = orgVehicles(org.organization_id);
      const sub = subOf(org.organization_id);
      const plan = sub ? db.find("plans", sub.plan_id) : null;
      const admin = orgAdminOf(org.organization_id);
      const versions = versionsOf(org);
      const amHistory = versions.filter((v, i) => i === 0 ? v.row.account_manager_user_id : v.row.account_manager_user_id !== versions[i - 1].row.account_manager_user_id);
      const roleCounts = {};
      members.forEach((m) => m.roles.forEach((r) => { roleCounts[r] = (roleCounts[r] || 0) + 1; }));
      const dpaRow = consents().find((c) => c.purpose === "DATA_PROCESSING_AGREEMENT" && c.organization_id === org.organization_id);
      const dpaDoc = dpaRow ? legalDocs.find((d) => d.legal_document_id === dpaRow.legal_document_id) : null;
      const dpaAcceptedHere = (G3.store.get("g3.dpa", {}) || {})[org.organization_id];
      const canEdit = scope.has("SALES", "CO_ADMIN", "HEAD_ADMIN");
      const revealed = query.reveal === "1";

      const overview = `<div class="grid side">
        <div class="stack">
          ${ui.card({
            title: t("Profile", "Hồ sơ"), sub: "ACC-01", actions: canEdit ? ui.btn(t("Edit", "Sửa"), { icon: "edit", sm: true, action: "edit" }) : "",
            body: ui.kv([
              [t("Legal name", "Tên pháp lý"), esc(org.name)],
              [t("Display name", "Tên hiển thị"), esc(org.short_name)],
              [t("Legal form", "Loại hình"), esc(G3.label(org.legal_form))],
              [org.legal_form === "INDIVIDUAL" ? t("Citizen ID (tax code)", "Số CCCD (mã số thuế)") : t("Tax code", "Mã số thuế"), org.legal_form === "INDIVIDUAL" && !revealed ? `<span class="mono">${esc(maskId(org.tax_code))}</span> ${ui.btn(t("Show", "Hiện"), { sm: true, kind: "ghost", icon: "eye", action: "reveal" })}` : `<span class="mono">${esc(org.tax_code)}</span>`],
              [t("Address", "Địa chỉ"), esc(org.address)],
              [t("Kind", "Phân loại"), ui.badge(org.kind, KIND_TONES[org.kind]) + (org.is_internal ? ` <span class="small muted">${esc(t("users see every organization", "người dùng xem mọi tổ chức"))}</span>` : "")],
              [t("Created", "Ngày tạo"), `${fmt.date(org.created_at)} · ${ui.userName(versions[0].by)}`],
              org.legal_form === "COMPANY" && org.kind === "CUSTOMER" ? [t("Data processing agreement", "Thỏa thuận xử lý dữ liệu"), dpaDoc && (dpaDoc === currentDoc("DATA_PROCESSING_AGREEMENT") || dpaAcceptedHere) ? `${ui.badge("ACCEPTED")} <span class="small muted">v${esc(currentDoc("DATA_PROCESSING_AGREEMENT").version)} · ${fmt.date(dpaAcceptedHere && dpaAcceptedHere !== "demo" ? dpaAcceptedHere : dpaRow.accepted_at)}</span>` : dpaDoc ? `${ui.badge("PENDING")} <span class="small muted">${esc(t(`v${dpaDoc.version} accepted; v${currentDoc("DATA_PROCESSING_AGREEMENT").version} waiting for the administrator`, `đã chấp nhận v${dpaDoc.version}; v${currentDoc("DATA_PROCESSING_AGREEMENT").version} chờ quản trị viên`))}</span>` : `${ui.badge("PENDING")} <span class="small muted">${esc(t("accepted at the administrator's first login", "chấp nhận ở lần đăng nhập đầu của quản trị viên"))}</span>`] : null,
            ]),
          })}
          ${ui.card({
            title: t("Members", "Thành viên"), sub: `${members.length} · ACC-12`, flush: true,
            actions: G3.canSee("members") ? ui.btn(t("Manage members", "Quản lý thành viên"), { href: `#/members?org=${org.organization_id}`, sm: true, kind: "ghost" }) : "",
            body: `<div class="toolbar">${Object.entries(roleCounts).map(([r, n]) => `<span class="tag">${esc(G3.roleLabel(r))} · ${n}</span>`).join("") || `<span class="muted small">${esc(t("No member yet", "Chưa có thành viên"))}</span>`}</div>
              ${ui.table({
                rows: staff.slice(0, 8), empty: t("No office staff yet", "Chưa có nhân sự văn phòng"),
                columns: [
                  { label: t("Person", "Người dùng"), render: (m) => personCell(db.user(m.user_id)) },
                  { label: t("Roles", "Vai trò"), render: (m) => roleTags(m.roles) },
                  { label: t("Status", "Trạng thái"), render: (m) => ui.badge(m.status) },
                  { label: t("Joined", "Tham gia"), render: (m) => (m.joined_at ? fmt.date(m.joined_at) : `<span class="muted">${esc(t("invited", "đã mời"))} ${fmt.date(m.invited_at)}</span>`) },
                ],
              })}${staff.length > 8 ? `<div class="card-body small muted">+${staff.length - 8} ${esc(t("more", "người khác"))}</div>` : ""}`,
          })}
          ${ui.card({
            title: t("Trucks", "Xe"), sub: `${vehicles.length}`, flush: true,
            actions: G3.canSee("vehicles") ? ui.btn(t("All trucks", "Tất cả xe"), { href: "#/vehicles", sm: true, kind: "ghost" }) : "",
            body: ui.table({
              rows: vehicles.slice(0, 6), href: G3.canSee("vehicle-detail") ? (v) => `/vehicles/${v.vehicle_id}` : null, empty: t("No truck yet", "Chưa có xe"),
              columns: [
                { label: t("Truck", "Xe"), render: (v) => ui.vehicleCell(v) },
                { label: t("Model", "Dòng xe"), render: (v) => esc(db.model(v.vehicle_model_id).name) },
                { label: t("Fleet", "Đội xe"), render: (v) => esc((db.fleet(v.fleet_id) || {}).name || "—") },
                { label: t("Status", "Trạng thái"), render: (v) => ui.badge(v.status) },
              ],
            }) + (vehicles.length > 6 ? `<div class="card-body small muted">+${vehicles.length - 6} ${esc(t("more", "xe khác"))}</div>` : ""),
          })}
        </div>
        <div class="stack">
          ${ui.card({
            title: t("Status", "Trạng thái"), actions: canEdit && !org.is_internal ? ui.btn(t("Change", "Đổi"), { sm: true, action: "status" }) : "",
            body: `<div class="row">${ui.badge(org.status)}${org.status_reason ? `<span class="small">${esc(org.status_reason)}</span>` : ""}</div>${org.status === "SUSPENDED" ? `<div class="mt">${ui.callout("warn", esc(t("Logins are blocked; vehicle data is still collected.", "Đăng nhập bị chặn; dữ liệu xe vẫn được thu thập.")))}</div>` : ""}`,
          })}
          ${org.kind === "CUSTOMER" ? ui.card({
            title: t("Account manager", "Người phụ trách"), sub: "ACC-02", actions: scope.has("SALES", "CO_ADMIN", "HEAD_ADMIN") ? ui.btn(t("Reassign", "Giao lại"), { sm: true, action: "am" }) : "",
            body: `${org.account_manager_user_id ? `<div class="row"><span class="avatar">${esc(ui.initials(db.user(org.account_manager_user_id).full_name))}</span><div><b>${ui.userName(org.account_manager_user_id)}</b><div class="small muted">${esc(db.user(org.account_manager_user_id).phone_number)} · ${esc(db.user(org.account_manager_user_id).email || "")}</div></div></div>` : ui.callout("warn", esc(t("No account manager assigned.", "Chưa giao người phụ trách.")))}
              <div class="divider"></div><div class="small muted mb">${esc(t("Reassignment history", "Lịch sử giao người phụ trách"))}</div>
              <ul class="timeline">${amHistory.slice().reverse().map((v) => `<li><b>${v.row.account_manager_user_id ? ui.userName(v.row.account_manager_user_id) : esc(t("Unassigned", "Bỏ giao"))}</b><div class="small">${esc(v.reason)}</div><div class="when">${fmt.date(v.at)} · ${ui.userName(v.by)}</div></li>`).join("") || `<li class="small muted">${esc(t("No change yet", "Chưa thay đổi"))}</li>`}</ul>`,
          }) : ""}
          ${ui.card({
            title: t("Subscription", "Gói dịch vụ"), actions: G3.canSee("plans") ? ui.btn(t("Plans", "Gói"), { href: "#/plans", sm: true, kind: "ghost" }) : "",
            body: sub ? ui.kv([
              [t("Plan", "Gói"), `<b>${esc(plan.name)}</b> ${plan.visibility === "PRIVATE" ? `<span class="tag">${esc(t("private", "riêng"))}</span>` : ""}`],
              [t("Status", "Trạng thái"), ui.badge(sub.status)],
              [t("Agreed price", "Giá thỏa thuận"), `${fmt.vnd(sub.agreed_price_vnd_truck_month)} / ${esc(t("truck / month", "xe / tháng"))}`],
              [t("Monthly amount", "Tiền tháng"), fmt.vnd(sub.agreed_price_vnd_truck_month * vehicles.length)],
              [t("Billing cycle", "Chu kỳ"), esc(lbl(sub.billing_cycle))],
              [t("Period", "Thời hạn"), `${fmt.date(sub.starts_on)} → ${sub.ends_on ? fmt.date(sub.ends_on) : esc(t("open-ended", "không thời hạn"))}`],
              [t("Add-ons", "Gói bổ sung"), sub.add_ons.length ? sub.add_ons.map((a) => `${esc(a.code)} × ${a.quantity} (${fmt.num(a.used)}/${fmt.num(a.quota)})`).join(", ") : '<span class="muted">—</span>'],
            ]) : ui.empty(org.kind === "CUSTOMER" ? t("No subscription", "Chưa có gói dịch vụ") : t("Not applicable", "Không áp dụng")),
          })}
          ${!org.is_internal ? ui.card({
            title: t("Organization administrator", "Quản trị tổ chức"),
            body: admin ? `${personCell(db.user(admin.user_id), db.user(admin.user_id).phone_number)}<div class="mt">${ui.badge(admin.status)}</div>` : ui.callout("warn", esc(t("No organization admin: our co-administrator appoints a new one (Members screen).", "Chưa có quản trị tổ chức: đồng quản trị của chúng ta chỉ định người mới (màn hình Thành viên)."))),
          }) : ""}
        </div>
      </div>`;

      return `${ui.pageHead({
        title: org.short_name,
        sub: `${esc(org.name)} · ${ui.badge(org.status)}`,
        crumbs: [{ label: t("Organizations", "Tổ chức"), href: "#/organizations" }, { label: org.short_name }],
        features: ["ACC-01", "ACC-02", "ACC-12", "ACC-22"],
        actions: canEdit ? `${ui.btn(t("Edit", "Sửa"), { icon: "edit", action: "edit" })}${!org.is_internal ? ui.btn(t("Change status", "Đổi trạng thái"), { icon: "power", action: "status" }) : ""}` : "",
      })}
      ${ui.tabs([["overview", t("Overview", "Tổng quan")], ["history", `${t("Change history", "Lịch sử thay đổi")} (${versions.length})`]], tab)}
      ${tab === "history" ? historyTab(org, query) : overview}`;
    },
    mount(root, { params }) {
      const org = db.org(params.id);
      if (!org) return;
      bindQuery(root);
      delegate(root, {
        edit: () => editOrgProfile(org),
        status: () => changeOrgStatus(org),
        am: () => reassignAm(org),
        reveal: () => { G3.audit("VIEW", "CITIZEN_ID", org.short_name, null); G3.setQuery({ reveal: "1" }); },
        "pick-ver": (el) => G3.setQuery({ ver: el.dataset.ver }),
      });
    },
  });

  /* =====================================================================
     3. Members (ACC-09, ACC-11, ACC-12, ACC-13, FLT-03)
     ===================================================================== */
  function assignableRoles(org) {
    if (org.is_internal) return ["CO_ADMIN", "SALES", "OPERATIONS", "CUSTOMER_CARE", "ACCOUNTANT", "WARRANTY", "MAINTENANCE"];
    if (org.kind === "PARTNER") return ["TECHNICIAN", "ACCOUNTANT"];
    return ["FLEET_MANAGER", "DISPATCHER", "ACCOUNTANT", "DRIVER"];
  }
  /** Who may manage this organization's members: our administrators, or its own ORG_ADMIN. */
  function canManage(org) {
    if (scope.has("HEAD_ADMIN", "CO_ADMIN")) return scope.isInternal();
    return scope.has("ORG_ADMIN") && scope.org().organization_id === org.organization_id && !org.is_internal;
  }
  /** Head and co-administrator roles are internal only, and a co-administrator cannot grant them (ACC-13). */
  function canGrant(role, org) {
    if (role === "HEAD_ADMIN" || role === "ORG_ADMIN") return false;
    if (role === "CO_ADMIN") return org.is_internal && scope.has("HEAD_ADMIN");
    return canManage(org);
  }
  function canManageMember(m, org) {
    if (!canManage(org) || m.user_id === me().user_id || isLeft(m)) return false;
    if (m.roles.includes("HEAD_ADMIN")) return false;
    if (m.roles.includes("CO_ADMIN") && !scope.has("HEAD_ADMIN")) return false;
    return true;
  }
  const hasFleetRole = (m) => m.roles.includes("FLEET_MANAGER") || m.roles.includes("DISPATCHER");
  function fleetLimitText(m) {
    if (!hasFleetRole(m)) return '<span class="muted">—</span>';
    if (!m.fleet_ids.length) return `<span class="small muted">${esc(t("Whole organization", "Toàn tổ chức"))}</span>`;
    return m.fleet_ids.map((id) => ui.tag((db.fleet(id) || {}).name || id)).join(" ");
  }

  function inviteModal(org) {
    const roles = assignableRoles(org).filter((r) => canGrant(r, org));
    ui.formModal({
      title: t(`Invite to ${org.short_name}`, `Mời vào ${org.short_name}`), wide: true,
      intro: `<p class="small muted">${esc(t("The person receives an SMS and joins with a one-time code. An existing account just gains this organization; their other organizations are not affected.", "Người được mời nhận SMS và tham gia bằng mã OTP. Nếu đã có tài khoản, người đó chỉ được thêm tổ chức này; các tổ chức khác không bị ảnh hưởng."))}</p>`,
      fields: [
        { name: "phone", label: t("Phone number", "Số điện thoại"), type: "tel", required: true, placeholder: "09xx xxx xxx" },
        { name: "full_name", label: t("Full name", "Họ và tên"), hint: t("Not needed when the phone already has an account.", "Không cần nếu số điện thoại đã có tài khoản.") },
        { name: "email", label: t("E-mail (optional)", "E-mail (không bắt buộc)"), type: "email" },
        { name: "roles", label: t("Roles", "Vai trò"), type: "checkboxes", required: true, full: true, options: roles.map((r) => [r, G3.roleLabel(r)]), value: [] },
      ],
      submitLabel: t("Send invitation", "Gửi lời mời"),
      onSubmit(v) {
        if (!PHONE_RE.test(v.phone)) return t("The phone number has 10 digits and starts with 0.", "Số điện thoại gồm 10 chữ số, bắt đầu bằng 0.");
        let user = db.users.find((u) => u.phone_number === v.phone);
        if (user && orgMemberships(org.organization_id).some((m) => m.user_id === user.user_id && !isLeft(m))) return t("This person is already a member or invited.", "Người này đã là thành viên hoặc đã được mời.");
        if (user && user.status === "LOCKED") return t("This account is locked by our administrators.", "Tài khoản này đang bị quản trị viên khóa.");
        if (!user && !v.full_name) return t("Enter the full name of the new person.", "Nhập họ tên người mới.");
        const now = nowIso();
        const existing = !!user;
        if (!user) {
          user = { user_id: db.uid("usr"), full_name: v.full_name, phone_number: v.phone, email: v.email || null, status: "INVITED", status_reason: null, created_at: now, last_login_at: null };
          db.users.push(user);
          createdByMap.set(user.user_id, me().user_id);
        }
        const m = { membership_id: db.uid("mbr"), user_id: user.user_id, organization_id: org.organization_id, status: "INVITED", status_reason: null, roles: [], fleet_ids: [], invited_at: now, joined_at: null, left_at: null };
        db.memberships.push(m);
        v.roles.forEach((r) => grantRole(m, r));
        inviteMeta[m.membership_id] = { sent: 1, last_sent_at: now, invited_by: me().user_id };
        G3.setQuery({ tab: "invitations" });
        ui.toast(esc(existing ? t(`${user.full_name} already has an account: an invitation notice was sent to ${v.phone}.`, `${user.full_name} đã có tài khoản: đã gửi thông báo mời tới ${v.phone}.`) : t(`Invitation SMS with a code sent to ${v.phone}.`, `Đã gửi SMS mời kèm mã tới ${v.phone}.`)), "", "send");
      },
    });
  }

  function editRolesModal(m, org) {
    const roles = assignableRoles(org).filter((r) => canGrant(r, org));
    const fixed = m.roles.filter((r) => !roles.includes(r));
    const user = db.user(m.user_id);
    ui.formModal({
      title: t(`Roles of ${user.full_name}`, `Vai trò của ${user.full_name}`),
      intro: fixed.length ? `<div class="small muted">${esc(t("Kept as is:", "Giữ nguyên:"))} ${roleTags(fixed)}${fixed.includes("ORG_ADMIN") ? ` · ${esc(t("hand it over instead of revoking it", "bàn giao thay vì thu hồi"))}` : ""}</div>` : "",
      fields: [{ name: "roles", label: t("Roles", "Vai trò"), type: "checkboxes", full: true, options: roles.map((r) => [r, G3.roleLabel(r)]), value: m.roles.filter((r) => roles.includes(r)) }],
      onSubmit(v) {
        if (!v.roles.length && !fixed.length) return t("A member needs at least one role; remove the member instead.", "Thành viên cần ít nhất một vai trò; hãy xóa thành viên nếu không cần nữa.");
        const added = v.roles.filter((r) => !m.roles.includes(r));
        const removed = m.roles.filter((r) => roles.includes(r) && !v.roles.includes(r));
        if (!added.length && !removed.length) return t("Nothing changed.", "Không có thay đổi.");
        added.forEach((r) => grantRole(m, r));
        removed.forEach((r) => revokeRole(m, r));
        if (!hasFleetRole(m)) m.fleet_ids = [];
        const notes = [];
        if (added.includes("DRIVER") && !db.drivers.some((d) => d.membership_id === m.membership_id)) notes.push(t("Create the driver profile with a valid licence (Drivers screen).", "Tạo hồ sơ tài xế với GPLX còn hạn (màn hình Tài xế)."));
        if (removed.includes("DRIVER")) notes.push(t("The open driving session was ended.", "Phiên lái xe đang mở đã được kết thúc."));
        done(esc(t(`Roles updated: +${added.length} / −${removed.length}.`, `Đã cập nhật vai trò: +${added.length} / −${removed.length}.`) + (notes.length ? " " + notes.join(" ") : "")));
      },
    });
  }

  function handOverModal(org) {
    const current = orgAdminOf(org.organization_id);
    const candidates = liveMemberships(org.organization_id).filter((m) => m.status === "ACTIVE" && (!current || m.membership_id !== current.membership_id));
    if (!candidates.length) { ui.toast(t("No active member to hand the role to. Invite one first.", "Chưa có thành viên đang hoạt động để bàn giao. Hãy mời trước."), "alert"); return; }
    const isSelf = current && current.user_id === me().user_id;
    ui.formModal({
      title: current ? t("Hand over organization admin", "Bàn giao quản trị tổ chức") : t("Appoint organization admin", "Chỉ định quản trị tổ chức"),
      intro: ui.callout("warn", esc(current ? t(`Exactly one organization admin. ${db.user(current.user_id).full_name} loses the role in the same step${isSelf ? " (you will lose access to this screen)" : ""}.`, `Mỗi tổ chức đúng một quản trị viên. ${db.user(current.user_id).full_name} mất vai trò ngay trong cùng bước${isSelf ? " (bạn sẽ không còn vào được màn hình này)" : ""}.`) : t("The organization has no admin: as our administrator you appoint one.", "Tổ chức chưa có quản trị viên: với tư cách quản trị viên của chúng ta, bạn chỉ định người mới."))),
      fields: [
        { name: "to", label: t("New organization admin", "Quản trị tổ chức mới"), type: "select", options: candidates.map((m) => [m.membership_id, `${db.user(m.user_id).full_name} · ${m.roles.map(G3.roleLabel).join(", ")}`]) },
        { name: "confirm", label: t("Confirmation", "Xác nhận"), type: "checkbox", checkLabel: t("I understand the role moves to this person now.", "Tôi hiểu vai trò được chuyển cho người này ngay bây giờ.") },
      ],
      danger: true,
      submitLabel: current ? t("Hand over", "Bàn giao") : t("Appoint", "Chỉ định"),
      onSubmit(v) {
        if (!v.confirm) return t("Tick the confirmation first.", "Hãy đánh dấu xác nhận trước.");
        const target = db.memberships.find((m) => m.membership_id === v.to);
        if (current) revokeRole(current, "ORG_ADMIN");
        grantRole(target, "ORG_ADMIN");
        done(esc(t(`${db.user(target.user_id).full_name} is now the organization admin.`, `${db.user(target.user_id).full_name} đã là quản trị tổ chức.`)));
      },
    });
  }

  function fleetLimitModal(m, org) {
    const fleets = db.fleets.filter((f) => f.organization_id === org.organization_id);
    if (!fleets.length) { ui.toast(t("This organization has no fleet yet.", "Tổ chức này chưa có đội xe."), "alert"); return; }
    const depth = (f) => (f.parent_fleet_id ? 1 + depth(db.fleet(f.parent_fleet_id)) : 0);
    ui.formModal({
      title: t(`Fleet limit of ${db.user(m.user_id).full_name}`, `Giới hạn đội xe của ${db.user(m.user_id).full_name}`),
      intro: `<p class="small muted">${esc(t("A limited fleet manager or dispatcher sees only the trucks of these fleets and every fleet below them. No fleet ticked = the whole organization.", "Quản lý đội xe hoặc điều vận bị giới hạn chỉ thấy xe của các đội này và mọi đội con. Không chọn đội nào = toàn tổ chức."))}</p>`,
      fields: [{ name: "fleets", label: t("Fleets", "Đội xe"), type: "checkboxes", full: true, options: fleets.map((f) => [f.fleet_id, `${"— ".repeat(depth(f))}${f.name} (${f.code})`]), value: m.fleet_ids }],
      onSubmit(v) {
        m.fleet_ids = v.fleets;
        fleetLimitHistory.push({ at: nowIso(), by: me().user_id, membership_id: m.membership_id, fleet_ids: v.fleets.slice() });
        done(v.fleets.length ? t(`Limited to ${v.fleets.length} fleet(s) and the fleets below.`, `Đã giới hạn ${v.fleets.length} đội xe và các đội con.`) : t("Limit removed: whole organization.", "Đã bỏ giới hạn: toàn tổ chức."));
      },
    });
  }

  function lockMember(m, org) {
    if (m.roles.includes("ORG_ADMIN")) { ui.toast(t("Hand over the organization admin role first.", "Hãy bàn giao vai trò quản trị tổ chức trước."), "alert"); return; }
    const user = db.user(m.user_id);
    const locking = m.status !== "LOCKED";
    ui.reasonModal({
      title: locking ? t(`Lock ${user.full_name}`, `Khóa ${user.full_name}`) : t(`Unlock ${user.full_name}`, `Mở khóa ${user.full_name}`),
      intro: `<p class="small muted">${esc(locking ? t(`Only this membership is locked: ${user.full_name} keeps access to other organizations. Their open driving session ends.`, `Chỉ khóa tư cách thành viên này: ${user.full_name} vẫn vào được các tổ chức khác. Phiên lái xe đang mở sẽ kết thúc.`) : t("The member can log in to this organization again.", "Thành viên có thể đăng nhập lại vào tổ chức này."))}</p>`,
      danger: locking,
      submitLabel: locking ? t("Lock", "Khóa") : t("Unlock", "Mở khóa"),
      onSubmit(reason) {
        const old = m.status;
        m.status = locking ? "LOCKED" : "ACTIVE";
        m.status_reason = locking ? reason : null;
        memberChanges.push({ at: nowIso(), by: me().user_id, membership_id: m.membership_id, fields: [["status", old, m.status]], reason });
        done(locking ? t("Member locked", "Đã khóa thành viên") : t("Member unlocked", "Đã mở khóa thành viên"));
      },
    });
  }
  function removeMember(m) {
    if (m.roles.includes("ORG_ADMIN")) { ui.toast(t("Hand over the organization admin role first.", "Hãy bàn giao vai trò quản trị tổ chức trước."), "alert"); return; }
    const user = db.user(m.user_id);
    ui.reasonModal({
      title: t(`Remove ${user.full_name}`, `Xóa ${user.full_name} khỏi tổ chức`),
      intro: `<p class="small muted">${esc(t("The membership ends (left_at); roles and fleet limits close, the driver profile and its history stay. The account itself is not deleted.", "Tư cách thành viên kết thúc (left_at); vai trò và giới hạn đội xe đóng lại, hồ sơ tài xế và lịch sử được giữ. Tài khoản không bị xóa."))}</p>`,
      submitLabel: t("Remove", "Xóa khỏi tổ chức"),
      onSubmit(reason) {
        m.roles.slice().forEach((r) => revokeRole(m, r));
        m.fleet_ids = [];
        m.left_at = nowIso();
        m.status_reason = reason;
        memberChanges.push({ at: nowIso(), by: me().user_id, membership_id: m.membership_id, fields: [["left_at", null, m.left_at]], reason });
        done(t("Member removed; history kept", "Đã xóa thành viên; lịch sử được giữ"));
      },
    });
  }
  function memberHistoryModal(m) {
    const user = db.user(m.user_id);
    const events = [];
    roleEvents.filter((e) => e.membership_id === m.membership_id).forEach((e) => {
      events.push({ at: e.granted_at, by: e.granted_by, text: t(`Granted ${G3.roleLabel(e.role)}`, `Cấp vai trò ${G3.roleLabel(e.role)}`), tone: "ok" });
      if (e.revoked_at) events.push({ at: e.revoked_at, by: e.revoked_by, text: t(`Revoked ${G3.roleLabel(e.role)}`, `Thu hồi vai trò ${G3.roleLabel(e.role)}`), tone: "danger" });
    });
    memberChanges.filter((c) => c.membership_id === m.membership_id).forEach((c) => events.push({ at: c.at, by: c.by, text: c.fields.map(([f, a, b]) => `${f}: ${a === null ? "—" : lbl(a)} → ${b === null ? "—" : f === "left_at" ? fmt.date(b) : lbl(b)}`).join(", ") + ` · ${c.reason}`, tone: "warn" }));
    fleetLimitHistory.filter((c) => c.membership_id === m.membership_id).forEach((c) => events.push({ at: c.at, by: c.by, text: t("Fleet limit: ", "Giới hạn đội xe: ") + (c.fleet_ids.map((id) => (db.fleet(id) || {}).name).join(", ") || t("whole organization", "toàn tổ chức")), tone: "info" }));
    events.push({ at: m.invited_at, by: (inviteMeta[m.membership_id] || {}).invited_by || createdBy(user), text: t("Invited", "Được mời"), tone: "info" });
    if (m.joined_at) events.push({ at: m.joined_at, by: m.user_id, text: t("Joined with OTP", "Tham gia bằng OTP"), tone: "ok" });
    events.sort((a, b) => (a.at < b.at ? 1 : -1));
    ui.modal({
      title: t(`History of ${user.full_name}`, `Lịch sử của ${user.full_name}`),
      body: `<ul class="timeline">${events.map((e) => `<li><b>${esc(e.text)}</b><div class="when">${fmt.dateTime(e.at)} · ${ui.userName(e.by)}</div></li>`).join("")}</ul>`,
    });
  }
  function memberMenu(anchor, m, org) {
    const items = [{ title: db.user(m.user_id).full_name }];
    const manageable = canManageMember(m, org);
    if (manageable) items.push({ label: t("Edit roles", "Sửa vai trò"), icon: "shield", onClick: () => editRolesModal(m, org) });
    if (manageable && hasFleetRole(m) && !org.is_internal) items.push({ label: t("Set fleet limit", "Đặt giới hạn đội xe"), sub: "FLT-03", icon: "layers", onClick: () => fleetLimitModal(m, org) });
    if (m.roles.includes("ORG_ADMIN") && canManage(org)) items.push({ label: t("Hand over organization admin", "Bàn giao quản trị tổ chức"), icon: "key", onClick: () => handOverModal(org) });
    if (manageable && m.status !== "INVITED") items.push({ label: m.status === "LOCKED" ? t("Unlock member", "Mở khóa thành viên") : t("Lock member", "Khóa thành viên"), icon: m.status === "LOCKED" ? "unlock" : "lock", onClick: () => lockMember(m, org) });
    if (manageable && m.status !== "INVITED") items.push({ label: t("Remove from organization", "Xóa khỏi tổ chức"), icon: "trash", onClick: () => removeMember(m) });
    items.push({ label: t("History (roles, status)", "Lịch sử (vai trò, trạng thái)"), icon: "clock", onClick: () => memberHistoryModal(m) });
    if (!manageable && m.user_id === me().user_id) items.splice(1, 0, { label: t("This is you", "Đây là bạn"), sub: t("Your own membership is managed by another admin", "Tư cách thành viên của bạn do quản trị viên khác quản lý"), icon: "info", onClick: () => {} });
    ui.popover(anchor, items, { align: "right" });
  }

  /* ---- Bulk import (ACC-11) ---- */
  const importState = {};
  function sampleImportRows(org) {
    const fleets = db.fleets.filter((f) => f.organization_id === org.organization_id);
    const f0 = fleets[0] ? fleets[0].name : "";
    const f1 = fleets[1] ? fleets[1].name : f0;
    const existing = liveMemberships(org.organization_id).map((m) => db.user(m.user_id)).find((u) => u && u.status === "ACTIVE");
    const exp = (days) => new Date(NOW + days * DAY).toISOString().slice(0, 10);
    const raw = [
      ["Nguyễn Văn An", "0912345601", "DRIVER", "CE", "791234500001", exp(900), f0],
      ["Trần Quốc Bảo", "0912345602", "DRIVER", "CE", "791234500002", exp(420), f0],
      ["Lê Thị Cúc", "091234560", "DISPATCHER", "", "", "", f1],
      ["Phạm Minh Đức", "0912345604", "DRIVER", "C", "791234500004", exp(-30), f1],
      ["Hoàng Văn Em", "0912345605", "TAI_XE", "CE", "791234500005", exp(600), f0],
      ["Vũ Thanh Giang", "0912345606", "FLEET_MANAGER", "", "", "", f1],
      ["Đỗ Hữu Hạnh", existing ? existing.phone_number : "0912345607", "DRIVER", "CE", "791234500007", exp(700), f0],
      ["Bùi Văn Khánh", "0912345608", "DRIVER", "CE", "", exp(500), f0],
      ["Ngô Thị Lan Anh", "0912345609", "ACCOUNTANT", "", "", "", ""],
      ["Dương Văn Minh", "0912345610", "DRIVER", "CE", "791234500010", exp(1100), "Depot Hà Nội"],
      ["Hồ Thị Ngọc", "0912345611", "DISPATCHER", "", "", "", f0],
      ["Trịnh Văn Phát", "0912345602", "DRIVER", "CE", "791234500012", exp(800), f0],
    ];
    const allowed = assignableRoles(org);
    const seenPhones = new Set();
    return raw.map(([full_name, phone, role, license_class, license_number, license_expires_on, fleet], i) => {
      const errors = [];
      if (!PHONE_RE.test(phone)) errors.push(t("Phone must have 10 digits", "Số điện thoại phải có 10 chữ số"));
      else if (seenPhones.has(phone)) errors.push(t("Phone repeated in the file", "Số điện thoại lặp lại trong tệp"));
      else if (db.users.some((u) => u.phone_number === phone && orgMemberships(org.organization_id).some((m) => m.user_id === u.user_id && !isLeft(m)))) errors.push(t("Already a member", "Đã là thành viên"));
      seenPhones.add(phone);
      if (!allowed.includes(role)) errors.push(t(`Unknown role "${role}"`, `Vai trò không hợp lệ "${role}"`));
      if (role === "DRIVER") {
        if (!license_number) errors.push(t("Licence number missing", "Thiếu số GPLX"));
        if (license_expires_on && license_expires_on < new Date(NOW).toISOString().slice(0, 10)) errors.push(t("Licence expired", "GPLX đã hết hạn"));
      }
      const fleetRow = fleet ? db.fleets.find((f) => f.organization_id === org.organization_id && f.name === fleet) : null;
      if (fleet && !fleetRow) errors.push(t(`Unknown fleet "${fleet}"`, `Không có đội xe "${fleet}"`));
      return { row: i + 2, full_name, phone, role, license_class, license_number, license_expires_on, fleet, fleet_id: fleetRow ? fleetRow.fleet_id : null, errors };
    });
  }
  function importTab(org) {
    const st = importState[org.organization_id];
    const steps = `<div class="grid cols-3">
      ${ui.card({ title: `1. ${t("Download the template", "Tải tệp mẫu")}`, body: `<p class="small muted">${esc(t("Columns: full name, phone, role, licence class, licence number, licence expiry, fleet.", "Các cột: họ tên, số điện thoại, vai trò, hạng GPLX, số GPLX, hạn GPLX, đội xe."))}</p>${ui.btn(t("Template (.csv)", "Tệp mẫu (.csv)"), { icon: "download", sm: true, action: "import-template" })}` })}
      ${ui.card({ title: `2. ${t("Upload the spreadsheet", "Tải bảng tính lên")}`, body: `<label class="btn sm">${ui.icon("upload", 14)}${esc(t("Choose a file", "Chọn tệp"))}<input type="file" accept=".xlsx,.xls,.csv" data-import-file style="display:none"></label> ${ui.btn(t("Use the sample file", "Dùng tệp ví dụ"), { sm: true, kind: "ghost", action: "import-sample" })}<p class="small muted" style="margin-top:8px">${esc(t("Every row is checked before anything is created.", "Mọi dòng được kiểm tra trước khi tạo dữ liệu."))}</p>` })}
      ${ui.card({ title: `3. ${t("Create invitations", "Tạo lời mời")}`, body: `<p class="small muted">${esc(t("Valid rows become invitations (SMS) and, for drivers, driver profiles. Rows with errors are skipped; fix them and import again.", "Dòng hợp lệ được tạo lời mời (SMS) và, với tài xế, hồ sơ tài xế. Dòng lỗi bị bỏ qua; sửa rồi nhập lại."))}</p>` })}
    </div>`;
    if (!st) return steps + `<div class="mt">${ui.card({ body: ui.empty(t("No file uploaded yet", "Chưa tải tệp nào")) })}</div>`;
    const valid = st.rows.filter((r) => !r.errors.length);
    const report = ui.card({
      title: t(`Validation report · ${st.fileName}`, `Kết quả kiểm tra · ${st.fileName}`),
      sub: `${st.rows.length} ${t("rows", "dòng")} · ${valid.length} ${t("valid", "hợp lệ")} · ${st.rows.length - valid.length} ${t("with errors", "có lỗi")}`,
      flush: true,
      actions: st.done ? ui.btn(t("New import", "Nhập tệp khác"), { sm: true, action: "import-reset" }) : `${ui.btn(t("Error report", "Báo cáo lỗi"), { sm: true, icon: "download", action: "import-errors" })}${ui.btn(t(`Create ${valid.length} invitations`, `Tạo ${valid.length} lời mời`), { sm: true, kind: "primary", icon: "send", action: "import-run", disabled: !valid.length })}`,
      body: (st.done ? `<div class="card-body">${ui.callout("ok", esc(t(`${st.done.created} invitations sent and ${st.done.drivers} driver profiles created on ${fmt.dateTime(st.done.at)}.`, `Đã gửi ${st.done.created} lời mời và tạo ${st.done.drivers} hồ sơ tài xế lúc ${fmt.dateTime(st.done.at)}.`)))}</div>` : "") + ui.table({
        rows: st.rows,
        rowAttrs: (r) => (r.errors.length ? 'style="background:var(--danger-soft)"' : ""),
        columns: [
          { label: t("Row", "Dòng"), align: "right", render: (r) => r.row },
          { label: t("Full name", "Họ tên"), render: (r) => esc(r.full_name) },
          { label: t("Phone", "Số điện thoại"), render: (r) => `<span class="mono">${esc(r.phone)}</span>` },
          { label: t("Role", "Vai trò"), render: (r) => (G3.ROLES.includes(r.role) ? ui.tag(G3.roleLabel(r.role)) : `<span class="mono">${esc(r.role)}</span>`) },
          { label: t("Licence", "GPLX"), render: (r) => (r.role === "DRIVER" ? `${esc(r.license_class)} · <span class="mono">${esc(r.license_number || "—")}</span><span class="sub">${r.license_expires_on ? fmt.date(r.license_expires_on) : "—"}</span>` : '<span class="muted">—</span>') },
          { label: t("Fleet", "Đội xe"), render: (r) => esc(r.fleet || "—") },
          { label: t("Result", "Kết quả"), render: (r) => (r.errors.length ? `<span class="badge danger">${esc(t("Error", "Lỗi"))}</span><span class="sub" style="color:var(--danger)">${esc(r.errors.join(" · "))}</span>` : r.created ? ui.badge("INVITED") : `<span class="badge ok">${esc(t("Valid", "Hợp lệ"))}</span>`) },
        ],
      }),
    });
    return steps + `<div class="mt">${report}</div>`;
  }
  function runImport(org) {
    const st = importState[org.organization_id];
    const now = nowIso();
    let created = 0;
    let drivers = 0;
    st.rows.filter((r) => !r.errors.length).forEach((r) => {
      let user = db.users.find((u) => u.phone_number === r.phone);
      if (!user) {
        user = { user_id: db.uid("usr"), full_name: r.full_name, phone_number: r.phone, email: null, status: "INVITED", status_reason: null, created_at: now, last_login_at: null };
        db.users.push(user);
        createdByMap.set(user.user_id, me().user_id);
      }
      const m = { membership_id: db.uid("mbr"), user_id: user.user_id, organization_id: org.organization_id, status: "INVITED", status_reason: null, roles: [], fleet_ids: r.fleet_id && r.role !== "DRIVER" && r.role !== "ACCOUNTANT" ? [r.fleet_id] : [], invited_at: now, joined_at: null, left_at: null };
      db.memberships.push(m);
      grantRole(m, r.role);
      inviteMeta[m.membership_id] = { sent: 1, last_sent_at: now, invited_by: me().user_id, imported: true };
      if (r.role === "DRIVER") {
        db.drivers.push({ driver_id: db.uid("drv"), membership_id: m.membership_id, user_id: user.user_id, organization_id: org.organization_id, full_name: r.full_name, phone_number: r.phone, license_number: r.license_number, license_class: r.license_class, license_expires_on: r.license_expires_on, status: "ACTIVE", status_reason: null, safety_score: 80, created_at: now });
        drivers++;
      }
      r.created = true;
      created++;
    });
    st.done = { created, drivers, at: now };
    done(esc(t(`${created} invitations sent by SMS`, `Đã gửi ${created} lời mời qua SMS`)));
  }

  function membersTab(org, query, rowsAll) {
    const q = (query.q || "").toLowerCase();
    const status = query.status || "";
    const rows = rowsAll.filter((m) => {
      const u = db.user(m.user_id);
      if (status === "LEFT" ? !isLeft(m) : isLeft(m)) return false;
      if (status && status !== "LEFT" && m.status !== status) return false;
      if (query.role && !m.roles.includes(query.role)) return false;
      if (q && !(u.full_name.toLowerCase().includes(q) || u.phone_number.includes(q))) return false;
      return true;
    }).sort((a, b) => (b.roles.includes("ORG_ADMIN") - a.roles.includes("ORG_ADMIN")) || db.user(a.user_id).full_name.localeCompare(db.user(b.user_id).full_name, "vi"));
    const roleOptions = [...new Set(rowsAll.flatMap((m) => m.roles))];
    return ui.card({
      flush: true,
      body: `<div class="toolbar">${ui.search("q", query.q, t("Name or phone", "Tên hoặc số điện thoại"))}
        ${ui.select("role", [["", t("All roles", "Mọi vai trò")], ...roleOptions.map((r) => [r, G3.roleLabel(r)])], query.role || "")}
        ${ui.select("status", [["", t("Current members", "Thành viên hiện tại")], ["ACTIVE", G3.label("ACTIVE")], ["INVITED", G3.label("INVITED")], ["LOCKED", G3.label("LOCKED")], ["LEFT", lbl("LEFT")]], status)}
        <span class="grow"></span><span class="small muted">${rows.length} ${esc(t("members", "thành viên"))}</span></div>
        ${ui.table({
          rows, empty: t("No member matches", "Không có thành viên phù hợp"),
          columns: [
            { label: t("Person", "Người dùng"), render: (m) => personCell(db.user(m.user_id), db.user(m.user_id).email || (m.user_id === me().user_id ? t("you", "bạn") : "")) },
            { label: t("Phone", "Số điện thoại"), render: (m) => `<span class="mono">${esc(db.user(m.user_id).phone_number)}</span>` },
            { label: t("Roles", "Vai trò"), render: (m) => roleTags(m.roles) },
            { label: t("Fleet limit", "Giới hạn đội xe"), render: (m) => fleetLimitText(m) },
            { label: t("Status", "Trạng thái"), render: (m) => `${isLeft(m) ? badge("LEFT", "muted") : ui.badge(m.status)}${m.status_reason ? `<span class="sub">${esc(m.status_reason)}</span>` : ""}` },
            { label: t("Invited", "Ngày mời"), render: (m) => fmt.date(m.invited_at) },
            { label: status === "LEFT" ? t("Joined / left", "Tham gia / rời") : t("Joined", "Tham gia"), render: (m) => `${m.joined_at ? fmt.date(m.joined_at) : '<span class="muted">—</span>'}${m.left_at ? `<span class="sub">${esc(t("left", "rời"))} ${fmt.date(m.left_at)}</span>` : ""}` },
            { label: "", align: "right", render: (m) => ui.btn("", { sm: true, kind: "ghost", icon: "menu", action: "member-menu", attrs: `data-id="${m.membership_id}" title="${esc(t("Actions", "Thao tác"))}"` }) },
          ],
        })}`,
    });
  }
  function invitationsTab(org, rowsAll) {
    const pending = rowsAll.filter((m) => m.status === "INVITED" && !isLeft(m));
    const manage = canManage(org);
    const cancelled = cancelledInvites.filter((c) => c.organization_id === org.organization_id);
    return ui.card({
      title: t("Pending invitations", "Lời mời đang chờ"), sub: `${pending.length} · ${t("a code is valid for 7 days", "mã có hiệu lực 7 ngày")}`, flush: true,
      body: ui.table({
        rows: pending, empty: t("No pending invitation", "Không có lời mời đang chờ"),
        columns: [
          { label: t("Person", "Người được mời"), render: (m) => personCell(db.user(m.user_id), db.user(m.user_id).status === "ACTIVE" ? t("existing account", "đã có tài khoản") : t("new account", "tài khoản mới")) },
          { label: t("Phone", "Số điện thoại"), render: (m) => `<span class="mono">${esc(db.user(m.user_id).phone_number)}</span>` },
          { label: t("Roles", "Vai trò"), render: (m) => roleTags(m.roles) },
          { label: t("Invited", "Ngày mời"), render: (m) => `${fmt.dateTime(m.invited_at)}<span class="sub">${t("by", "bởi")} ${ui.userName((inviteMeta[m.membership_id] || {}).invited_by || createdBy(db.user(m.user_id)))}</span>` },
          { label: t("SMS sent", "Đã gửi SMS"), align: "right", render: (m) => { const meta = inviteMeta[m.membership_id]; return meta ? `${meta.sent}<span class="sub">${fmt.ago(meta.last_sent_at)}</span>` : "1"; } },
          { label: t("Code expires", "Mã hết hạn"), render: (m) => { const exp = new Date(new Date((inviteMeta[m.membership_id] || {}).last_sent_at || m.invited_at).getTime() + 7 * DAY); return exp.getTime() < Date.now() ? ui.badge("EXPIRED") : fmt.ago(exp.toISOString()); } },
          { label: "", align: "right", render: (m) => (manage ? `<span class="row" style="justify-content:flex-end;gap:6px">${ui.btn(t("Resend", "Gửi lại"), { sm: true, icon: "send", action: "resend", attrs: `data-id="${m.membership_id}"` })}${ui.btn(t("Cancel", "Hủy"), { sm: true, kind: "ghost", icon: "x", action: "cancel-invite", attrs: `data-id="${m.membership_id}"` })}</span>` : "") },
        ],
      }) + (cancelled.length ? `<div class="card-body small muted">${esc(t("Cancelled:", "Đã hủy:"))} ${cancelled.map((c) => `${esc(c.name)} (${esc(c.phone)}, ${fmt.date(c.at)})`).join(" · ")}</div>` : ""),
    });
  }
  function rolesHistoryTab(org) {
    const rows = [];
    roleEvents.filter((e) => e.organization_id === org.organization_id).forEach((e) => {
      const m = db.memberships.find((x) => x.membership_id === e.membership_id);
      rows.push({ at: e.granted_at, by: e.granted_by, m, role: e.role, kind: "GRANTED" });
      if (e.revoked_at) rows.push({ at: e.revoked_at, by: e.revoked_by, m, role: e.role, kind: "REVOKED" });
    });
    rows.sort((a, b) => (a.at < b.at ? 1 : -1));
    return ui.card({
      title: t("Role assignments", "Lịch sử gán vai trò"), sub: `${rows.length} · ACC-13`, flush: true,
      body: ui.table({
        rows: rows.slice(0, 60),
        columns: [
          { label: t("When", "Thời điểm"), render: (r) => `${fmt.dateTime(r.at)}<span class="sub">${fmt.ago(r.at)}</span>` },
          { label: t("Member", "Thành viên"), render: (r) => (r.m ? esc(db.user(r.m.user_id).full_name) : "—") },
          { label: t("Role", "Vai trò"), render: (r) => roleTags([r.role]) },
          { label: t("Change", "Thay đổi"), render: (r) => (r.kind === "GRANTED" ? `<span class="badge ok">${esc(t("Granted", "Cấp"))}</span>` : `<span class="badge danger">${esc(t("Revoked", "Thu hồi"))}</span>`) },
          { label: t("By", "Người thực hiện"), render: (r) => ui.userName(r.by) },
        ],
      }),
    });
  }

  G3.page({
    id: "members",
    path: "/members",
    title: ["Members & roles", "Thành viên & vai trò"],
    roles: ["ORG_ADMIN", "CO_ADMIN", "OPERATIONS"],
    features: ["ACC-09", "ACC-11", "ACC-12", "ACC-13", "FLT-03"],
    nav: { group: "admin", order: 2, icon: "users" },
    render({ query }) {
      const org = contextOrg(query);
      const head = (o) => ui.pageHead({
        title: t("Members & roles", "Thành viên & vai trò"),
        sub: o ? esc(`${o.name}`) : esc(t("Members of an organization, their roles and fleet limits", "Thành viên của một tổ chức, vai trò và giới hạn đội xe")),
        features: ["ACC-09", "ACC-11", "ACC-12", "ACC-13", "FLT-03"],
        actions: o ? `${orgSwitchSelect(o)}${ui.btn(t("Export", "Xuất"), { icon: "download", action: "export" })}${canManage(o) ? ui.btn(t("Invite", "Mời thành viên"), { icon: "plus", kind: "primary", action: "invite" }) : ""}` : "",
      });
      if (!org) return head(null) + orgPicker(t("Choose an organization", "Chọn tổ chức"), query, true);
      const rowsAll = orgMemberships(org.organization_id);
      const live = rowsAll.filter((m) => !isLeft(m));
      const admin = orgAdminOf(org.organization_id);
      const canImport = scope.has("OPERATIONS", "CO_ADMIN", "HEAD_ADMIN") && scope.isInternal();
      const tabs = [["members", t("Members", "Thành viên")], ["invitations", `${t("Invitations", "Lời mời")} (${live.filter((m) => m.status === "INVITED").length})`], ["roles", t("Role history", "Lịch sử vai trò")]];
      if (canImport) tabs.push(["import", t("Bulk import", "Nhập hàng loạt")]);
      const tab = tabs.some(([k]) => k === query.tab) ? query.tab : scope.has("OPERATIONS") && !scope.has("CO_ADMIN", "HEAD_ADMIN") ? "import" : "members";
      const limited = live.filter((m) => hasFleetRole(m) && m.fleet_ids.length).length;
      const adminCard = org.is_internal ? ui.callout("info", esc(t("Internal organization: head and co-administrator roles exist only here; a co-administrator cannot grant or revoke administrator roles.", "Tổ chức nội bộ: vai trò quản trị trưởng và đồng quản trị chỉ có ở đây; đồng quản trị không cấp hay thu hồi vai trò quản trị viên.")), "shield")
        : admin ? ui.callout("info", `${esc(t("Organization admin:", "Quản trị tổ chức:"))} <b>${esc(db.user(admin.user_id).full_name)}</b>${admin.status === "INVITED" ? ` (${esc(t("invited", "đã mời"))})` : ""} · ${esc(t("exactly one per organization, handed over in one step.", "mỗi tổ chức đúng một người, bàn giao trong một bước."))} ${canManage(org) ? ui.btn(t("Hand over", "Bàn giao"), { sm: true, kind: "ghost", icon: "key", action: "handover" }) : ""}`, "key")
        : ui.callout("warn", `${esc(t("This organization has no admin.", "Tổ chức này chưa có quản trị viên."))} ${scope.has("CO_ADMIN", "HEAD_ADMIN") ? ui.btn(t("Appoint one", "Chỉ định"), { sm: true, action: "handover" }) : ""}`);
      return `${head(org)}
      <div class="grid cols-4">
        ${ui.kpi({ label: t("Active members", "Thành viên hoạt động"), icon: "users", value: live.filter((m) => m.status === "ACTIVE").length, foot: `${live.filter((m) => m.roles.includes("DRIVER")).length} ${t("drivers", "tài xế")}` })}
        ${ui.kpi({ label: t("Pending invitations", "Lời mời đang chờ"), icon: "send", value: live.filter((m) => m.status === "INVITED").length, tone: live.some((m) => m.status === "INVITED") ? "info" : undefined, foot: t("joined with OTP", "tham gia bằng OTP") })}
        ${ui.kpi({ label: t("Locked", "Đã khóa"), icon: "lock", value: live.filter((m) => m.status === "LOCKED").length, tone: live.some((m) => m.status === "LOCKED") ? "warn" : undefined, foot: `${rowsAll.filter(isLeft).length} ${t("former members", "thành viên cũ")}` })}
        ${ui.kpi({ label: t("Fleet-limited managers", "Quản lý bị giới hạn đội"), icon: "layers", value: limited, foot: `FLT-03 · ${db.fleets.filter((f) => f.organization_id === org.organization_id).length} ${t("fleets", "đội xe")}` })}
      </div>
      <div class="mt mb">${adminCard}</div>
      ${ui.tabs(tabs, tab)}
      ${tab === "invitations" ? invitationsTab(org, rowsAll) : tab === "roles" ? rolesHistoryTab(org) : tab === "import" ? importTab(org) : membersTab(org, query, rowsAll)}`;
    },
    mount(root, { query }) {
      bindQuery(root);
      bindOrgPicker(root);
      const org = contextOrg(query);
      if (!org) return;
      const byId = (el) => db.memberships.find((m) => m.membership_id === el.dataset.id);
      const fileInput = root.querySelector("[data-import-file]");
      if (fileInput) fileInput.addEventListener("change", () => {
        const file = fileInput.files[0];
        if (!file) return;
        importState[org.organization_id] = { fileName: file.name, rows: sampleImportRows(org) };
        done(t("File checked: see the validation report", "Đã kiểm tra tệp: xem kết quả bên dưới"));
      });
      delegate(root, {
        invite: () => inviteModal(org),
        handover: () => handOverModal(org),
        "member-menu": (el) => memberMenu(el, byId(el), org),
        resend: (el) => {
          const m = byId(el);
          const meta = inviteMeta[m.membership_id] || (inviteMeta[m.membership_id] = { sent: 1, last_sent_at: m.invited_at });
          if (meta.sent >= 3 && Date.now() - new Date(meta.last_sent_at).getTime() < DAY) { ui.toast(t("Daily limit reached: 3 SMS per invitation per day.", "Đã đạt giới hạn: 3 SMS cho mỗi lời mời mỗi ngày."), "alert"); return; }
          meta.sent++;
          meta.last_sent_at = nowIso();
          done(esc(t(`New code sent to ${db.user(m.user_id).phone_number}`, `Đã gửi mã mới tới ${db.user(m.user_id).phone_number}`)));
        },
        "cancel-invite": (el) => {
          const m = byId(el);
          const user = db.user(m.user_id);
          ui.formModal({
            title: t("Cancel invitation", "Hủy lời mời"), danger: true, submitLabel: t("Cancel invitation", "Hủy lời mời"),
            intro: `<p>${esc(t(`The code sent to ${user.phone_number} stops working.`, `Mã đã gửi tới ${user.phone_number} sẽ hết hiệu lực.`))}</p>`,
            fields: [],
            onSubmit() {
              m.roles.slice().forEach((r) => revokeRole(m, r));
              db.memberships.splice(db.memberships.indexOf(m), 1);
              cancelledInvites.push({ organization_id: m.organization_id, name: user.full_name, phone: user.phone_number, at: nowIso() });
              // A brand-new account that was only invited here disappears with the invitation.
              if (user.status === "INVITED" && !db.memberships.some((x) => x.user_id === user.user_id)) db.users.splice(db.users.indexOf(user), 1);
              done(t("Invitation cancelled", "Đã hủy lời mời"));
            },
          });
        },
        export: () => ui.auditedExport({
          dataType: "MEMBERS", subject: org.short_name, filename: `members-${org.short_name}.csv`,
          columns: [{ label: "full_name", csv: (m) => db.user(m.user_id).full_name }, { label: "phone_number", csv: (m) => db.user(m.user_id).phone_number }, { label: "roles", csv: (m) => m.roles.join(" ") }, { label: "status", key: "status" }, { label: "fleet_limit", csv: (m) => m.fleet_ids.map((id) => db.fleet(id).name).join(" | ") }, { label: "invited_at", key: "invited_at" }, { label: "joined_at", key: "joined_at" }, { label: "left_at", key: "left_at" }],
          rows: orgMemberships(org.organization_id),
        }),
        "import-sample": () => { importState[org.organization_id] = { fileName: "danh-sach-tai-xe.xlsx", rows: sampleImportRows(org) }; done(t("File checked: see the validation report", "Đã kiểm tra tệp: xem kết quả bên dưới")); },
        "import-template": () => ui.downloadCsv("import-template.csv", [{ label: "full_name", key: "full_name" }, { label: "phone_number", key: "phone" }, { label: "role", key: "role" }, { label: "license_class", key: "license_class" }, { label: "license_number", key: "license_number" }, { label: "license_expires_on", key: "license_expires_on" }, { label: "fleet", key: "fleet" }], sampleImportRows(org).slice(0, 2)),
        "import-errors": () => ui.downloadCsv("import-errors.csv", [{ label: "row", key: "row" }, { label: "full_name", key: "full_name" }, { label: "phone_number", key: "phone" }, { label: "errors", csv: (r) => r.errors.join("; ") }], importState[org.organization_id].rows.filter((r) => r.errors.length)),
        "import-run": () => runImport(org),
        "import-reset": () => { delete importState[org.organization_id]; G3.render(); },
      });
    },
  });

  /* =====================================================================
     4. Users (ACC-03, ACC-04, ACC-06)
     ===================================================================== */
  const userMemberships = (u) => db.memberships.filter((m) => m.user_id === u.user_id && !isLeft(m));
  /** Only our administrators lock a whole account; a co-administrator cannot act on administrators (ACC-03, ACC-13). */
  function canLockUser(u) {
    if (!scope.has("HEAD_ADMIN", "CO_ADMIN") || u.user_id === me().user_id) return false;
    const roles = userMemberships(u).flatMap((m) => m.roles);
    if (roles.includes("HEAD_ADMIN")) return false;
    if (roles.includes("CO_ADMIN") && !scope.has("HEAD_ADMIN")) return false;
    return true;
  }
  function lockUser(u) {
    const locking = u.status !== "LOCKED";
    ui.reasonModal({
      title: locking ? t(`Lock the account of ${u.full_name}`, `Khóa tài khoản ${u.full_name}`) : t(`Unlock the account of ${u.full_name}`, `Mở khóa tài khoản ${u.full_name}`),
      intro: `<p class="small muted">${esc(locking ? t(`Blocks login in every organization (${userMemberships(u).length}) and ends every session; push tokens are removed.`, `Chặn đăng nhập ở mọi tổ chức (${userMemberships(u).length}) và kết thúc mọi phiên; token thông báo đẩy bị xóa.`) : t("The person can log in again; memberships keep their own status.", "Người dùng đăng nhập lại được; tư cách thành viên giữ nguyên trạng thái riêng."))}</p>`,
      danger: locking,
      submitLabel: locking ? t("Lock account", "Khóa tài khoản") : t("Unlock account", "Mở khóa tài khoản"),
      onSubmit(reason) {
        const old = u.status;
        u.status = locking ? "LOCKED" : u.last_login_at ? "ACTIVE" : "INVITED";
        u.status_reason = locking ? reason : null;
        userChanges.push({ at: nowIso(), by: me().user_id, user_id: u.user_id, fields: [["status", old, u.status]], reason });
        done(locking ? t("Account locked; every session ended", "Đã khóa tài khoản; mọi phiên đã kết thúc") : t("Account unlocked", "Đã mở khóa tài khoản"));
      },
    });
  }
  function resetPassword(u) {
    if (isLongInactive(u)) {
      ui.formModal({
        title: t("Reset the password of a long-inactive account", "Đặt lại mật khẩu tài khoản lâu không dùng"),
        intro: ui.callout("warn", esc(t(`No login since ${fmt.date(lastLogin(u)) || "—"}. Phone numbers get recycled: confirm the person still owns ${u.phone_number} before sending the code (ACC-06).`, `Không đăng nhập từ ${fmt.date(lastLogin(u)) || "—"}. Số điện thoại có thể đã đổi chủ: xác nhận người dùng vẫn sở hữu ${u.phone_number} trước khi gửi mã (ACC-06).`))),
        fields: [
          { name: "method", label: t("Checked by", "Xác minh bởi"), type: "select", options: [["CARE_CALL", t("Customer care called the person", "CSKH gọi điện xác minh")], ["ORG_ADMIN", t("The organization admin confirmed", "Quản trị tổ chức xác nhận")], ["ID_DOCUMENT", t("Citizen ID checked", "Đối chiếu CCCD")]] },
          { name: "ticket", label: t("Ticket", "Mã yêu cầu"), required: true, placeholder: "SC-2310" },
          { name: "owns", label: t("Phone ownership", "Chủ sở hữu số"), type: "checkbox", checkLabel: t("The phone number still belongs to this person", "Số điện thoại vẫn thuộc người này") },
        ],
        submitLabel: t("Send reset code", "Gửi mã đặt lại"),
        onSubmit(v) {
          if (!v.owns) return t("Confirm the phone ownership first.", "Hãy xác nhận chủ sở hữu số điện thoại trước.");
          userChanges.push({ at: nowIso(), by: me().user_id, user_id: u.user_id, fields: [["password_reset", null, "OTP"]], reason: `${v.method} · ${v.ticket}` });
          ui.toast(esc(t(`Reset code sent to ${u.phone_number}. Every session ends when the password changes.`, `Đã gửi mã đặt lại tới ${u.phone_number}. Mọi phiên kết thúc khi mật khẩu được đổi.`)), "", "send");
        },
      });
      return;
    }
    ui.formModal({
      title: t("Send a password reset code", "Gửi mã đặt lại mật khẩu"),
      intro: `<p>${esc(t(`A one-time code goes by SMS to ${u.phone_number}; the person sets the new password themselves.`, `Mã OTP được gửi qua SMS tới ${u.phone_number}; người dùng tự đặt mật khẩu mới.`))}</p>`,
      fields: [],
      submitLabel: t("Send code", "Gửi mã"),
      onSubmit() { ui.toast(esc(t(`Code sent to ${u.phone_number}`, `Đã gửi mã tới ${u.phone_number}`)), "", "send"); },
    });
  }
  function userDetailModal(u) {
    const ms = db.memberships.filter((m) => m.user_id === u.user_id);
    const changes = userChanges.filter((c) => c.user_id === u.user_id).sort((a, b) => (a.at < b.at ? 1 : -1));
    G3.audit("VIEW", "ACCOUNT", u.full_name, null);
    ui.modal({
      title: u.full_name, wide: true,
      body: `<div class="grid cols-2">
        <div>${ui.kv([
          [t("Phone (login ID)", "Số điện thoại (đăng nhập)"), `<span class="mono">${esc(u.phone_number)}</span>`],
          [t("E-mail", "E-mail"), esc(u.email)],
          [t("Status", "Trạng thái"), `${ui.badge(u.status)} ${u.status_reason ? `<span class="small">${esc(u.status_reason)}</span>` : ""}`],
          [t("Created", "Ngày tạo"), `${fmt.date(u.created_at)} · ${ui.userName(createdBy(u))}`],
          [t("Last login", "Đăng nhập gần nhất"), lastLogin(u) ? `${fmt.dateTime(lastLogin(u))} (${fmt.ago(lastLogin(u))})` : t("never", "chưa")],
        ])}</div>
        <div>${ui.table({ rows: ms, columns: [
          { label: t("Organization", "Tổ chức"), render: (m) => `<b>${ui.orgName(m.organization_id)}</b>` },
          { label: t("Roles", "Vai trò"), render: (m) => roleTags(m.roles) },
          { label: t("Status", "Trạng thái"), render: (m) => (isLeft(m) ? badge("LEFT", "muted") : ui.badge(m.status)) },
        ] })}</div></div>
        <h4 class="mt mb">${esc(t("Login history", "Lịch sử đăng nhập"))}</h4>${loginHistoryTable(u, 8)}
        <h4 class="mt mb">${esc(t("Change history", "Lịch sử thay đổi"))}</h4>
        ${changes.length ? `<ul class="timeline">${changes.map((c) => `<li><b>${esc(c.fields.map(([f, a, b]) => `${f}: ${a === null ? "—" : lbl(a)} → ${lbl(b)}`).join(", "))}</b><div class="small">${esc(c.reason)}</div><div class="when">${fmt.dateTime(c.at)} · ${ui.userName(c.by)}</div></li>`).join("")}</ul>` : `<p class="small muted">${esc(t("No change since the account was created.", "Chưa có thay đổi từ khi tạo tài khoản."))}</p>`}`,
    });
  }

  G3.page({
    id: "users",
    path: "/users",
    title: ["User accounts", "Tài khoản người dùng"],
    roles: ["HEAD_ADMIN", "CO_ADMIN"],
    features: ["ACC-03", "ACC-04", "ACC-06"],
    nav: { group: "admin", order: 3, icon: "user" },
    render({ query }) {
      if (!scope.isInternal()) return ui.pageHead({ title: t("User accounts", "Tài khoản người dùng") }) + ui.callout("warn", esc(t("Internal screen.", "Màn hình nội bộ.")), "lock");
      const q = (query.q || "").toLowerCase();
      const orgIds = scope.orgIds();
      const inScope = db.users.filter((u) => db.memberships.some((m) => m.user_id === u.user_id && orgIds.includes(m.organization_id)));
      const rows = inScope.filter((u) => {
        if (query.status && u.status !== query.status) return false;
        if (query.org && !userMemberships(u).some((m) => m.organization_id === query.org)) return false;
        if (query.inactive === "1" && !isLongInactive(u)) return false;
        if (q && !(u.full_name.toLowerCase().includes(q) || u.phone_number.includes(q) || (u.email || "").toLowerCase().includes(q))) return false;
        return true;
      }).sort((a, b) => a.full_name.localeCompare(b.full_name, "vi"));
      const shown = query.all === "1" ? rows : rows.slice(0, 25);
      const week = inScope.filter((u) => lastLogin(u) && NOW - new Date(lastLogin(u)).getTime() < 7 * DAY).length;
      const multi = inScope.filter((u) => userMemberships(u).length > 1).length;
      return `${ui.pageHead({
        title: t("User accounts", "Tài khoản người dùng"),
        sub: esc(t("One account per person, identified by phone number, across every organization they work for.", "Mỗi người một tài khoản, định danh bằng số điện thoại, dùng cho mọi tổ chức họ làm việc.")),
        features: ["ACC-03", "ACC-04", "ACC-06"],
        actions: ui.btn(t("Export", "Xuất"), { icon: "download", action: "export" }),
      })}
      <div class="grid cols-5">
        ${ui.kpi({ label: t("Accounts", "Tài khoản"), icon: "users", value: inScope.length, foot: `${multi} ${t("in several organizations", "thuộc nhiều tổ chức")}` })}
        ${ui.kpi({ label: t("Active", "Đang hoạt động"), icon: "check", tone: "ok", value: inScope.filter((u) => u.status === "ACTIVE").length, foot: `${week} ${t("logged in this week", "đăng nhập tuần này")}` })}
        ${ui.kpi({ label: t("Invited", "Đã mời"), icon: "send", value: inScope.filter((u) => u.status === "INVITED").length, foot: t("waiting for the OTP", "chờ xác nhận OTP") })}
        ${ui.kpi({ label: t("Locked", "Đã khóa"), icon: "lock", tone: inScope.some((u) => u.status === "LOCKED") ? "danger" : undefined, value: inScope.filter((u) => u.status === "LOCKED").length, foot: t("whole account", "toàn bộ tài khoản") })}
        ${ui.kpi({ label: t("Inactive > 90 days", "Không dùng > 90 ngày"), icon: "clock", tone: "warn", value: inScope.filter(isLongInactive).length, foot: t("extra check before reset", "cần xác minh trước khi đặt lại") })}
      </div>
      ${ui.card({
        cls: "mt", flush: true,
        body: `<div class="toolbar">${ui.search("q", query.q, t("Name, phone or e-mail", "Tên, số điện thoại hoặc e-mail"))}
          ${ui.select("status", [["", t("All statuses", "Mọi trạng thái")], ["ACTIVE", G3.label("ACTIVE")], ["INVITED", G3.label("INVITED")], ["LOCKED", G3.label("LOCKED")]], query.status || "")}
          ${ui.select("org", [["", t("All organizations", "Mọi tổ chức")], ...scope.filter(db.organizations).map((o) => [o.organization_id, o.short_name])], query.org || "")}
          <label class="check small"><input type="checkbox" data-inactive ${query.inactive === "1" ? "checked" : ""}> ${esc(t("Inactive > 90 days", "Không dùng > 90 ngày"))}</label>
          <span class="grow"></span><span class="small muted">${shown.length} / ${rows.length}</span></div>
          ${ui.table({
            rows: shown, empty: t("No account matches", "Không có tài khoản phù hợp"),
            rowAttrs: (u) => `data-user="${u.user_id}" style="cursor:pointer"`,
            columns: [
              { label: t("Person", "Người dùng"), render: (u) => personCell(u, u.email || "—") },
              { label: t("Phone (login ID)", "Số điện thoại (đăng nhập)"), render: (u) => `<span class="mono">${esc(u.phone_number)}</span>` },
              { label: t("Status", "Trạng thái"), render: (u) => `${ui.badge(u.status)}${u.status_reason ? `<span class="sub">${esc(u.status_reason)}</span>` : ""}` },
              { label: t("Organizations", "Tổ chức"), render: (u) => { const ms = userMemberships(u); return `<span class="row" style="gap:4px">${ms.slice(0, 2).map((m) => ui.tag(db.org(m.organization_id).short_name)).join("")}${ms.length > 2 ? `<span class="small muted">+${ms.length - 2}</span>` : ""}</span>`; } },
              { label: t("Last login", "Đăng nhập gần nhất"), render: (u) => (lastLogin(u) ? `${fmt.ago(lastLogin(u))}${isLongInactive(u) ? ` <span class="badge warn plain">${esc(t("inactive", "lâu không dùng"))}</span>` : ""}` : `<span class="muted">${esc(t("never", "chưa"))}</span>`) },
              { label: t("Created by", "Người tạo"), render: (u) => `${ui.userName(createdBy(u))}<span class="sub">${fmt.date(u.created_at)}</span>` },
              { label: "", align: "right", render: (u) => `<span class="row" style="justify-content:flex-end;gap:4px;flex-wrap:nowrap">${ui.btn("", { sm: true, kind: "ghost", icon: "clock", action: "history", attrs: `data-id="${u.user_id}" title="${esc(t("Login history", "Lịch sử đăng nhập"))}"` })}${ui.btn("", { sm: true, kind: "ghost", icon: "key", action: "reset", attrs: `data-id="${u.user_id}" title="${esc(t("Reset password", "Đặt lại mật khẩu"))}"` })}${canLockUser(u) ? ui.btn("", { sm: true, kind: "ghost", icon: u.status === "LOCKED" ? "unlock" : "lock", action: "lock", attrs: `data-id="${u.user_id}" title="${esc(u.status === "LOCKED" ? t("Unlock account", "Mở khóa tài khoản") : t("Lock account", "Khóa tài khoản"))}"` }) : ""}</span>` },
            ],
          })}
          ${rows.length > shown.length ? `<div class="card-body">${ui.btn(t(`Show all ${rows.length}`, `Hiện tất cả ${rows.length}`), { sm: true, action: "all" })}</div>` : ""}`,
      })}`;
    },
    mount(root, { query }) {
      bindQuery(root);
      auditViewOnce("users", "USER_ACCOUNTS");
      const cb = root.querySelector("[data-inactive]");
      if (cb) cb.addEventListener("change", () => G3.setQuery({ inactive: cb.checked ? "1" : null }));
      const byId = (el) => db.user(el.dataset.id);
      delegate(root, {
        history: (el) => { const u = byId(el); ui.modal({ title: t(`Login history · ${u.full_name}`, `Lịch sử đăng nhập · ${u.full_name}`), wide: true, body: loginHistoryTable(u, 20) }); },
        reset: (el) => resetPassword(byId(el)),
        lock: (el) => lockUser(byId(el)),
        all: () => G3.setQuery({ all: "1" }),
        export: () => ui.auditedExport({
          dataType: "USER_ACCOUNTS", subject: null, filename: "users.csv",
          columns: [{ label: "full_name", key: "full_name" }, { label: "phone_number", key: "phone_number" }, { label: "email", key: "email" }, { label: "status", key: "status" }, { label: "status_reason", key: "status_reason" }, { label: "organizations", csv: (u) => userMemberships(u).map((m) => db.org(m.organization_id).short_name).join(" | ") }, { label: "last_login_at", csv: (u) => lastLogin(u) }],
          rows: db.users.filter((u) => db.memberships.some((m) => m.user_id === u.user_id && scope.orgIds().includes(m.organization_id))),
        }),
        _click: (e) => {
          const tr = e.target.closest("tr[data-user]");
          if (tr && !e.target.closest("button, a, input")) userDetailModal(db.user(tr.dataset.user));
        },
      });
      if (query.user && db.user(query.user)) userDetailModal(db.user(query.user));
    },
  });

  /* =====================================================================
     5. Audit (ACC-18, ACC-22)
     ===================================================================== */
  /* Security events the core log does not seed (lockouts, phone changes, failed-login bursts). */
  const extraAudit = (function () {
    const r = seeded("security-events");
    const people = [P.tcxDispatch, P.mnAdmin, P.lockedOne, P.tcxAcc, P.dnAdmin, P.indOwner];
    const rows = [];
    let id = 530000;
    people.forEach((p, i) => {
      const base = NOW - (i * 1.3 + r() * 2 + 0.2) * DAY;
      const ip = `27.72.${Math.floor(r() * 250) + 1}.${Math.floor(r() * 250) + 1}`;
      const burst = i % 2 === 0 ? 5 : 2;
      for (let k = 0; k < burst; k++) rows.push({ access_audit_log_id: id++, user_id: p.user.user_id, organization_id: p.membership.organization_id, action: "LOGIN_FAILED", data_type: "ACCOUNT", subject: null, reason: null, ip_address: ip, device: "Chrome 129 / Android 14", occurred_at: new Date(base + k * 40 * 1000).toISOString() });
      if (burst === 5) rows.push({ access_audit_log_id: id++, user_id: p.user.user_id, organization_id: p.membership.organization_id, action: "LOGIN_LOCKED", data_type: "ACCOUNT", subject: null, reason: t("5 failed logins: locked 15 minutes", "5 lần sai: khóa 15 phút"), ip_address: ip, device: "Chrome 129 / Android 14", occurred_at: new Date(base + 5 * 40 * 1000).toISOString() });
    });
    rows.push({ access_audit_log_id: id++, user_id: P.tcxFm.user.user_id, organization_id: "org-tcx", action: "PHONE_CHANGED", data_type: "ACCOUNT", subject: null, reason: null, ip_address: "113.161.40.12", device: "G3 Fleet 1.4 / Android 14", occurred_at: isoAgo(26 * DAY) });
    rows.push({ access_audit_log_id: id++, user_id: P.care.user.user_id, organization_id: "org-g3", action: "EXPORT", data_type: "LOCATION_HISTORY", subject: "SC-2307", reason: "SC-2307 tai nạn Dầu Giây – gửi bảo hiểm", ip_address: "113.161.72.40", device: "Chrome 129 / Windows", occurred_at: isoAgo(2 * DAY + 3 * HOUR) });
    return rows;
  })();
  const auditRows = () => db.access_audit_logs.concat(extraAudit).sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : -1));
  const SECURITY = ["LOGIN_SUCCESS", "LOGIN_FAILED", "LOGIN_LOCKED", "LOGOUT", "PASSWORD_CHANGED", "PHONE_CHANGED"];
  const PERIODS = { "24h": DAY, "7d": 7 * DAY, "30d": 30 * DAY, all: Infinity };

  /** Change feed across audited records (ACC-22): organizations, roles, memberships, accounts, settings. */
  function allChanges() {
    const out = [];
    db.organizations.forEach((o) => {
      const versions = versionsOf(o);
      versions.forEach((v, i) => {
        if (v.created) { out.push({ at: v.at, by: v.by, type: "ORGANIZATION", label: o.short_name, href: `/organizations/${o.organization_id}?tab=history&ver=${i}`, fields: [[t("created", "tạo mới"), null, null]], reason: v.reason }); return; }
        out.push({ at: v.at, by: v.by, type: "ORGANIZATION", label: o.short_name, href: `/organizations/${o.organization_id}?tab=history&ver=${i}`, fields: diffRows(versions[i - 1].row, v.row).map((k) => [fieldName(k), versions[i - 1].row[k], v.row[k], k]), reason: v.reason });
      });
    });
    roleEvents.forEach((e) => {
      const m = db.memberships.find((x) => x.membership_id === e.membership_id);
      const who = m ? `${db.user(m.user_id).full_name} @ ${db.org(m.organization_id).short_name}` : "—";
      out.push({ at: e.granted_at, by: e.granted_by, type: "ROLE", label: who, fields: [[t("role granted", "cấp vai trò"), null, G3.roleLabel(e.role)]], reason: "" });
      if (e.revoked_at) out.push({ at: e.revoked_at, by: e.revoked_by, type: "ROLE", label: who, fields: [[t("role revoked", "thu hồi vai trò"), G3.roleLabel(e.role), null]], reason: "" });
    });
    memberChanges.forEach((c) => {
      const m = db.memberships.find((x) => x.membership_id === c.membership_id);
      out.push({ at: c.at, by: c.by, type: "MEMBERSHIP", label: m ? `${db.user(m.user_id).full_name} @ ${db.org(m.organization_id).short_name}` : "—", fields: c.fields.map(([f, a, b]) => [f, a, b]), reason: c.reason });
    });
    userChanges.forEach((c) => out.push({ at: c.at, by: c.by, type: "USER", label: (db.user(c.user_id) || {}).full_name || "—", fields: c.fields, reason: c.reason }));
    settingChanges.forEach((c) => out.push({ at: c.at, by: c.by, type: "SETTING", label: c.label, fields: c.fields, reason: c.reason }));
    return out.sort((a, b) => (a.at < b.at ? 1 : -1));
  }
  function changeValue(f) {
    const [, , , key] = f;
    return (v) => (v === null || v === undefined ? '<span class="muted">—</span>' : key ? orgFieldValue(key, v) : /^\d{4}-\d\d-\d\dT/.test(String(v)) ? fmt.dateTime(v) : esc(lbl(v)));
  }

  function accessLogTab(query) {
    const q = (query.q || "").toLowerCase();
    const period = PERIODS[query.period || "7d"] || PERIODS["7d"];
    const family = query.family || "all";
    const rows = auditRows().filter((a) => {
      if (Date.now() - new Date(a.occurred_at).getTime() > period) return false;
      if (query.org && a.organization_id !== query.org) return false;
      if (query.dtype && a.data_type !== query.dtype) return false;
      if (query.action && a.action !== query.action) return false;
      if (family === "data" && SECURITY.includes(a.action)) return false;
      if (family === "security" && !SECURITY.includes(a.action)) return false;
      if (q && !((db.user(a.user_id) || {}).full_name || "").toLowerCase().includes(q) && !(a.subject || "").toLowerCase().includes(q)) return false;
      return true;
    });
    const days = 14;
    const perDay = Array.from({ length: days }, (_, i) => { const d = new Date(NOW - (days - 1 - i) * DAY); return { key: d.toISOString().slice(0, 10), label: `${d.getDate()}/${d.getMonth() + 1}`, view: 0, export: 0, security: 0 }; });
    auditRows().forEach((a) => { const d = perDay.find((x) => x.key === a.occurred_at.slice(0, 10)); if (d) d[a.action === "VIEW" ? "view" : a.action === "EXPORT" ? "export" : "security"]++; });
    const dataTypes = [...new Set(auditRows().map((a) => a.data_type))];
    const actions = [...new Set(auditRows().map((a) => a.action))];
    const failed = rows.filter((a) => a.action === "LOGIN_FAILED");
    const topIps = Object.entries(failed.reduce((acc, a) => { acc[a.ip_address] = (acc[a.ip_address] || 0) + 1; return acc; }, {})).sort((a, b) => b[1] - a[1]).slice(0, 5);
    return `<div class="grid cols-5">
        ${ui.kpi({ label: t("Entries", "Bản ghi"), icon: "list", value: rows.length, foot: esc(t("in the selected period", "trong khoảng đã chọn")) })}
        ${ui.kpi({ label: t("Personal data viewed", "Lượt xem dữ liệu cá nhân"), icon: "eye", value: rows.filter((a) => a.action === "VIEW").length, foot: `${new Set(rows.filter((a) => a.action === "VIEW").map((a) => a.user_id)).size} ${t("people", "người")}` })}
        ${ui.kpi({ label: t("Exports", "Lượt xuất"), icon: "download", value: rows.filter((a) => a.action === "EXPORT").length, tone: "warn", foot: t("each with a reason", "đều có lý do") })}
        ${ui.kpi({ label: t("Failed logins", "Đăng nhập lỗi"), icon: "alert", value: failed.length, tone: failed.length ? "danger" : undefined, foot: `${rows.filter((a) => a.action === "LOGIN_LOCKED").length} ${t("lockouts", "lần khóa")}` })}
        ${ui.kpi({ label: t("Password / phone changes", "Đổi mật khẩu / số điện thoại"), icon: "key", value: rows.filter((a) => a.action === "PASSWORD_CHANGED" || a.action === "PHONE_CHANGED").length, foot: t("account security", "an ninh tài khoản") })}
      </div>
      <div class="grid side mt">
        ${ui.card({ title: t("Entries per day", "Bản ghi theo ngày"), sub: t("14 days", "14 ngày"), actions: ui.legend([[t("Views", "Xem"), ui.PALETTE[1]], [t("Exports", "Xuất"), ui.PALETTE[2]], [t("Security", "An ninh"), ui.PALETTE[3]]]), body: ui.barChart({ labels: perDay.map((d) => d.label), stacked: true, height: 200, series: [{ name: t("Views", "Xem"), values: perDay.map((d) => d.view), color: ui.PALETTE[1] }, { name: t("Exports", "Xuất"), values: perDay.map((d) => d.export), color: ui.PALETTE[2] }, { name: t("Security", "An ninh"), values: perDay.map((d) => d.security), color: ui.PALETTE[3] }] }) })}
        ${ui.card({ title: t("Failed logins by IP", "Đăng nhập lỗi theo IP"), sub: t("rate limit per device / IP", "giới hạn theo thiết bị / IP"), body: topIps.length ? ui.hBars({ labels: topIps.map(([ip]) => ip), series: [{ values: topIps.map(([, n]) => n), color: ui.PALETTE[4] }] }) : ui.empty(t("No failed login", "Không có đăng nhập lỗi")) })}
      </div>
      ${ui.card({
        cls: "mt", flush: true,
        body: `<div class="toolbar">${ui.search("q", query.q, t("Person or subject", "Người dùng hoặc đối tượng"))}
          ${ui.select("org", [["", t("All organizations", "Mọi tổ chức")], ...db.organizations.map((o) => [o.organization_id, o.short_name])], query.org || "")}
          ${ui.select("period", [["24h", t("Last 24 h", "24 giờ qua")], ["7d", t("Last 7 days", "7 ngày qua")], ["30d", t("Last 30 days", "30 ngày qua")], ["all", t("Everything", "Toàn bộ")]], query.period || "7d")}
          ${ui.select("dtype", [["", t("All data types", "Mọi loại dữ liệu")], ...dataTypes.map((d) => [d, lbl(d)])], query.dtype || "")}
          ${ui.select("action", [["", t("All actions", "Mọi thao tác")], ...actions.map((a) => [a, G3.label(a)])], query.action || "")}
          ${ui.segmented([["all", t("All", "Tất cả")], ["data", t("Data access", "Truy cập dữ liệu")], ["security", t("Security events", "Sự kiện an ninh")]], family, "family")}</div>
          ${ui.table({
            rows: rows.slice(0, 120), empty: t("No entry matches", "Không có bản ghi phù hợp"),
            columns: [
              { label: t("When", "Thời điểm"), render: (a) => `${fmt.dateTime(a.occurred_at)}<span class="sub">${fmt.ago(a.occurred_at)}</span>` },
              { label: t("Who", "Người dùng"), render: (a) => { const u = db.user(a.user_id); const m = db.memberships.find((x) => x.user_id === a.user_id && x.organization_id === a.organization_id); return u ? `<b>${esc(u.full_name)}</b><span class="sub">${esc(m ? m.roles.map(G3.roleLabel).join(", ") : "")}</span>` : ui.userName(null); } },
              { label: t("Organization", "Tổ chức"), render: (a) => ui.orgName(a.organization_id) },
              { label: t("Action", "Thao tác"), render: (a) => ui.badge(a.action, ACTION_TONES[a.action]) },
              { label: t("Data type", "Loại dữ liệu"), render: (a) => esc(lbl(a.data_type)) },
              { label: t("Subject", "Đối tượng"), render: (a) => esc(a.subject || "—") },
              { label: t("Reason / ticket", "Lý do / mã yêu cầu"), render: (a) => (a.reason ? `<span class="small">${esc(a.reason)}</span>` : '<span class="muted">—</span>') },
              { label: "IP", render: (a) => `<span class="mono">${esc(a.ip_address)}</span>` },
              { label: t("Device", "Thiết bị"), render: (a) => `<span class="small">${esc(a.device)}</span>` },
            ],
          })}
          <div class="card-body small muted">${esc(t("Append-only: entries are never edited or deleted; one entry per screen opened, not per refresh; older months are compressed and kept.", "Chỉ ghi thêm: bản ghi không bao giờ bị sửa hay xóa; mỗi lần mở màn hình một bản ghi, không ghi khi làm mới; các tháng cũ được nén và lưu giữ."))}${rows.length > 120 ? ` · ${esc(t(`showing 120 of ${rows.length}`, `hiện 120 / ${rows.length}`))}` : ""}</div>`,
      })}`;
  }
  function changesTab(query) {
    const rows = allChanges().filter((c) => (!query.rtype || c.type === query.rtype) && (!query.q || c.label.toLowerCase().includes(query.q.toLowerCase())));
    return ui.card({
      title: t("Change history", "Lịch sử thay đổi"), sub: `${rows.length} · ACC-22`, flush: true,
      body: `<div class="toolbar">${ui.search("q", query.q, t("Record name", "Tên bản ghi"))}${ui.select("rtype", [["", t("All records", "Mọi loại bản ghi")], ...["ORGANIZATION", "USER", "MEMBERSHIP", "ROLE", "SETTING"].map((k) => [k, lbl(k)])], query.rtype || "")}</div>
        ${ui.table({
          rows: rows.slice(0, 100),
          columns: [
            { label: t("When", "Thời điểm"), render: (c) => `${fmt.dateTime(c.at)}<span class="sub">${fmt.ago(c.at)}</span>` },
            { label: t("Record", "Bản ghi"), render: (c) => `${ui.tag(lbl(c.type))} ${c.href ? `<a href="#${c.href}">${esc(c.label)}</a>` : esc(c.label)}` },
            { label: t("What changed", "Thay đổi"), render: (c) => c.fields.map((f) => { const val = changeValue(f); return f[1] === null && f[2] === null ? `<b>${esc(f[0])}</b>` : `<div class="small"><b>${esc(f[0])}</b>: <span style="text-decoration:line-through;color:var(--text-3)">${val(f[1])}</span> → ${val(f[2])}</div>`; }).join("") },
            { label: t("Reason", "Lý do"), render: (c) => (c.reason ? `<span class="small">${esc(c.reason)}</span>` : '<span class="muted">—</span>') },
            { label: t("By", "Người thực hiện"), render: (c) => ui.userName(c.by) },
          ],
        })}
        <div class="card-body small muted">${esc(t("Organizations open their full version comparison; every audited table keeps a copy of the old row with who, when and why.", "Mở tổ chức để so sánh đầy đủ các phiên bản; mỗi bảng có lịch sử lưu bản sao dòng cũ kèm người, thời điểm và lý do."))}</div>`,
    });
  }

  G3.page({
    id: "audit",
    path: "/audit",
    title: ["Audit log", "Nhật ký truy cập"],
    roles: ["HEAD_ADMIN", "CO_ADMIN"],
    features: ["ACC-18", "ACC-22"],
    nav: { group: "admin", order: 4, icon: "shield" },
    render({ query }) {
      if (!scope.isInternal()) return ui.pageHead({ title: t("Audit log", "Nhật ký truy cập") }) + ui.callout("warn", esc(t("Internal screen.", "Màn hình nội bộ.")), "lock");
      const tab = query.tab === "changes" ? "changes" : "access";
      return `${ui.pageHead({
        title: t("Audit log", "Nhật ký truy cập"),
        sub: esc(t("Who viewed, downloaded or exported personal, location or camera data, plus account security events and record changes.", "Ai đã xem, tải hoặc xuất dữ liệu cá nhân, vị trí hay camera, cùng các sự kiện an ninh tài khoản và thay đổi bản ghi.")),
        features: ["ACC-18", "ACC-22"],
        actions: ui.btn(t("Export", "Xuất"), { icon: "download", action: "export" }),
      })}
      ${ui.tabs([["access", t("Access & security log", "Nhật ký truy cập & an ninh")], ["changes", t("Change history", "Lịch sử thay đổi")]], tab)}
      ${tab === "changes" ? changesTab(query) : accessLogTab(query)}`;
    },
    mount(root, { query }) {
      bindQuery(root);
      delegate(root, {
        export: () => {
          if (query.tab === "changes") {
            ui.downloadCsv("change-history.csv", [{ label: "at", key: "at" }, { label: "record_type", key: "type" }, { label: "record", key: "label" }, { label: "changes", csv: (c) => c.fields.map((f) => `${f[0]}: ${f[1] ?? ""} -> ${f[2] ?? ""}`).join("; ") }, { label: "reason", key: "reason" }, { label: "by", csv: (c) => (db.user(c.by) || {}).full_name || "system" }], allChanges());
            return;
          }
          ui.auditedExport({
            dataType: "ACCESS_AUDIT_LOG", subject: null, filename: "access-audit-log.csv",
            columns: [{ label: "occurred_at", key: "occurred_at" }, { label: "user", csv: (a) => (db.user(a.user_id) || {}).full_name }, { label: "organization", csv: (a) => (db.org(a.organization_id) || {}).short_name }, { label: "action", key: "action" }, { label: "data_type", key: "data_type" }, { label: "subject", key: "subject" }, { label: "reason", key: "reason" }, { label: "ip_address", key: "ip_address" }, { label: "device", key: "device" }],
            rows: auditRows(),
          });
        },
      });
    },
  });

  /* =====================================================================
     6. Settings (NTF-05, MON-17, ACC-17, ACC-21, DEV-07)
     ===================================================================== */
  /* ---- Notification channels (organization_notification_settings, NTF-05) ---- */
  const SERVICES = [
    ["battery", ["Battery & safety", "Pin & an toàn"], ["BATTERY_ALERT", "ANOMALY_ALERT", "SOH_ALERT"]],
    ["fleet", ["Fleet & trips", "Đội xe & chuyến"], ["GEOFENCE_ALERT", "NO_DRIVER_CHECK_IN_ALERT", "OUTSIDE_DRIVER_CHECK_IN", "NO_TRIP_STARTED", "TRIP_ASSIGNED"]],
    ["support", ["Support & SOS", "Hỗ trợ & SOS"], ["SOS_ALERT"]],
    ["devices", ["Devices", "Thiết bị"], ["DEVICE_OFFLINE_ALERT"]],
    ["billing", ["Charging & wallet", "Sạc & ví"], ["LOW_WALLET_BALANCE", "TOP_UP_RECEIVED", "CHARGING_RECEIPT"]],
  ];
  const RECIPIENTS = {
    BATTERY_ALERT: ["FLEET_MANAGER", "DISPATCHER", "DRIVER"], ANOMALY_ALERT: ["ORG_ADMIN", "FLEET_MANAGER", "DRIVER"], SOH_ALERT: ["ORG_ADMIN", "FLEET_MANAGER"],
    GEOFENCE_ALERT: ["FLEET_MANAGER", "DISPATCHER"], NO_DRIVER_CHECK_IN_ALERT: ["FLEET_MANAGER", "DISPATCHER"], OUTSIDE_DRIVER_CHECK_IN: ["FLEET_MANAGER"], NO_TRIP_STARTED: ["DISPATCHER"], TRIP_ASSIGNED: ["DRIVER"],
    SOS_ALERT: ["FLEET_MANAGER", "DISPATCHER"], DEVICE_OFFLINE_ALERT: ["ORG_ADMIN", "FLEET_MANAGER"],
    LOW_WALLET_BALANCE: ["ORG_ADMIN", "ACCOUNTANT"], TOP_UP_RECEIVED: ["ACCOUNTANT"], CHARGING_RECEIPT: ["ACCOUNTANT", "DRIVER"],
  };
  const INTERNAL_COPY = { ANOMALY_ALERT: "OPERATIONS", SOS_ALERT: "CUSTOMER_CARE", DEVICE_OFFLINE_ALERT: "OPERATIONS" };
  const channelStore = {};
  function channelsOf(orgId) {
    if (!channelStore[orgId]) {
      channelStore[orgId] = {};
      SERVICES.forEach(([, , types]) => types.forEach((type) => {
        channelStore[orgId][type] = { push: !["TOP_UP_RECEIVED", "CHARGING_RECEIPT"].includes(type), sms: ["SOS_ALERT", "ANOMALY_ALERT"].includes(type), email: ["SOH_ALERT", "LOW_WALLET_BALANCE", "TOP_UP_RECEIVED", "CHARGING_RECEIPT"].includes(type) };
      }));
    }
    return channelStore[orgId];
  }
  function channelsTab(org) {
    const ch = channelsOf(org.organization_id);
    const sub = subOf(org.organization_id);
    const smsAddOn = sub && sub.add_ons.find((a) => a.code === "SMS");
    const editable = scope.has("ORG_ADMIN", "HEAD_ADMIN") && (scope.isInternal() || scope.org().organization_id === org.organization_id);
    const sw = (type, channel, on, locked) => `<label class="switch" title="${esc(locked || "")}"><input type="checkbox" data-ch="${channel}" data-type="${type}" ${on ? "checked" : ""} ${locked || !editable ? "disabled" : ""}><span></span></label>`;
    const always = `<label class="switch" title="${esc(t("Always on", "Luôn bật"))}"><input type="checkbox" checked disabled><span></span></label>`;
    const rows = SERVICES.map(([key, name, types]) => `<tr><td colspan="7" style="background:var(--surface-2)"><b>${esc(t(name[0], name[1]))}</b></td></tr>${types.map((type) => `<tr>
      <td><b>${esc(G3.label(type))}</b><span class="sub">${roleTags(RECIPIENTS[type])}${INTERNAL_COPY[type] ? ` <span class="small muted">+ G3 ${esc(G3.roleLabel(INTERNAL_COPY[type]))}</span>` : ""}</span></td>
      <td>${always}</td><td>${always}</td>
      <td>${sw(type, "push", ch[type].push || type === "SOS_ALERT", type === "SOS_ALERT" ? t("SOS push cannot be turned off", "Không tắt được thông báo đẩy SOS") : "")}</td>
      <td>${sw(type, "sms", smsAddOn && ch[type].sms, !smsAddOn ? t("Needs the SMS add-on", "Cần gói SMS bổ sung") : "")}</td>
      <td>${sw(type, "email", ch[type].email)}</td>
      <td class="small muted">${key === "billing" ? esc(t("finance", "tài chính")) : ""}</td></tr>`).join("")}`).join("");
    return `<div class="grid side">
      ${ui.card({
        title: t("Notification channels", "Kênh thông báo"), sub: esc(org.short_name), flush: true,
        actions: editable ? ui.btn(t("Reset to defaults", "Khôi phục mặc định"), { sm: true, kind: "ghost", icon: "refresh", action: "channels-reset" }) : "",
        body: `<div class="table-wrap"><table class="tbl"><thead><tr><th>${esc(t("Alert · who receives it", "Cảnh báo · người nhận"))}</th><th>${esc(t("In app", "Trong app"))}</th><th>${esc(t("Portal", "Cổng web"))}</th><th>${esc(t("Push", "Đẩy"))}</th><th>SMS</th><th>E-mail</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`,
      })}
      <div class="stack">
        ${ui.card({ title: t("How delivery works", "Cách gửi thông báo"), sub: "NTF-05 · NTF-06", body: `<ul class="small" style="margin:0;padding-left:18px;line-height:1.7">
          <li>${esc(t("The app and portal inbox always show every alert of the person's roles.", "Hộp thư trong app và cổng web luôn hiện mọi cảnh báo theo vai trò của người dùng."))}</li>
          <li>${esc(t("Push goes to every logged-in device of each recipient.", "Thông báo đẩy tới mọi thiết bị đang đăng nhập của người nhận."))}</li>
          <li>${esc(t("E-mail only reaches recipients with an address on file.", "E-mail chỉ tới người nhận có địa chỉ e-mail."))}</li>
          <li>${esc(t("Recipients come from roles; there is no per-person channel choice.", "Người nhận xác định theo vai trò; không chọn kênh theo từng người."))}</li></ul>` })}
        ${ui.card({ title: t("SMS add-on", "Gói SMS bổ sung"), body: smsAddOn ? `<div class="row between small"><span>${fmt.num(smsAddOn.used)} / ${fmt.num(smsAddOn.quota)} ${esc(t("SMS this month", "SMS tháng này"))}</span><b>${fmt.pct((smsAddOn.used / smsAddOn.quota) * 100)}</b></div>${ui.meter((smsAddOn.used / smsAddOn.quota) * 100, smsAddOn.used / smsAddOn.quota > 0.8 ? "warn" : "")}` : `${ui.callout("info", esc(t("No SMS add-on: SMS switches are off. Buy a pack from the subscription.", "Chưa có gói SMS: các công tắc SMS bị tắt. Mua gói trong phần gói dịch vụ.")))}` })}
      </div></div>`;
  }

  /* ---- Organization settings (organization_settings) ---- */
  const orgSettingStore = {};
  function settingsOf(orgId) {
    if (!orgSettingStore[orgId]) {
      const dev = db.telematics.find((x) => x.vehicle_id && (db.vehicle(x.vehicle_id) || {}).organization_id === orgId);
      orgSettingStore[orgId] = { auto_end_minutes: 120, report_timezone: "Asia/Ho_Chi_Minh", telemetry_interval_seconds: dev ? dev.send_interval_seconds : 15 };
    }
    return orgSettingStore[orgId];
  }
  function orgSettingsTab(org) {
    const s = settingsOf(org.organization_id);
    const editable = scope.has("ORG_ADMIN", "HEAD_ADMIN");
    const am = org.account_manager_user_id ? db.user(org.account_manager_user_id) : null;
    const history = settingChanges.filter((c) => c.organization_id === org.organization_id && c.kind === "org");
    return `<div class="grid cols-2">
      ${ui.card({
        title: t("Organization settings", "Cấu hình tổ chức"), sub: esc(org.short_name),
        body: `<form class="form" data-org-settings>
          <div class="field"><label>${esc(t("Auto-end an idle driving session after", "Tự kết thúc phiên lái xe không hoạt động sau"))}</label>${plainSelect("auto_end", [["60", t("1 hour", "1 giờ")], ["120", t("2 hours (default)", "2 giờ (mặc định)")], ["240", t("4 hours", "4 giờ")], ["480", t("8 hours", "8 giờ")]], s.auto_end_minutes, !editable)}<span class="hint">${esc(t("A truck parked with nobody moving it ends its driver's session, so the next driver can check in.", "Xe đỗ không di chuyển sẽ kết thúc phiên của tài xế, để tài xế tiếp theo nhận xe."))}</span></div>
          <div class="field"><label>${esc(t("Report time zone", "Múi giờ báo cáo"))}</label>${plainSelect("report_tz", [["Asia/Ho_Chi_Minh", "Asia/Ho_Chi_Minh (UTC+7)"], ["Asia/Bangkok", "Asia/Bangkok (UTC+7)"], ["Asia/Singapore", "Asia/Singapore (UTC+8)"]], s.report_timezone, !editable)}<span class="hint">${esc(t("Cuts reports into days, weeks and months; stored times stay UTC.", "Dùng để chia báo cáo theo ngày, tuần, tháng; thời gian lưu vẫn là UTC."))}</span></div>
          ${editable ? `<div>${ui.btn(t("Save", "Lưu"), { kind: "primary", action: "save-org-settings" })}</div>` : ""}
        </form>`,
      })}
      <div class="stack">
        ${ui.card({
          title: t("Organization profile", "Hồ sơ tổ chức"),
          body: `${ui.kv([[t("Legal name", "Tên pháp lý"), esc(org.name)], [t("Tax code", "Mã số thuế"), `<span class="mono">${esc(org.legal_form === "INDIVIDUAL" ? maskId(org.tax_code) : org.tax_code)}</span>`], [t("Address", "Địa chỉ"), esc(org.address)], [t("Account manager", "Người phụ trách"), am ? `${esc(am.full_name)} · <span class="mono">${esc(am.phone_number)}</span>` : "—"]])}
            <p class="small muted mt">${esc(t("Invoice details are changed by your account manager, so every change is checked and kept in history.", "Thông tin xuất hóa đơn do người phụ trách thay đổi, để mọi thay đổi được kiểm tra và lưu lịch sử."))}</p>`,
        })}
        ${ui.card({ title: t("Changes", "Lịch sử thay đổi"), body: history.length ? `<ul class="timeline">${history.slice().reverse().map((c) => `<li><b>${esc(c.fields.map((f) => `${f[0]}: ${f[1]} → ${f[2]}`).join(", "))}</b><div class="when">${fmt.dateTime(c.at)} · ${ui.userName(c.by)}</div></li>`).join("")}</ul>` : `<p class="small muted">${esc(t("Defaults since the organization was created.", "Dùng giá trị mặc định từ khi tạo tổ chức."))}</p>` })}
      </div></div>`;
  }

  /* ---- Alert thresholds (MON-17) ---- */
  const thresholds = [
    { key: "soc_early", group: ["Battery level", "Mức pin"], label: ["Early warning", "Báo sớm"], op: "≤", value: 30, unit: "%", severity: "INFO", alert: "BATTERY_ALERT" },
    { key: "soc_main", group: ["Battery level", "Mức pin"], label: ["Low battery", "Pin yếu"], op: "≤", value: 20, unit: "%", severity: "WARNING", alert: "BATTERY_ALERT" },
    { key: "soc_critical", group: ["Battery level", "Mức pin"], label: ["Critical battery", "Pin cạn"], op: "≤", value: 10, unit: "%", severity: "CRITICAL", alert: "BATTERY_ALERT" },
    { key: "pack_temp_warn", group: ["Pack temperature", "Nhiệt độ pin"], label: ["High", "Cao"], op: "≥", value: 50, unit: "°C", severity: "WARNING", alert: "ANOMALY_ALERT" },
    { key: "pack_temp_crit", group: ["Pack temperature", "Nhiệt độ pin"], label: ["Dangerous", "Nguy hiểm"], op: "≥", value: 55, unit: "°C", severity: "CRITICAL", alert: "ANOMALY_ALERT" },
    { key: "cell_temp", group: ["Cell temperature", "Nhiệt độ cell"], label: ["Hottest cell", "Cell nóng nhất"], op: "≥", value: 58, unit: "°C", severity: "CRITICAL", alert: "ANOMALY_ALERT" },
    { key: "cell_delta", group: ["Cell voltage spread", "Chênh áp cell"], label: ["Max − min cell", "Cell cao − thấp nhất"], op: "≥", value: 80, unit: "mV", severity: "WARNING", alert: "ANOMALY_ALERT" },
    { key: "soh", group: ["Battery health (SOH)", "Sức khỏe pin (SOH)"], label: ["Below", "Dưới ngưỡng"], op: "<", value: 90, unit: "%", severity: "WARNING", alert: "SOH_ALERT" },
    { key: "consumption", group: ["Abnormal consumption", "Tiêu thụ bất thường"], label: ["Above the model baseline", "Cao hơn mức chuẩn dòng xe"], op: "≥ +", value: 25, unit: "%", severity: "WARNING", alert: "ANOMALY_ALERT" },
  ];
  const thresholdOverrides = [
    { scope: "MODEL", target: "vm-825", key: "pack_temp_warn", value: 48, by: uidOf("ops"), at: isoAgo(18 * DAY), reason: "Khuyến nghị CATL cho pin 423 kWh" },
    { scope: "FLEET", target: "flt-tcx-caimep", key: "soc_main", value: 25, by: uidOf("ops"), at: isoAgo(9 * DAY), reason: "Tuyến Cái Mép xa trạm sạc dự phòng" },
  ];
  const thresholdHistory = [
    { at: isoAgo(9 * DAY), by: uidOf("ops"), text: "Đội Depot Cái Mép · Pin yếu: 20% → 25%", reason: "Tuyến Cái Mép xa trạm sạc dự phòng" },
    { at: isoAgo(18 * DAY), by: uidOf("ops"), text: "Tri-Ring EVT-825 · Nhiệt độ pin cao: 50 °C → 48 °C", reason: "Khuyến nghị CATL cho pin 423 kWh" },
    { at: isoAgo(41 * DAY), by: uidOf("ops"), text: "Mặc định · Chênh áp cell: 100 mV → 80 mV", reason: "Theo báo cáo phân tích lỗi cell tháng 8" },
    { at: isoAgo(75 * DAY), by: uidOf("head"), text: "Mặc định · Sức khỏe pin: 85% → 90%", reason: "Đồng bộ với điều khoản bảo hành pin" },
  ];
  const targetName = (o) => (o.scope === "MODEL" ? (db.model(o.target) || {}).name : `${(db.fleet(o.target) || {}).name} · ${ui.orgName((db.fleet(o.target) || {}).organization_id)}`);
  function thresholdsTab() {
    const editable = scope.has("OPERATIONS", "HEAD_ADMIN");
    return `<div class="grid side">
      <div class="stack">
        ${ui.card({
          title: t("Default thresholds", "Ngưỡng mặc định"), sub: t("used by every truck unless overridden", "áp dụng cho mọi xe nếu không có ngưỡng riêng"), flush: true,
          body: ui.table({
            rows: thresholds,
            columns: [
              { label: t("Measure", "Chỉ số"), render: (x) => `<b>${esc(t(x.group[0], x.group[1]))}</b><span class="sub">${esc(t(x.label[0], x.label[1]))}</span>` },
              { label: t("Threshold", "Ngưỡng"), align: "right", render: (x) => `<b class="num">${esc(x.op)} ${fmt.num(x.value)} ${esc(x.unit)}</b>` },
              { label: t("Severity", "Mức độ"), render: (x) => ui.badge(x.severity) },
              { label: t("Alert", "Cảnh báo"), render: (x) => `<span class="small">${esc(G3.label(x.alert))}</span>` },
              { label: t("Overrides", "Ngưỡng riêng"), align: "right", render: (x) => thresholdOverrides.filter((o) => o.key === x.key).length || '<span class="muted">0</span>' },
              { label: "", align: "right", render: (x) => (editable ? ui.btn(t("Edit", "Sửa"), { sm: true, kind: "ghost", icon: "edit", action: "edit-threshold", attrs: `data-id="${x.key}"` }) : "") },
            ],
          }),
        })}
        ${ui.card({
          title: t("Overrides per model or fleet", "Ngưỡng riêng theo dòng xe hoặc đội xe"), flush: true,
          actions: editable ? ui.btn(t("Add override", "Thêm ngưỡng riêng"), { sm: true, icon: "plus", action: "add-override" }) : "",
          body: ui.table({
            rows: thresholdOverrides, empty: t("No override", "Chưa có ngưỡng riêng"),
            columns: [
              { label: t("Applies to", "Áp dụng cho"), render: (o) => `${ui.tag(lbl(o.scope))} <b>${esc(targetName(o))}</b>` },
              { label: t("Measure", "Chỉ số"), render: (o) => { const x = thresholds.find((y) => y.key === o.key); return `${esc(t(x.group[0], x.group[1]))} · ${esc(t(x.label[0], x.label[1]))}`; } },
              { label: t("Default → override", "Mặc định → riêng"), render: (o) => { const x = thresholds.find((y) => y.key === o.key); return `<span class="muted">${fmt.num(x.value)}</span> → <b>${fmt.num(o.value)} ${esc(x.unit)}</b>`; } },
              { label: t("Reason", "Lý do"), render: (o) => `<span class="small">${esc(o.reason)}</span>` },
              { label: t("Since", "Từ"), render: (o) => `${fmt.date(o.at)}<span class="sub">${ui.userName(o.by)}</span>` },
              { label: "", align: "right", render: (o) => (editable ? ui.btn("", { sm: true, kind: "ghost", icon: "trash", action: "remove-override", attrs: `data-id="${thresholdOverrides.indexOf(o)}" title="${esc(t("Remove", "Xóa"))}"` }) : "") },
            ],
          }),
        })}
      </div>
      <div class="stack">
        ${ui.card({
          title: t("Severity table", "Bảng mức độ"), sub: t("shared by every alert", "dùng chung cho mọi cảnh báo"), flush: true,
          body: ui.table({
            rows: [
              ["INFO", t("Inbox only", "Chỉ hộp thư"), t("no escalation", "không leo thang")],
              ["WARNING", t("Inbox + push", "Hộp thư + đẩy"), t("dispatcher acts within 30 min", "điều vận xử lý trong 30 phút")],
              ["CRITICAL", t("Inbox + push + SMS", "Hộp thư + đẩy + SMS"), t("G3 operations called after 5 min unacknowledged", "G3 vận hành gọi điện nếu 5 phút chưa xác nhận")],
            ],
            columns: [
              { label: t("Severity", "Mức độ"), render: (r) => ui.badge(r[0]) },
              { label: t("Channels", "Kênh"), render: (r) => `<span class="small">${esc(r[1])}</span>` },
              { label: t("Escalation", "Leo thang"), render: (r) => `<span class="small">${esc(r[2])}</span>` },
            ],
          }),
        })}
        ${ui.card({ title: t("History of changes", "Lịch sử thay đổi ngưỡng"), body: `<ul class="timeline">${thresholdHistory.map((h) => `<li><b>${esc(h.text)}</b><div class="small">${esc(h.reason)}</div><div class="when">${fmt.dateTime(h.at)} · ${ui.userName(h.by)}</div></li>`).join("")}</ul>` })}
      </div></div>`;
  }

  /* ---- Legal documents and acceptances (legal_documents, user_consents, ACC-17) ---- */
  const legalDocs = [
    { legal_document_id: "ld-tos-22", purpose: "TERMS_OF_SERVICE", version: "2.2", title: "Điều khoản sử dụng dịch vụ G3 Network", created_at: isoAgo(260 * DAY), created_by: uidOf("coadmin") },
    { legal_document_id: "ld-tos-30", purpose: "TERMS_OF_SERVICE", version: "3.0", title: "Điều khoản sử dụng dịch vụ G3 Network", created_at: isoAgo(76 * DAY), created_by: uidOf("coadmin") },
    { legal_document_id: "ld-pp-21", purpose: "PRIVACY_POLICY", version: "2.1", title: "Chính sách quyền riêng tư", created_at: isoAgo(140 * DAY), created_by: uidOf("coadmin") },
    { legal_document_id: "ld-dpa-20", purpose: "DATA_PROCESSING_AGREEMENT", version: "2.0", title: "Thỏa thuận xử lý dữ liệu cá nhân", created_at: isoAgo(230 * DAY), created_by: uidOf("head") },
    { legal_document_id: "ld-dpa-21", purpose: "DATA_PROCESSING_AGREEMENT", version: "2.1", title: "Thỏa thuận xử lý dữ liệu cá nhân", created_at: isoAgo(45 * DAY), created_by: uidOf("coadmin") },
    { legal_document_id: "ld-pn-13", purpose: "PRIVACY_NOTICE", version: "1.3", title: "Thông báo xử lý dữ liệu cá nhân của tài xế", created_at: isoAgo(120 * DAY), created_by: uidOf("coadmin") },
    { legal_document_id: "ld-loc-10", purpose: "LOCATION_TRACKING", version: "1.0", title: "Đồng ý theo dõi vị trí xe", created_at: isoAgo(200 * DAY), created_by: uidOf("coadmin") },
  ];
  const PURPOSES = ["TERMS_OF_SERVICE", "PRIVACY_POLICY", "DATA_PROCESSING_AGREEMENT", "PRIVACY_NOTICE", "LOCATION_TRACKING"];
  const docsOf = (purpose) => legalDocs.filter((d) => d.purpose === purpose).sort((a, b) => (a.created_at < b.created_at ? -1 : 1));
  const currentDoc = (purpose) => { const list = docsOf(purpose); return list[list.length - 1]; };
  let consentCache = null;
  /** Who must accept which text: every user the terms and policy, a company the DPA, employed drivers the notice, an individual customer location tracking. */
  function consents() {
    if (consentCache) return consentCache;
    const rows = [];
    db.users.filter((u) => u.status === "ACTIVE").forEach((u) => {
      const ms = db.memberships.filter((m) => m.user_id === u.user_id && m.status === "ACTIVE");
      if (!ms.length) return;
      const r = seeded("consent" + u.user_id);
      const purposes = ["TERMS_OF_SERVICE", "PRIVACY_POLICY"];
      if (ms.some((m) => m.roles.includes("DRIVER") && orgOf(m).legal_form === "COMPANY")) purposes.push("PRIVACY_NOTICE");
      if (ms.some((m) => orgOf(m).legal_form === "INDIVIDUAL")) purposes.push("LOCATION_TRACKING");
      purposes.forEach((p) => {
        const list = docsOf(p);
        const onOld = list.length > 1 && r() < 0.18;
        const doc = onOld ? list[list.length - 2] : list[list.length - 1];
        const start = Math.max(new Date(doc.created_at).getTime(), new Date(u.created_at).getTime());
        rows.push({ user_id: u.user_id, organization_id: ms[0].organization_id, purpose: p, legal_document_id: doc.legal_document_id, accepted_at: new Date(start + r() * Math.max(DAY, (onOld ? new Date(list[list.length - 1].created_at).getTime() : NOW) - start) * 0.8).toISOString(), ip_address: `113.161.${Math.floor(r() * 250) + 1}.${Math.floor(r() * 250) + 1}`, device: DEVICES[Math.floor(r() * DEVICES.length)] });
      });
    });
    db.organizations.filter((o) => o.kind === "CUSTOMER" && o.legal_form === "COMPANY").forEach((o) => {
      const admin = orgAdminOf(o.organization_id);
      if (!admin || admin.status !== "ACTIVE") return;
      const doc = o.organization_id === "org-dn" ? docsOf("DATA_PROCESSING_AGREEMENT")[0] : currentDoc("DATA_PROCESSING_AGREEMENT");
      rows.push({ user_id: admin.user_id, organization_id: o.organization_id, purpose: "DATA_PROCESSING_AGREEMENT", legal_document_id: doc.legal_document_id, accepted_at: new Date(new Date(doc.created_at).getTime() + 3 * DAY).toISOString(), ip_address: "113.161.72.18", device: "Chrome 129 / Windows 11" });
    });
    consentCache = rows;
    return rows;
  }
  function legalTab(query) {
    const rows = consents();
    const editable = scope.has("CO_ADMIN", "HEAD_ADMIN");
    const purpose = query.doc || "";
    const shown = rows.filter((c) => !purpose || c.purpose === purpose).sort((a, b) => (a.accepted_at < b.accepted_at ? 1 : -1));
    const stats = PURPOSES.map((p) => {
      const concerned = rows.filter((c) => c.purpose === p);
      const cur = currentDoc(p);
      return { p, cur, concerned: concerned.length, current: concerned.filter((c) => c.legal_document_id === cur.legal_document_id).length, versions: docsOf(p).length };
    });
    return `${ui.card({
        title: t("Legal texts", "Văn bản pháp lý"), sub: t("Decree 13/2023 · Law 91/2025/QH15", "Nghị định 13/2023 · Luật 91/2025/QH15"), flush: true,
        body: ui.table({
          rows: stats,
          columns: [
            { label: t("Text", "Văn bản"), render: (s) => `<b>${esc(lbl(s.p))}</b><span class="sub">${esc(s.cur.title)}</span>` },
            { label: t("Current version", "Phiên bản hiện hành"), render: (s) => `<b>v${esc(s.cur.version)}</b><span class="sub">${fmt.date(s.cur.created_at)} · ${s.versions} ${esc(t("versions", "phiên bản"))}</span>` },
            { label: t("Accepted the current version", "Đã chấp nhận bản hiện hành"), render: (s) => `<div class="row between small"><span>${s.current} / ${s.concerned}</span><b>${fmt.pct((s.current / (s.concerned || 1)) * 100)}</b></div>${ui.meter((s.current / (s.concerned || 1)) * 100, s.current < s.concerned ? "warn" : "")}` },
            { label: t("Who accepts", "Ai chấp nhận"), render: (s) => `<span class="small">${esc({ TERMS_OF_SERVICE: t("every user", "mọi người dùng"), PRIVACY_POLICY: t("every user", "mọi người dùng"), DATA_PROCESSING_AGREEMENT: t("the company, by its admin", "doanh nghiệp, qua quản trị viên"), PRIVACY_NOTICE: t("employed drivers", "tài xế là nhân viên"), LOCATION_TRACKING: t("individual customers", "khách hàng cá nhân") }[s.p])}</span>` },
            { label: "", align: "right", render: (s) => (editable ? ui.btn(t("Publish new version", "Phát hành phiên bản mới"), { sm: true, icon: "upload", action: "publish-doc", attrs: `data-id="${s.p}"` }) : "") },
          ],
        }),
      })}
      ${ui.card({
        cls: "mt", title: t("Acceptances", "Lượt chấp nhận"), sub: t("proof: exact version, time, IP and device; never changed", "bằng chứng: phiên bản, thời gian, IP và thiết bị; không bao giờ sửa"), flush: true,
        actions: ui.select("doc", [["", t("All texts", "Mọi văn bản")], ...PURPOSES.map((p) => [p, lbl(p)])], purpose),
        body: ui.table({
          rows: shown.slice(0, 40),
          columns: [
            { label: t("Person", "Người dùng"), render: (c) => personCell(db.user(c.user_id), (db.org(c.organization_id) || {}).short_name) },
            { label: t("Text", "Văn bản"), render: (c) => esc(lbl(c.purpose)) },
            { label: t("Version", "Phiên bản"), render: (c) => { const d = legalDocs.find((x) => x.legal_document_id === c.legal_document_id); const isCur = d === currentDoc(c.purpose); return `v${esc(d.version)} ${isCur ? "" : `<span class="badge warn plain">${esc(t("must accept again", "cần chấp nhận lại"))}</span>`}`; } },
            { label: t("Accepted", "Chấp nhận lúc"), render: (c) => fmt.dateTime(c.accepted_at) },
            { label: "IP", render: (c) => `<span class="mono">${esc(c.ip_address)}</span>` },
            { label: t("Device", "Thiết bị"), render: (c) => `<span class="small">${esc(c.device)}</span>` },
          ],
        }) + `<div class="card-body small muted">${esc(t(`Showing ${Math.min(40, shown.length)} of ${shown.length}. Withdrawing a required acceptance means leaving the service (account closure).`, `Hiện ${Math.min(40, shown.length)} / ${shown.length}. Rút lại sự chấp nhận bắt buộc đồng nghĩa với rời dịch vụ (đóng tài khoản).`))}</div>`,
      })}`;
  }
  function publishDoc(purpose) {
    const cur = currentDoc(purpose);
    const [major, minor] = cur.version.split(".").map(Number);
    ui.formModal({
      title: t(`New version · ${lbl(purpose)}`, `Phiên bản mới · ${lbl(purpose)}`), wide: true,
      fields: [
        { name: "version", label: t("Version", "Phiên bản"), value: `${major}.${minor + 1}`, required: true, hint: t(`Current: v${cur.version}`, `Hiện hành: v${cur.version}`) },
        { name: "title", label: t("Title", "Tiêu đề"), value: cur.title, required: true },
        { name: "summary", label: t("What changes", "Nội dung thay đổi"), type: "textarea", required: true, full: true },
        { name: "reaccept", label: t("Re-acceptance", "Chấp nhận lại"), type: "checkbox", value: true, full: true, checkLabel: t("Everyone concerned must accept again before continuing", "Mọi người liên quan phải chấp nhận lại trước khi tiếp tục") },
      ],
      submitLabel: t("Publish", "Phát hành"),
      onSubmit(v) {
        if (!/^\d+\.\d+$/.test(v.version)) return t("Use a version like 3.1.", "Dùng dạng phiên bản như 3.1.");
        const [a, b] = v.version.split(".").map(Number);
        if (a < major || (a === major && b <= minor)) return t(`The version must be above v${cur.version}.`, `Phiên bản phải lớn hơn v${cur.version}.`);
        legalDocs.push({ legal_document_id: db.uid("ld"), purpose, version: v.version, title: v.title, created_at: nowIso(), created_by: me().user_id });
        const concerned = consents().filter((c) => c.purpose === purpose).length;
        done(esc(v.reaccept ? t(`v${v.version} published: ${concerned} people must accept it at their next login.`, `Đã phát hành v${v.version}: ${concerned} người phải chấp nhận ở lần đăng nhập tới.`) : t(`v${v.version} published`, `Đã phát hành v${v.version}`)));
      },
    });
  }

  /* ---- API keys (ACC-21) ---- */
  const API_SCOPES = {
    TELEMETRY_READ: ["Vehicle telemetry (read)", "Dữ liệu xe (đọc)"],
    LIVE_POSITION_READ: ["Live positions (read)", "Vị trí trực tuyến (đọc)"],
    TRIPS_WRITE: ["Trips (create, update)", "Chuyến (tạo, sửa)"],
    SUPPORT_CASES: ["Support & SOS cases", "Yêu cầu hỗ trợ & SOS"],
    CHARGING_SESSIONS_READ: ["Charging sessions (read)", "Phiên sạc (đọc)"],
    INVOICES_READ: ["Invoices (read)", "Hóa đơn (đọc)"],
  };
  const apiKeys = [
    { api_key_id: "ak-1", organization_id: "org-p1", name: "Điều phối cứu hộ", prefix: "g3_live_7Hq2", scopes: ["SUPPORT_CASES", "LIVE_POSITION_READ"], created_at: isoAgo(120 * DAY), created_by: uidOf("coadmin"), rotated_at: null, last_used_at: isoAgo(4 * MIN), status: "ACTIVE", status_reason: null },
    { api_key_id: "ak-2", organization_id: "org-tcx", name: "TMS Tân Cảng Xanh", prefix: "g3_live_Pk9x", scopes: ["TRIPS_WRITE", "TELEMETRY_READ", "CHARGING_SESSIONS_READ"], created_at: isoAgo(160 * DAY), created_by: uidOf("coadmin"), rotated_at: isoAgo(20 * DAY), last_used_at: isoAgo(40 * 1000), status: "ACTIVE", status_reason: null },
    { api_key_id: "ak-3", organization_id: "org-mn", name: "ERP kế toán", prefix: "g3_live_Zt4m", scopes: ["INVOICES_READ"], created_at: isoAgo(140 * DAY), created_by: uidOf("coadmin"), rotated_at: null, last_used_at: isoAgo(33 * DAY), status: "REVOKED", status_reason: "Khách hàng đổi phần mềm kế toán" },
    { api_key_id: "ak-4", organization_id: "org-g3", name: "Cổng dữ liệu Sở GTVT (thử nghiệm)", prefix: "g3_test_Rr1c", scopes: ["TELEMETRY_READ"], created_at: isoAgo(25 * DAY), created_by: uidOf("head"), rotated_at: null, last_used_at: isoAgo(6 * HOUR), status: "ACTIVE", status_reason: null },
  ];
  const apiCalls = (function () {
    const r = seeded("api-calls");
    const endpoints = { "ak-1": [["GET", "/support/cases?status=OPEN"], ["GET", "/telemetry/vehicles/{id}/live"], ["PATCH", "/support/cases/{id}"]], "ak-2": [["POST", "/trips"], ["GET", "/telemetry/vehicles/{id}/live"], ["GET", "/charging-sessions?from=…"], ["PATCH", "/trips/{id}"]], "ak-4": [["GET", "/telemetry/fleet-summary"]] };
    const out = [];
    for (let k = 0; k < 28; k++) {
      const key = ["ak-1", "ak-2", "ak-2", "ak-2", "ak-4"][Math.floor(r() * 5)];
      const [method, path] = endpoints[key][Math.floor(r() * endpoints[key].length)];
      const roll = r();
      out.push({ at: new Date(NOW - k * (r() * 25 + 3) * MIN).toISOString(), api_key_id: key, method, path, status_code: roll < 0.06 ? 429 : roll < 0.1 ? 403 : method === "POST" ? 201 : 200, latency_ms: Math.round(40 + r() * 260), ip: key === "ak-2" ? "103.21.148.9" : key === "ak-1" ? "14.241.226.30" : "203.162.4.190" });
    }
    return out;
  })();
  function showSecret(key) {
    const secret = `${key.prefix}_${Array.from({ length: 28 }, (_, i) => "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"[(hashOf(key.api_key_id + key.prefix + i) + i * 7) % 56]).join("")}`;
    ui.modal({
      title: t("Copy the key now", "Sao chép khóa ngay"),
      body: `${ui.callout("warn", esc(t("The full key is shown once. We only keep its hash; a lost key is rotated, not recovered.", "Khóa đầy đủ chỉ hiện một lần. Hệ thống chỉ lưu bản băm; mất khóa thì xoay khóa mới, không khôi phục được.")))}<div class="mt card" style="padding:12px;box-shadow:none"><code style="word-break:break-all">${esc(secret)}</code></div>`,
    });
  }
  function apiKeysTab() {
    const editable = scope.has("CO_ADMIN", "HEAD_ADMIN");
    const active = apiKeys.filter((k) => k.status === "ACTIVE");
    const day = apiCalls.filter((c) => NOW - new Date(c.at).getTime() < DAY);
    return `<div class="grid cols-4">
        ${ui.kpi({ label: t("Active keys", "Khóa đang dùng"), icon: "key", value: active.length, foot: `${apiKeys.length - active.length} ${t("revoked", "đã thu hồi")}` })}
        ${ui.kpi({ label: t("Calls (24 h)", "Lượt gọi (24 giờ)"), icon: "activity", value: fmt.num(day.length * 137), foot: t("every call is logged", "mọi lượt gọi đều ghi nhật ký") })}
        ${ui.kpi({ label: t("Rejected calls", "Lượt bị từ chối"), icon: "alert", value: apiCalls.filter((c) => c.status_code >= 400).length, tone: "warn", foot: t("403 scope · 429 rate limit", "403 ngoài phạm vi · 429 vượt giới hạn") })}
        ${ui.kpi({ label: t("Median latency", "Độ trễ trung vị"), icon: "clock", value: apiCalls.map((c) => c.latency_ms).sort((a, b) => a - b)[Math.floor(apiCalls.length / 2)], unit: "ms" })}
      </div>
      ${ui.card({
        cls: "mt", title: t("API keys", "Khóa API"), sub: t("per organization, limited to named features", "theo tổ chức, giới hạn theo tính năng"), flush: true,
        actions: editable ? ui.btn(t("Create key", "Tạo khóa"), { sm: true, kind: "primary", icon: "plus", action: "create-key" }) : "",
        body: ui.table({
          rows: apiKeys,
          columns: [
            { label: t("Key", "Khóa"), render: (k) => `<b>${esc(k.name)}</b><span class="sub mono">${esc(k.prefix)}••••••••</span>` },
            { label: t("Organization", "Tổ chức"), render: (k) => ui.orgName(k.organization_id) },
            { label: t("Allowed features", "Tính năng được phép"), render: (k) => `<span class="row" style="gap:4px">${k.scopes.map((s) => ui.tag(t(API_SCOPES[s][0], API_SCOPES[s][1]))).join("")}</span>` },
            { label: t("Created", "Ngày tạo"), render: (k) => `${fmt.date(k.created_at)}<span class="sub">${ui.userName(k.created_by)}${k.rotated_at ? ` · ${t("rotated", "xoay")} ${fmt.date(k.rotated_at)}` : ""}</span>` },
            { label: t("Last used", "Dùng gần nhất"), render: (k) => fmt.ago(k.last_used_at) },
            { label: t("Status", "Trạng thái"), render: (k) => `${badge(k.status, k.status === "ACTIVE" ? "ok" : "muted")}${k.status_reason ? `<span class="sub">${esc(k.status_reason)}</span>` : ""}` },
            { label: "", align: "right", render: (k) => (editable && k.status === "ACTIVE" ? `<span class="row" style="justify-content:flex-end;gap:4px;flex-wrap:nowrap">${ui.btn(t("Rotate", "Xoay khóa"), { sm: true, icon: "refresh", action: "rotate-key", attrs: `data-id="${k.api_key_id}"` })}${ui.btn(t("Revoke", "Thu hồi"), { sm: true, kind: "ghost", icon: "x", action: "revoke-key", attrs: `data-id="${k.api_key_id}"` })}</span>` : "") },
          ],
        }),
      })}
      ${ui.card({
        cls: "mt", title: t("Call log", "Nhật ký gọi API"), sub: t("latest calls", "lượt gọi gần nhất"), flush: true,
        body: ui.table({
          rows: apiCalls.slice(0, 15),
          columns: [
            { label: t("When", "Thời điểm"), render: (c) => `${fmt.time(c.at)}<span class="sub">${fmt.ago(c.at)}</span>` },
            { label: t("Key", "Khóa"), render: (c) => esc((apiKeys.find((k) => k.api_key_id === c.api_key_id) || {}).name) },
            { label: t("Request", "Yêu cầu"), render: (c) => `<span class="mono"><b>${esc(c.method)}</b> ${esc(c.path)}</span>` },
            { label: t("Result", "Kết quả"), render: (c) => `<span class="badge ${c.status_code < 300 ? "ok" : c.status_code === 429 ? "warn" : "danger"} plain">${c.status_code}</span>` },
            { label: t("Latency", "Độ trễ"), align: "right", render: (c) => `${c.latency_ms} ms` },
            { label: "IP", render: (c) => `<span class="mono">${esc(c.ip)}</span>` },
          ],
        }),
      })}`;
  }
  function createKey() {
    ui.formModal({
      title: t("Create API key", "Tạo khóa API"), wide: true,
      fields: [
        { name: "org", label: t("Organization", "Tổ chức"), type: "select", options: db.organizations.map((o) => [o.organization_id, o.short_name]) },
        { name: "name", label: t("Name", "Tên"), required: true, placeholder: "TMS, ERP…" },
        { name: "scopes", label: t("Allowed features", "Tính năng được phép"), type: "checkboxes", required: true, full: true, options: Object.entries(API_SCOPES).map(([k, v]) => [k, t(v[0], v[1])]), value: [] },
      ],
      submitLabel: t("Create", "Tạo"),
      onSubmit(v) {
        const key = { api_key_id: db.uid("ak"), organization_id: v.org, name: v.name, prefix: `g3_live_${db.uid("k").slice(2, 6)}`, scopes: v.scopes, created_at: nowIso(), created_by: me().user_id, rotated_at: null, last_used_at: null, status: "ACTIVE", status_reason: null };
        apiKeys.unshift(key);
        G3.render();
        showSecret(key);
      },
    });
  }

  /* ---- Device send interval (organization_settings.telemetry_interval_seconds, DEV-07) ---- */
  const deviceCommands = [];
  const devicesOf = (orgId) => db.telematics.filter((x) => x.vehicle_id && (db.vehicle(x.vehicle_id) || {}).organization_id === orgId);
  function deviceState(dev, configured) {
    if (dev.health === "SILENT" || dev.health === "NO_DATA") return "NO_ANSWER";
    return dev.reported_interval_seconds === configured ? "APPLIED" : "MISMATCH";
  }
  function seedCommands(orgId) {
    if (deviceCommands.some((c) => c.organization_id === orgId)) return;
    const s = settingsOf(orgId);
    const devs = devicesOf(orgId);
    const results = (interval) => devs.map((d) => ({ telematic_id: d.telematic_id, outcome: d.health === "SILENT" || d.health === "NO_DATA" ? "TIMEOUT" : "ACCEPTED", interval }));
    const prev = s.telemetry_interval_seconds === 10 ? 15 : 30;
    deviceCommands.push({ command_id: db.uid("dcmd"), organization_id: orgId, at: isoAgo(64 * DAY), by: uidOf("ops"), from: 30, to: prev, reason: "Cấu hình chuẩn khi bàn giao", results: results(prev) });
    if (prev !== s.telemetry_interval_seconds) deviceCommands.push({ command_id: db.uid("dcmd"), organization_id: orgId, at: isoAgo(12 * DAY), by: uidOf("ops"), from: prev, to: s.telemetry_interval_seconds, reason: "Khách hàng cần bản đồ cập nhật nhanh hơn", results: results(s.telemetry_interval_seconds) });
  }
  function pushInterval(org, to, reason, isRollback) {
    const s = settingsOf(org.organization_id);
    const from = s.telemetry_interval_seconds;
    const results = devicesOf(org.organization_id).map((d) => {
      if (d.health === "SILENT" || d.health === "NO_DATA") return { telematic_id: d.telematic_id, outcome: "TIMEOUT", interval: to };
      d.send_interval_seconds = to;
      // Firmware v2.2.8 acknowledges the command but keeps its old interval: the status report shows the mismatch.
      if (d.firmware_version !== "v2.2.8") d.reported_interval_seconds = to;
      return { telematic_id: d.telematic_id, outcome: "ACCEPTED", interval: to };
    });
    s.telemetry_interval_seconds = to;
    deviceCommands.push({ command_id: db.uid("dcmd"), organization_id: org.organization_id, at: nowIso(), by: me().user_id, from, to, reason: (isRollback ? t("Rollback: ", "Hoàn tác: ") : "") + reason, results });
    settingChanges.push({ at: nowIso(), by: me().user_id, organization_id: org.organization_id, kind: "device", label: `${org.short_name} · telemetry_interval_seconds`, fields: [["telemetry_interval_seconds", `${from} s`, `${to} s`]], reason });
    done(esc(t(`Interval ${to} s pushed to ${results.length} devices (${results.filter((r) => r.outcome === "ACCEPTED").length} accepted).`, `Đã đẩy chu kỳ ${to} giây tới ${results.length} thiết bị (${results.filter((r) => r.outcome === "ACCEPTED").length} chấp nhận).`)));
  }
  function devicesTab(org) {
    seedCommands(org.organization_id);
    const s = settingsOf(org.organization_id);
    const devs = devicesOf(org.organization_id);
    const states = devs.map((d) => deviceState(d, s.telemetry_interval_seconds));
    const commands = deviceCommands.filter((c) => c.organization_id === org.organization_id).slice().reverse();
    const editable = scope.has("OPERATIONS", "HEAD_ADMIN");
    return `<div class="grid cols-4">
        ${ui.kpi({ label: t("Send interval", "Chu kỳ gửi"), icon: "clock", value: s.telemetry_interval_seconds, unit: "s", foot: esc(org.short_name) })}
        ${ui.kpi({ label: t("Applied", "Đã áp dụng"), icon: "check", tone: "ok", value: states.filter((x) => x === "APPLIED").length, foot: `/ ${devs.length} ${t("devices", "thiết bị")}` })}
        ${ui.kpi({ label: t("Not applied", "Chưa áp dụng"), icon: "alert", tone: states.includes("MISMATCH") ? "warn" : undefined, value: states.filter((x) => x === "MISMATCH").length, foot: t("reported ≠ configured", "báo cáo ≠ cấu hình") })}
        ${ui.kpi({ label: t("No answer", "Không phản hồi"), icon: "signal", tone: states.includes("NO_ANSWER") ? "danger" : undefined, value: states.filter((x) => x === "NO_ANSWER").length, foot: t("sent again when back online", "gửi lại khi có kết nối") })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Devices of the organization", "Thiết bị của tổ chức"), flush: true,
          actions: editable ? ui.btn(t("Push new interval", "Đẩy chu kỳ mới"), { sm: true, kind: "primary", icon: "send", action: "push-interval" }) : "",
          body: ui.table({
            rows: devs, empty: t("No mounted device", "Chưa có thiết bị được lắp"),
            columns: [
              { label: t("Device", "Thiết bị"), render: (d) => `<b class="mono">${esc(d.telematic_serial)}</b><span class="sub">${esc(d.firmware_version)}</span>` },
              { label: t("Truck", "Xe"), render: (d) => ui.vehicleCell(db.vehicle(d.vehicle_id)) },
              { label: t("Configured", "Cấu hình"), align: "right", render: () => `${s.telemetry_interval_seconds} s` },
              { label: t("Reported", "Thiết bị báo"), align: "right", render: (d) => (d.reported_interval_seconds ? `${d.reported_interval_seconds} s` : "—") },
              { label: t("Result", "Kết quả"), render: (d) => { const st = deviceState(d, s.telemetry_interval_seconds); return badge(st, st === "APPLIED" ? "ok" : st === "MISMATCH" ? "warn" : "danger") + (st === "MISMATCH" && d.firmware_version === "v2.2.8" ? `<span class="sub">${esc(t("firmware update needed", "cần cập nhật firmware"))}</span>` : ""); } },
              { label: t("Health", "Tình trạng"), render: (d) => ui.badge(d.health) },
            ],
          }),
        })}
        ${ui.card({
          title: t("Command history", "Lịch sử lệnh"),
          body: `<ul class="timeline">${commands.map((c, i) => `<li><div class="row between"><b>${c.from} s → ${c.to} s</b>${editable && i === 0 && commands.length > 1 ? ui.btn(t("Roll back", "Hoàn tác"), { sm: true, kind: "ghost", icon: "refresh", action: "rollback", attrs: `data-id="${c.command_id}"` }) : ""}</div><div class="small">${esc(c.reason)}</div><div class="small muted">${c.results.filter((r) => r.outcome === "ACCEPTED").length} ${esc(t("accepted", "chấp nhận"))} · ${c.results.filter((r) => r.outcome === "TIMEOUT").length} ${esc(t("no answer", "không phản hồi"))}</div><div class="when">${fmt.dateTime(c.at)} · ${ui.userName(c.by)}</div></li>`).join("")}</ul>`,
        })}
      </div>`;
  }

  const SETTINGS_TABS = [
    { key: "channels", label: ["Notification channels", "Kênh thông báo"], roles: ["ORG_ADMIN"], features: ["NTF-05"], org: true },
    { key: "organization", label: ["Organization", "Tổ chức"], roles: ["ORG_ADMIN"], features: [], org: true },
    { key: "thresholds", label: ["Alert thresholds", "Ngưỡng cảnh báo"], roles: ["OPERATIONS"], features: ["MON-17"], internal: true },
    { key: "devices", label: ["Device interval", "Chu kỳ thiết bị"], roles: ["OPERATIONS"], features: ["DEV-07"], internal: true, org: true },
    { key: "legal", label: ["Legal documents", "Văn bản pháp lý"], roles: ["CO_ADMIN"], features: ["ACC-17"], internal: true },
    { key: "api", label: ["API keys", "Khóa API"], roles: ["CO_ADMIN"], features: ["ACC-21"], internal: true },
  ];
  const visibleTabs = () => SETTINGS_TABS.filter((x) => (scope.has("HEAD_ADMIN") || scope.has(...x.roles)) && (!x.internal || scope.isInternal()));

  G3.page({
    id: "settings",
    path: "/settings",
    title: ["Settings", "Cấu hình"],
    roles: ["ORG_ADMIN", "OPERATIONS", "CO_ADMIN"],
    features: ["NTF-05", "MON-17", "ACC-17", "ACC-21", "DEV-07"],
    nav: { group: "admin", order: 5, icon: "settings" },
    render({ query }) {
      const tabs = visibleTabs();
      const current = tabs.find((x) => x.key === query.tab) || tabs[0];
      const headHtml = ui.pageHead({
        title: t("Settings", "Cấu hình"),
        sub: esc(scope.isInternal() ? t("Each tab belongs to the team that owns it.", "Mỗi thẻ thuộc về bộ phận phụ trách.") : t(`${scope.org().name} · settings of your organization`, `${scope.org().name} · cấu hình của tổ chức bạn`)),
        features: current ? current.features : [],
        actions: current && current.org && scope.isInternal() ? ui.select("org", [["", t("Choose an organization…", "Chọn tổ chức…")], ...db.organizations.filter((o) => !o.is_internal).map((o) => [o.organization_id, o.short_name])], (contextOrg(query) || {}).organization_id || "") : "",
      });
      if (!current) return headHtml + ui.empty();
      let body;
      if (current.org) {
        let org = contextOrg(query);
        if (!org && current.key === "devices") org = db.org("org-tcx");
        if (org && org.is_internal) org = null;
        body = !org ? orgPicker(t("Choose an organization", "Chọn tổ chức"), query, false) : current.key === "channels" ? channelsTab(org) : current.key === "organization" ? orgSettingsTab(org) : devicesTab(org);
      } else body = current.key === "thresholds" ? thresholdsTab() : current.key === "legal" ? legalTab(query) : apiKeysTab();
      return `${headHtml}${ui.tabs(tabs.map((x) => [x.key, t(x.label[0], x.label[1])]), current.key)}${body}`;
    },
    mount(root, { query }) {
      bindQuery(root);
      bindOrgPicker(root);
      const tabs = visibleTabs();
      const current = tabs.find((x) => x.key === query.tab) || tabs[0];
      let org = contextOrg(query);
      if (!org && current && current.key === "devices") org = db.org("org-tcx");
      root.querySelectorAll("[data-ch]").forEach((input) => input.addEventListener("change", () => {
        channelsOf(org.organization_id)[input.dataset.type][input.dataset.ch] = input.checked;
        ui.toast(esc(t(`${G3.label(input.dataset.type)}: ${input.dataset.ch} ${input.checked ? "on" : "off"}`, `${G3.label(input.dataset.type)}: ${input.dataset.ch} ${input.checked ? "bật" : "tắt"}`)));
      }));
      delegate(root, {
        "channels-reset": () => { delete channelStore[org.organization_id]; done(t("Defaults restored", "Đã khôi phục mặc định")); },
        "save-org-settings": () => {
          const s = settingsOf(org.organization_id);
          const form = root.querySelector("[data-org-settings]");
          const autoEnd = Number(form.querySelector("[name=auto_end]").value);
          const tz = form.querySelector("[name=report_tz]").value;
          const fields = [];
          if (autoEnd !== s.auto_end_minutes) fields.push(["driving_session_auto_end_minutes", s.auto_end_minutes, autoEnd]);
          if (tz !== s.report_timezone) fields.push(["report_timezone", s.report_timezone, tz]);
          if (!fields.length) { ui.toast(t("Nothing changed", "Không có thay đổi"), "", "info"); return; }
          s.auto_end_minutes = autoEnd;
          s.report_timezone = tz;
          settingChanges.push({ at: nowIso(), by: me().user_id, organization_id: org.organization_id, kind: "org", label: `${org.short_name} · organization_settings`, fields, reason: t("Edited by the organization admin", "Quản trị tổ chức chỉnh sửa") });
          done(t("Settings saved", "Đã lưu cấu hình"));
        },
        "edit-threshold": (el) => {
          const x = thresholds.find((y) => y.key === el.dataset.id);
          ui.formModal({
            title: `${t(x.group[0], x.group[1])} · ${t(x.label[0], x.label[1])}`,
            fields: [
              { name: "value", label: `${t("Threshold", "Ngưỡng")} (${x.unit})`, type: "number", value: x.value, required: true },
              { name: "severity", label: t("Severity", "Mức độ"), type: "select", value: x.severity, options: ["INFO", "WARNING", "CRITICAL"].map((s) => [s, G3.label(s)]) },
              { name: "reason", label: t("Reason", "Lý do"), type: "textarea", required: true, hint: t("Thresholds follow the manufacturer and operations.", "Ngưỡng theo khuyến nghị nhà sản xuất và vận hành.") },
            ],
            onSubmit(v) {
              const value = Number(v.value);
              if (!Number.isFinite(value) || value <= 0) return t("Enter a positive number.", "Nhập một số dương.");
              thresholdHistory.unshift({ at: nowIso(), by: me().user_id, text: `${t("Default", "Mặc định")} · ${t(x.group[0], x.group[1])}: ${x.value} ${x.unit} → ${value} ${x.unit}${v.severity !== x.severity ? ` · ${G3.label(x.severity)} → ${G3.label(v.severity)}` : ""}`, reason: v.reason });
              x.value = value;
              x.severity = v.severity;
              done(t("Threshold saved; applies to new readings", "Đã lưu ngưỡng; áp dụng cho dữ liệu mới"));
            },
          });
        },
        "add-override": () => ui.formModal({
          title: t("Add override", "Thêm ngưỡng riêng"), wide: true,
          fields: [
            { name: "target", label: t("Applies to", "Áp dụng cho"), type: "select", full: true, options: [...db.vehicle_models.map((m) => [`MODEL:${m.vehicle_model_id}`, `${lbl("MODEL")} · ${m.name}`]), ...db.fleets.map((f) => [`FLEET:${f.fleet_id}`, `${lbl("FLEET")} · ${f.name} (${db.org(f.organization_id).short_name})`])] },
            { name: "key", label: t("Measure", "Chỉ số"), type: "select", options: thresholds.map((x) => [x.key, `${t(x.group[0], x.group[1])} · ${t(x.label[0], x.label[1])}`]) },
            { name: "value", label: t("Value", "Giá trị"), type: "number", required: true },
            { name: "reason", label: t("Reason", "Lý do"), type: "textarea", required: true, full: true },
          ],
          onSubmit(v) {
            const [kind, target] = v.target.split(":");
            const x = thresholds.find((y) => y.key === v.key);
            const value = Number(v.value);
            if (!Number.isFinite(value) || value <= 0) return t("Enter a positive number.", "Nhập một số dương.");
            if (thresholdOverrides.some((o) => o.scope === kind && o.target === target && o.key === v.key)) return t("An override already exists for this measure and target.", "Đã có ngưỡng riêng cho chỉ số và đối tượng này.");
            const o = { scope: kind, target, key: v.key, value, by: me().user_id, at: nowIso(), reason: v.reason };
            thresholdOverrides.push(o);
            thresholdHistory.unshift({ at: o.at, by: o.by, text: `${targetName(o)} · ${t(x.group[0], x.group[1])}: ${x.value} ${x.unit} → ${value} ${x.unit}`, reason: v.reason });
            done(t("Override added", "Đã thêm ngưỡng riêng"));
          },
        }),
        "remove-override": (el) => {
          const o = thresholdOverrides[Number(el.dataset.id)];
          ui.reasonModal({
            title: t("Remove override", "Xóa ngưỡng riêng"),
            onSubmit(reason) {
              thresholdOverrides.splice(thresholdOverrides.indexOf(o), 1);
              thresholdHistory.unshift({ at: nowIso(), by: me().user_id, text: `${targetName(o)} · ${t("back to the default", "quay về mặc định")}`, reason });
              done(t("Override removed", "Đã xóa ngưỡng riêng"));
            },
          });
        },
        "publish-doc": (el) => publishDoc(el.dataset.id),
        "create-key": () => createKey(),
        "rotate-key": (el) => {
          const key = apiKeys.find((k) => k.api_key_id === el.dataset.id);
          ui.formModal({
            title: t(`Rotate ${key.name}`, `Xoay khóa ${key.name}`), fields: [], submitLabel: t("Rotate", "Xoay khóa"),
            intro: `<p>${esc(t("A new key is issued; the old one keeps working for 24 hours so the partner can switch.", "Khóa mới được cấp; khóa cũ vẫn dùng được 24 giờ để đối tác chuyển đổi."))}</p>`,
            onSubmit() { key.prefix = `g3_live_${db.uid("k").slice(2, 6)}`; key.rotated_at = nowIso(); G3.render(); showSecret(key); },
          });
        },
        "revoke-key": (el) => {
          const key = apiKeys.find((k) => k.api_key_id === el.dataset.id);
          ui.reasonModal({
            title: t(`Revoke ${key.name}`, `Thu hồi ${key.name}`), submitLabel: t("Revoke", "Thu hồi"),
            intro: `<p class="small muted">${esc(t("Calls with this key are refused immediately.", "Mọi lượt gọi bằng khóa này bị từ chối ngay."))}</p>`,
            onSubmit(reason) { key.status = "REVOKED"; key.status_reason = reason; done(t("Key revoked", "Đã thu hồi khóa")); },
          });
        },
        "push-interval": () => {
          const s = settingsOf(org.organization_id);
          ui.formModal({
            title: t(`Send interval · ${org.short_name}`, `Chu kỳ gửi · ${org.short_name}`),
            intro: `<p class="small muted">${esc(t("Pushed over MQTT to every device on the organization's trucks, and to a device when it is mounted later. Each device confirms by reporting the interval it uses.", "Đẩy qua MQTT tới mọi thiết bị trên xe của tổ chức, và tới thiết bị được lắp sau này. Mỗi thiết bị xác nhận bằng cách báo chu kỳ đang dùng."))}</p>`,
            fields: [
              { name: "interval", label: t("New interval", "Chu kỳ mới"), type: "select", value: String(s.telemetry_interval_seconds), options: [5, 10, 15, 30, 60].map((n) => [String(n), `${n} ${t("seconds", "giây")}`]) },
              { name: "reason", label: t("Reason", "Lý do"), type: "textarea", required: true },
            ],
            submitLabel: t("Push", "Đẩy cấu hình"),
            onSubmit(v) {
              if (Number(v.interval) === s.telemetry_interval_seconds) return t("This is already the interval.", "Đây đã là chu kỳ hiện tại.");
              pushInterval(org, Number(v.interval), v.reason, false);
            },
          });
        },
        rollback: () => {
          const commands = deviceCommands.filter((c) => c.organization_id === org.organization_id);
          const last = commands[commands.length - 1];
          ui.reasonModal({
            title: t(`Roll back to ${last.from} s`, `Hoàn tác về ${last.from} giây`), danger: false, submitLabel: t("Roll back", "Hoàn tác"),
            onSubmit(reason) { pushInterval(org, last.from, reason, true); },
          });
        },
      });
    },
  });

  /* =====================================================================
     7. My account (ACC-04, ACC-06, ACC-16, ACC-19)
     ===================================================================== */
  const PREVIOUS_PASSWORDS = ["demo-password", "G3network@2025", "Tancang2026"];
  const dataRequests = {};
  const DSR_DAYS = 15; // prototype deadline; the legal team sets the real one per request type
  function requestsOf(user) {
    if (!dataRequests[user.user_id]) {
      const submitted = NOW - 52 * DAY;
      dataRequests[user.user_id] = [{ request_id: `DSR-${(hashOf(user.user_id) % 900) + 100}`, kind: "CORRECTION", details: t("Wrong e-mail spelling on the profile", "Sai chính tả e-mail trên hồ sơ"), status: "COMPLETED", submitted_at: new Date(submitted).toISOString(), due_at: new Date(submitted + DSR_DAYS * DAY).toISOString(), completed_at: new Date(submitted + 2 * DAY).toISOString(), handled_by: uidOf("care") }];
    }
    return dataRequests[user.user_id];
  }
  function changePassword() {
    ui.formModal({
      title: t("Change password", "Đổi mật khẩu"),
      intro: `<p class="small muted">${esc(t("At least 8 characters with letters and digits. A password you used before cannot be reused. Every other session is logged out.", "Tối thiểu 8 ký tự gồm chữ và số. Không dùng lại mật khẩu cũ. Mọi phiên khác sẽ bị đăng xuất."))}</p>`,
      fields: [
        { name: "current", label: t("Current password", "Mật khẩu hiện tại"), type: "password", required: true },
        { name: "next", label: t("New password", "Mật khẩu mới"), type: "password", required: true },
        { name: "confirm", label: t("Repeat the new password", "Nhập lại mật khẩu mới"), type: "password", required: true },
      ],
      onSubmit(v) {
        if (v.next.length < 8 || !/[A-Za-z]/.test(v.next) || !/\d/.test(v.next)) return t("Use at least 8 characters with letters and digits.", "Dùng tối thiểu 8 ký tự gồm chữ và số.");
        if (v.next !== v.confirm) return t("The two new passwords differ.", "Hai mật khẩu mới không khớp.");
        if (v.next === v.current || PREVIOUS_PASSWORDS.includes(v.next)) return t("You used this password before: pick a new one.", "Bạn đã dùng mật khẩu này trước đây: hãy chọn mật khẩu mới.");
        PREVIOUS_PASSWORDS.push(v.next);
        const before = db.login_sessions.length;
        db.login_sessions.splice(0, db.login_sessions.length, ...db.login_sessions.filter((s) => s.current));
        G3.audit("PASSWORD_CHANGED", "ACCOUNT", null, null);
        done(esc(t(`Password changed; ${before - db.login_sessions.length} other session(s) logged out.`, `Đã đổi mật khẩu; ${before - db.login_sessions.length} phiên khác đã bị đăng xuất.`)));
      },
    });
  }
  function changePhone(user) {
    ui.formModal({
      title: t("Change phone number", "Đổi số điện thoại"),
      intro: `<p class="small muted">${esc(t("The phone number is your login ID. We send a code to the new number; the old one stops working for login once confirmed.", "Số điện thoại là tên đăng nhập. Mã được gửi tới số mới; số cũ không còn dùng để đăng nhập sau khi xác nhận."))}</p>`,
      fields: [{ name: "phone", label: t("New phone number", "Số điện thoại mới"), type: "tel", required: true, placeholder: "09xx xxx xxx" }],
      submitLabel: t("Send code", "Gửi mã"),
      onSubmit(v) {
        if (!PHONE_RE.test(v.phone)) return t("The phone number has 10 digits and starts with 0.", "Số điện thoại gồm 10 chữ số, bắt đầu bằng 0.");
        if (v.phone === user.phone_number) return t("This is already your number.", "Đây đã là số của bạn.");
        if (db.users.some((u) => u.phone_number === v.phone)) return t("This number belongs to another account.", "Số này thuộc một tài khoản khác.");
        setTimeout(() => ui.formModal({
          title: t("Enter the code", "Nhập mã xác nhận"),
          intro: `<p class="small muted">${esc(t(`Code sent by SMS to ${v.phone}; valid 5 minutes, one use (demo: any 6 digits).`, `Mã đã gửi qua SMS tới ${v.phone}; hiệu lực 5 phút, dùng một lần (demo: 6 chữ số bất kỳ).`))}</p>`,
          fields: [{ name: "otp", label: t("One-time code", "Mã OTP"), required: true, attrs: 'inputmode="numeric" maxlength="6"' }],
          submitLabel: t("Confirm", "Xác nhận"),
          onSubmit(o) {
            if (!/^\d{6}$/.test(o.otp)) return t("The code has 6 digits.", "Mã gồm 6 chữ số.");
            userChanges.push({ at: nowIso(), by: user.user_id, user_id: user.user_id, fields: [["phone_number", user.phone_number, v.phone]], reason: t("Phone number change confirmed by OTP", "Đổi số điện thoại, xác nhận bằng OTP") });
            user.phone_number = v.phone;
            G3.audit("PHONE_CHANGED", "ACCOUNT", null, null);
            done(esc(t(`Phone number changed to ${v.phone}`, `Đã đổi số điện thoại sang ${v.phone}`)));
          },
        }), 0);
      },
    });
  }

  G3.page({
    id: "account",
    path: "/account",
    title: ["My account", "Tài khoản của tôi"],
    roles: "*",
    features: ["ACC-04", "ACC-06", "ACC-16", "ACC-19"],
    render() {
      const user = me();
      const ms = db.memberships.filter((m) => m.user_id === user.user_id && !isLeft(m));
      const pushOn = G3.store.get("g3.push.registered", false);
      const history = loginHistory(user);
      const newDevice = history.find((h) => h.is_new_device);
      const requests = requestsOf(user);
      const myConsents = consents().filter((c) => c.user_id === user.user_id);
      return `${ui.pageHead({
        title: t("My account", "Tài khoản của tôi"),
        sub: esc(`${user.full_name} · ${user.phone_number}`),
        features: ["ACC-04", "ACC-06", "ACC-16", "ACC-19"],
      })}
      <div class="grid cols-3">
        ${ui.card({
          title: t("Profile", "Hồ sơ"), actions: ui.btn(t("Edit", "Sửa"), { sm: true, icon: "edit", action: "edit-profile" }),
          body: `<div class="row mb"><span class="avatar" style="width:44px;height:44px;font-size:16px">${esc(ui.initials(user.full_name))}</span><div><b>${esc(user.full_name)}</b><div class="small muted">${esc(t("one account for every organization", "một tài khoản cho mọi tổ chức"))}</div></div></div>
            ${ui.kv([
              [t("Phone (login ID)", "Số điện thoại (đăng nhập)"), `<span class="mono">${esc(user.phone_number)}</span>`],
              [t("E-mail", "E-mail"), esc(user.email)],
              [t("Status", "Trạng thái"), ui.badge(user.status)],
              [t("Member since", "Tham gia từ"), fmt.date(user.created_at)],
            ])}`,
        })}
        ${ui.card({
          title: t("Security", "Bảo mật"), sub: "ACC-06",
          body: `${ui.kv([
              [t("Password", "Mật khẩu"), `•••••••• <span class="small muted">${esc(t("changed", "đổi"))} ${fmt.ago(isoAgo(47 * DAY))}</span>`],
              [t("Last login", "Đăng nhập gần nhất"), fmt.dateTime(user.last_login_at)],
              [t("Login lockout", "Khóa đăng nhập"), `<span class="small">${esc(t("15 min after 5 wrong passwords", "15 phút sau 5 lần sai mật khẩu"))}</span>`],
            ])}
            <div class="row mt">${ui.btn(t("Change password", "Đổi mật khẩu"), { sm: true, icon: "key", action: "password" })}${ui.btn(t("Change phone number", "Đổi số điện thoại"), { sm: true, icon: "phone", action: "phone" })}</div>`,
        })}
        ${ui.card({
          title: t("Push notifications", "Thông báo đẩy"), sub: "ACC-16",
          body: `<div class="row between"><div><b>${esc(t("This browser", "Trình duyệt này"))}</b><div class="small muted">Chrome 129 / Windows 11</div></div>${pushOn ? ui.badge("ACTIVE", "ok", t("Registered", "Đã đăng ký")) : ui.badge("INACTIVE", "muted", t("Not registered", "Chưa đăng ký"))}</div>
            <div class="mt">${pushOn ? ui.btn(t("Stop notifications here", "Tắt thông báo trên trình duyệt này"), { sm: true, action: "push-off" }) : ui.btn(t("Receive notifications here", "Nhận thông báo trên trình duyệt này"), { sm: true, kind: "primary", icon: "bell", action: "push-on" })}</div>
            <p class="small muted mt">${esc(t("The token belongs to this login session: it is removed at logout, expiry, password change or account lock. Alerts from all your organizations reach every registered device; opening one switches to its organization.", "Token gắn với phiên đăng nhập này: bị xóa khi đăng xuất, hết hạn, đổi mật khẩu hoặc khóa tài khoản. Cảnh báo từ mọi tổ chức của bạn đến mọi thiết bị đã đăng ký; mở thông báo sẽ chuyển sang tổ chức tương ứng."))}</p>`,
        })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Logged-in devices", "Thiết bị đang đăng nhập"), sub: "ACC-04", flush: true,
          actions: db.login_sessions.length > 1 ? ui.btn(t("Log out all other devices", "Đăng xuất mọi thiết bị khác"), { sm: true, icon: "logout", action: "logout-all" }) : "",
          body: ui.table({
            rows: db.login_sessions,
            columns: [
              { label: t("Device", "Thiết bị"), render: (s) => `<b>${esc(s.device)}</b>${s.current ? ` <span class="badge ok plain">${esc(t("this device", "thiết bị này"))}</span>` : ""}<span class="sub">${esc(lbl(s.platform))}</span>` },
              { label: "IP", render: (s) => `<span class="mono">${esc(s.ip_address)}</span>` },
              { label: t("Logged in", "Đăng nhập lúc"), render: (s) => fmt.dateTime(s.created_at) },
              { label: t("Last active", "Hoạt động gần nhất"), render: (s) => fmt.ago(s.last_active_at) },
              { label: t("Push", "Đẩy"), render: (s) => ((s.current ? pushOn : s.platform !== "WEB") ? ui.icon("bell", 15) : '<span class="muted">—</span>') },
              { label: "", align: "right", render: (s) => (s.current ? "" : ui.btn(t("Log out", "Đăng xuất"), { sm: true, kind: "ghost", action: "logout-one", attrs: `data-id="${s.session_id}"` })) },
            ],
          }),
        })}
        ${ui.card({
          title: t("My organizations", "Tổ chức của tôi"), flush: true,
          body: ui.table({ rows: ms, columns: [
            { label: t("Organization", "Tổ chức"), render: (m) => `<b>${ui.orgName(m.organization_id)}</b><span class="sub">${m.joined_at ? `${esc(t("joined", "tham gia"))} ${fmt.date(m.joined_at)}` : esc(t("invited", "đã mời"))}</span>` },
            { label: t("Roles", "Vai trò"), render: (m) => roleTags(m.roles) },
            { label: t("Status", "Trạng thái"), render: (m) => ui.badge(m.status) },
          ] }),
        })}
      </div>
      <div class="grid side mt">
        ${ui.card({
          title: t("Login history", "Lịch sử đăng nhập"), flush: true,
          body: (newDevice ? `<div class="card-body">${ui.callout("warn", esc(t(`Login from a new device (${newDevice.device}, ${newDevice.ip_address}) on ${fmt.dateTime(newDevice.occurred_at)}: we sent you an alert. If it was not you, change your password.`, `Đăng nhập từ thiết bị mới (${newDevice.device}, ${newDevice.ip_address}) lúc ${fmt.dateTime(newDevice.occurred_at)}: hệ thống đã gửi cảnh báo cho bạn. Nếu không phải bạn, hãy đổi mật khẩu.`)))}</div>` : "") + loginHistoryTable(user, 8),
        })}
        ${ui.card({
          title: t("Legal texts I accepted", "Văn bản tôi đã chấp nhận"), sub: "ACC-17", flush: true,
          body: ui.table({ rows: myConsents, empty: t("Nothing accepted yet", "Chưa chấp nhận văn bản nào"), columns: [
            { label: t("Text", "Văn bản"), render: (c) => esc(lbl(c.purpose)) },
            { label: t("Version", "Phiên bản"), render: (c) => { const d = legalDocs.find((x) => x.legal_document_id === c.legal_document_id); return `v${esc(d.version)}${d === currentDoc(c.purpose) ? "" : ` <span class="badge warn plain">${esc(t("new version", "có bản mới"))}</span>`}`; } },
            { label: t("Accepted", "Chấp nhận"), render: (c) => fmt.date(c.accepted_at) },
          ] }),
        })}
      </div>
      ${ui.card({
        cls: "mt", title: t("My data requests", "Yêu cầu về dữ liệu của tôi"), sub: t("see, export, correct or delete your personal data · ACC-19", "xem, xuất, sửa hoặc xóa dữ liệu cá nhân · ACC-19"), flush: true,
        actions: `${ui.btn(t("Download my data", "Tải dữ liệu của tôi"), { sm: true, icon: "download", action: "download-mine" })}${ui.btn(t("New request", "Gửi yêu cầu"), { sm: true, kind: "primary", icon: "plus", action: "new-request" })}`,
        body: ui.table({
          rows: requests.slice().sort((a, b) => (a.submitted_at < b.submitted_at ? 1 : -1)),
          columns: [
            { label: t("Request", "Yêu cầu"), render: (r) => `<b class="mono">${esc(r.request_id)}</b><span class="sub">${esc(lbl(r.kind))}</span>` },
            { label: t("Details", "Nội dung"), render: (r) => `<span class="small">${esc(r.details)}</span>` },
            { label: t("Submitted", "Ngày gửi"), render: (r) => fmt.date(r.submitted_at) },
            { label: t("Deadline", "Hạn xử lý"), render: (r) => { const left = (new Date(r.due_at).getTime() - Date.now()) / DAY; return `${fmt.date(r.due_at)}${r.status === "COMPLETED" ? "" : `<span class="sub" style="color:var(${left < 3 ? "--danger" : "--text-3"})">${esc(t(`${Math.max(0, Math.ceil(left))} days left`, `còn ${Math.max(0, Math.ceil(left))} ngày`))}</span>`}`; } },
            { label: t("Progress", "Tiến độ"), render: (r) => (r.status === "COMPLETED" ? ui.meter(100) : ui.meter(Math.min(95, ((Date.now() - new Date(r.submitted_at).getTime()) / (DSR_DAYS * DAY)) * 100 + 8), "warn")) },
            { label: t("Status", "Trạng thái"), render: (r) => `${badge(r.status, r.status === "COMPLETED" ? "ok" : "info")}<span class="sub">${r.handled_by ? ui.userName(r.handled_by) : esc(t("customer care", "CSKH"))}</span>` },
          ],
        }),
      })}`;
    },
    mount(root) {
      const user = me();
      delegate(root, {
        "edit-profile": () => ui.formModal({
          title: t("Edit profile", "Sửa hồ sơ"),
          fields: [
            { name: "full_name", label: t("Full name", "Họ và tên"), value: user.full_name, required: true },
            { name: "email", label: t("E-mail (optional)", "E-mail (không bắt buộc)"), type: "email", value: user.email || "" },
          ],
          onSubmit(v) {
            if (v.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v.email)) return t("The e-mail address is not valid.", "Địa chỉ e-mail không hợp lệ.");
            const fields = [];
            if (v.full_name !== user.full_name) fields.push(["full_name", user.full_name, v.full_name]);
            if ((v.email || null) !== user.email) fields.push(["email", user.email, v.email || null]);
            if (!fields.length) return t("Nothing changed.", "Không có thay đổi.");
            user.full_name = v.full_name;
            user.email = v.email || null;
            userChanges.push({ at: nowIso(), by: user.user_id, user_id: user.user_id, fields, reason: t("Edited by the user", "Người dùng tự sửa") });
            G3.refreshChrome();
            done(t("Profile saved", "Đã lưu hồ sơ"));
          },
        }),
        password: () => changePassword(),
        phone: () => changePhone(user),
        "push-on": () => { G3.store.set("g3.push.registered", true); done(t("This browser now receives push notifications", "Trình duyệt này đã nhận thông báo đẩy")); },
        "push-off": () => { G3.store.set("g3.push.registered", false); done(t("Push token removed for this browser", "Đã xóa token thông báo đẩy của trình duyệt này")); },
        "logout-one": (el) => { const i = db.login_sessions.findIndex((s) => s.session_id === el.dataset.id); if (i >= 0) db.login_sessions.splice(i, 1); done(t("Device logged out", "Đã đăng xuất thiết bị")); },
        "logout-all": () => { db.login_sessions.splice(0, db.login_sessions.length, ...db.login_sessions.filter((s) => s.current)); done(t("Every other device was logged out", "Đã đăng xuất mọi thiết bị khác")); },
        "download-mine": () => {
          G3.audit("EXPORT", "OWN_DATA", user.full_name, t("Data-subject request by the person", "Chủ thể dữ liệu tự yêu cầu"));
          const rows = [
            ["full_name", user.full_name], ["phone_number", user.phone_number], ["email", user.email || ""], ["created_at", user.created_at],
            ...db.memberships.filter((m) => m.user_id === user.user_id).map((m) => [`membership:${db.org(m.organization_id).short_name}`, `${m.roles.join(" ")} · ${m.status}`]),
            ...loginHistory(user).slice(0, 20).map((h) => [`login:${h.occurred_at}`, `${h.action} ${h.ip_address} ${h.device}`]),
          ];
          ui.downloadCsv("my-data.csv", [{ label: "field", csv: (r) => r[0] }, { label: "value", csv: (r) => r[1] }], rows);
        },
        "new-request": () => ui.formModal({
          title: t("New data request", "Yêu cầu mới về dữ liệu"),
          intro: `<p class="small muted">${esc(t("Customer care handles it and tracks it against its deadline. Data the law requires us to keep (invoices, audit log) is anonymized rather than deleted.", "CSKH xử lý và theo dõi theo hạn. Dữ liệu luật yêu cầu lưu giữ (hóa đơn, nhật ký truy cập) được ẩn danh thay vì xóa."))}</p>`,
          fields: [
            { name: "kind", label: t("Request", "Loại yêu cầu"), type: "select", options: [["EXPORT_REQUEST", lbl("EXPORT_REQUEST")], ["CORRECTION", lbl("CORRECTION")], ["DELETION", lbl("DELETION")]] },
            { name: "details", label: t("Details", "Nội dung"), type: "textarea", required: true },
          ],
          submitLabel: t("Send request", "Gửi yêu cầu"),
          onSubmit(v) {
            const now = Date.now();
            requestsOf(user).push({ request_id: `DSR-${(hashOf(user.user_id + now) % 900) + 100}`, kind: v.kind, details: v.details, status: "RECEIVED", submitted_at: new Date(now).toISOString(), due_at: new Date(now + DSR_DAYS * DAY).toISOString(), completed_at: null, handled_by: null });
            done(t("Request sent to customer care", "Đã gửi yêu cầu tới CSKH"));
          },
        }),
      });
    },
  });
})();
