// Headless Mapbox GL JS render -> out/map.png (Playwright + Chromium).
// Use when you need a real GL render (custom style/markers) beyond the Static Images API.
// Usage: node render.mjs "<place|lat,lon>" [zoom] [style]
//   node render.mjs "Gas Works Park, Seattle" 15
//   node render.mjs "47.6457,-122.3344" 16 mapbox/dark-v11
import { chromium } from 'playwright';
import { readFileSync, mkdirSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const here = dirname(fileURLToPath(import.meta.url));
const token = readFileSync('/Volumes/dev/maps/mapbox.key', 'utf8').trim();
// The pk token is URL-restricted to the maps site origin; every Mapbox request (geocode +
// GL tiles/style/fonts) must carry this Referer or Mapbox returns 403.
const REFERER = process.env.MAPBOX_REFERER || 'https://tommyroar.github.io/';

const q = process.argv[2];
if (!q) { console.error('usage: node render.mjs "<place|lat,lon>" [zoom] [style]'); process.exit(1); }
const zoom = Number(process.argv[3] || 14);
const style = process.argv[4] || 'mapbox/streets-v12';
const W = 800, H = 600;

let lon, lat;
if (/^-?[0-9.]+,-?[0-9.]+$/.test(q)) {
  const [a, b] = q.split(',').map(Number); lat = a; lon = b;
} else {
  // Geocode via OSM Nominatim (more accurate than Mapbox v5); Mapbox token is only for GL tiles.
  const r = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=${encodeURIComponent(q)}`,
    { headers: { 'User-Agent': 'MapBot/1 (tommy.b.doerr@gmail.com; https://tommyroar.github.io)' } });
  const j = await r.json();
  if (!Array.isArray(j) || !j.length) { console.error('no geocode match'); process.exit(1); }
  lon = Number(j[0].lon); lat = Number(j[0].lat);
}

const html = `<!DOCTYPE html><html><head><meta charset="utf-8">
<link href="https://api.mapbox.com/mapbox-gl-js/v3.7.0/mapbox-gl.css" rel="stylesheet">
<script src="https://api.mapbox.com/mapbox-gl-js/v3.7.0/mapbox-gl.js"></script>
<style>html,body,#m{margin:0;width:${W}px;height:${H}px}</style></head>
<body><div id="m"></div><script>
mapboxgl.accessToken=${JSON.stringify(token)};
const map=new mapboxgl.Map({container:'m',style:'mapbox://styles/${style}',center:[${lon},${lat}],zoom:${zoom},interactive:false,attributionControl:false});
new mapboxgl.Marker({color:'#ff2200'}).setLngLat([${lon},${lat}]).addTo(map);
map.on('idle',()=>{window.__ready=true});
</script></body></html>`;

mkdirSync(join(here, 'out'), { recursive: true });
const out = join(here, 'out', 'map.png');
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 2 });
  // Attach the Referer to every request so the URL-restricted token is accepted.
  await page.setExtraHTTPHeaders({ referer: REFERER });
  await page.setContent(html, { waitUntil: 'load' });
  await page.waitForFunction('window.__ready===true', { timeout: 20000 });
  await page.screenshot({ path: out });
  console.log(`${out}  (${q} -> ${lat},${lon} z${zoom})`);
} finally {
  await browser.close();
}
