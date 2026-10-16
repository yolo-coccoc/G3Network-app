/* Feature coverage: every catalog feature with a portal surface, the prototype
   screen(s) that show it, and its build status from the catalog snapshot. */

(function () {
  const { ui } = G3;
  const esc = G3.esc;

  function screensFor(code) {
    return G3.pages.filter((p) => (p.features || []).includes(code));
  }

  G3.page({
    id: "coverage",
    path: "/coverage",
    title: ["Feature coverage", "Độ phủ tính năng"],
    roles: "*",
    features: [],
    nav: { group: "prototype", order: 1, icon: "grid" },
    render({ query }) {
      const snapshot = window.G3_FEATURES || { features: [], domains: {} };
      const all = snapshot.features;
      const covered = all.filter((f) => screensFor(f.code).length);
      const domain = query.domain || "";
      const show = query.show || "all";
      const rows = all.filter((f) => (!domain || f.code.startsWith(domain + "-")) && (show === "all" || (show === "covered" ? screensFor(f.code).length : !screensFor(f.code).length)));
      const byRelease = ["P1.0", "P1.1", "P1.5", "P2"].map((r) => {
        const inRelease = all.filter((f) => f.release === r);
        return [r, inRelease.filter((f) => screensFor(f.code).length).length, inRelease.length];
      });
      return `${ui.pageHead({
        title: t("Feature coverage", "Độ phủ tính năng"),
        sub: esc(t(`Portal features of the catalog (docs/product/features/features.yaml, snapshot ${snapshot.taken_on}) and the prototype screens that show them. Chips on every screen use the same data: green = backend done, amber = doing, grey = todo.`, `Các tính năng portal trong danh mục (docs/product/features/features.yaml, bản chụp ${snapshot.taken_on}) và màn hình bản mẫu thể hiện chúng. Nhãn trên mỗi màn hình dùng cùng dữ liệu: xanh = backend xong, vàng = đang làm, xám = chưa làm.`)),
      })}
      <div class="grid cols-5">
        ${ui.kpi({ label: t("Portal features", "Tính năng portal"), value: all.length })}
        ${ui.kpi({ label: t("Shown in the prototype", "Có trong bản mẫu"), value: covered.length, foot: ui.meter((covered.length / all.length) * 100) })}
        ${byRelease.map(([r, c, n]) => ui.kpi({ label: `${t("Release", "Phát hành")} ${r}`, value: `${c}<small>/ ${n}</small>` })).slice(0, 3).join("")}
      </div>
      <div class="card mt">
        <div class="toolbar">
          ${ui.select("domain", [["", t("All domains", "Mọi nhóm")], ...Object.entries(snapshot.domains).map(([k, v]) => [k, `${k} — ${t(v[0], v[1])}`])], domain)}
          ${ui.segmented([["all", t("All", "Tất cả")], ["covered", t("Shown", "Đã có")], ["missing", t("Not shown", "Chưa có")]], show, "show")}
          <span class="grow"></span><span class="muted small">${rows.length} ${esc(t("features", "tính năng"))}</span>
        </div>
        ${ui.table({
          rows,
          columns: [
            { label: t("Code", "Mã"), render: (f) => `<span class="mono">${esc(f.code)}</span>` },
            { label: t("Feature", "Tính năng"), render: (f) => `<b>${esc(t(f.name, f.name_vi))}</b><span class="sub">${esc(t(f.what, f.what_vi || f.what))}</span>` },
            { label: t("Users", "Người dùng"), render: (f) => f.users.filter((u) => u !== "SYSTEM").map((u) => ui.tag(G3.roleLabel(u))).join(" ") },
            { label: t("Release", "Phát hành"), render: (f) => `${esc(f.release)}<span class="sub">${esc(f.priority)}</span>` },
            { label: "Backend", render: (f) => ui.badge(f.backend === "done" ? "COMPLETED" : f.backend === "doing" ? "IN_PROGRESS" : "PLANNED", null, f.backend) },
            { label: t("Prototype screen", "Màn hình bản mẫu"), render: (f) => screensFor(f.code).map((p) => (p.path.includes(":") ? ui.tag(t(p.title[0], p.title[1])) : `<a href="#${p.path}">${esc(t(p.title[0], p.title[1]))}</a>`)).join("<br>") || '<span class="muted">—</span>' },
          ],
        })}
      </div>`;
    },
    mount(root) {
      root.querySelector("[data-filter=domain]").addEventListener("change", (e) => G3.setQuery({ domain: e.target.value }));
      root.querySelectorAll("[data-seg=show] button").forEach((b) => b.addEventListener("click", () => G3.setQuery({ show: b.dataset.value })));
    },
  });
})();
