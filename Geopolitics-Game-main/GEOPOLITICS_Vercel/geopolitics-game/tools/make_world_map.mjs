import fs from 'fs';
import {feature} from 'topojson-client';
import {geoPath, geoCentroid, geoArea, geoNaturalEarth1} from 'd3-geo';
import iso from 'i18n-iso-countries';
const topo = JSON.parse(fs.readFileSync('node_modules/world-atlas/countries-50m.json'));
const fc = feature(topo, topo.objects.countries);
const W=1000,H=520;
const proj = geoNaturalEarth1().fitExtent([[6,6],[W-6,H-6]], {type:'Sphere'});
const path = geoPath(proj).digits(1);
const raw = JSON.parse(fs.readFileSync('countries_raw.json'));
const want = new Set(raw.map(r=>r[0]));
const paths={}, cent={}, extra={};
const topo110 = JSON.parse(fs.readFileSync('node_modules/world-atlas/countries-110m.json'));
for (const f of feature(topo110, topo110.objects.countries).features){
  if (f.properties.name==='Antarctica'||!f.id) continue;
  const a2 = iso.numericToAlpha2(String(f.id).padStart(3,'0')); if(!a2) continue;
  const d = geoPath(proj).digits(0)(f); if(d) paths[a2]=(paths[a2]||'')+d;
}
for (const f of fc.features){
  if (f.properties.name==='Antarctica') continue;
  let a2 = f.id ? iso.numericToAlpha2(String(f.id).padStart(3,'0')) : null;
  if (!a2) { extra[f.properties.name]=1; continue; }
  // centroid of largest polygon
  let geom=f.geometry, best=f;
  if (geom.type==='MultiPolygon'){
    let m=-1,bi=0; geom.coordinates.forEach((c,i)=>{const ar=geoArea({type:'Polygon',coordinates:c}); if(ar>m){m=ar;bi=i}});
    best={type:'Feature',geometry:{type:'Polygon',coordinates:geom.coordinates[bi]}};
  }
  const c = proj(geoCentroid(best)); cent[a2]=[Math.round(c[0]*10)/10, Math.round(c[1]*10)/10];
}
const missing=[...want].filter(c=>!cent[c]);
console.log('shapes',Object.keys(paths).length,'missing',missing.join(','),'unmapped',Object.keys(extra).join('|'));
// fallback centroids for missing: projected lon/lat table
const ll={AD:[1.6,42.5],AG:[-61.8,17.1],BB:[-59.5,13.2],BH:[50.6,26.0],CV:[-23.6,15.1],KM:[43.3,-11.7],DM:[-61.4,15.4],FM:[158.2,6.9],GD:[-61.7,12.1],KI:[173.0,1.4],KN:[-62.7,17.3],LC:[-61.0,13.9],LI:[9.55,47.15],MH:[171.2,7.1],MC:[7.4,43.7],MT:[14.4,35.9],MU:[57.6,-20.3],MV:[73.5,3.2],NR:[166.9,-0.5],PW:[134.6,7.5],SC:[55.5,-4.7],SG:[103.8,1.35],SM:[12.45,43.94],ST:[6.6,0.3],TO:[-175.2,-21.2],TV:[179.2,-8.5],VA:[12.45,41.9],VC:[-61.2,13.2],WS:[-172.1,-13.8]};
for (const c of missing){ if(ll[c]){const p=proj(ll[c]); cent[c]=[Math.round(p[0]*10)/10,Math.round(p[1]*10)/10]; } else console.log('NO COORD',c); }
fs.writeFileSync('world.json', JSON.stringify({width:W,height:H,paths,centroids:cent}));
console.log(fs.statSync('world.json').size);
