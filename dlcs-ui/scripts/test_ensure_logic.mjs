import { chromium } from 'playwright';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage();
await page.goto('http://127.0.0.1:5173/insights',{waitUntil:'networkidle',timeout:60000});
await page.waitForTimeout(5000);
const frame=page.frames().filter(f=>f!==page.mainFrame())[2];
const res=await frame.evaluate(()=>{
  const normalize=(s)=>String(s||'').trim().replace(/\s+/g,' ').toLowerCase();
  const qsa=(selector,root)=>{try{return Array.from((root||document).querySelectorAll(selector));}catch{return[];}};
  const title=qsa('h2').find((el)=>normalize(el.textContent)==='cluster explorer');
  const titleCard=title?title.closest('div'):null;
  const panel=(titleCard && titleCard.parentElement) || (title ? title.closest('section') : null);
  let graphWrap = (panel && panel.querySelector('div.bg-gradient-to-br')) || (panel ? Array.from(panel.querySelectorAll('div')).find((el)=>{
    const classText=String(el.className||'');
    return classText.includes('flex-1') && classText.includes('overflow-hidden');
  }) : null);
  if (!(graphWrap instanceof HTMLElement) && panel) {
    const bubble = Array.from(panel.querySelectorAll('div')).find((el)=>normalize(el.textContent).startsWith('c-'));
    if (bubble instanceof HTMLElement) {
      const bubbleLayer = bubble.closest('div.absolute.inset-0');
      graphWrap = (bubbleLayer && bubbleLayer.parentElement) || bubble.closest('div');
    }
  }
  return {
    hasTitle: !!title,
    titleCardClass: titleCard ? titleCard.className : null,
    panelClass: panel ? panel.className : null,
    panelHasBG: panel ? !!panel.querySelector('div.bg-gradient-to-br') : null,
    graphWrapClass: graphWrap ? graphWrap.className : null,
    isHTMLElement: graphWrap instanceof HTMLElement,
  };
});
console.log(JSON.stringify(res,null,2));
await browser.close();
