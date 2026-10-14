"""Regresión offline: el modo fullscreen amplía el gráfico, nunca Order Book ni Trades.
Ejecutar: python tools/test_terminal_fullscreen_chart_only.py
No utiliza datos reales ni conecta con el exchange.
"""
from __future__ import annotations
import base64
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'app/web/static/market'
sys.path.insert(0, str(ROOT / 'tools'))
from test_terminal_price_line_and_pairs import MOCK


def uri(text: str) -> str:
    return 'data:text/javascript;base64,' + base64.b64encode(text.encode()).decode()


def assert_stage(page):
    return page.evaluate('''() => {
      const stage = document.querySelector('.hl-chart-stage');
      const r = stage.getBoundingClientRect();
      const canvas = stage.querySelector('.hl-canvas').getBoundingClientRect();
      return {fs: document.fullscreenElement === stage || stage.classList.contains('hl-fullscreen-fallback'),
        inStage: stage.contains(document.querySelector('.hl-canvas')),
        bookOutside: !stage.contains(document.querySelector('.hl-book')),
        tradesOutside: !stage.contains(document.querySelector('.hl-trades')),
        chartFraction: canvas.height / innerHeight,
        stageHeight: r.height, viewportHeight: innerHeight,
        microVisible: getComputedStyle(document.querySelector('.hl-micro')).display !== 'none',
        rootFullscreen: document.fullscreenElement === document.querySelector('.hl-terminal')};
    }''')


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1366, 'height': 768}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        html = '''<!doctype html><html><head><style>body{background:#06121b;color:white;font-family:sans-serif;margin:0}
          section{max-width:1340px;margin:auto}</style><style>''' + (ASSETS / 'market-terminal.css').read_text() + '''</style></head>
          <body><section data-panel><div class="hl-terminal" data-market-terminal data-mode="DEMO"></div></section></body></html>'''
        page.set_content(html)
        page.evaluate('()=>{' + MOCK + '}')
        module = (ASSETS / 'market-terminal.js').read_text().replace("'./chart-local.js'", repr(uri((ASSETS / 'chart-local.js').read_text())))
        page.evaluate('(src)=>{const s=document.createElement("script");s.type="module";s.src=src;document.body.append(s);}', uri(module))
        page.locator('.hl-local-chart').wait_for()
        page.wait_for_function("document.querySelector('.hl-book .hl-row') !== null")
        page.locator('.hl-fullscreen').click()
        page.wait_for_function("document.querySelector('.hl-chart-stage') === document.fullscreenElement")
        page.wait_for_timeout(180)
        d = assert_stage(page)
        assert d['fs'] and d['inStage'] and d['bookOutside'] and d['tradesOutside'], d
        assert d['chartFraction'] > .60, f'Gráfico no ocupa pantalla: {d}'
        assert d['stageHeight'] >= d['viewportHeight']-2, d
        assert not d['rootFullscreen'], d
        assert page.locator('.hl-fullscreen').inner_text() == 'Salir de pantalla completa'
        page.screenshot(path='/mnt/data/HyperLeague_fullscreen_grafico_PC.png')
        page.locator('.hl-fullscreen').click()
        page.wait_for_function('document.fullscreenElement === null')
        assert page.locator('.hl-fullscreen').inner_text() == 'Pantalla completa'
        assert page.locator('.hl-micro').is_visible(), 'Order Book no fue restaurado al salir'
        # En móviles sin Element.requestFullscreen se usa una superposición CSS segura.
        page.set_viewport_size({'width':393,'height':852})
        page.evaluate('Element.prototype.requestFullscreen = undefined')
        page.locator('.hl-fullscreen').click()
        page.wait_for_function("document.querySelector('.hl-chart-stage').classList.contains('hl-fullscreen-fallback')")
        page.wait_for_timeout(150)
        d2 = assert_stage(page)
        assert d2['chartFraction'] > .67, f'Gráfico demasiado pequeño en móvil: {d2}'
        assert d2['bookOutside'] and d2['tradesOutside'], d2
        assert page.locator('.hl-symbol-picker').is_visible(), 'Se perdió el selector de pares'
        page.screenshot(path='/mnt/data/HyperLeague_fullscreen_grafico_MOVIL.png')
        page.keyboard.press('Escape')
        page.wait_for_function("!document.querySelector('.hl-chart-stage').classList.contains('hl-fullscreen-fallback')")
        assert not page.evaluate("document.body.classList.contains('hl-fullscreen-lock')")
        assert not errors, errors
        print('OK: pantalla completa solo gráfico, velas ocupan ventana PC/móvil; order book y tape fuera; Esc y salida funcionan.')
        print('PC:', d)
        print('Móvil:', d2)
        browser.close()

if __name__=='__main__': main()
