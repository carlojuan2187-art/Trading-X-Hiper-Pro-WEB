/* Hyper League: motor de velas local, sin dependencias npm ni CDN.
 * Compatibilidad mínima con la interfaz usada por market-terminal/chart.js.
 * SOLO lectura: no implementa órdenes ni acceso a claves privadas.
 */
const overlays = new Map();
export const registerOverlay = definition => { if (definition?.name) overlays.set(definition.name, definition); };
const finite = v => Number.isFinite(Number(v)) ? Number(v) : null;
const format = v => v == null ? '—' : Number(v).toLocaleString('en-US', {maximumFractionDigits: 6});
const time = t => new Date(t).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
const safeBar = row => {
  if (!row || !Number.isFinite(row.timestamp) || !Number.isFinite(row.open) || !Number.isFinite(row.close) ||
      !Number.isFinite(row.high) || !Number.isFinite(row.low) || row.low <= 0) return null;
  return row;
};
class LocalCandleChart {
  constructor(host) {
    this.host = host;
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'hl-local-chart';
    this.canvas.setAttribute('aria-label', 'Velas de precio de Hyperliquid');
    this.canvas.style.cssText = 'display:block;width:100%;height:100%;touch-action:pan-y';
    host.replaceChildren(this.canvas);
    this.ctx = this.canvas.getContext('2d');
    this.bars = [];
    this.overlays = [];
    this.indicators = new Map();
    this.visibleCount = 85;
    this.scrollBack = 0;
    this.hover = null;
    this.loadingGeneration = 0;
    this.canvas.addEventListener('pointermove', e => {
      const r = this.canvas.getBoundingClientRect(); this.hover = {x:e.clientX-r.left,y:e.clientY-r.top}; this.render();
    });
    this.canvas.addEventListener('pointerleave', () => { this.hover = null; this.render(); });
    this.canvas.addEventListener('wheel', e => {
      e.preventDefault();
      this.visibleCount = Math.min(240, Math.max(20, this.visibleCount + (e.deltaY > 0 ? 12 : -12)));
      this.render();
    }, {passive:false});
    this.canvas.addEventListener('pointerdown', e => { this.dragX = e.clientX; this.dragStart = this.scrollBack; });
    this.canvas.addEventListener('pointerup', () => { this.dragX = null; });
    this.canvas.addEventListener('pointercancel', () => { this.dragX = null; });
    this.canvas.addEventListener('pointermove', e => {
      if (this.dragX == null || !this.bars.length) return;
      const px = Math.max(3, this.canvas.clientWidth / this.visibleCount);
      this.scrollBack = Math.max(0, Math.min(this.bars.length - 12, this.dragStart + Math.round((e.clientX-this.dragX)/px)));
      this.render();
    });
    this.observer = typeof ResizeObserver === 'function' ? new ResizeObserver(() => this.resize()) : null;
    this.observer?.observe(host);
    this.resize();
  }
  setDataLoader(loader) { this.loader = loader; this.reload(); }
  setSymbol(symbol) { this.symbol = symbol; this.reload(); }
  setPeriod(period) { this.period = period; this.reload(); }
  reload() {
    if (!this.loader || !this.symbol || !this.period) return;
    this.loader.unsubscribeBar?.({symbol:this.symbol,period:this.period});
    const generation = ++this.loadingGeneration;
    this.bars = [];
    this.scrollBack = 0;
    this.render();
    this.loader.getBars({type:'init',symbol:this.symbol,period:this.period,callback:(rows) => {
      if (generation !== this.loadingGeneration) return;
      this.bars = (Array.isArray(rows) ? rows : []).map(safeBar).filter(Boolean).sort((a,b)=>a.timestamp-b.timestamp);
      this.render();
    }});
    this.loader.subscribeBar({symbol:this.symbol,period:this.period,callback:row => {
      if (generation !== this.loadingGeneration || !safeBar(row)) return;
      const i = this.bars.findIndex(b => b.timestamp === row.timestamp);
      if (i >= 0) this.bars[i] = row;
      else if (!this.bars.length || row.timestamp > this.bars[this.bars.length-1].timestamp) this.bars.push(row);
      if (this.bars.length > 700) this.bars.splice(0, this.bars.length-700);
      this.render();
    }});
  }
  createIndicator(definition) { const id = 'li-'+Math.random().toString(36).slice(2); this.indicators.set(id,definition); this.render(); return id; }
  removeIndicator(arg) { this.indicators.delete(arg?.id); this.render(); }
  createOverlay(spec) { const id = 'lo-'+Math.random().toString(36).slice(2); this.overlays.push({id,...spec}); this.render(); return id; }
  removeOverlay({groupId,id}={}) { this.overlays = this.overlays.filter(o => groupId ? o.groupId !== groupId : o.id !== id); this.render(); }
  resize() {
    const rect = this.host.getBoundingClientRect();
    const w = Math.max(250, rect.width || this.host.clientWidth || 320);
    const h = Math.max(260, rect.height || this.host.clientHeight || 320);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (this.canvas.width !== Math.round(w*dpr) || this.canvas.height !== Math.round(h*dpr)) {
      this.canvas.width = Math.round(w*dpr); this.canvas.height = Math.round(h*dpr);
    }
    this.ctx?.setTransform(dpr,0,0,dpr,0,0);
    this.width = w; this.height = h; this.render();
  }
  render() {
    const c=this.ctx,w=this.width,h=this.height;
    if (!c || !w || !h) return;
    c.clearRect(0,0,w,h);
    c.fillStyle='#06151d';c.fillRect(0,0,w,h);
    const left=12, right=68, top=18, axis=26, volHeight=Math.round(h*.17);
    const priceBottom=h-axis-volHeight-10, chartWidth=w-left-right, plotHeight=priceBottom-top;
    const end=Math.max(0,this.bars.length-this.scrollBack);
    const begin=Math.max(0,end-this.visibleCount);
    const rows=this.bars.slice(begin,end);
    if (!rows.length) { c.textAlign='center';c.fillStyle='#8caeb8';c.font='13px sans-serif';c.fillText('Cargando velas históricas de Hyperliquid…',w/2,h/2);return; }
    let min=Math.min(...rows.map(x=>x.low)),max=Math.max(...rows.map(x=>x.high));
    for (const ov of this.overlays) for (const pt of (ov.points||[])) {
      const v=finite(pt.value);if(v!==null && v>0){min=Math.min(min,v);max=Math.max(max,v);}
    }
    const pad=(max-min)*.09||max*.0005||1;min-=pad;max+=pad;
    const yAt=p=>top+(max-p)/(max-min)*plotHeight;
    const barW=chartWidth/rows.length;
    const xAt=i=>left+(i+.5)*barW;
    c.lineWidth=1;c.font='11px ui-monospace,monospace';
    for(let i=0;i<=5;i++) {
      const yy=top+i*plotHeight/5;
      c.strokeStyle='#1a333d';c.beginPath();c.moveTo(left,yy);c.lineTo(w-right,yy);c.stroke();
      c.textAlign='left';c.fillStyle='#91aebb';c.fillText(format(max-i*(max-min)/5),w-right+5,yy+3);
    }
    for(let i=0;i<rows.length;i++) {
      const b=rows[i],x=xAt(i),hi=yAt(b.high),lo=yAt(b.low),op=yAt(b.open),cl=yAt(b.close);
      c.strokeStyle=c.fillStyle=b.close>=b.open?'#0bdc98':'#ff5275';
      c.beginPath();c.moveTo(x,hi);c.lineTo(x,lo);c.stroke();
      c.fillRect(x-Math.max(1,barW*.31),Math.min(op,cl),Math.max(2,barW*.62),Math.max(1.4,Math.abs(op-cl)));
    }
    // Optional moving average overlays, without running any order logic.
    if([...this.indicators.values()].some(v => ['MA','EMA','BOLL'].includes(v.name))) {
      c.strokeStyle='#f7be56';c.lineWidth=1.5;c.beginPath();
      for(let i=0;i<rows.length;i++){ const original=begin+i, sample=this.bars.slice(Math.max(0,original-19),original+1);const v=sample.reduce((a,b)=>a+b.close,0)/sample.length; if(i)c.lineTo(xAt(i),yAt(v));else c.moveTo(xAt(i),yAt(v)); }c.stroke();
    }
    const vmax=Math.max(...rows.map(b=>finite(b.volume)||0),1);
    const volY=priceBottom+8;
    for(let i=0;i<rows.length;i++) {
      const b=rows[i];const size=Math.max(0,Number(b.volume)||0)/vmax*volHeight;
      c.fillStyle=b.close>=b.open?'#0e7d65':'#953d56';c.fillRect(xAt(i)-Math.max(1,barW*.25),volY+volHeight-size,Math.max(2,barW*.5),size);
    }
    c.strokeStyle='#35505b';c.beginPath();c.moveTo(left,priceBottom+5);c.lineTo(w-right,priceBottom+5);c.stroke();
    for(let i=0;i<5;i++) {const j=Math.round((rows.length-1)*i/4);c.fillStyle='#98adba';c.textAlign='center';c.fillText(time(rows[j].timestamp),xAt(j),h-8);}
    // Entry/TP/SL and historic markers. Labels remain read-only.
    for(const ov of this.overlays) {
      const pts=ov.points||[];const color=ov.extendData?.color || '#39bbde';
      if(ov.name==='hyperleague-level' && pts[0]?.value) {
        const yy=yAt(Number(pts[0].value));c.strokeStyle=color;c.setLineDash([6,4]);c.beginPath();c.moveTo(left,yy);c.lineTo(w-right,yy);c.stroke();c.setLineDash([]);
        c.fillStyle=color;c.textAlign='left';c.font='bold 11px sans-serif';c.fillText(`${ov.extendData?.label||''} ${format(pts[0].value)}`,left+6,yy-5);
      } else if (ov.name==='hyperleague-zone' && pts.length===2) {
        c.fillStyle=color;c.fillRect(left,Math.min(yAt(pts[0].value),yAt(pts[1].value)),chartWidth,Math.abs(yAt(pts[0].value)-yAt(pts[1].value)));
      } else if (ov.name==='hyperleague-marker' && pts[0]) {
        const ts=Number(pts[0].timestamp), p=Number(pts[0].value);
        const i=rows.findIndex((b,j)=> ts <= b.timestamp || j===rows.length-1);
        if(i>=0 && ts>=rows[0].timestamp && ts<=rows[rows.length-1].timestamp+300000){const x=xAt(i),y=yAt(p);c.fillStyle=color;c.beginPath();c.arc(x,y,4,0,Math.PI*2);c.fill();c.font='bold 10px sans-serif';c.textAlign='center';c.fillText(String(ov.extendData?.label||'').slice(0,28),x,y-10);}
      }
    }
    if(this.hover && this.hover.x>=left && this.hover.x<w-right && this.hover.y>=top && this.hover.y<priceBottom){
      const i=Math.min(rows.length-1,Math.max(0,Math.floor((this.hover.x-left)/barW)));
      const b=rows[i],x=xAt(i);
      c.strokeStyle='#7197a8';c.setLineDash([2,4]);c.beginPath();c.moveTo(x,top);c.lineTo(x,priceBottom);c.moveTo(left,this.hover.y);c.lineTo(w-right,this.hover.y);c.stroke();c.setLineDash([]);
      c.fillStyle='rgba(8,31,43,.94)';c.fillRect(left+4,top+4,Math.min(chartWidth-8,265),24);
      c.font='10px ui-monospace,monospace';c.textAlign='left';c.fillStyle=b.close>=b.open?'#2cdea7':'#ff6d89';c.fillText(`O ${format(b.open)}  H ${format(b.high)}  L ${format(b.low)}  C ${format(b.close)}`,left+9,top+19);
    }
    const last=rows[rows.length-1];c.fillStyle=last.close>=last.open?'#0bdc98':'#ff5275';c.font='bold 12px monospace';c.textAlign='left';c.fillText(format(last.close),w-right+4,priceBottom-8);
  }
  destroy() {this.observer?.disconnect();this.loader?.unsubscribeBar?.({symbol:this.symbol,period:this.period});this.canvas.remove();}
}
const instances = new WeakMap();
export const init = (host) => {const instance=new LocalCandleChart(host);instances.set(host,instance);return instance;};
export const dispose = host => {const instance=instances.get(host);if(instance){instance.destroy();instances.delete(host);}};
