/* Hyper League / KAELEON-inspired deterministic canvas terminal.
   No CDN, npm runtime, trading keys or order placement. */
const overlays = new Map();
export const registerOverlay = config => { if (config?.name) overlays.set(config.name, config); };
const val = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v)) ? Number(v) : null;
const fmt = (v, digits=6) => val(v) === null ? '—' : Number(v).toLocaleString('en-US',{maximumFractionDigits:digits});
const time = (t,daily=false) => daily ? new Date(t).toLocaleDateString([], {month:'2-digit',day:'2-digit'}) : new Date(t).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hour12:false});
const valid = b => b && [b.timestamp,b.open,b.high,b.low,b.close].every(v=>val(v)!==null) && b.timestamp>0 && b.low>0 && b.high>=Math.max(b.open,b.close) && b.low<=Math.min(b.open,b.close);
const sma = (values,n) => { const result=Array(values.length).fill(null); let sum=0; for(let i=0;i<values.length;i++){sum+=values[i];if(i>=n)sum-=values[i-n];if(i>=n-1)result[i]=sum/n;}return result;};
const ema = (values,n) => {const result=Array(values.length).fill(null);if(values.length<n)return result;let avg=0;for(let i=0;i<n;i++)avg+=values[i];avg/=n;result[n-1]=avg;const a=2/(n+1);for(let i=n;i<values.length;i++){avg=values[i]*a+avg*(1-a);result[i]=avg;}return result;};
const boll = (values,n=20,m=2) => {const mid=sma(values,n),upper=[],lower=[];for(let i=0;i<values.length;i++){if(mid[i]===null){upper.push(null);lower.push(null);continue;}let variance=0;for(let j=i-n+1;j<=i;j++)variance+=(values[j]-mid[i])**2;const dev=Math.sqrt(variance/n)*m;upper.push(mid[i]+dev);lower.push(mid[i]-dev);}return [mid,upper,lower];};
const macd = values => {const f=ema(values,12),s=ema(values,26),diff=values.map((_,i)=>f[i]===null||s[i]===null?null:f[i]-s[i]);const compressed=diff.slice(25).filter(x=>x!==null),signalRaw=ema(compressed,9),signal=Array(25).fill(null).concat(signalRaw);return [diff,signal,diff.map((v,i)=>v===null||signal[i]===null?null:v-signal[i])];};
const rsi = (values,n=14) => {const out=Array(values.length).fill(null);if(values.length<=n)return out;let gain=0,loss=0;for(let i=1;i<=n;i++){const d=values[i]-values[i-1];gain+=Math.max(d,0);loss+=Math.max(-d,0);}gain/=n;loss/=n;out[n]=loss===0?100:100-100/(1+gain/loss);for(let i=n+1;i<values.length;i++){const d=values[i]-values[i-1];gain=(gain*(n-1)+Math.max(d,0))/n;loss=(loss*(n-1)+Math.max(-d,0))/n;out[i]=loss===0?100:100-100/(1+gain/loss);}return out;};
const UP='#0fe3a1',DOWN='#ff5b7a',AXIS='#789bab';
class Chart {
  constructor(host) {
    this.host=host;this.canvas=document.createElement('canvas');this.canvas.className='hl-local-chart';
    this.canvas.setAttribute('aria-label','Gráfico profesional: velas, indicadores y operaciones');
    this.canvas.style.cssText='display:block;width:100%;height:100%;touch-action:none;';host.replaceChildren(this.canvas);
    this.ctx=this.canvas.getContext('2d');this.bars=[];this.indicators=new Map();this.overlays=[];
    this.visibleCount=40;this.scrollBack=0;this.hover=null;this.loadingGeneration=0;this.paintPending=false;
    this.onWheel=e=>{e.preventDefault();this.zoom(e.deltaY>0?1:-1);};
    this.canvas.addEventListener('wheel',this.onWheel,{passive:false});
    this.canvas.addEventListener('pointerdown',e=>{
      this.canvas.setPointerCapture?.(e.pointerId);this.pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
      if(this.pointers.size===2){const pair=[...this.pointers.values()];this.pinch={distance:Math.hypot(pair[0].x-pair[1].x,pair[0].y-pair[1].y),count:this.visibleCount};this.drag=null;this.down=null;return;}
      this.drag={x:e.clientX,scroll:this.scrollBack};this.down=this.positionAt(e.offsetX,e.offsetY);
    });
    this.canvas.addEventListener('pointerup',e=>{this.pointers.delete(e.pointerId);if(this.pointers.size<2)this.pinch=null;if(this.tool&&this.down){this.userDrawings.push({tool:this.tool,a:this.down,b:this.positionAt(e.offsetX,e.offsetY)});this.tool=null;}this.drag=null;this.down=null;this.requestRender();});
    this.canvas.addEventListener('pointercancel',e=>{this.pointers.delete(e.pointerId);this.pinch=null;this.drag=null;this.down=null;});
    this.canvas.addEventListener('pointerleave',()=>{if(!this.drag){this.hover=null;this.requestRender();}});
    this.canvas.addEventListener('pointermove',e=>{
      if(this.pointers.has(e.pointerId))this.pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
      if(this.pinch&&this.pointers.size===2){const pair=[...this.pointers.values()];const current=Math.hypot(pair[0].x-pair[1].x,pair[0].y-pair[1].y);this.visibleCount=Math.max(16,Math.min(160,Math.round(this.pinch.count*this.pinch.distance/Math.max(5,current))));this.requestRender();return;}
      const r=this.canvas.getBoundingClientRect();this.hover={x:e.clientX-r.left,y:e.clientY-r.top};if(this.drag&&!this.tool){const barWidth=Math.max(3,(this.width-88)/this.visibleCount);const delta=Math.round((this.drag.x-e.clientX)/barWidth);this.scrollBack=Math.max(0,Math.min(Math.max(0,this.bars.length-10),this.drag.scroll+delta));}this.requestRender();});
    this.userDrawings=[];this.pointers=new Map();
    this.observer=typeof ResizeObserver==='function'?new ResizeObserver(()=>this.resize()):null;this.observer?.observe(host);this.resize();
  }
  setDataLoader(loader){this.loader=loader;this.reload();}
  setSymbol(s){const name=s?.ticker;if(!name||this.symbol?.ticker===name)return;this.symbol=s;this.reload();}
  setPeriod(p){if(!p||JSON.stringify(this.period)===JSON.stringify(p))return;this.period=p;this.reload();}
  reload(){if(!this.loader||!this.symbol||!this.period)return;const old=this.activeSubscription;this.activeSubscription=null;old?.unsubscribe?.();const gen=++this.loadingGeneration;this.bars=[];this.scrollBack=0;this.hover=null;this.userDrawings=[];this.requestRender();
    const symbol={...this.symbol},period={...this.period};const cb=rows=>{if(gen!==this.loadingGeneration)return;this.bars=(Array.isArray(rows)?rows:[]).filter(valid).map(x=>({...x,volume:val(x.volume)||0})).sort((a,b)=>a.timestamp-b.timestamp).filter((x,i,a)=>i===0||x.timestamp!==a[i-1].timestamp);this.requestRender();};
    this.loader.getBars({type:'init',symbol,period,callback:cb});
    const callback=row=>{if(gen!==this.loadingGeneration||!valid(row))return;const last=this.bars[this.bars.length-1];if(last?.timestamp===row.timestamp)this.bars[this.bars.length-1]=row;else if(!last||row.timestamp>last.timestamp)this.bars.push(row);else return;if(this.bars.length>700)this.bars.splice(0,this.bars.length-700);this.requestRender();};
    const unsub=this.loader.subscribeBar?.({symbol,period,callback});this.activeSubscription={unsubscribe:typeof unsub==='function'?unsub:()=>this.loader?.unsubscribeBar?.({symbol,period})};
  }
  createIndicator(def){if(!def?.name)return null;const id='ind-'+Math.random().toString(36).slice(2);this.indicators.set(id,def);this.requestRender();return id;}
  removeIndicator(arg){if(this.indicators.delete(arg?.id))this.requestRender();}
  createOverlay(spec){if(!spec?.name)return null;if(['straightLine','horizontalStraightLine','fibonacciLine','segment'].includes(spec.name)){this.tool=spec.name;return 'draw-armed';}const id='ov-'+Math.random().toString(36).slice(2);this.overlays.push({...spec,id});this.requestRender();return id;}
  removeOverlay({groupId,id}={}){this.overlays=this.overlays.filter(x=>groupId?x.groupId!==groupId:x.id!==id);if(groupId==='hl-drawings')this.userDrawings=[];this.requestRender();}
  zoom(steps){this.visibleCount=Math.max(16,Math.min(160,this.visibleCount+steps*9));this.requestRender();}
  positionAt(x,y){const {rows}=this.viewport(),p=this.priceBounds(),left=10,right=this.width-78,top=24,bottom=this.height-150;
    const i=Math.max(0,Math.min(rows.length-1,Math.floor((x-left)/(right-left)*rows.length)));
    return {timestamp:rows[i]?.timestamp||0,price:p.high-(y-top)/(bottom-top)*(p.high-p.low)};
  }
  resize(){const r=this.host.getBoundingClientRect(),w=Math.max(260,r.width||this.host.clientWidth||320),h=Math.max(290,r.height||this.host.clientHeight||330),dpr=Math.min(window.devicePixelRatio||1,2);if(this.canvas.width!==Math.round(w*dpr)||this.canvas.height!==Math.round(h*dpr)){this.canvas.width=Math.round(w*dpr);this.canvas.height=Math.round(h*dpr);}this.ctx?.setTransform(dpr,0,0,dpr,0,0);this.width=w;this.height=h;this.requestRender();}
  requestRender(){if(this.paintPending)return;this.paintPending=true;requestAnimationFrame(()=>{this.paintPending=false;this.render();});}
  viewport(){const end=Math.max(0,this.bars.length-this.scrollBack),begin=Math.max(0,end-this.visibleCount);return {begin,end,rows:this.bars.slice(begin,end)};}
  priceBounds(){const {rows}=this.viewport();if(!rows.length)return {low:0,high:1};let low=Math.min(...rows.map(x=>x.low)),high=Math.max(...rows.map(x=>x.high));for(const ov of this.overlays)for(const p of ov.points||[]){const v=val(p.value);if(v!==null&&v>0){low=Math.min(low,v);high=Math.max(high,v);}}const pad=(high-low)*.10||Math.abs(high)*.005||.01;return {low:low-pad,high:high+pad};}
  render(){const c=this.ctx,w=this.width,h=this.height;if(!c||!w||!h)return;c.clearRect(0,0,w,h);c.fillStyle='#071721';c.fillRect(0,0,w,h);
    const {begin,rows}=this.viewport();const price={left:10,right:w-78,top:24,bottom:h-150},sub={top:h-116,bottom:h-29};
    const {low,high}=this.priceBounds();const py=v=>price.top+(high-v)/(high-low)*(price.bottom-price.top),bw=(price.right-price.left)/Math.max(1,rows.length),px=i=>price.left+(i+.5)*bw;
    if(!rows.length){c.textAlign='center';c.fillStyle='#8dadbb';c.font='13px sans-serif';c.fillText('Cargando velas OHLCV…',w/2,h/2);return;}
    c.font='11px ui-monospace,monospace';c.lineWidth=1;
    for(let i=0;i<=5;i++){const y=price.top+(price.bottom-price.top)*i/5;c.strokeStyle='#19313d';c.beginPath();c.moveTo(price.left,y);c.lineTo(price.right,y);c.stroke();c.textAlign='left';c.fillStyle=AXIS;c.fillText(fmt(high-i*(high-low)/5),price.right+6,y+3);}
    // Bar bodies preserve visible width on mobile; pixels never sub-pixel thin.
    const body=Math.max(4.5,Math.min(24,bw*.82));
    rows.forEach((b,i)=>{const x=px(i),color=b.close>=b.open?UP:DOWN;c.fillStyle=c.strokeStyle=color;c.lineWidth=Math.max(1.25,Math.min(2,bw*.1));c.beginPath();c.moveTo(x,py(b.high));c.lineTo(x,py(b.low));c.stroke();c.fillRect(x-body/2,Math.min(py(b.open),py(b.close)),body,Math.max(2,Math.abs(py(b.open)-py(b.close))));});
    const close=this.bars.map(x=>x.close),config=new Set([...this.indicators.values()].map(x=>x.name));
    const drawLine=(series,color,width=1.7)=>{c.strokeStyle=color;c.lineWidth=width;c.beginPath();let started=false;for(let i=0;i<rows.length;i++){const v=series[begin+i];if(v===null||v===undefined||!Number.isFinite(v)){started=false;continue;}if(!started){c.moveTo(px(i),py(v));started=true;}else c.lineTo(px(i),py(v));}c.stroke();};
    if(config.has('MA')){drawLine(sma(close,5),'#ffd276');drawLine(sma(close,10),'#5cc8ff');drawLine(sma(close,30),'#e6a3ff');drawLine(sma(close,60),'#a6e4b5');}
    if(config.has('EMA')){drawLine(ema(close,6),'#ffd276');drawLine(ema(close,12),'#5cc8ff');drawLine(ema(close,20),'#e6a3ff');}
    if(config.has('BOLL')){const [m,u,l]=boll(close);drawLine(m,'#f2bd69');drawLine(u,'#ad82f8');drawLine(l,'#ad82f8');}
    c.strokeStyle='#35515c';c.beginPath();c.moveTo(price.left,sub.top-10);c.lineTo(price.right,sub.top-10);c.stroke();
    const lower=[...this.indicators.values()].find(x=>['VOL','MACD','RSI'].includes(x.name))?.name||'VOL';
    if(lower==='VOL'){const maxVol=Math.max(1,...rows.map(x=>x.volume||0));rows.forEach((b,i)=>{const height=(b.volume||0)/maxVol*(sub.bottom-sub.top);c.fillStyle=b.close>=b.open?'#117861':'#894254';c.fillRect(px(i)-Math.max(2,body*.38),sub.bottom-height,Math.max(3,body*.76),height);});}
    if(lower==='MACD'){const [d,s,hist]=macd(close),vals=[...d,...s,...hist].filter(x=>x!==null),mx=Math.max(.00000001,...vals.map(Math.abs));const zy=sub.top+(sub.bottom-sub.top)/2;const sy=v=>zy-v/mx*(sub.bottom-sub.top)*.43;c.strokeStyle='#385866';c.beginPath();c.moveTo(price.left,zy);c.lineTo(price.right,zy);c.stroke();hist.slice(begin,begin+rows.length).forEach((v,i)=>{if(v===null)return;c.fillStyle=v>=0?UP:DOWN;c.fillRect(px(i)-body*.3,Math.min(zy,sy(v)),Math.max(3,body*.6),Math.max(1,Math.abs(zy-sy(v))));});const plot=(series,col)=>{c.strokeStyle=col;c.lineWidth=1.5;c.beginPath();let started=false;rows.forEach((_,i)=>{const v=series[begin+i];if(v===null){started=false;return;}if(!started){c.moveTo(px(i),sy(v));started=true;}else c.lineTo(px(i),sy(v));});c.stroke();};plot(d,'#ffd276');plot(s,'#74b7ff');}
    if(lower==='RSI'){
      const sy=v=>sub.bottom-(v/100)*(sub.bottom-sub.top);
      c.setLineDash([3,4]);for(const level of [30,70]){c.strokeStyle='#465568';c.beginPath();c.moveTo(price.left,sy(level));c.lineTo(price.right,sy(level));c.stroke();}c.setLineDash([]);
      for(const [period,color] of [[6,'#b195ff'],[12,'#f6ba69'],[24,'#66c1ed']]){
        const data=rsi(close,period);c.strokeStyle=color;c.lineWidth=1.5;c.beginPath();let started=false;
        rows.forEach((_,i)=>{const v=data[begin+i];if(v===null){started=false;return;}if(!started){c.moveTo(px(i),sy(v));started=true;}else c.lineTo(px(i),sy(v));});c.stroke();
      }
    }
    c.fillStyle='#98b6c4';c.font='bold 10px sans-serif';c.fillText(lower,price.left+4,sub.top+10);
    for(let i=0;i<=4;i++){const idx=Math.min(rows.length-1,Math.round(i*(rows.length-1)/4));c.fillStyle=AXIS;c.font='10px ui-monospace,monospace';c.textAlign='center';c.fillText(time(rows[idx].timestamp,this.period?.type==='day'),px(idx),h-9);}
    for(const o of this.overlays){const points=o.points||[],color=o.extendData?.color||'#47d4f3',v=val(points[0]?.value);if(v===null)continue;const y=py(v);if(o.name==='hyperleague-level'){c.strokeStyle=color;c.lineWidth=o.extendData?.label==='ENTRY'?2:1.5;c.setLineDash([7,4]);c.beginPath();c.moveTo(price.left,y);c.lineTo(price.right,y);c.stroke();c.setLineDash([]);c.fillStyle=color;c.textAlign='left';c.font='bold 11px sans-serif';c.fillText(`${o.extendData?.label||''}  ${fmt(v)}`,price.left+8,Math.max(price.top+10,y-6));}else if(o.name==='hyperleague-zone'&&val(points[1]?.value)!==null){c.fillStyle=color;c.fillRect(price.left,Math.min(y,py(points[1].value)),price.right-price.left,Math.abs(y-py(points[1].value)));}else if(o.name==='hyperleague-marker'){const raw=points[0]?.timestamp;const ts=typeof raw==='number'?raw:((typeof raw==='string'&&/^\d+$/.test(raw))?Number(raw):Date.parse(raw));const j=rows.findIndex((b,i)=>b.timestamp>=ts||i===rows.length-1);if(j>=0&&ts>=rows[0].timestamp&&ts<=rows[rows.length-1].timestamp+86400000){c.fillStyle=color;c.beginPath();c.arc(px(j),y,4,0,Math.PI*2);c.fill();c.textAlign='center';c.font='bold 10px sans-serif';c.fillText(String(o.extendData?.label||'').slice(0,24),px(j),y-9);}}}
    for(const d of this.userDrawings){
      const coord=p=>({x:px(Math.max(0,Math.min(rows.length-1,rows.findIndex(b=>b.timestamp>=p.timestamp) < 0 ? rows.length-1:rows.findIndex(b=>b.timestamp>=p.timestamp)))),y:py(p.price)});
      const a=coord(d.a),b=coord(d.b);c.strokeStyle='#5bdbff';c.lineWidth=1.8;c.beginPath();if(d.tool==='horizontalStraightLine'){c.moveTo(price.left,a.y);c.lineTo(price.right,a.y);}else if(d.tool==='fibonacciLine'){const top=Math.min(a.y,b.y),height=Math.abs(a.y-b.y);for(const ratio of [0,.236,.382,.5,.618,.786,1]){const y=top+height*ratio;c.moveTo(price.left,y);c.lineTo(price.right,y);c.fillStyle='#b6ecff';c.fillText(`${Math.round(ratio*100)}%`,price.left+6,y-3);}}else{c.moveTo(a.x,a.y);c.lineTo(b.x,b.y);}c.stroke();}
    if(this.hover&&this.hover.x>=price.left&&this.hover.x<=price.right){const j=Math.min(rows.length-1,Math.max(0,Math.floor((this.hover.x-price.left)/bw))),b=rows[j];c.strokeStyle='#7493a1';c.setLineDash([3,3]);c.beginPath();c.moveTo(px(j),price.top);c.lineTo(px(j),sub.bottom);c.moveTo(price.left,this.hover.y);c.lineTo(price.right,this.hover.y);c.stroke();c.setLineDash([]);c.fillStyle='rgba(7,22,34,.96)';c.fillRect(price.left+3,price.top+4,Math.min(price.right-price.left-4,330),24);c.fillStyle=b.close>=b.open?UP:DOWN;c.font='10px ui-monospace,monospace';c.textAlign='left';c.fillText(`O ${fmt(b.open)}  H ${fmt(b.high)}  L ${fmt(b.low)}  C ${fmt(b.close)}`,price.left+7,price.top+20);}
    const last=rows[rows.length-1],up=last.close>=last.open;c.fillStyle=up?UP:DOWN;c.fillRect(price.right+1,Math.max(price.top,Math.min(price.bottom-19,py(last.close)-8)),w-price.right-3,20);c.fillStyle='#041520';c.textAlign='center';c.font='bold 11px monospace';c.fillText(fmt(last.close),price.right+(w-price.right)/2,Math.max(price.top+13,Math.min(price.bottom-6,py(last.close)+5)));
  }
  destroy(){this.observer?.disconnect();this.activeSubscription?.unsubscribe?.();this.canvas.remove();}
}
const chartInstances=new WeakMap();
export const init=host=>{const c=new Chart(host);chartInstances.set(host,c);return c;};
export const dispose=host=>{const c=chartInstances.get(host);if(c){c.destroy();chartInstances.delete(host);}};
// Export calculations for deterministic regression tests, not used by browser UI.
export const indicators={sma,ema,boll,macd,rsi};
