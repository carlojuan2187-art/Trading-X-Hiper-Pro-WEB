"""Optional local Playwright smoke test; not executed by Railway."""
from __future__ import annotations
import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

STATIC=Path(__file__).resolve().parents[1]/'app/web/static'

FIXTURE='''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/market/market-terminal.css"></head><body style="margin:0;background:#050e17;color:white"><section data-panel><div data-market-terminal data-mode="DEMO"></div></section><script type="module" src="/market/market-terminal.js"></script></body></html>'''
MOCK='''
window.hyperleagueMarketFetch=async path=>{
 const base=1735400000000;const bars=[];
 for(let i=0;i<210;i++){const p=100+i*.065+Math.sin(i/8)*2;bars.push({timestamp:base+i*300000,open:p,high:p+1.1,low:p-1.2,close:p+(.5*Math.sin(i/3)),volume:10+i%23});}
 if(path.includes('/instruments'))return {items:[{symbol:'BTC',display:'BTC/USDC'},{symbol:'ETH',display:'ETH/USDC'},{symbol:'SOL',display:'SOL/USDC'}]};
 if(path.includes('/candles'))return {items:bars};
 if(path.includes('/snapshot'))return {bids:[{price:110.3,quantity:5},{price:110.2,quantity:3}],asks:[{price:110.4,quantity:2},{price:110.5,quantity:5}]};
 if(path.includes('/overlays'))return {mode:'DEMO',active:null,history:[]};
 throw Error(path);
};
window.WebSocket=class{
 static OPEN=1;constructor(url){this.url=url;this.readyState=0;setTimeout(()=>{this.readyState=1;this.onopen?.();},20);}
 send(raw){const req=JSON.parse(raw);if(req.method!=='subscribe')return;
   if(req.subscription.type==='l2Book')setTimeout(()=>{this.onmessage?.({data:JSON.stringify({channel:'l2Book',data:{coin:req.subscription.coin,levels:[[{px:'110.30',sz:'5.3'},{px:'110.20',sz:'8.4'}],[{px:'110.40',sz:'3.2'},{px:'110.50',sz:'7.2'}]]}})});},60);
   if(req.subscription.type==='candle')setTimeout(()=>{this.onmessage?.({data:JSON.stringify({channel:'candle',data:{s:req.subscription.coin,i:req.subscription.interval,t:1735463000000,o:'120',h:'125',l:'118',c:'124',v:'25'}})});},150);
 }
 close(){this.readyState=3;}
};
'''

def main():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':393,'height':852},device_scale_factor=1)
        errors=[];page.on('pageerror',lambda exc:errors.append(str(exc)))
        html=FIXTURE.replace('<link rel="stylesheet" href="/market/market-terminal.css">',
             '<style>'+ (STATIC/'market/market-terminal.css').read_text() + '</style>').replace('<script type="module" src="/market/market-terminal.js"></script>','')
        page.set_content(html)
        page.evaluate("()=>{ " + MOCK + " }")
        local=(STATIC/'market/chart-local.js').read_text()
        local_url='data:text/javascript;base64,'+base64.b64encode(local.encode()).decode()
        main=(STATIC/'market/market-terminal.js').read_text().replace("'./chart-local.js'",repr(local_url))
        main_url='data:text/javascript;base64,'+base64.b64encode(main.encode()).decode()
        page.evaluate('(url)=>{const s=document.createElement("script");s.type="module";s.src=url;document.body.append(s);}',main_url)
        page.locator('.hl-local-chart').wait_for(timeout=8000)
        page.locator('.hl-depth.ask').first.wait_for(timeout=8000)
        assert page.locator('.hl-depth.bid').count()>0
        page.wait_for_function("document.querySelector('.hl-last-price')?.textContent !== '—'",timeout=6000)
        page.locator('.hl-indicator').first.select_option('EMA')
        page.locator('.hl-indicator').nth(1).select_option('RSI')
        page.locator('.hl-timeframe').select_option('1h')
        page.locator('.hl-symbol').fill('ETH')
        page.locator('.hl-symbol').press('Enter')
        assert page.locator('.hl-symbol').input_value()=='ETH'
        page.locator('.hl-zoom').first.click()
        page.screenshot(path='/mnt/data/HyperLeague_terminal_v2_mobile.png',full_page=True)
        assert not errors,errors
        print('OK: velas, profundidad, tick, EMA/RSI, timeframe, ETH, zoom, sin JS errors')
        browser.close()
if __name__=='__main__':main()
