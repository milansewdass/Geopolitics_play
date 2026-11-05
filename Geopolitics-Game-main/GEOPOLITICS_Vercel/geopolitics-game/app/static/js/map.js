"use strict";
/* Real world map (Natural Earth 110m, pre-projected) with a label per participating nation. */
class WorldMap {
  constructor(container, world, onPick) {
    this.world = world; this.onPick = onPick; this.labels = {}; this.shapes = {};
    const NS = "http://www.w3.org/2000/svg";
    this.NS = NS;
    this.svg = document.createElementNS(NS, "svg");
    this.svg.setAttribute("class", "map");
    this.svg.setAttribute("role", "group");
    this.svg.setAttribute("aria-label", "World map showing the ten nations in play");
    container.replaceChildren(this.svg);
    this.built = false;
  }
  _s(tag, attrs = {}) { const n = document.createElementNS(this.NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; }

  build(territories) {
    const { world } = this, svg = this.svg;
    svg.append(this._s("path", { d: world.sphere, class: "sphere" }));
    const land = this._s("g");
    for (const [code, c] of Object.entries(world.countries)) land.append(this._s("path", { d: c.d, class: "land" }));
    svg.append(land);
    const nations = this._s("g"), labels = this._s("g");
    for (const t of territories) {
      const c = world.countries[t.code]; if (!c) continue;
      const p = this._s("path", { d: c.d, class: "nation" });
      p.addEventListener("click", () => this.onPick(t.code));
      nations.append(p); this.shapes[t.code] = p;
    }
    svg.append(nations, labels);
    this.labelLayer = labels;
    this._viewBox(territories);
    this._layoutLabels(territories);
    this.built = true;
  }

  _viewBox(territories) {
    const W = this.world.width, H = this.world.height;
    const pts = territories.map(t => this.world.countries[t.code]).filter(Boolean).map(c => c.c);
    let x0 = Math.min(...pts.map(p => p[0])) - 110, x1 = Math.max(...pts.map(p => p[0])) + 110;
    let y0 = Math.min(...pts.map(p => p[1])) - 70, y1 = Math.max(...pts.map(p => p[1])) + 70;
    let w = Math.max(x1 - x0, 420), h = Math.max(y1 - y0, w * H / W * 0.8);
    if (h * W / H > w) w = h * W / H; else h = w * H / W;
    w = Math.min(w, W); h = Math.min(h, H);
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    x0 = Math.max(0, Math.min(W - w, cx - w / 2)); y0 = Math.max(0, Math.min(H - h, cy - h / 2));
    this.svg.setAttribute("viewBox", `${x0.toFixed(1)} ${y0.toFixed(1)} ${w.toFixed(1)} ${h.toFixed(1)}`);
    this.scale = w / 1000;
  }

  _layoutLabels(territories) {
    const fs = Math.max(10.5 * this.scale, 7);
    const items = territories.filter(t => this.world.countries[t.code]).map(t => {
      const c = this.world.countries[t.code].c; const w = (t.name.length * 0.56 + 3.2) * fs, h = fs * 1.7;
      return { t, w, h, x: c[0] - w / 2, y: c[1] - h / 2 };
    }).sort((a, b) => a.y - b.y);
    const placed = [];
    for (const it of items) {                       // nudge overlapping labels apart
      for (let guard = 0; guard < 30; guard++) {
        const hit = placed.find(p => it.x < p.x + p.w + 2 && it.x + it.w + 2 > p.x && it.y < p.y + p.h + 1 && it.y + it.h + 1 > p.y);
        if (!hit) break;
        it.y = hit.y + hit.h + 1.5;
      }
      placed.push(it);
    }
    for (const it of items) {
      const g = this._s("g", { class: "lab", tabindex: "0", role: "button" });
      const r = this._s("rect", { x: it.x, y: it.y, width: it.w, height: it.h, rx: 3 * this.scale });
      const name = this._s("text", { x: it.x + 4 * this.scale, y: it.y + it.h * 0.68, style: `font-size:${fs}px` });
      name.textContent = it.t.name;
      const n = this._s("text", { class: "n", x: it.x + it.w - 4 * this.scale, y: it.y + it.h * 0.68, "text-anchor": "end", style: `font-size:${fs}px` });
      g.append(r, name, n);
      g.addEventListener("click", () => this.onPick(it.t.code));
      g.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this.onPick(it.t.code); } });
      this.labelLayer.append(g); this.labels[it.t.code] = { g, n };
    }
  }

  update(territories, ctx) {          // ctx: {colorOf(t), selFrom, selTo, humanId, ownerName(t)}
    if (!this.built) this.build(territories);
    for (const t of territories) {
      const shape = this.shapes[t.code], lab = this.labels[t.code];
      if (shape) {
        shape.setAttribute("fill", ctx.colorOf(t));
        shape.setAttribute("class", "nation" + (ctx.selFrom === t.code ? " from" : "") + (ctx.selTo === t.code ? " to" : ""));
      }
      if (lab) {
        lab.n.textContent = String(t.soldiers);
        lab.g.setAttribute("class", "lab" + (t.owner === ctx.humanId ? " mine" : "") + (ctx.selFrom === t.code ? " from" : "") + (ctx.selTo === t.code ? " to" : ""));
        lab.g.setAttribute("aria-label", `${t.name}, ${t.soldiers} soldiers, ${ctx.ownerName(t)}`);
      }
    }
  }
}
