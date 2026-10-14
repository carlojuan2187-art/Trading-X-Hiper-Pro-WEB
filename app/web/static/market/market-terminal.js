/* Hyper League: KAELEON's KLineCharts 10.0.3 approach, adapted for Hyperliquid.
 * Trading overlays are read-only and never interact with order execution.
 * Frontend browser sends ONLY public Hyperliquid market subscriptions.
 */
// Un único motor determinista en PC y móvil: la carga CDN causaba dos APIs distintas
// y a veces inicializaba KLineCharts, a veces el fallback incompleto.
import { init, registerOverlay } from './chart-local.js';

const WS_URL = 'wss://api.hyperliquid.xyz/ws';
const FRAMES = {
  '1m': { type: 'minute', span: 1 }, '3m': { type: 'minute', span: 3 },
  '5m': { type: 'minute', span: 5 }, '15m': { type: 'minute', span: 15 },
  '30m': { type: 'minute', span: 30 }, '1h': { type: 'hour', span: 1 },
  '4h': { type: 'hour', span: 4 }, '1d': { type: 'day', span: 1 },
};
const KEY = 'hyperleague-level', ZONE = 'hyperleague-zone', MARK = 'hyperleague-marker';
const GROUPS = { levels: 'hl-levels', zones: 'hl-zones', markers: 'hl-markers', drawings: 'hl-drawings' };
const fmt = (v, digits = 5) => Number.isFinite(Number(v)) ? Number(v).toLocaleString('en-US', { maximumFractionDigits: digits }) : '—';
const num = value => Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : null;
const coin = symbol => String(symbol || '').toUpperCase().replace(/[-/](USDC|USDT)$/i, '').replace(/_USDC$/i, '').replace(/_USDT$/i, '');
const frame = period => Object.keys(FRAMES).find(k => FRAMES[k].type === period?.type && FRAMES[k].span === period?.span) || '5m';
const bar = row => {
  const timestamp = Number(row.timestamp ?? row.t);
  const open = Number(row.open ?? row.o), high = Number(row.high ?? row.h);
  const low = Number(row.low ?? row.l), close = Number(row.close ?? row.c);
  const volume = Number(row.volume ?? row.v ?? 0);
  if (![timestamp, open, high, low, close, volume].every(Number.isFinite) || timestamp <= 0 || low <= 0 ||
      high < Math.max(open, close) || low > Math.min(open, close) || volume < 0) return null;
  return { timestamp, open, high, low, close, volume };
};

