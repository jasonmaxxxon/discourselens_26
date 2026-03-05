import { chromium } from 'playwright';

const base = 'http://localhost:5173/pipeline';
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1680, height: 1278 } });
await context.addInitScript(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
  } catch {}
});
const page = await context.newPage();
await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(3000);
await page.screenshot({ path: '/tmp/pipeline_shader_forced.png', fullPage: false });
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
await page.waitForTimeout(800);
await page.screenshot({ path: '/tmp/pipeline_shader_forced_bottom.png', fullPage: false });
await browser.close();
console.log('/tmp/pipeline_shader_forced.png');
console.log('/tmp/pipeline_shader_forced_bottom.png');
