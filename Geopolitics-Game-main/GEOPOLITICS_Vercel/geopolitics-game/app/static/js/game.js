"use strict";
/* War Room: owner (play) and public spectator (watch) share this script. Every number shown comes from the
   server's persisted state or events -- nothing is invented in the browser. Playback only paces how the
   already-decided events are revealed. */
(async function () {
  const app = $("app"), mode = app.dataset.mode, ref = app.dataset.ref;
  const root = mode === "play" ? `/games/${ref}` : `/watch/${ref}`;
  let S = null, D = [], map = null, view = "map";
  let sel = { from: null, to: null, info: null }, busy = false, playing = false, fast = false, skipping = false;
  let wz = { action: null };                       // step wizard: country -> action -> target
  let msg = "", msgBad = false, placeN = 1, tf = { from: "", to: "", count: 1 };
  let lastSeq = 0, B = null;                       // B = battle panel model
  const setMsg = (t, bad = false) => { msg = t; msgBad = bad; };
  const byCode = c => D.find(t => t.code === c);
  const hid = () => S.human_id;
  const pname = id => S.players[id].name;
  const clone = x => JSON.parse(JSON.stringify(x));

  // ------------------------------------------------------------------ loading
  let world;
  try {
    const [st, w] = await Promise.all([api(root), fetch("/static/data/world.json").then(r => r.json())]);
    S = st.state; world = w;
  } catch (e) { app.replaceChildren(el("div", { class: "card" }, el("p", { class: "error" }, "Could not load this game: " + e.message))); return; }
  D = clone(S.territories); lastSeq = S.event_seq;
  map = new WorldMap($("mapwrap"), world, onPick);
  for (const ev of S.events) $("feed").append(feedItem(ev, hid()));
  $("feed").scrollTop = $("feed").scrollHeight;
  if (S.battle) B = battleFromState(S.battle);
  $("vmap").onclick = () => setView("map"); $("vgrid").onclick = () => setView("grid");
  $("speed").onclick = () => { fast = !fast; $("speed").setAttribute("aria-pressed", String(fast)); };
  renderAll();
  setInterval(() => { if (!document.hidden && !playing && !busy) sync(); }, mode === "watch" ? 3000 : 8000);

  function setView(v) {
    view = v; $("mapwrap").hidden = v !== "map"; $("gridwrap").hidden = v !== "grid";
    $("vmap").setAttribute("aria-pressed", String(v === "map")); $("vgrid").setAttribute("aria-pressed", String(v === "grid")); drawBoard();
  }

  // ------------------------------------------------------------------ state sync + playback
  async function sync() {
    try {
      const st = (await api(root)).state;
      if (st.event_seq > lastSeq) {
        const ev = (await api(`${root}/events?after=${lastSeq}&limit=1000`)).events;
        await playback(ev, st);
      } else { S.commentary = st.commentary; S.version = st.version; renderCommentary(); renderBattle(); }
    } catch (_) { /* transient network issue: the next poll retries */ }
  }

  const OWN = { attack: 250, phase: 900, conquest: 700, elimination: 700, victory: 900, withdraw: 300, exhausted: 500 };
  const DELAY = { turn: 450, attack: 900, phase: 1500, conquest: 1300, elimination: 1100, victory: 1500, withdraw: 900, exhausted: 900 };
  async function playback(events, finalState, own = false) {
    const fresh = events.filter(e => e.seq > lastSeq);
    S = finalState; playing = true; skipping = false;
    if (fresh.length > 3) showSkip();
    renderCmd();
    for (const ev of fresh) {
      lastSeq = ev.seq;
      if (ev.delta) for (const [c, v] of Object.entries(ev.delta)) { const t = byCode(c); if (t) { t.owner = v.owner; t.soldiers = v.soldiers; } }
      addFeed(ev); battleFromEvent(ev); drawBoard(); renderRoster();
      if (!skipping) await sleep(fast ? (ev.type === "phase" ? 260 : 60) : (own ? (OWN[ev.type] ?? 150) : (DELAY[ev.type] || 600)));
    }
    lastSeq = Math.max(lastSeq, finalState.event_seq);
    D = clone(S.territories); playing = false; hideSkip();
    if (S.battle) B = battleFromState(S.battle); else if (B && B.active) B.active = false;
    renderAll();
    if (fresh.some(e => e.type === "conquest" || e.type === "withdraw" || e.type === "exhausted" || e.type === "phase")) scheduleCommentarySync();
  }
  function showSkip() {
    if ($("skipbtn")) return;
    $("speed").after(el("button", { id: "skipbtn", class: "btn small", type: "button", onclick: () => { skipping = true; } }, "Skip animation"));
  }
  function hideSkip() { const b = $("skipbtn"); if (b) b.remove(); }
  function addFeed(ev) {
    const f = $("feed"); f.append(feedItem(ev, hid()));
    while (f.children.length > 2000) f.firstChild.remove();
    f.scrollTop = f.scrollHeight;
  }
  function scheduleCommentarySync() { for (const ms of [2500, 6000, 12000]) setTimeout(() => { if (!playing && !busy) sync(); }, ms); }

  // ------------------------------------------------------------------ battle panel model
  function battleFromState(b) {
    const last = b.phases[b.phases.length - 1] || null;
    return { active: true, no: b.no, from: b.from, to: b.to, attacker: b.attacker, defender: b.defender,
      a: last ? last.att_after : b.att_start, d: last ? last.def_after : b.def_start, phase: last, result: "", plan: !last };
  }
  function battleFromEvent(ev) {
    const d = ev.data || {};
    if (ev.type === "attack") {
      const a = byCode(d.from), t = byCode(d.to);
      B = { active: true, no: d.battle_no, from: d.from, to: d.to, attacker: d.attacker, defender: d.defender, a: a.soldiers, d: t.soldiers, phase: null, result: "", plan: true };
    } else if (ev.type === "phase" && B) {
      B.phase = d; B.a = d.att_after; B.d = d.def_after; B.plan = false;
    } else if (B && (ev.type === "conquest" || ev.type === "withdraw" || ev.type === "exhausted")) {
      B.result = ev.text; B.active = false; B.ended = true;
      if (ev.type === "conquest") B.d = byCode(B.to).soldiers;
    } else if (ev.type === "turn" && B && !(S.battle)) { B = null; }
    renderBattle();
  }
  function renderBattle() {
    const box = $("battle");
    if (!B) { box.hidden = true; return; }
    box.hidden = false;
    const f = byCode(B.from), t = byCode(B.to);
    const live = B.active && S.battle && S.can_act && !playing && !busy;
    const names = el("div", { class: "names" },
      el("div", { class: "side" }, el("div", { class: "eyebrow" }, "Attacker · " + pname(B.attacker)), el("div", { class: "nm" }, f.name), el("span", { class: "sol" }, String(B.a))),
      el("div", { class: "vs" }, "VS"),
      el("div", { class: "side r" }, el("div", { class: "eyebrow" }, "Defender · " + pname(B.defender)), el("div", { class: "nm" }, t.name), el("span", { class: "sol" }, String(B.d))));
    const body = [el("div", { class: "eyebrow" }, `Battle #${B.no}`), names];
    if (B.phase) {
      body.push(pairChips(B.phase, playing && !skipping), el("div", { class: "pairtxt" }, pairText(B.phase)),
        el("div", { class: "result" }, `Phase ${B.phase.no}: ${phaseSummary(B.phase)} Soldiers now ${B.phase.att_after} v ${B.phase.def_after}.`));
    } else body.push(el("p", { class: "muted", style: "text-align:center" }, `Awaiting the first phase: both sides will roll ${Math.min(B.a - 1, B.d)} ${Math.min(B.a - 1, B.d) === 1 ? "die" : "dice"}.`));
    if (B.result) body.push(el("div", { class: "result", style: "color:var(--gold)" }, B.result));
    const liveLine = (S.commentary || []).filter(c => c.battle_no === B.no && c.live && B.phase && c.phase === B.phase.no).pop();
    if (liveLine) body.push(el("p", { class: "livecomm" }, liveLine.text,
      el("span", { class: "src" }, liveLine.source === "ai" ? " · AI live commentary" : " · Automated summary (AI unavailable)")));
    if (live) body.push(el("div", { class: "btns" },
      el("button", { class: "btn primary", type: "button", onclick: () => act("/attack/roll", { blitz: false }) }, "Continue Attack"),
      el("button", { class: "btn", type: "button", onclick: () => act("/attack/roll", { blitz: true }) }, "Roll to the end"),
      el("button", { class: "btn danger", type: "button", onclick: () => { if (confirm("Stop the attack? This also ends your turn.")) act("/attack/stop"); } }, "Stop Attack")));
    box.replaceChildren(...body);
  }

  // ------------------------------------------------------------------ actions
  async function act(path, body = {}) {
    if (busy || playing) return;
    busy = true; setMsg(""); renderCmd(); renderBattle();
    try {
      const r = await api(`${root}${path}`, "POST", { ...body, version: S.version });
      const out = (r.result || {}).outcome;
      if (path === "/attack/start") { sel = { from: null, to: null, info: null }; wz.action = null; }
      await playback(r.events, r.state, path !== "/end-turn" && path !== "/skip" && path !== "/attack/stop" && path !== "/fast-forward");
      if (out === "conquest") { setMsg("Conquest! New soldiers were added to the captured country."); sel = { from: null, to: null, info: null }; wz.action = null; }
      else if (out === "exhausted") setMsg("Your attackers were stalled at one soldier.", true);
      else if (out === "withdrawn") setMsg("You stopped the attack.");
      else if (path === "/fast-forward") setMsg(`Played ${(r.result || {}).turns_played} computer turn(s).`);
      if (path === "/end-turn" || path === "/skip" || path === "/attack/stop") { sel = { from: null, to: null, info: null }; wz.action = null; }
    } catch (e) {
      setMsg(e.message, true);
      if (e.status === 409 || e.status === 400) { try { await sync(); } catch (_) { } }
    }
    busy = false; renderAll();
  }

  // ------------------------------------------------------------------ picking
  function onPick(code) {
    const t = byCode(code); if (!t) return;
    sel.info = code;
    if (mode === "play" && S.can_act && !playing && !busy && !S.battle) {
      const mine = t.owner === hid();
      if (S.pending > 0) { if (mine) sel.from = code; else setMsg("Place reinforcements on one of your own countries.", true); }
      else if (wz.action === "move" && mine && sel.from && code !== sel.from) { sel.to = code; setMsg(""); }
      else if (mine) {
        if (t.soldiers < 2) setMsg(`${t.name} needs at least 2 soldiers to launch an attack.`, true);
        else if (t.captured_turn === S.turn_no) setMsg(`${t.name} was conquered this turn and can launch from your next turn.`, true);
        else { sel.from = code; sel.to = null; wz.action = null; setMsg(""); }
      } else {
        if (wz.action === "attack" && sel.from) { sel.to = code; setMsg(""); }
        else setMsg("Step 2 first: choose an action, or pick the target from the prompt.", true);
      }
    }
    drawBoard(); renderCmd();
  }

  // ------------------------------------------------------------------ rendering
  function renderAll() { renderBanner(); drawBoard(); renderRoster(); renderCommentary(); renderCmd(); renderBattle(); renderShare(); }

  function renderBanner() {
    const b = $("banner"); b.hidden = true; b.className = "banner";
    if (S.status === "finished") {
      b.hidden = false;
      if (S.winner === hid()) b.textContent = "Victory. You control all ten countries.";
      else { b.textContent = `${S.winner_name} controls all ten countries. The campaign is lost.`; b.classList.add("bad"); }
    } else if (S.human_eliminated) {
      b.hidden = false; b.classList.add("bad");
      b.textContent = mode === "play" ? "You have been eliminated. You can fast-forward to watch the rest of the campaign." : "The human player has been eliminated.";
    }
  }

  function drawBoard() {
    const selFrom = (B && B.active && B.from) || sel.from, selTo = (B && B.active && B.to) || sel.to;
    $("roundlabel").textContent = `Round ${S.round}`;
    $("nationcount").textContent = String(D.length);
    map.update(D, { colorOf: t => ownerColor(t.owner, hid()), selFrom, selTo, humanId: hid(), ownerName: t => pname(t.owner) });
    const g = $("gridwrap");
    if (view === "grid") g.replaceChildren(...D.map(t => el("button", {
      type: "button", class: "gcard" + (selFrom === t.code ? " from" : "") + (selTo === t.code ? " to" : ""),
      style: `--oc:${ownerColor(t.owner, hid())}`, "aria-label": `${t.name}, ${t.soldiers} soldiers, ${pname(t.owner)}`, onclick: () => onPick(t.code) },
      el("b", {}, `${t.flag} ${t.name}`), el("span", { class: "s" }, String(t.soldiers)),
      el("span", {}, pname(t.owner) + (t.owner === hid() ? " (you)" : "")), el("span", {}, `original value ${t.base_soldiers}`))));
    $("legend").replaceChildren(...S.players.map(p => el("span", {}, el("i", { style: `background:${ownerColor(p.id, hid())}` }), p.name + (p.id === hid() ? " (you)" : ""))));
  }

  function renderRoster() {
    const counts = {}, sol = {};
    for (const t of D) { counts[t.owner] = (counts[t.owner] || 0) + 1; sol[t.owner] = (sol[t.owner] || 0) + t.soldiers; }
    $("roster").replaceChildren(...S.players.map(p => el("div", { class: "rrow" + ((p.eliminated || !counts[p.id]) ? " dead" : "") + (S.turn === p.id && S.status === "active" ? " turn" : "") },
      el("span", {}, el("i", { class: "dot", style: `background:${ownerColor(p.id, hid())}` }), p.name + (p.id === hid() ? " (you)" : "")),
      el("span", { class: "muted small" }, counts[p.id] ? `${counts[p.id]} ${counts[p.id] === 1 ? "country" : "countries"} · ${sol[p.id]} ${sol[p.id] === 1 ? "soldier" : "soldiers"}` : "eliminated"))));
  }

  function renderCommentary() {
    const box = $("commentary");
    const items = S.commentary.slice().reverse();
    box.replaceChildren(...(items.length ? items.map(c => el("p", {}, c.text,
      el("span", { class: "src" }, `Battle #${c.battle_no}` + (c.live ? ` · phase ${c.phase} (live)` : "") + " · " + (c.source === "ai" ? "AI commentary" : "Automated summary (AI unavailable)"))))
      : [el("p", { class: "muted" }, "Commentary appears after the first battle.")]));
  }

  function renderShare() {
    const c = $("sharecard");
    if (mode === "play") { c.hidden = false; $("shareurl").value = `${location.origin}/watch/${S.share_code}`; } else c.hidden = true;
  }

  function kv(label, t, extra) {
    return el("div", { class: "kv" }, el("div", { class: "eyebrow" }, label),
      el("h3", {}, `${t.flag} ${t.name}`), el("div", { class: "muted small" }, `${t.soldiers} soldiers · ${pname(t.owner)} · original value ${t.base_soldiers}${extra || ""}`));
  }

  function renderCmd() {
    const c = $("cmd"); const kids = [];
    const turnLine = el("div", {}, el("div", { class: "eyebrow" }, `Round ${S.round}`),
      el("h3", { style: "margin:2px 0" }, S.status === "finished" ? "Campaign finished" : (S.turn === hid() ? "Your turn" : `${S.current_player}'s turn`)));
    kids.push(turnLine);
    const note = el("div", { class: "msg" + (msgBad ? " bad" : ""), role: "status" }, msg);
    const f = sel.from && byCode(sel.from), t = sel.to && byCode(sel.to), info = sel.info && byCode(sel.info);

    if (mode !== "play") {
      kids.push(el("p", { class: "muted small" }, "You are watching. Spectators cannot take actions."));
      if (info) kids.push(kv("Selected country", info));
      kids.push(el("a", { class: "btn block", href: "/new" }, "Start your own campaign")); c.replaceChildren(...kids); return;
    }
    if (playing || busy) { kids.push(el("p", { class: "muted small" }, playing ? "Replaying events…" : "Working…"), note); c.replaceChildren(...kids); return; }
    if (S.status === "finished") { kids.push(el("a", { class: "btn primary block", href: "/new" }, "New campaign"), el("a", { class: "btn block", href: "/headquarters" }, "Headquarters")); c.replaceChildren(...kids); return; }
    if (S.human_eliminated) { kids.push(el("button", { class: "btn primary block", type: "button", onclick: () => { fast = true; $("speed").setAttribute("aria-pressed", "true"); act("/fast-forward"); } }, "Fast-forward the computer turns"), note); c.replaceChildren(...kids); return; }
    if (!S.can_act) { kids.push(el("p", { class: "muted small" }, "Waiting for the other nations."), note); c.replaceChildren(...kids); return; }
    if (S.battle) { kids.push(el("p", { class: "muted small" }, "A battle is in progress. Use the battle panel above."), note); c.replaceChildren(...kids); return; }

    if (S.pending > 0) {
      kids.push(el("div", { class: "kv" }, el("div", { class: "eyebrow" }, "Reinforcements"), el("h3", {}, `${S.pending} soldiers to place`),
        el("div", { class: "muted small" }, "Select one of your countries, then add soldiers.")));
      if (f) {
        const n = Math.max(1, Math.min(placeN, S.pending));
        kids.push(kv("Selected", f), el("div", { class: "tf", style: "grid-template-columns:90px 1fr" },
          el("div", {}, el("label", { for: "pn" }, "Soldiers"), el("input", { id: "pn", type: "number", min: 1, max: S.pending, value: String(n), oninput: e => { placeN = parseInt(e.target.value) || 1; } })),
          el("div", { class: "btns", style: "margin:0;align-self:end" }, el("button", { class: "btn primary", type: "button", onclick: () => act("/reinforce", { country: sel.from, count: Math.max(1, Math.min(placeN, S.pending)) }) }, "Place soldiers"))));
        kids.push(el("button", { class: "btn small", type: "button", style: "margin-top:8px", onclick: () => act("/reinforce", { country: sel.from, count: S.pending }) }, `Place all ${S.pending} here`));
      }
      kids.push(note); c.replaceChildren(...kids); return;
    }

    // -------- guided prompts: 1) select country  2) choose action  3) choose target ----
    const mine = D.filter(x => x.owner === hid());
    const pickBtn = (code, title, sub, opts = {}) => el("button", { type: "button", class: "btn block pick", ...opts,
      onclick: opts.onclick || (() => { if (!opts.disabled) onPick(code); }) },
      el("b", {}, title), el("span", { class: "psub" }, sub));
    const endBtn = S.has_attacked
      ? el("button", { class: "btn block", type: "button", onclick: () => act("/end-turn") }, "End turn")
      : el("button", { class: "btn block", type: "button", onclick: () => act("/skip") }, "Skip turn");

    if (!f) {
      kids.push(el("div", { class: "eyebrow" }, "Step 1 of 3"), el("p", { class: "muted small" }, "Select the country you want to act with."));
      kids.push(el("div", { class: "picklist" }, mine.map(x => pickBtn(x.code, `${x.flag} ${x.name}`,
        `${x.soldiers} soldiers${x.captured_turn === S.turn_no ? " · conquered this turn, can act next turn" : ""}`,
        { disabled: x.soldiers < 2 || x.captured_turn === S.turn_no }))));
    } else if (!wz.action) {
      kids.push(el("div", { class: "eyebrow" }, "Step 2 of 3"), kv("Acting with", f),
        el("p", { class: "muted small" }, "Choose an action."));
      const attackBlocked = S.has_attacked ? "You have already attacked this turn." : (f.soldiers < 2 ? "Needs at least 2 soldiers." : (f.captured_turn === S.turn_no ? "Conquered this turn - can act next turn." : ""));
      kids.push(el("div", { class: "picklist" },
        el("button", { type: "button", class: "btn block primary pick", disabled: !!attackBlocked, onclick: () => { wz.action = "attack"; sel.to = null; renderCmd(); } },
          el("b", {}, "Attack"), el("span", { class: "psub" }, attackBlocked || "Attack an enemy country.")),
        el("button", { type: "button", class: "btn block pick", disabled: mine.length < 2 || f.soldiers < 2,
          onclick: () => { wz.action = "move"; sel.to = null; renderCmd(); } },
          el("b", {}, "Move soldiers"), el("span", { class: "psub" }, mine.length < 2 ? "You control only one country." : (f.soldiers < 2 ? "Needs at least 2 soldiers." : "Move soldiers to another of your countries."))),
        el("button", { type: "button", class: "btn block pick", onclick: () => { sel.from = null; sel.to = null; wz.action = null; renderCmd(); } },
          el("b", {}, "Choose a different country"), el("span", { class: "psub" }, "Go back to step 1."))));
    } else if (wz.action === "attack") {
      kids.push(el("div", { class: "eyebrow" }, "Step 3 of 3"), kv("Acting with", f),
        el("div", { class: "kv" }, el("div", { class: "eyebrow" }, "Action"), el("h3", {}, "Attack")),
        el("p", { class: "muted small" }, "Which country do you want to take action against?"));
      kids.push(el("div", { class: "picklist" }, D.filter(x => x.owner !== hid()).map(x => pickBtn(x.code, `${x.flag} ${x.name}`,
        `${x.soldiers} soldiers · estimated win chance ${Math.round(winChance(f.soldiers, x.soldiers) * 100)}%`))));
      if (t) kids.push(kv("Target", t), el("button", { class: "btn primary block", type: "button",
        onclick: () => act("/attack/start", { from: sel.from, to: sel.to }) }, `Attack ${t.name}`));
      kids.push(el("button", { class: "btn small", type: "button", onclick: () => { wz.action = null; sel.to = null; renderCmd(); } }, "Back to actions"));
    } else if (wz.action === "move") {
      kids.push(el("div", { class: "eyebrow" }, "Step 3 of 3"), kv("Acting with", f),
        el("p", { class: "muted small" }, "Which of your countries should receive the soldiers?"));
      const others = mine.filter(x => x.code !== f.code);
      kids.push(el("div", { class: "picklist" }, others.map(x => pickBtn(x.code, `${x.flag} ${x.name}`, `${x.soldiers} soldiers`))));
      if (t) {
        const n = Math.max(1, Math.min(tf.count, f.soldiers - 1));
        kids.push(kv("Target", t), el("div", { class: "tf", style: "grid-template-columns:90px 1fr" },
          el("div", {}, el("label", { for: "mvn" }, "Soldiers"), el("input", { id: "mvn", type: "number", min: 1, max: f.soldiers - 1, value: String(n), oninput: e => { tf.count = parseInt(e.target.value) || 1; } })),
          el("div", { class: "btns", style: "margin:0;align-self:end" }, el("button", { class: "btn primary", type: "button",
            onclick: () => act("/transfer", { from: sel.from, to: sel.to, count: Math.max(1, Math.min(tf.count, f.soldiers - 1)) }) }, "Transfer soldiers"))));
      }
      kids.push(el("button", { class: "btn small", type: "button", onclick: () => { wz.action = null; sel.to = null; renderCmd(); } }, "Back to actions"));
    }
    kids.push(el("div", { class: "btns" }, endBtn), note);
    c.replaceChildren(...kids);
  }
})();
