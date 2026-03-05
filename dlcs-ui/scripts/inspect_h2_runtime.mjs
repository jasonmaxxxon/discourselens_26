import { chromium } from 'playwright';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage();
await page.goto('http://127.0.0.1:5173/insights',{waitUntil:'networkidle',timeout:60000});
await page.waitForTimeout(6000);
const frame=page.frames().filter(f=>f!==page.mainFrame())[2];
const res=await frame.evaluate(()=>{
  const normalize=(s)=>String(s||'').trim().replace(/\s+/g,' ').toLowerCase();
  const h2=Array.from(document.querySelectorAll('h2')).map((el)=>({raw:(el.textContent||''),norm:normalize(el.textContent)}));
  const divs=Array.from(document.querySelectorAll('div')).slice(0,20).map((el)=>String(el.className||''));
  return {h2};
});
console.log(JSON.stringify(res,null,2));
await browser.close();
