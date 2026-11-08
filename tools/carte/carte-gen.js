// Génère le fond de carte (graticule + terres) — repris tel quel du script UX
// (claude/ux-carte-gen.js dans le projet). Sortie : assets/carte-pacifique.svg
// Usage : cd tools/carte && npm ci && node carte-gen.js
// Données : Natural Earth 1:10 m (domaine public) via le paquet npm world-atlas.
const fs = require('fs');
const path = require('path');
const topo = require('topojson-client');

const W = 800, H = 480;
const LON0 = 130, LON1 = 310, LAT1 = 28;
const K = W / (LON1 - LON0);
const P = (lon, lat) => [(lon - LON0) * K, (LAT1 - lat) * K];

const CL = { x0: -6, y0: -6, x1: W + 6, y1: H + 6 };
function clip(poly) {
  const edges = [
    (p) => p[0] >= CL.x0, (p) => p[0] <= CL.x1, (p) => p[1] >= CL.y0, (p) => p[1] <= CL.y1,
  ];
  const inter = [
    (a, b) => { const t = (CL.x0 - a[0]) / (b[0] - a[0]); return [CL.x0, a[1] + t * (b[1] - a[1])]; },
    (a, b) => { const t = (CL.x1 - a[0]) / (b[0] - a[0]); return [CL.x1, a[1] + t * (b[1] - a[1])]; },
    (a, b) => { const t = (CL.y0 - a[1]) / (b[1] - a[1]); return [a[0] + t * (b[0] - a[0]), CL.y0]; },
    (a, b) => { const t = (CL.y1 - a[1]) / (b[1] - a[1]); return [a[0] + t * (b[0] - a[0]), CL.y1]; },
  ];
  let out = poly;
  for (let e = 0; e < 4; e++) {
    const inp = out; out = [];
    if (!inp.length) break;
    for (let i = 0; i < inp.length; i++) {
      const cur = inp[i], prev = inp[(i + inp.length - 1) % inp.length];
      const ci = edges[e](cur), pi = edges[e](prev);
      if (ci) { if (!pi) out.push(inter[e](prev, cur)); out.push(cur); }
      else if (pi) out.push(inter[e](prev, cur));
    }
  }
  return out;
}

function dp(pts, tol) {
  if (pts.length < 3) return pts;
  const keep = new Uint8Array(pts.length); keep[0] = keep[pts.length - 1] = 1;
  const stack = [[0, pts.length - 1]];
  while (stack.length) {
    const [a, b] = stack.pop();
    let md = 0, mi = -1;
    const [ax, ay] = pts[a], [bx, by] = pts[b];
    const dx = bx - ax, dy = by - ay, L = Math.hypot(dx, dy) || 1e-9;
    for (let i = a + 1; i < b; i++) {
      const d = Math.abs(dy * pts[i][0] - dx * pts[i][1] + bx * ay - by * ax) / L;
      if (d > md) { md = d; mi = i; }
    }
    if (md > tol && mi > 0) { keep[mi] = 1; stack.push([a, mi], [mi, b]); }
  }
  return pts.filter((_, i) => keep[i]);
}

const land = JSON.parse(fs.readFileSync(require.resolve('world-atlas/land-10m.json')));
const geo = topo.feature(land, land.objects.land);
let d = '';
for (const f of geo.features) {
  const polys = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates;
  for (const poly of polys) {
    for (const ring0 of poly) {
      let lomin = Infinity, lomax = -Infinity, lamin = Infinity;
      for (const [lo, la] of ring0) { lomin = Math.min(lomin, lo); lomax = Math.max(lomax, lo); lamin = Math.min(lamin, la); }
      if (lamin < -60) continue;
      let ring = ring0;
      if (lomax - lomin > 180) {
        const un = ring0.map(([lo, la]) => [lo < 0 ? lo + 360 : lo, la]);
        let a = Infinity, b = -Infinity; for (const [lo] of un) { a = Math.min(a, lo); b = Math.max(b, lo); }
        if (b - a < lomax - lomin) ring = un;
      }
      for (const off of [0, 360]) {
        let pts = ring.map(([lo, la]) => P(lo + off, la));
        let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
        for (const [x, y] of pts) { minx = Math.min(minx, x); maxx = Math.max(maxx, x); miny = Math.min(miny, y); maxy = Math.max(maxy, y); }
        if (maxx < CL.x0 || minx > CL.x1 || maxy < CL.y0 || miny > CL.y1) continue;
        if (maxx - minx < 1.2 && maxy - miny < 1.2) continue;
        pts = clip(pts);
        if (pts.length < 3) continue;
        const mid = Math.floor(pts.length / 2);
        pts = dp(pts.slice(0, mid + 1), 0.45).concat(dp(pts.slice(mid).concat([pts[0]]), 0.45).slice(1));
        if (pts.length < 4) continue;
        d += 'M' + pts.map(([x, y]) => x.toFixed(1) + ' ' + y.toFixed(1)).join('L') + 'Z';
      }
    }
  }
}

let grat = '';
for (let lo = 135; lo <= 315; lo += 15) { const x = ((lo - LON0) * K).toFixed(1); grat += `M${x} 0V${H}`; }
for (let la = 15; la >= -75; la -= 15) { const y = ((LAT1 - la) * K).toFixed(1); grat += `M0 ${y}H${W}`; }

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
<defs><pattern id="h" patternUnits="userSpaceOnUse" width="6" height="6"><path d="M-1 7L7 -1" stroke="#000000" stroke-width="1.3"/></pattern></defs>
<path d="${grat}" fill="none" stroke="#1E50A2" stroke-width="1" stroke-dasharray="2 4"/>
<path d="${d}" fill="url(#h)" fill-rule="evenodd" stroke="#000000" stroke-width="1.5" stroke-linejoin="round"/>
</svg>`;
const out = path.join(__dirname, '..', '..', 'assets', 'carte-pacifique.svg');
fs.writeFileSync(out, svg);
console.log('écrit', out, svg.length, 'octets');
