import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
page.on('console', (msg) => {
  console.log('console', msg.type(), msg.text());
});
page.on('pageerror', (err) => {
  console.log('pageerror', err.message);
});

await page.goto('http://127.0.0.1:5173/insights', { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForTimeout(5000);

const frames = page.frames();
console.log('frames', frames.map((f) => f.url()));
const target = frames.find((f) => f !== page.mainFrame());
if (!target) {
  console.log('INFO', JSON.stringify({ ok: false, reason: 'no child frame' }, null, 2));
  await browser.close();
  process.exit(0);
}

const info = await target.evaluate(() => {
  const nodeBadge = Array.from(document.querySelectorAll('span')).find((el) => (el.textContent || '').trim().toLowerCase().startsWith('node c-'))?.textContent?.trim() || null;
  const nodesMetric = Array.from(document.querySelectorAll('span')).find((el) => {
    if ((el.textContent || '').trim().toLowerCase() !== 'nodes') return false;
    return true;
  })?.parentElement?.querySelector('span.text-sm')?.textContent?.trim() || null;
  const edgesMetric = Array.from(document.querySelectorAll('span')).find((el) => {
    if ((el.textContent || '').trim().toLowerCase() !== 'edges') return false;
    return true;
  })?.parentElement?.querySelector('span.text-sm')?.textContent?.trim() || null;
  const graphHosts = document.querySelectorAll('[data-insights-graph="1"]').length;
  const graphNodes = document.querySelectorAll('[data-insights-graph="1"] button[data-cluster-key]').length;
  const graphLinks = document.querySelectorAll('[data-insights-graph-links="1"] line').length;
  const topClusterCode = Array.from(document.querySelectorAll('span')).find((el) => (el.textContent || '').includes('• C-'))?.textContent || null;
  const stackTitles = Array.from(document.querySelectorAll('h3.text-sm.font-bold.text-slate-800')).map((el) => (el.textContent || '').trim()).slice(0,3);
  return {
    nodeBadge,
    nodesMetric,
    edgesMetric,
    graphHosts,
    graphNodes,
    graphLinks,
    topClusterCode,
    stackTitles,
    headerHidden: getComputedStyle(document.querySelector('body > header') || document.body).display,
  };
});

console.log('INFO', JSON.stringify(info, null, 2));
await browser.close();
