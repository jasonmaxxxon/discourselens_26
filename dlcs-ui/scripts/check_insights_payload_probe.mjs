import { chromium } from 'playwright';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.goto('http://127.0.0.1:5173/insights', { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForTimeout(6000);
const frames = page.frames().filter((f)=>f!==page.mainFrame());
for (let i=0;i<frames.length;i++) {
  const f=frames[i];
  const d = await f.evaluate(() => ({
    title: document.title,
    stitchPage: document.body?.dataset?.stitchPage || null,
    bridgeNodes: document.body?.dataset?.bridgeNodes || null,
    bridgeEdges: document.body?.dataset?.bridgeEdges || null,
    bridgeGraphNodes: document.body?.dataset?.bridgeGraphNodes || null,
    graphHosts: document.querySelectorAll('[data-insights-graph="1"]').length,
    graphButtons: document.querySelectorAll('[data-insights-graph="1"] button').length,
  }));
  console.log(i, d);
}
await browser.close();
