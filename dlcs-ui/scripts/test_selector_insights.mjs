import { chromium } from 'playwright';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage();
await page.goto('http://127.0.0.1:5173/insights',{waitUntil:'networkidle',timeout:60000});
await page.waitForTimeout(5000);
const frame=page.frames().filter(f=>f!==page.mainFrame())[2];
const res=await frame.evaluate(()=>{
  const sels=[
    'div.flex-1.relative.overflow-hidden.bg-gradient-to-br',
    'div.bg-gradient-to-br',
    'h2',
    'div.flex.flex-col.h-\\[60\\%\\]'
  ];
  const out={};
  for(const s of sels){
    try{ out[s]=!!document.querySelector(s);}catch(e){ out[s]='ERR '+String(e);}
  }
  const h2=Array.from(document.querySelectorAll('h2')).map(el=>el.textContent?.trim());
  return {out,h2};
});
console.log(JSON.stringify(res,null,2));
await browser.close();
