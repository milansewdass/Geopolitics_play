import fs from "fs";
import * as topojson from "topojson-client";
import { geoNaturalEarth1, geoPath } from "d3-geo";
const topo = JSON.parse(fs.readFileSync("node_modules/world-atlas/countries-110m.json"));
const feats = topojson.feature(topo, topo.objects.countries).features;
const W = 1000, H = 520;
const proj = geoNaturalEarth1().fitExtent([[6, 6], [W - 6, H - 6]], { type: "Sphere" });
const path = geoPath(proj).digits ? geoPath(proj).digits(1) : geoPath(proj);
const out = { width: W, height: H, countries: {} };
for (const f of feats) {
  const d = path(f); if (!d) continue;
  const c = geoPath(proj).centroid(f);
  // centroid of the largest polygon for multi-polygon countries (avoids ocean centroids, e.g. USA/France)
  let best = c;
  if (f.geometry.type === "MultiPolygon") {
    let max = -1;
    for (const poly of f.geometry.coordinates) {
      const g = { type: "Feature", geometry: { type: "Polygon", coordinates: poly } };
      const a = geoPath(proj).area(g);
      if (a > max) { max = a; best = geoPath(proj).centroid(g); }
    }
  }
  out.countries[String(f.id).padStart(3, "0")] = { name: f.properties.name, d, c: best.map(v => Math.round(v * 10) / 10) };
}
out.sphere = geoPath(proj)({ type: "Sphere" });
fs.writeFileSync("world.json", JSON.stringify(out));
console.log(Object.keys(out.countries).length, fs.statSync("world.json").size);
