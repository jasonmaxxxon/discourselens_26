import { chromium } from 'playwright';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.goto('http://127.0.0.1:5173/insights', { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForTimeout(6000);
await page.screenshot({ path: '/tmp/dlens-insights-after-fix.png', fullPage: true });
await browser.close();
console.log('/tmp/dlens-insights-after-fix.png');