// Copied/adapted conceptually from the KAELEON repository's three locked overlays.
registerOverlay({
  name: KEY, totalStep: 2, lock: true, fixedZLevel: true, needDefaultPointFigure: false,
  needDefaultXAxisFigure: false, needDefaultYAxisFigure: false,
  createPointFigures: ({ coordinates, bounding, overlay }) => {
    const y = Number(coordinates?.[0]?.y);
    if (!Number.isFinite(y)) return [];
    const d = overlay.extendData || {}, color = d.color || '#36d5ff';
    const nearTop = y < 30;
    return [
      { key: 'line', type: 'line', attrs: { coordinates: [{ x: 0, y }, { x: bounding.width, y }] },
        styles: { color, size: d.label === 'ENTRY' ? 2 : 1, style: 'dashed', dashedValue: [6, 4] }, ignoreEvent: true },
      { key: 'label', type: 'text', attrs: { x: 8, y: nearTop ? y + 5 : y - 5,
          text: `${d.label}  ${d.value}`, align: 'left', baseline: nearTop ? 'top' : 'bottom' },
        styles: { style: 'fill', color, size: 11, weight: 700, paddingLeft: 5, paddingRight: 5,
          paddingTop: 3, paddingBottom: 3, backgroundColor: 'rgba(3,16,25,.92)',
          borderColor: color, borderSize: 1, borderRadius: 4 }, ignoreEvent: true },
    ];
  },
});
registerOverlay({
  name: ZONE, totalStep: 3, lock: true, needDefaultPointFigure: false,
  needDefaultXAxisFigure: false, needDefaultYAxisFigure: false,
  createPointFigures: ({ coordinates, bounding, overlay }) => {
    const a = Number(coordinates?.[0]?.y), b = Number(coordinates?.[1]?.y);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return [];
    return [{ key: 'zone', type: 'rect', attrs: { x: 0, y: Math.min(a, b), width: bounding.width, height: Math.max(1, Math.abs(a - b)) },
      styles: { style: 'fill', color: overlay.extendData?.color || 'rgba(0,220,145,.05)' }, ignoreEvent: true }];
  },
});
registerOverlay({
  name: MARK, totalStep: 2, lock: true, needDefaultPointFigure: false,
  needDefaultXAxisFigure: false, needDefaultYAxisFigure: false,
  createPointFigures: ({ coordinates, overlay }) => {
    const x = Number(coordinates?.[0]?.x), y = Number(coordinates?.[0]?.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return [];
    const d = overlay.extendData || {}, color = d.color || '#36cfff';
    return [
      { key: 'dot', type: 'circle', attrs: { x, y, r: 4 },
        styles: { style: 'fill', color, borderColor: '#04111a', borderSize: 1 }, ignoreEvent: true },
      { key: 'label', type: 'text', attrs: { x, y: d.top ? y - 10 : y + 10, text: d.label || '',
          align: 'center', baseline: d.top ? 'bottom' : 'top' },
        styles: { style: 'fill', color, size: 9, weight: 800, backgroundColor: 'rgba(3,16,25,.92)',
          borderColor: color, borderSize: 1, borderRadius: 3, paddingLeft: 4, paddingRight: 4,
          paddingTop: 2, paddingBottom: 2 }, ignoreEvent: true },
    ];
  },
});

function el(name, cls = '', value) {
  const element = document.createElement(name);
  if (cls) element.className = cls;
  if (value !== undefined) element.textContent = String(value);
  return element;
}

async function getApi(path) {
  if (typeof window.hyperleagueMarketFetch === 'function') return window.hyperleagueMarketFetch(path);
  const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

class Terminal {
  constructor(root) {
    this.root = root;
    this.mode = root.dataset.mode;
    this.symbol = 'BTC';
    this.timeframe = '5m';
    this.follow = true;
    this.overlayData = null;
    this.overlayKey = '';
    this.ws = null;
    this.wsGeneration = 0;
    this.chart = null;
    this.active = false;
    this.closed = false;
    this.reconnect = 0;
    this.book = null;
    this.trades = [];
    this.indicatorId = null;
    this.instruments = new Set();
    this.instrumentGeneration = 0;
    this.quotePrice = null;
    this.prevPrice = null;
    this.priceSource = null;
    this.microPending = false;
    this.subIndicatorId = null;
    this.renderShell();
    this.panel = this.root.closest('[data-panel]');
    this.visibilityObserver = new MutationObserver(() => this.run());
    if (this.panel) this.visibilityObserver.observe(this.panel, { attributes: true, attributeFilter: ['class'] });
    document.addEventListener('visibilitychange', () => this.run());
    window.addEventListener('hl:realtime', event => {
      const data = event.detail;
      if (data?.type !== 'overlays' || data.mode !== this.mode) return;
      this.overlayData = data.data;
      const active = data.data?.active;
      if (active?.symbol && this.follow && coin(active.symbol) !== this.symbol) this.choose(coin(active.symbol), true);
      this.applyOverlays(); this.updateSummary();
    });
    this.run();
  }
  renderShell() {
    this.root.replaceChildren();
    const head = el('div', 'hl-head');
    const heading = el('div', 'hl-heading');
    heading.append(el('small', 'hl-eyebrow', `${this.mode} · HYPERLIQUID`), el('h3', '', 'Terminal de mercado'));
    const ticker = el('div','hl-ticker');
    this.tickPrice = el('strong','hl-last-price','—');
    this.tickArrow = el('span','hl-tick-arrow','●');
    ticker.append(this.tickArrow,this.tickPrice);
    heading.append(ticker);
    head.append(heading);
    const controls = el('div', 'hl-controls');
    const symbolInput = el('input', 'hl-symbol');
    symbolInput.setAttribute('aria-label', 'Buscar contrato');
    symbolInput.placeholder = 'BTC, ETH, SOL…';
    symbolInput.value = 'BTC';
    symbolInput.autocomplete = 'off';
    const suggestions = el('datalist');
    suggestions.id = `hl-symbols-${this.mode.toLowerCase()}`;
    symbolInput.setAttribute('list', suggestions.id);
    const confirmSymbol = () => {
      const candidate = coin(symbolInput.value);
      if (this.instruments.has(candidate)) this.choose(candidate, false);
      else {this.notice('Contrato no disponible: selecciona un par de la lista.', 'error');symbolInput.value=this.symbol;}
    };
    symbolInput.addEventListener('change', confirmSymbol);
    symbolInput.addEventListener('keydown', event=>{if(event.key==='Enter'){event.preventDefault();confirmSymbol();symbolInput.blur();}});
    const picker=el('div','hl-symbol-picker');
    const results=el('div','hl-symbol-results');results.setAttribute('role','listbox');results.hidden=true;
    picker.append(symbolInput,suggestions,results);
    this.symbolPicker=picker;this.symbolResults=results;
    symbolInput.addEventListener('focus',()=>this.renderSymbolResults(symbolInput.value));
    symbolInput.addEventListener('blur',()=>setTimeout(()=>{results.hidden=true;},180));
    this.symbolInput = symbolInput;
    this.suggestions = suggestions;
    this.symbolSearchTimer = null;
    symbolInput.addEventListener('input', () => {
      clearTimeout(this.symbolSearchTimer);
      this.symbolSearchTimer = setTimeout(() => this.loadInstruments(symbolInput.value), 180);
    });
    const period = el('select', 'hl-timeframe');
    period.setAttribute('aria-label', 'Temporalidad');
    Object.keys(FRAMES).forEach(k => { const item = el('option', '', k.toUpperCase()); item.value = k; period.append(item); });
    period.value = this.timeframe;
    period.addEventListener('change', () => { this.timeframe = period.value; this.chart?.setPeriod(FRAMES[this.timeframe]); });
    const draw = el('select', 'hl-tool');
    draw.setAttribute('aria-label', 'Herramientas de dibujo');
    [['', 'Dibujar…'], ['straightLine', 'Tendencia'], ['horizontalStraightLine', 'Horizontal'],
      ['fibonacciLine', 'Fibonacci'], ['segment', 'Segmento']].forEach(([v, t]) => { const o = el('option', '', t); o.value = v; draw.append(o); });
    draw.addEventListener('change', () => {
      if (draw.value && this.chart) this.chart.createOverlay({ name: draw.value, groupId: GROUPS.drawings });
      draw.value = '';
    });
    const indicator = el('select', 'hl-indicator');
    indicator.setAttribute('aria-label', 'Indicador principal');
    for (const name of ['NONE', 'MA', 'EMA', 'BOLL']) { const opt = el('option', '', name); opt.value = name; indicator.append(opt); }
    indicator.addEventListener('change', () => {
      if (!this.chart) return;
      if (this.indicatorId) this.chart.removeIndicator({ id: this.indicatorId });
      this.indicatorId = indicator.value === 'NONE' ? null : this.chart.createIndicator({ name: indicator.value, paneId: 'candle_pane' });
      this.notice(`Indicador ${indicator.value} aplicado al gráfico`);
    });
    const subIndicator = el('select', 'hl-indicator');
    subIndicator.setAttribute('aria-label', 'Indicador inferior');
    for (const name of ['VOL', 'MACD', 'RSI']) { const opt = el('option', '', name); opt.value = name; subIndicator.append(opt); }
    subIndicator.value = 'VOL';
    subIndicator.addEventListener('change', () => {
      if (!this.chart) return;
      if (this.subIndicatorId) this.chart.removeIndicator({ id: this.subIndicatorId });
      this.subIndicatorId = this.chart.createIndicator({ name: subIndicator.value, paneId: 'hl_sub_pane' });
      this.notice(`Panel ${subIndicator.value} activado`);
    });
    const follow = el('button', 'hl-follow', 'Ver operación');
    follow.type = 'button';
    follow.addEventListener('click', () => {
      const wanted = this.overlayData?.active?.symbol || this.overlayData?.history?.[0]?.symbol;
      if (wanted) this.choose(coin(wanted), true);
    });
    this.followButton = follow;
    const fullscreen = el('button', 'hl-fullscreen', 'Pantalla completa');
    fullscreen.type = 'button';
    fullscreen.addEventListener('click', () => {
      if (document.fullscreenElement) document.exitFullscreen();
      else this.root.requestFullscreen?.();
    });
    const zoomIn = el('button', 'hl-zoom', '＋'); zoomIn.type='button'; zoomIn.title='Acercar velas';
    zoomIn.addEventListener('click',()=>this.chart?.zoom?.(-1));
    const zoomOut = el('button', 'hl-zoom', '－'); zoomOut.type='button'; zoomOut.title='Alejar velas';
    zoomOut.addEventListener('click',()=>this.chart?.zoom?.(1));
    const clear = el('button', 'hl-clear', 'Borrar dibujos');clear.type='button';
    clear.addEventListener('click',()=>this.chart?.removeOverlay({groupId:GROUPS.drawings}));
    controls.append(picker, period, indicator, subIndicator, draw, zoomIn, zoomOut, clear, follow, fullscreen);
    head.append(controls);
    this.root.append(head);
    this.state = el('div', 'hl-state', 'Conectando datos de mercado…');
    this.state.setAttribute('role', 'status');
    this.root.append(this.state);
    const summary = el('div', 'hl-summary'); this.summary = summary; this.root.append(summary);
    const surface = el('div', 'hl-surface'); this.surface = surface;
    const canvas = el('div', 'hl-canvas'); this.canvas = canvas; surface.append(canvas); this.root.append(surface);
    const micro = el('div', 'hl-micro');
    const book = el('div', 'hl-book'); book.append(el('h4', '', 'Order Book')); this.bookNode = el('div'); book.append(this.bookNode);
    const tape = el('div', 'hl-trades'); tape.append(el('h4', '', 'Trades públicos')); this.tradesNode = el('div'); tape.append(this.tradesNode);
    micro.append(book, tape); this.root.append(micro);
  }
  notice(value, kind = 'info') { this.state.textContent = String(value); this.state.dataset.kind = kind; }
  visible() {
    // display:none panel cannot be measured by the canvas library.
    return Boolean(this.root.offsetWidth && this.root.offsetHeight && !document.hidden);
  }
  async run() {
    if (this.closed) return;
    if (!this.visible()) {
      if (this.active) { this.active = false; this.closeSocket(); }
      return;
    }
    if (!this.active) {
      this.active = true;
      if (!this.chart) this.mount();
      else this.chart.resize();
      void this.loadInstruments('');
      this.startSocket();
    }
    if (this.loadingOverlays || this.overlayData) return;
    this.loadingOverlays = true;
    try {
      const result = await getApi(`/api/v1/market/overlays?mode=${this.mode}`);
      this.overlayData = result;
      const active = result.active;
      if (active?.symbol && this.follow && coin(active.symbol) !== this.symbol) this.choose(coin(active.symbol), true);
      this.applyOverlays();
      this.updateSummary();
    } catch (e) { this.notice(`No se pudieron sincronizar operaciones: ${e.message}`, 'error'); }
    finally { this.loadingOverlays = false; }
  }
  async loadInstruments(search) {
    if (!this.active) return;
    const generation=++this.instrumentGeneration;
    try {
      // El catálogo es metadata, no precio: se consulta solo una vez por sesión.
      const items=this.instrumentCache || (await getApi('/api/v1/market/instruments?limit=500')).items;
      if (Array.isArray(items)) this.instrumentCache=items;
      if(generation!==this.instrumentGeneration || !this.active)return;
      if(!Array.isArray(items)) throw new Error('Catálogo inválido');
      this.instruments=new Set(items.map(x=>x.symbol));
      const q=coin(search); const filtered=items.filter(x=>!search || x.symbol.includes(q)).slice(0,120);
      this.suggestions.replaceChildren(...filtered.map(item=>{const node=el('option');node.value=item.symbol;node.label=item.display||`${item.symbol}/USDC`;return node;}));
      this.renderSymbolResults(search);
    } catch(e){if(generation===this.instrumentGeneration) this.notice(`Catálogo no disponible: ${e.message}`,'error');}
  }
  renderSymbolResults(search){
    if(!this.symbolResults||!this.instrumentCache)return;
    const q=coin(search);
    const choices=this.instrumentCache.filter(item=>!search||item.symbol.includes(q)).slice(0,24);
    this.symbolResults.replaceChildren(...choices.map(item=>{
      const choice=el('button','hl-symbol-option',item.display||`${item.symbol}/USDC`);
      choice.type='button';choice.setAttribute('role','option');
      choice.addEventListener('pointerdown',e=>e.preventDefault());
      choice.addEventListener('click',()=>{this.choose(item.symbol,false);this.symbolResults.hidden=true;});
      return choice;
    }));
    this.symbolResults.hidden=!choices.length||document.activeElement!==this.symbolInput;
  }
  choose(symbol, follow) {
    if(!this.instruments.has(symbol) && !follow){this.notice('Contrato no disponible en el catálogo','error');return;}
    if(!symbol || (symbol===this.symbol && !follow)) { this.symbolInput.value=this.symbol; return; }
    this.follow = follow;
    this.symbol = symbol;
    this.symbolInput.value = symbol;
    this.book = null; this.trades = []; this.assetContext = null; this.prevPrice=null;this.quotePrice=null;this.priceSource=null;
    this.tickPrice.textContent='—';this.tickPrice.dataset.direction='flat';this.tickArrow.textContent='●';this.tickArrow.dataset.direction='flat';
    this.drawMicro();
    this.overlayKey = '';
    if (this.chart) this.chart.setSymbol({ ticker: symbol, pricePrecision: 6, volumePrecision: 4 });
    // setSymbol provoca unsubscribe/subscribe y abre exactamente un WebSocket.
    this.applyOverlays(); this.updateSummary();
  }
  mount() {
    try {
      this.chart = init(this.canvas, {
        layout: { barSpaceLimit: { min: 4, max: 44 }, yAxis: { position: 'right', inside: false } },
        styles: {
          grid: { horizontal: { show: true, color: '#17303b', style: 'dashed', dashedValue: [3, 4] },
            vertical: { show: true, color: '#112631', style: 'dashed', dashedValue: [3, 4] } },
          candle: { type: 'candle_solid', bar: { upColor: '#00df91', downColor: '#ff466d',
            upBorderColor: '#00df91', downBorderColor: '#ff466d', upWickColor: '#00df91', downWickColor: '#ff466d' } },
        },
      });
      if (!this.chart) throw new Error('La librería no pudo inicializarse');
      this.chart.setDataLoader({
        getBars: async ({ type, symbol, period, callback }) => {
          if (type !== 'init') { callback([], false); return; }
          try {
            const data = await getApi(`/api/v1/market/candles?symbol=${encodeURIComponent(symbol.ticker)}&timeframe=${frame(period)}&limit=400`);
            callback((data.items || []).map(bar).filter(Boolean), false);
            if (!data.items?.length) this.notice('Sin velas históricas para este contrato.', 'error');
          } catch (e) { callback([], false); this.notice(`No se pudo cargar el histórico: ${e.message}`, 'error'); }
        },
        subscribeBar: ({ symbol, period, callback }) => {
          this.currentCallback = callback;
          this.currentBarKey = `${coin(symbol.ticker)}|${frame(period)}`;
          this.closeSocket(); this.startSocket();
        },
        unsubscribeBar: () => { this.currentCallback = null; this.currentBarKey = null; this.closeSocket(); },
      });
      this.chart.setSymbol({ ticker: this.symbol, pricePrecision: 6, volumePrecision: 4 });
      this.chart.setPeriod(FRAMES[this.timeframe]);
      this.subIndicatorId = this.chart.createIndicator({ name: 'VOL', paneId: 'hl_sub_pane' });
      this.applyOverlays();
    } catch (e) { this.notice(`No se pudo inicializar KLineCharts: ${e.message}`, 'error'); }
  }
  startSocket() {
    if (!this.active || !this.chart || !this.currentBarKey || this.ws || !this.visible()) return;
    const [wantedSymbol, wantedFrame] = this.currentBarKey.split('|');
    const epoch = ++this.wsGeneration;
    let ws;
    try { ws = new WebSocket(WS_URL); } catch { this.onDisconnected(epoch); return; }
    this.ws = ws;
    void getApi(`/api/v1/market/snapshot?symbol=${encodeURIComponent(wantedSymbol)}`)
      .then(book => {
        if (epoch !== this.wsGeneration || !this.active) return;
        if (Array.isArray(book.bids) && Array.isArray(book.asks)) {
          this.book = book;
          this.scheduleMicro();
        }
      }).catch(() => {});
    this.notice('Conectando WebSocket público de Hyperliquid…');
    ws.onopen = () => {
      if (epoch !== this.wsGeneration) return;
      this.reconnect = 0;
      for (const subscription of [
        { type: 'candle', coin: wantedSymbol, interval: wantedFrame },
        { type: 'l2Book', coin: wantedSymbol },
        { type: 'trades', coin: wantedSymbol },
        { type: 'activeAssetCtx', coin: wantedSymbol },
      ]) ws.send(JSON.stringify({ method: 'subscribe', subscription }));
      this.notice('● Mercado EN VIVO · Hyperliquid');
      if (this.wsPing) clearInterval(this.wsPing);
      this.wsPing = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ method: 'ping' })); }, 25000);
    };
    ws.onmessage = event => {
      if (epoch !== this.wsGeneration) return;
      try {
        const packet = JSON.parse(event.data);
        if (!packet || typeof packet !== 'object') return;
        if (packet.channel === 'candle') {
          const current = packet.data;
          if (coin(current?.s) !== wantedSymbol || current?.i !== wantedFrame) return;
          const item = bar(current);
          if (item && this.currentBarKey === `${wantedSymbol}|${wantedFrame}`) {
            this.paintTick(item.close,'candle');this.currentCallback?.(item);
          }
        } else if (packet.channel === 'l2Book' && coin(packet.data?.coin) === wantedSymbol) {
          this.readBook(packet.data);
          this.updateSummary();
        } else if (packet.channel === 'trades') {
          const rows = (Array.isArray(packet.data) ? packet.data : []).filter(t => coin(t.coin) === wantedSymbol);
          this.trades = [...rows, ...this.trades].slice(0, 20); this.scheduleMicro();
          const newest=rows[rows.length-1];if(newest) this.paintTick(Number(newest.px),'trade');
        } else if (packet.channel === 'activeAssetCtx' && coin(packet.data?.coin) === wantedSymbol) {
          this.assetContext = packet.data?.ctx || {};
          this.paintTick(this.assetContext?.markPx,'mark'); this.updateSummary();
        }
      } catch { /* Drop malformed public frames, never inject HTML */ }
    };
    ws.onerror = () => { this.notice('Reconectando datos de Hyperliquid…', 'error'); };
    ws.onclose = () => { if (epoch === this.wsGeneration) { this.ws = null; this.onDisconnected(epoch); } };
  }
  onDisconnected(epoch) {
    if (epoch !== this.wsGeneration || !this.active) return;
    this.notice('Socket desconectado: datos detenidos hasta reconexión automática.', 'error');
    if (this.retryTimer) clearTimeout(this.retryTimer);
    const wait = Math.min(12_000, 1_000 * 2 ** Math.min(this.reconnect++, 4));
    this.retryTimer = setTimeout(() => { this.retryTimer = null; this.startSocket(); }, wait);
  }
  closeSocket() {
    this.wsGeneration++;
    if (this.wsPing) { clearInterval(this.wsPing); this.wsPing = null; }
    if (this.ws) { this.ws.onclose = null; this.ws.close(); }
    this.ws = null;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
  }
  readBook(raw) {
    const levels = raw?.levels;
    if (!Array.isArray(levels) || levels.length < 2) return;
    const valid = items => (Array.isArray(items) ? items : []).slice(0, 20).map(p => ({
      price: num(p.px), quantity: num(p.sz),
    })).filter(p => p.price && p.quantity);
    this.book = { bids: valid(levels[0]).sort((a,b)=>b.price-a.price), asks: valid(levels[1]).sort((a,b)=>a.price-b.price) };
    const bid = this.book.bids[0]?.price, ask = this.book.asks[0]?.price;
    if (bid > 0 && ask >= bid) window.dispatchEvent(new CustomEvent('hl:market-quote', {
      detail: { symbol: this.symbol, bid, ask }
    }));
    this.scheduleMicro();
  }
  paintTick(value,source='mark'){
    const p=num(value);if(!p)return;
    // No comparar fuentes distintas (mark / candle / trade): darían falsas flechas.
    const rank={mark:1,candle:2,trade:3};
    if(this.priceSource && rank[source]<rank[this.priceSource])return;
    const previous=this.priceSource===source?this.quotePrice:null;
    this.prevPrice=previous;this.quotePrice=p;this.priceSource=source;
    this.tickPrice.textContent=fmt(p,7);
    const direction=previous===null? 'flat' :p>previous?'up':p<previous?'down':this.tickPrice.dataset.direction||'flat';
    this.tickPrice.dataset.direction=direction;this.tickArrow.dataset.direction=direction;
    this.tickArrow.textContent=direction==='up'?'▲':direction==='down'?'▼':'●';
  }
  scheduleMicro(){
    if(this.microPending)return;this.microPending=true;
    requestAnimationFrame(()=>{this.microPending=false;this.drawMicro();});
  }
  drawMicro() {
    const book = this.book || { bids: [], asks: [] };
    this.bookNode.replaceChildren();
    if (!book.asks.length && !book.bids.length) this.bookNode.append(el('p', 'hl-empty', 'Esperando libro…'));
    const asks=(book.asks||[]).slice(0,12).sort((a,b)=>b.price-a.price);
    const bids=(book.bids||[]).slice(0,12).sort((a,b)=>b.price-a.price);
    const total=(arr)=>arr.reduce((sum,x)=>sum+(num(x.quantity)||0),0);
    const maxDepth=Math.max(1,total(asks),total(bids));
    for(const [side,rows] of [['ASK',asks],['BID',bids]]){
      // El lado ASK se muestra por encima del spread, el BID por debajo.
      let cumulative=0;
      const rendered=rows.map(item=>{
        cumulative+=(num(item.quantity)||0);
        const row=el('div',`hl-row hl-depth ${side.toLowerCase()}`);
        row.style.setProperty('--depth',`${Math.max(0,Math.min(100,cumulative/maxDepth*100)).toFixed(1)}%`);
        row.append(el('span','',side),el('span','',fmt(item.price,7)),el('span','',fmt(item.quantity,4)));
        return row;
      });
      this.bookNode.append(...rendered);
      if(side==='ASK'&&asks.length&&bids.length){
        const spread=el('div','hl-spread');
        spread.append(el('span','',`Spread ${fmt(asks[asks.length-1].price-bids[0].price,7)}`),el('strong','',`Bid ${fmt(bids[0].price,7)} / Ask ${fmt(asks[asks.length-1].price,7)}`));
        this.bookNode.append(spread);
      }
    }
    this.tradesNode.replaceChildren();
    if (!this.trades.length) this.tradesNode.append(el('p', 'hl-empty', 'Esperando ejecuciones…'));
    for (const trade of this.trades) {
      const row = el('div', `hl-row hl-trade ${trade.side === 'B' ? 'bid' : 'ask'}`);
      row.append(el('span', '', trade.side === 'B' ? 'BUY' : 'SELL'),
        el('span', '', fmt(trade.px, 7)), el('span', '', fmt(trade.sz, 4)));
      this.tradesNode.append(row);
    }
  }
  updateSummary() {
    const a = this.overlayData?.active;
    this.summary.replaceChildren();
    const cells = [
      ['Par', `${this.symbol}/USDC`], ['Modo', this.mode],
      ['Mark Price', fmt(this.assetContext?.markPx, 7)],
      ['Funding', Number.isFinite(Number(this.assetContext?.funding)) ? `${fmt(Number(this.assetContext.funding) * 100, 5)}%` : '—'],
    ];
    if (a && coin(a.symbol) === this.symbol) {
      const entry = Number(a.entry_price), qty = Number(a.quantity), leverage = Number(a.leverage);
      const margin = Number(a.margin_usdc);
      const d = a.direction === 'SHORT' ? -1 : 1;
      const market = d > 0 ? Number(this.book?.bids?.[0]?.price) : Number(this.book?.asks?.[0]?.price);
      const px = market > 0 ? market : Number(this.assetContext?.markPx);
      const movementPct = entry > 0 && px > 0 ? d * ((px / entry) - 1) * 100 : null;
      const estimatedGross = px > 0 && qty > 0 ? d * (px - entry) * qty : null;
      // Estimación antes de comisiones de cierre y funding. No sustituye al ledger.
      const grossRoe = estimatedGross != null && margin > 0 ? estimatedGross / margin * 100 : null;
      const pnl = Number.isFinite(Number(a.unrealized_pnl_usdc)) ? Number(a.unrealized_pnl_usdc) : null;
      cells.push(
        ['Operación', `${a.direction} · ${a.strategy || 'BOT'} · ${leverage > 0 ? `${leverage}x` : '—'}`],
        ['Precio de entrada', fmt(entry, 7)], ['Precio actual (bid/ask)', fmt(px, 7)],
        ['Stop Loss', fmt(a.stop_price, 7)], ['Take Profit', fmt(a.target_price, 7)],
        ['Variación de precio', movementPct == null ? '—' : `${fmt(movementPct, 4)}%`],
        ['ROE bruto estimado (en vivo)', grossRoe == null ? '—' : `${fmt(grossRoe, 3)}%`],
        ['PnL bruto estimado (en vivo)', estimatedGross == null ? '—' : `${fmt(estimatedGross, 5)} USDC`],
        ['PnL registrado', pnl == null ? '—' : `${fmt(pnl, 5)} USDC`],
        ['Margen', margin > 0 ? `${fmt(margin, 4)} USDC` : '—'],
        ['Exposición', Number(a.notional_usdc) > 0 ? `${fmt(a.notional_usdc, 4)} USDC` : '—'],
        ['Cantidad', qty > 0 ? fmt(qty, 8) : '—'],
        ['Abierta', a.opened_at ? new Date(a.opened_at).toLocaleString() : '—'],
      );
    }
    const last = (this.overlayData?.history || []).find(p => coin(p.symbol) === this.symbol);
    if (last && !a) {
      const exit = Number(last.exit_price), entry = Number(last.entry_price), margin = Number(last.margin_usdc);
      const signed = last.direction === 'SHORT' ? -1 : 1;
      const move = exit > 0 && entry > 0 ? signed * (exit / entry - 1) * 100 : null;
      const roe = last.pnl_net_usdc != null && margin > 0 ? Number(last.pnl_net_usdc) / margin * 100 : null;
      cells.push(['Entrada histórica', fmt(entry, 7)], ['Precio de salida', fmt(exit, 7)],
        ['Motivo cierre', last.close_reason || '—'],
        ['Movimiento de precio', move == null ? '—' : `${fmt(move, 4)}%`],
        ['ROE neto realizado', roe == null ? '—' : `${fmt(roe, 3)}%`],
        ['PnL neto realizado', last.pnl_net_usdc == null ? '—' : `${fmt(last.pnl_net_usdc, 4)} USDC`],
        ['Comisión de salida', last.exit_fee_usdc == null ? '—' : `${fmt(last.exit_fee_usdc, 5)} USDC`],
        ['Fecha de salida', last.closed_at ? new Date(last.closed_at).toLocaleString() : '—']);
    }
    for (const [name, value] of cells) {
      const item = el('div', 'hl-summary-item'); item.append(el('small', '', name), el('strong', '', value)); this.summary.append(item);
    }
  }
  applyOverlays() {
    if (!this.chart || !this.overlayData) return;
    const active = this.overlayData.active && coin(this.overlayData.active.symbol) === this.symbol ? this.overlayData.active : null;
    const closed = (this.overlayData.history || []).filter(p => coin(p.symbol) === this.symbol).slice(0, 60);
    const key = JSON.stringify([this.symbol, active && { symbol: active.symbol, entry_price: active.entry_price, stop_price: active.stop_price, target_price: active.target_price, opened_at: active.opened_at, direction: active.direction }, closed]);
    if (key === this.overlayKey) return;
    this.overlayKey = key;
    Object.values(GROUPS).filter(group => group !== GROUPS.drawings).forEach(group => this.chart.removeOverlay({ groupId: group }));
    if (active) {
      const anchor = active.opened_at || Date.now();
      const entry = num(active.entry_price), sl = num(active.stop_price), tp = num(active.target_price);
      for (const [label, value, color] of [
        ['ENTRY', entry, '#37cfff'], ['SL', sl, '#ff486e'], ['TP', tp, '#00e697'],
      ]) if (value) this.chart.createOverlay({ name: KEY, groupId: GROUPS.levels, lock: true,
        points: [{ timestamp: anchor, value }], extendData: { label, value: fmt(value, 7), color } });
      for (const [value, color] of [[tp, 'rgba(0,230,151,.07)'], [sl, 'rgba(255,72,110,.07)']]) {
        if (entry && value) this.chart.createOverlay({ name: ZONE, groupId: GROUPS.zones, lock: true, zLevel: -10,
          points: [{ timestamp: anchor, value: entry }, { timestamp: anchor, value }], extendData: { color } });
      }
      if (entry && active.opened_at) this.marker(active.opened_at, entry,
        active.direction === 'SHORT' ? '▼ SHORT' : '▲ LONG', active.direction === 'SHORT' ? '#ff647f' : '#30d8ff');
    }
    for (const p of closed) {
      const entry = num(p.entry_price), exit = num(p.exit_price);
      const side = p.direction === 'SHORT';
      if (entry && p.opened_at) this.marker(p.opened_at, entry, side ? '▼ SHORT' : '▲ LONG', side ? '#ff647f' : '#30d8ff');
      if (exit && p.closed_at) {
        const pnl = Number(p.pnl_net_usdc);
        const good = Number.isFinite(pnl) ? pnl >= 0 : p.close_reason === 'TP';
        const pnlLabel = p.pnl_net_usdc == null ? '' : ` ${pnl >= 0 ? '+' : ''}${fmt(pnl, 2)} USDC`;
        this.marker(p.closed_at, exit, `${p.close_reason || 'CLOSE'}${pnlLabel}`, good ? '#00e697' : '#ff486e', good);
      }
    }
  }
  marker(ts, price, label, color, top = false) {
    if (!Number.isFinite(ts) || !num(price)) return;
    this.chart.createOverlay({ name: MARK, groupId: GROUPS.markers, lock: true,
      points: [{ timestamp: ts, value: price }], extendData: { label, color, top } });
  }
}

for (const root of document.querySelectorAll('[data-market-terminal]')) new Terminal(root);
