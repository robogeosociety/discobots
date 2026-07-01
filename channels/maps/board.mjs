// Render a departure-board HTML file to a tight PNG (Playwright headless, 2x).
// Usage: node board.mjs "file:///abs/board.html" [outpath]
import { chromium } from 'playwright';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { mkdirSync } from 'fs';

const here = dirname(fileURLToPath(import.meta.url));
const url = process.argv[2];
const out = process.argv[3] || join(here, 'out', 'map.png');
if (!url) { console.error('usage: node board.mjs "file://..." [out]'); process.exit(1); }

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ deviceScaleFactor: 2 });
  await page.goto(url, { waitUntil: 'networkidle', timeout: 20000 });
  await page.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
  await page.waitForTimeout(150);
  const el = await page.$('#board');
  mkdirSync(dirname(out), { recursive: true });
  await (el || page).screenshot({ path: out });
  console.log(out);
} finally {
  await browser.close();
}
