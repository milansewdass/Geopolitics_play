"use strict";
(async function () {
  const panel = $("panel"); let tab = "users";
  const cell = v => v === true ? "yes" : v === false ? "no" : v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
  function table(rows, cols, actions) {
    if (!rows.length) return el("p", { class: "muted" }, "Nothing to show.");
    cols = cols || Object.keys(rows[0]).filter(k => k !== "_id");
    return el("div", { style: "overflow:auto" }, el("table", {},
      el("tr", {}, ...cols.map(c => el("th", {}, c.replace(/_/g, " "))), actions ? el("th", {}, "") : null),
      ...rows.map(r => el("tr", {}, ...cols.map(c => el("td", {}, cell(r[c]))), actions ? el("td", {}, actions(r)) : null))));
  }
  const stat = (v, l) => el("div", { class: "stat" }, el("b", {}, String(v)), el("span", {}, l));
  async function overview() {
    const o = await api("/admin/overview"); const e = o.email || {};
    $("ovr").replaceChildren(stat(o.users, "Registered users"), stat(o.games_active, "Live games"), stat(o.games_completed, "Completed games"),
      stat(o.errors_24h, "Errors (24h)"), stat(e.sent || 0, "E-mails sent"), stat((e.failed || 0) + (e.retrying || 0), "E-mails failing"));
    return o;
  }
  const views = {
    async users() {
      const q = el("input", { type: "search", placeholder: "Search username or e-mail", "aria-label": "Search users" });
      const out = el("div");
      const run = async () => {
        const rows = await api("/admin/users?q=" + encodeURIComponent(q.value));
        out.replaceChildren(table(rows, ["username", "email", "role", "email_verified", "disabled", "failed_logins", "created_at"], u =>
          u.role === "admin" ? null : el("button", { class: "btn small", type: "button", onclick: async () => {
            await api(`/admin/users/${u.id}/disable`, "POST", { disabled: !u.disabled }); run(); } }, u.disabled ? "Enable" : "Disable")));
      };
      q.addEventListener("input", run); await run(); return [q, out];
    },
    async games() {
      const sel = el("select", { "aria-label": "Filter by status" }, ...["", "active", "finished"].map(v => el("option", { value: v }, v || "All games")));
      const out = el("div");
      const run = async () => {
        const rows = await api("/admin/games" + (sel.value ? "?status=" + sel.value : ""));
        out.replaceChildren(table(rows, ["owner", "human", "status", "round", "share_code", "updated_at"], g =>
          el("button", { class: "btn small danger", type: "button", onclick: async () => {
            if (confirm("Delete this game permanently? This is logged.")) { await api("/admin/games/" + g.id, "DELETE"); run(); overview(); } } }, "Delete")));
      };
      sel.addEventListener("change", run); await run(); return [sel, out];
    },
    async emails() {
      const rows = await api("/admin/emails");
      const run = el("button", { class: "btn small", type: "button", onclick: async () => { const r = await api("/admin/emails/process", "POST", {}); alert(`Processed ${r.processed} job(s)`); show(); } }, "Process due jobs");
      return [run, table(rows, ["template", "to", "status", "attempts", "last_error", "created_at"], j =>
        j.status === "failed" ? el("button", { class: "btn small", type: "button", onclick: async () => { await api(`/admin/emails/${j.id}/retry`, "POST", {}); show(); } }, "Retry") : null)];
    },
    async errors() { return [table(await api("/admin/errors"))]; },
    async audit() { return [table(await api("/admin/audit"))]; },
    async system() {
      const o = await api("/admin/overview");
      return [el("div", { class: "eyebrow" }, "Database"), table([o.database], ["mode", "database", "server_version", "collections"]),
        el("p", {}, `E-mail provider configured: ${o.email_provider_configured ? "yes" : "no"}`),
        el("div", { class: "eyebrow" }, "Abuse indicators"), table(o.abuse_indicators)];
    },
  };
  async function show() {
    panel.replaceChildren(el("div", { class: "skeleton" }, "Loading…"));
    try { panel.replaceChildren(...await views[tab]()); }
    catch (e) { panel.replaceChildren(el("p", { class: "error" }, e.message)); }
  }
  $("tabs").addEventListener("click", ev => {
    const b = ev.target.closest("button[data-t]"); if (!b) return;
    tab = b.dataset.t; for (const x of $("tabs").children) x.setAttribute("aria-selected", String(x === b)); show();
  });
  try { await overview(); } catch (e) { panel.textContent = e.message; return; }
  show();
})();
