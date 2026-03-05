import { chromium } from 'playwright';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage();
await page.goto('http://127.0.0.1:5173/insights',{waitUntil:'networkidle',timeout:60000});
await page.waitForTimeout(16000);
const f=page.frames().filter(x=>x!==page.mainFrame())[2];
const d=await f.evaluate(()=>({
  graphDebug: document.body.dataset.graphDebug || null,
  graphHosts: document.querySelectorAll('[data-insights-graph="1"]').length,
  stitchPage: document.body.dataset.stitchPage || null,
  bridgeNodes: document.body.dataset.bridgeNodes || null,
}));
console.log(d);
await browser.close();
