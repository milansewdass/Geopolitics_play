"use strict";
const csrf = () => document.querySelector('meta[name="csrf-token"]').content;

async function api(path, method = "GET", body) {
  const opts = { method, headers: { "X-CSRF-Token": csrf() }, credentials: "same-origin" };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch("/api" + path, opts);
  let data = {};
  try { data = await res.json(); } catch (_) { /* non-JSON error body */ }
  if (!res.ok) {
    const err = new Error(data.error || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// Safe DOM builder: strings are always inserted as text, never parsed as HTML.
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid.nodeType ? kid : document.createTextNode(kid));
  return n;
}
const $ = id => document.getElementById(id);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const fmt = n => Number(n).toLocaleString("en-US");

// ---- colours: human = gold, the nine opponents get muted distinct hues ----------------------
const NPC_COLORS = ["#5b86c9", "#c0576f", "#3f9aa3", "#8f78c7", "#c9814f", "#6aa874", "#9aa7bd", "#b66ab0", "#7a9c3f"];
function ownerColor(playerId, humanId) {
  if (playerId === humanId) return "#d9ab43";
  return NPC_COLORS[(playerId - (playerId > humanId ? 1 : 0)) % NPC_COLORS.length];
}

// ---- exact win probability (same maths as the server: dynamic programming over (A, D)) ------
const _memo = new Map();
const _comb = (() => { const t = [[1]]; for (let n = 1; n <= 41; n++) { t[n] = [1]; for (let k = 1; k <= n; k++) t[n][k] = (t[n - 1][k - 1] || 0) + (t[n - 1][k] || 0); } return t; })();
function winChance(a, d) {
  if (d <= 0) return 1; if (a <= 1) return 0;
  if (a > 40 || d > 40) { const av = a - 1, z = (av - d) / Math.sqrt(av + d); return 0.5 * (1 + erf(z / Math.SQRT2)); }
  const key = a * 100 + d; if (_memo.has(key)) return _memo.get(key);
  const n = Math.min(a - 1, d), W = 15 / 36, T = 6 / 36; let total = 0, stay = 0;
  for (let i = 0; i <= n; i++) for (let j = 0; j <= n - i; j++) {
    const p = _comb[n][i] * _comb[n - i][j] * W ** i * W ** j * T ** (n - i - j);
    if (i === 0 && j === 0) stay = p; else total += p * winChance(a - j, d - i);
  }
  const r = total / (1 - stay); _memo.set(key, r); return r;
}
function erf(x) { const s = Math.sign(x); x = Math.abs(x); const t = 1 / (1 + 0.3275911 * x);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x); return s * y; }

// ---- shared view helpers --------------------------------------------------------------------
function phaseSummary(ph) {
  if (!ph.att_loss && !ph.def_loss) return "No casualties this phase (all ties).";
  if (ph.att_loss && ph.def_loss) return `Attacker loses ${ph.att_loss}, defender loses ${ph.def_loss}.`;
  return ph.att_loss ? `Attacker loses ${ph.att_loss}.` : `Defender loses ${ph.def_loss}.`;
}
function pairChips(ph, rolling = false) {
  const wrap = el("div", { class: "pairs", role: "list" });
  ph.attacker_dice.forEach((a, i) => {
    const res = ph.results ? ph.results[i] : null;
    wrap.append(el("div", { class: `pair ${res === "attacker" ? "aw" : res === "defender" ? "dw" : res === "tie" ? "tie" : ""} ${rolling ? "roll" : ""}`,
      role: "listitem", "aria-label": `Pair ${i + 1}: attacker ${a}, defender ${ph.defender_dice[i]}` },
      el("span", { class: "a" }, String(a)), el("span", { class: "d" }, String(ph.defender_dice[i]))));
  });
  return wrap;
}
function pairText(ph) { return ph.attacker_dice.map((a, i) => `PAIR ${i + 1}: ${a} vs ${ph.defender_dice[i]}`).join("  |  "); }

const EV_LABEL = { setup: "Setup", turn: "Turn", reinforce: "Reinforce", attack: "Attack", phase: "Phase", conquest: "Conquest",
  withdraw: "Stopped", exhausted: "Stalled", hold: "Hold", transfer: "Transfer", end: "End", elimination: "Eliminated", victory: "Victory" };
function feedItem(ev, humanId) {
  return el("div", { class: `ev ${ev.type} ${ev.actor === humanId && ev.type !== "turn" ? "me" : ""}` },
    el("span", { class: "k" }, EV_LABEL[ev.type] || ev.type), ev.text,
    ev.why ? el("span", { class: "why" }, "Why: " + ev.why) : null);
}
