// Screenshot the live walksheds.xyz app at a deeplink (Playwright headless Chromium).
// The app serves its own Mapbox token (origin walksheds.xyz), so no token handling here.
// Usage: node webshot.mjs "<url>" [width] [height]
import { chromium } from 'playwright';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { mkdirSync } from 'fs';

const here = dirname(fileURLToPath(import.meta.url));
const url = process.argv[2];
if (!url) { console.error('usage: node webshot.mjs "<url>" [w] [h] [outpath]'); process.exit(1); }
const W = Number(process.argv[3] || 1000), H = Number(process.argv[4] || 800);
const outArg = process.argv[5];   // optional explicit output path (cache workers pass this)

const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({
    viewport: { width: W, height: H }, deviceScaleFactor: 2,
    // Mark the first-run hints as already seen so they don't cover the map.
    storageState: { cookies: [], origins: [{ origin: 'https://walksheds.xyz',
      localStorage: [{ name: 'walksheds_hints_v1_seen', value: '1' }] }] },
  });
  const page = await ctx.newPage();
  // A live GL map never reaches networkidle (tiles keep streaming) -> wait on the canvas instead.
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 35000 });
  await page.waitForSelector('canvas.mapboxgl-canvas', { timeout: 25000 }).catch(() => {});
  await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(2500);   // let the isochrone + POI dots finish painting
  const out = outArg || join(here, 'out', 'map.png');
  mkdirSync(dirname(out), { recursive: true });
  await page.screenshot({ path: out });
  console.log(out);
} finally {
  await browser.close();
}
