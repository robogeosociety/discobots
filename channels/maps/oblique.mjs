// One-off oblique (3D terrain) Mapbox GL render -> out/oblique.png
// Usage: node oblique.mjs "<lat,lon>" [zoom] [pitch] [bearing] [style]
import { chromium } from 'playwright';
import { readFileSync, mkdirSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const here = dirname(fileURLToPath(import.meta.url));
const token = readFileSync('/Volumes/dev/maps/mapbox.key', 'utf8').trim();
const REFERER = process.env.MAPBOX_REFERER || 'https://tommyroar.github.io/';

const q = process.argv[2];
const zoom = Number(process.argv[3] || 13.5);
const pitch = Number(process.argv[4] || 72);
const bearing = Number(process.argv[5] || 0);
const style = process.argv[6] || 'mapbox/satellite-streets-v12';
const W = 1000, H = 700;

let lon, lat;
if (/^-?[0-9.]+,-?[0-9.]+$/.test(q)) {
  const [a, b] = q.split(',').map(Number); lat = a; lon = b;
} else {
  const r = await fetch(`https://api.mapbox.com/geocoding/v5/mapbox.places/${encodeURIComponent(q)}.json?limit=1&access_token=${token}`, { headers: { Referer: REFERER } });
  const j = await r.json();
  if (!j.features || !j.features.length) { console.error('no geocode match'); process.exit(1); }
  [lon, lat] = j.features[0].center;
}

const html = `<!DOCTYPE html><html><head><meta charset="utf-8">
<link href="https://api.mapbox.com/mapbox-gl-js/v3.7.0/mapbox-gl.css" rel="stylesheet">
<script src="https://api.mapbox.com/mapbox-gl-js/v3.7.0/mapbox-gl.js"></script>
<style>html,body,#m{margin:0;width:${W}px;height:${H}px}</style></head>
<body><div id="m"></div><script>
mapboxgl.accessToken=${JSON.stringify(token)};
const map=new mapboxgl.Map({container:'m',style:'mapbox://styles/${style}',center:[${lon},${lat}],zoom:${zoom},pitch:${pitch},bearing:${bearing},interactive:false,attributionControl:false});
map.on('style.load',()=>{
  map.addSource('dem',{type:'raster-dem',url:'mapbox://mapbox.mapbox-terrain-dem-v1',tileSize:512,maxzoom:14});
  map.setTerrain({source:'dem',exaggeration:1.2});
  map.setFog({range:[1,12],color:'#dfeefa','horizon-blend':0.2});
});
new mapboxgl.Marker({color:'#ff2200'}).setLngLat([${lon},${lat}]).addTo(map);
let done=false;
map.on('idle',()=>{if(!done){done=true;window.__ready=true}});
</script></body></html>`;

mkdirSync(join(here, 'out'), { recursive: true });
const out = join(here, 'out', 'oblique.png');
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 2 });
  await page.setExtraHTTPHeaders({ referer: REFERER });
  await page.setContent(html, { waitUntil: 'load' });
  await page.waitForFunction('window.__ready===true', { timeout: 25000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: out });
  console.log(`${out}  (${q} -> ${lat},${lon} z${zoom} pitch${pitch} bearing${bearing})`);
} finally {
  await browser.close();
}
