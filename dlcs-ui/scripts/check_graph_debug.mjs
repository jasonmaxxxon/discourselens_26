import { chromium } from 'playwright';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.goto('http://127.0.0.1:5173/insights', { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForTimeout(7000);
const frames=page.frames().filter((f)=>f!==page.mainFrame());
for (let i=0;i<frames.length;i++) {
  const d=await frames[i].evaluate(()=>({
    title: document.title,
    stitchPage: document.body?.dataset?.stitchPage || null,
    graphDebug: document.body?.dataset?.graphDebug || null,
    graphHosts: document.querySelectorAll('[data-insights-graph="1"]').length,
  }));
  console.log(i,d);
}
await browser.close();
