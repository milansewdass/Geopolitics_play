"use strict";
(async function () {
  const LABELS = {
    new_game: ["New-game confirmation", "An e-mail each time you start a campaign."],
    game_finished: ["Game-finished summary", "The result when a campaign ends."],
    security_alerts: ["Security alerts", "Important notices about your account."],
  };
  const box = $("prefs");
  async function load() {
    try { draw(await api("/notifications")); }
    catch (e) { box.replaceChildren(el("div", { class: "skeleton" }, "Could not load preferences: " + e.message)); }
  }
  function draw(d) {
    const rows = Object.entries(LABELS).map(([k, [t, sub]]) => el("label", { class: "toggle" },
      el("span", {}, el("strong", {}, t), el("div", { class: "muted small" }, sub)),
      el("input", { type: "checkbox", checked: d.preferences[k], "aria-label": t, onchange: async e => {
        try { draw(await api("/notifications", "PUT", { [k]: e.target.checked })); }
        catch (err) { alert(err.message); load(); } } })));
    box.replaceChildren(el("div", { class: "eyebrow" }, "E-mail preferences"),
      el("p", { class: "muted small" }, `Sending to ${d.email || "(no address)"} · ${d.email_verified ? "address confirmed" : "address not yet confirmed"}`),
      ...rows);
    const dv = $("deliveries");
    dv.replaceChildren(...(d.deliveries.length ? d.deliveries.map(j => el("div", { class: "rrow" },
      el("span", {}, j.template.replace(/_/g, " ")), el("span", { class: "muted small" }, `${j.status} · ${new Date(j.created_at).toLocaleString()}`)))
      : [el("p", { class: "muted" }, "No e-mails have been sent yet.")]));
  }
  load();
})();
