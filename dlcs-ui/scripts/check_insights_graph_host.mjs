import { chromium } from 'playwright';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.goto('http://127.0.0.1:5173/insights', { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForTimeout(5000);
const frame = page.frames().find((f) => f !== page.mainFrame() && f.url()==='about:srcdoc' && /Glass Narrative Intelligence/.test(f.title?.toString?.()||''));
const frames = page.frames().filter((f)=>f!==page.mainFrame());
let target = null;
for (const f of frames) {
  const t = await f.evaluate(() => document.title || '');
  if (t.includes('Narrative Intelligence')) { target = f; break; }
}
if (!target) {
  console.log('no insights frame');
  await browser.close();
  process.exit(0);
}
const res = await target.evaluate(() => {
  const hostCount = document.querySelectorAll('[data-insights-graph="1"]').length;
  const linkCount = document.querySelectorAll('[data-insights-graph-links="1"] line').length;
  const nodeCount = document.querySelectorAll('[data-insights-graph="1"] button[data-cluster-key]').length;
  const titles = Array.from(document.querySelectorAll('h3.text-sm.font-bold.text-slate-800')).map((el)=>(el.textContent||'').trim()).slice(0,3);
  const sampleDivs = Array.from(document.querySelectorAll('div')).map((el)=>(el.className||'')).filter((c)=>String(c).includes('bg-gradient-to-br')).slice(0,5);
  return { hostCount, linkCount, nodeCount, titles, sampleDivs };
});
console.log(res);
await browser.close();
