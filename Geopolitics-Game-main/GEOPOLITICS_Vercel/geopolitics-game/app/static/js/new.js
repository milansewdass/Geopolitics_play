"use strict";
(async function () {
  const list = $("clist"), detail = $("detail"), q = $("q");
  let countries = [], chosen = null, source = null;
  const render = () => {
    const term = q.value.trim().toLowerCase();
    const rows = countries.filter(c => c.name.toLowerCase().includes(term));
    list.replaceChildren(...(rows.length ? rows.map(c => el("button", {
      type: "button", class: "crow", role: "option", "aria-selected": String(chosen && chosen.code === c.code),
      onclick: () => { chosen = c; render(); showDetail(); }
    }, el("span", { class: "fl" }, c.flag), el("span", {}, c.name),
       el("span", { class: "muted small" }, `${(c.population / 1e6).toFixed(1)}m`), el("span", { class: "sol" }, String(c.soldiers))))
      : [el("div", { class: "skeleton" }, "No country matches your search.")]));
  };
  function showDetail() {
    if (!chosen) return;
    detail.replaceChildren(
      el("div", { class: "bigflag" }, chosen.flag), el("h2", {}, chosen.name),
      el("div", { class: "stats4" },
        el("div", { class: "stat" }, el("b", {}, fmt(chosen.population)), el("span", {}, "Population")),
        el("div", { class: "stat" }, el("b", {}, String(chosen.soldiers)), el("span", {}, "Starting soldiers"))),
      el("p", { class: "muted small" }, source ? `Population: ${source.source}, retrieved ${source.retrieved}.` : ""),
      el("p", {}, `Nine other nations will be drawn at random. You will command ${chosen.name}.`),
      el("button", { class: "btn primary block", id: "go", type: "button", onclick: begin }, "Begin campaign"),
      el("div", { class: "msg", id: "newmsg", role: "alert" }));
  }
  async function begin() {
    const b = $("go"); b.disabled = true; $("newmsg").textContent = "";
    try { const r = await api("/games", "POST", { nation: chosen.code }); location.href = "/game/" + r.state.id; }
    catch (e) { $("newmsg").textContent = e.message; $("newmsg").className = "msg bad"; b.disabled = false; }
  }
  q.addEventListener("input", render);
  try {
    const r = await api("/countries"); source = r.source;
    countries = r.countries.slice().sort((a, b) => a.name.localeCompare(b.name)); render();
  } catch (e) { list.replaceChildren(el("div", { class: "skeleton" }, "Could not load the country list: " + e.message)); }
})();
