import { chromium } from 'playwright';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.goto('http://127.0.0.1:5173/insights', { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForTimeout(5000);
const frame = page.frames().find((f) => (f.url()==='about:srcdoc' && f !== page.mainFrame() && true));
const frames = page.frames().filter((f)=>f!==page.mainFrame());
for (let i=0;i<frames.length;i++) {
  const f=frames[i];
  const d = await f.evaluate(() => {
    const title=document.title;
    const nLabel = Array.from(document.querySelectorAll('span')).find((el)=>(el.textContent||'').trim().toLowerCase()==='nodes');
    const eLabel = Array.from(document.querySelectorAll('span')).find((el)=>(el.textContent||'').trim().toLowerCase()==='edges');
    const nVal = nLabel?.parentElement?.querySelector('span.text-sm')?.textContent?.trim() || null;
    const eVal = eLabel?.parentElement?.querySelector('span.text-sm')?.textContent?.trim() || null;
    const h2 = Array.from(document.querySelectorAll('h2')).map((el)=>(el.textContent||'').trim());
    return {title, nVal, eVal, h2};
  });
  console.log(i, d);
}
await browser.close();
