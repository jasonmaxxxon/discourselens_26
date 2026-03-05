import { chromium } from 'playwright';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.goto('http://127.0.0.1:5173/insights', { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForTimeout(4000);
const frames = page.frames();
console.log('frame count', frames.length);
for (const [idx, frame] of frames.entries()) {
  if (frame === page.mainFrame()) continue;
  const info = await frame.evaluate(() => {
    const t = document.title || '';
    const h1 = document.querySelector('h1')?.textContent?.trim() || '';
    const h2 = Array.from(document.querySelectorAll('h2')).slice(0,3).map((el) => (el.textContent||'').trim());
    const marker = document.body?.innerText?.slice(0,120) || '';
    const pageAttr = document.body?.dataset?.stitchPage || null;
    return { t, h1, h2, marker, pageAttr };
  });
  console.log('frame', idx, frame.url(), JSON.stringify(info));
}
await browser.close();
