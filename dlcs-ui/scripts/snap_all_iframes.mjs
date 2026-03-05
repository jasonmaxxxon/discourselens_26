import { chromium } from 'playwright';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.goto('http://127.0.0.1:5173/insights', { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForTimeout(5000);
const loc = page.locator('iframe');
const count = await loc.count();
console.log('count', count);
for (let i=0;i<count;i++) {
  const frame = page.frames()[i+1];
  const marker = frame ? await frame.evaluate(() => {
    const t = document.title || '';
    const sample = (document.body?.innerText || '').slice(0,80).replace(/\s+/g,' ');
    return { t, sample };
  }) : null;
  const path = `/tmp/dlens-iframe-${i}.png`;
  await loc.nth(i).screenshot({ path });
  console.log(i, path, marker);
}
await browser.close();
