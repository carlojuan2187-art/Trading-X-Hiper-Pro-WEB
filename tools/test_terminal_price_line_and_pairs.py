"""Prueba offline del precio horizontal y selector de perpetuos (sin órdenes ni red real).
Ejecutar: python tools/test_terminal_price_line_and_pairs.py
"""
from __future__ import annotations
import base64
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE=Path(__file__).resolve().parents[1]/'app/web/static/market'

MOCK = r'''
window.instrumentCalls=[];
window.hyperleagueMarketFetch=async path=>{
 if(path.includes('/instruments')) {
   window.instrumentCalls.push(path);
   return {items:[{symbol:'BTC',display:'BTC/USDC'},{symbol:'ETH',display:'ETH/USDC'},
    ...Array.from({length:720},(_,i)=>{const symbol='COIN'+String(i).padStart(4,'0');return {symbol,display:symbol+'/USDC'}})]};
 }
 if(path.includes('/candles')){
   const base=1735400000000;
   return {items:Array.from({length:210},(_,i)=>{const p=83000+i*.6+Math.sin(i/6)*55;
     return {timestamp:base+i*300000,open:p,high:p+42,low:p-39,close:p+Math.sin(i/3)*25,volume:10+i%31};})};
 }
 if(path.includes('/snapshot'))return {bids:[{price:83100,quantity:4}],asks:[{price:83101,quantity:3}]};
 if(path.includes('/overlays'))return {mode:'DEMO',active:null,history:[]};
 throw Error(path);
};
window.wsSubscriptions=[];
window.WebSocket=class {
 static OPEN=1;
 constructor(url){this.readyState=0;setTimeout(()=>{this.readyState=1;this.onopen?.();},15);}
 send(payload){const x=JSON.parse(payload);if(x.method!=='subscribe')return;
  window.wsSubscriptions.push(x.subscription);
  if(x.subscription.type==='trades')setTimeout(()=>{
    this.onmessage?.({data:JSON.stringify({channel:'trades',data:[{coin:x.subscription.coin,px:'83240',sz:'0.8',side:'B'}]})});
  },120);
  if(x.subscription.type==='candle')setTimeout(()=>{
    this.onmessage?.({data:JSON.stringify({channel:'candle',data:{s:x.subscription.coin,i:x.subscription.interval,t:1735463000000,o:'83200',h:'83280',l:'83120',c:'83230',v:'25'}})});
  },80);
 }
 close(){this.readyState=3;this.onclose?.();}
};
'''

def uri(text: str)->str:
    return 'data:text/javascript;base64,'+base64.b64encode(text.encode()).decode()


def main():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1250,'height':950},device_scale_factor=1)
        page.set_default_timeout(7000)
        errors=[]
        page.on('pageerror',lambda err:errors.append(str(err)))
        css=(BASE/'market-terminal.css').read_text()
        html='''<!doctype html><html><head><style>body{background:#06121b;color:white;font-family:sans-serif}section{max-width:1160px;margin:12px auto}</style><style>'''+css+'''</style></head><body><section data-panel><div data-market-terminal data-mode="DEMO"></div></section></body></html>'''
        page.set_content(html)
        page.evaluate('()=>{'+MOCK+'}')
        module=(BASE/'market-terminal.js').read_text().replace("'./chart-local.js'",repr(uri((BASE/'chart-local.js').read_text())))
        page.evaluate('(src)=>{const s=document.createElement("script");s.type="module";s.src=src;document.body.append(s);}',uri(module))
        page.locator('.hl-local-chart').wait_for()
        page.wait_for_function("document.querySelector('.hl-last-price')?.textContent.includes('83,240')",timeout=8000)
        assert page.locator('.hl-symbol-toggle').count()==1
        page.locator('.hl-symbol-toggle').click()
        page.wait_for_function("document.querySelector('.hl-symbol-caption')?.textContent.includes('722')")
        assert page.locator('.hl-symbol-results').is_visible(), 'Dropdown no se abrió'
        assert page.locator('.hl-symbol-option').count()==80, 'Mostrar primera página del catálogo'
        # Todos los 722 contratos deben ser alcanzables desplazando el menú.
        for _ in range(10):
            page.locator('.hl-symbol-results').evaluate('(el)=>{el.scrollTop=el.scrollHeight;}')
            page.wait_for_timeout(120)
        assert page.locator('.hl-symbol-option').count()==722, 'Faltan contratos en la lista desplegable'
        # Buscar de forma directa cualquier contrato distante del primero.
        page.locator('.hl-symbol').fill('COIN0719')
        page.wait_for_function("document.querySelectorAll('.hl-symbol-option').length===1")
        page.locator('.hl-symbol-option').click()
        assert page.locator('.hl-symbol').input_value()=='COIN0719'
        assert not page.locator('.hl-symbol-results').is_visible()
        page.wait_for_function("window.wsSubscriptions.some(x=>x.coin==='COIN0719')")
        # Volver a BTC y comprobar precio de último tick + línea verde a lo largo del gráfico.
        page.locator('.hl-symbol-toggle').click()
        page.locator('.hl-symbol').fill('BTC')
        page.locator('.hl-symbol').press('Enter')
        page.wait_for_function("document.querySelector('.hl-last-price')?.textContent.includes('83,240')")
        canvas=page.locator('.hl-local-chart')
        pixels=canvas.evaluate('''canvas=>{
          const ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height;
          let green=0;let greenRows=0;let first=null;
          for(let y=20;y<h-155;y++){
             let count=0;
             for(let x=28;x<w-90;x+=7){const p=ctx.getImageData(x,y,1,1).data;
               if(p[1]>120 && p[1]>p[0]*1.8 && p[1]>p[2]*1.20)count++;
             }
             if(count>=35){greenRows++;if(first===null)first=y;}
          }
          return {greenRows,first,width:w,height:h};
        }''')
        assert pixels['greenRows']>0, f'No se trazó línea de precio horizontal: {pixels}'
        assert len(page.evaluate('window.instrumentCalls'))==1, 'Catálogo consultado más de una vez'
        assert not errors, errors
        page.screenshot(path='/mnt/data/HyperLeague_linea_precio_y_pares.png',full_page=True)
        page.set_viewport_size({'width':393,'height':852})
        page.locator('.hl-symbol-toggle').click()
        assert page.locator('.hl-symbol-results').is_visible()
        assert page.locator('.hl-symbol-results').evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+2}'), 'Selector desbordado en móvil'
        page.screenshot(path='/mnt/data/HyperLeague_pares_movil.png',full_page=True)

        print('OK: línea de precio por tick, dropdown 722/722, búsqueda/selección COIN0719, WebSocket re-suscrito y sin errores JS')
        print('canvas',pixels)
        browser.close()

if __name__=='__main__': main()
