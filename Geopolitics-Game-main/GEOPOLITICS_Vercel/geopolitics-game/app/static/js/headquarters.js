"use strict";
(async function () {
  const box = $("camps");
  const stat = (v, l) => el("div", { class: "stat" }, el("b", {}, String(v)), el("span", {}, l));
  async function load() {
    try {
      const [games, s] = await Promise.all([api("/games"), api("/stats")]);
      const live = games.filter(g => g.status === "active").length;
      $("hqstats").replaceChildren(stat(games.length, "Campaigns"), stat(live, "In progress"),
        stat(games.filter(g => g.won).length, "Won"), stat(games.filter(g => g.status === "finished" && !g.won).length, "Lost"));
      $("career").replaceChildren(stat(s.battles_won, "Battles won"), stat(s.battles_lost, "Battles lost"),
        stat(s.territories_captured, "Countries captured"), stat(s.soldiers_lost, "Soldiers lost"), stat(s.soldiers_killed, "Soldiers destroyed"));
      box.replaceChildren(...(games.length ? games.map(camp) : [el("div", { class: "card" },
        el("p", { class: "muted" }, "No campaigns yet."), el("a", { class: "btn primary", href: "/new" }, "Start your first campaign"))]));
    } catch (e) { box.replaceChildren(el("div", { class: "card" }, "Could not load campaigns: " + e.message)); }
  }
  function camp(g) {
    const status = g.status === "finished" ? (g.won ? "Victory" : "Defeat") : g.eliminated ? "Eliminated" : "In progress";
    return el("div", { class: "camp" + (g.status === "active" ? " live" : "") },
      el("div", {}, el("strong", {}, g.nation), " ", el("span", { class: "tag" + (g.won ? " win" : "") }, status),
        el("div", { class: "muted small" }, `Round ${g.round} · ${g.controlled} of ${g.nations} countries · ${g.soldiers} soldiers`),
        el("div", { class: "muted small" }, `Spectator link: ${location.origin}/watch/${g.share_code}`)),
      el("div", { class: "row" },
        el("a", { class: "btn primary small", href: "/game/" + g.id }, g.status === "active" ? "Continue" : "View"),
        el("a", { class: "btn small", href: "/intelligence/" + g.id }, "Intelligence"),
        el("button", { class: "btn small danger", type: "button", "aria-label": `Delete ${g.nation} campaign`, onclick: async () => {
          if (!confirm(`Delete the ${g.nation} campaign permanently?`)) return;
          try { await api("/games/" + g.id, "DELETE"); } catch (e) { alert(e.message); }
          load();
        } }, "Delete")));
  }
  load();
})();
