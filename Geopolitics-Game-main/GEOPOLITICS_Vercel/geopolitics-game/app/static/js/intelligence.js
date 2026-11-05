"use strict";
(async function () {
  const app = $("app"), mode = app.dataset.mode, ref = app.dataset.ref;
  const root = mode === "play" ? `/games/${ref}` : `/watch/${ref}`;
  let S = null, replayNo = null, timer = null;

  async function refresh() {
    try {
      const [st, ev, bt] = await Promise.all([api(root), api(`${root}/events?after=0&limit=1000`), api(`${root}/battles`)]);
      S = st.state; draw(ev.events, bt.battles);
    } catch (e) { $("bars").replaceChildren(el("div", { class: "skeleton" }, "Could not load intelligence: " + e.message)); }
  }

  function draw(events, battles) {
    const hid = S.human_id, owned = {};
    for (const t of S.territories) (owned[t.owner] = owned[t.owner] || []).push(t);
    const totals = S.players.map(p => ({ p, ts: owned[p.id] || [], sol: (owned[p.id] || []).reduce((a, t) => a + t.soldiers, 0) }));
    const max = Math.max(1, ...totals.map(x => x.sol));
    $("sub").textContent = `Round ${S.round} · ${S.status === "finished" ? "campaign finished" : S.current_player + " to move"}`;

    $("bars").replaceChildren(...totals.slice().sort((a, b) => b.sol - a.sol).map(x => el("div", { class: "bar" },
      el("span", {}, x.p.name + (x.p.id === hid ? " (you)" : "")),
      el("div", { class: "track" }, el("div", { class: "fill", style: `width:${(100 * x.sol / max).toFixed(1)}%;background:${ownerColor(x.p.id, hid)}` })),
      el("b", {}, String(x.sol)))));

    $("nations").replaceChildren(...totals.map(x => {
      const top = x.ts.slice().sort((a, b) => b.soldiers - a.soldiers)[0];
      return el("div", { class: "rrow" + (x.p.eliminated ? " dead" : "") },
        el("span", {}, el("i", { class: "dot", style: `background:${ownerColor(x.p.id, hid)}` }), x.p.name + (x.p.id === hid ? " (you)" : "")),
        el("span", { class: "muted small" }, x.p.eliminated ? "eliminated" :
          `${x.ts.length} ${x.ts.length === 1 ? "country" : "countries"} · ${x.sol} ${x.sol === 1 ? "soldier" : "soldiers"} · strongest ${top.name} (${top.soldiers})`));
    }));

    const head = el("tr", {}, ...["Country", "Controlled by", "Soldiers", "Original value", "Population"].map(h => el("th", {}, h)));
    const rows = S.territories.slice().sort((a, b) => b.soldiers - a.soldiers).map(t => el("tr", {},
      el("td", {}, `${t.flag} ${t.name}`), el("td", {}, S.players[t.owner].name), el("td", {}, String(t.soldiers)),
      el("td", {}, String(t.base_soldiers)), el("td", {}, fmt(t.population))));
    $("status").replaceChildren(head, ...rows);

    const feed = $("feed"), atEnd = feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 30;
    feed.replaceChildren(...(events.length ? events.map(e => feedItem(e, hid)) : [el("div", { class: "skeleton" }, "No events yet.")]));
    if (atEnd) feed.scrollTop = feed.scrollHeight;

    $("battles").replaceChildren(...(battles.length ? battles.map(b => el("div", { class: "rrow" },
      el("span", {}, `#${b.battle_no} · R${b.round} · ${b.attacker} → ${b.to}`,
        el("div", { class: "muted small" }, `${b.att_start} v ${b.def_start} · ${b.outcome} · lost ${b.att_lost}/${b.def_lost}`)),
      el("button", { class: "btn small", type: "button", onclick: () => openReplay(b.battle_no) }, "Replay")))
      : [el("p", { class: "muted" }, "No battles have been fought yet.")]));
  }

  async function openReplay(no) {
    replayNo = no; const box = $("replay"); box.hidden = false;
    let data;
    try { data = await api(`${root}/battles/${no}`); } catch (e) { box.replaceChildren(el("p", { class: "error" }, e.message)); return; }
    const b = data.battle; let i = 0;
    const view = el("div"); const ctl = el("div", { class: "btns" });
    const show = () => {
      const ph = b.phases[i];
      view.replaceChildren(
        el("div", { class: "names" }, el("div", { class: "side" }, el("div", { class: "eyebrow" }, "Attacker"), el("div", { class: "nm" }, b.from)),
          el("div", { class: "vs" }, "VS"), el("div", { class: "side r" }, el("div", { class: "eyebrow" }, "Defender"), el("div", { class: "nm" }, b.to))),
        ph ? pairChips(ph) : el("p", { class: "muted" }, "No dice were rolled."),
        ph ? el("div", { class: "pairtxt" }, pairText(ph)) : null,
        ph ? el("div", { class: "result" }, `Phase ${ph.no} of ${b.phases.length}: ${phaseSummary(ph)} Now ${ph.att_after} v ${ph.def_after}.`) : null,
        i === b.phases.length - 1 ? el("div", { class: "result" }, { conquest: `Conquest — ${b.to} garrisoned with ${b.gain} soldiers.`,
          exhausted: "Attack stalled; the defender held.", withdrawn: "The attacker stopped the attack." }[b.outcome]) : null,
        b.commentary && b.commentary.text ? el("div", { class: "commentary" }, el("p", {}, b.commentary.text,
          el("span", { class: "src" }, b.commentary.source === "ai" ? "AI commentary" : "Automated summary (AI unavailable)"))) : null);
      prev.disabled = i <= 0; next.disabled = i >= b.phases.length - 1;
    };
    const prev = el("button", { class: "btn small", type: "button", onclick: () => { i--; show(); } }, "◀ Previous phase");
    const next = el("button", { class: "btn small", type: "button", onclick: () => { i++; show(); } }, "Next phase ▶");
    const close = el("button", { class: "btn small", type: "button", onclick: () => { box.hidden = true; replayNo = null; } }, "Close");
    ctl.append(prev, next, close);
    box.replaceChildren(el("div", { class: "eyebrow" }, `Battle #${no} replay · round ${b.round}`), view, ctl);
    show();
  }

  await refresh();
  timer = setInterval(() => { if (!document.hidden && replayNo === null) refresh(); }, 6000);
})();
