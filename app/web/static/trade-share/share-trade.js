/** Tarjeta para compartir trades Hyper League. Solo consume snapshots del usuario autenticado.
 * No consulta claves privadas ni cambia órdenes. Basado en la experiencia KAELEON. */
import { drawQrCode, createQrDataUrl } from './qr-code.js';

export const numeric = (value) => value == null || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
const text = (value, max = 100) => String(value ?? '').trim().slice(0, max);
const positive = (value) => numeric(value) > 0 ? numeric(value) : null;
const direction = (value) => {
  const v = String(value || '').trim().toUpperCase();
  if (['LONG', 'BUY', 'B'].includes(v)) return 'LONG';
  if (['SHORT', 'SELL', 'S'].includes(v)) return 'SHORT';
  return '—';
};
const round = (value) => value == null ? null : Math.round(value * 10000) / 10000;
const percent = (pnl, margin) => margin && Number.isFinite(pnl) ? round(pnl / margin * 100) : null;
const iso = (value) => value && !Number.isNaN(new Date(value).getTime()) ? new Date(value).toISOString() : null;

export function normalizeTrade(kind, raw) {
  if (!raw || typeof raw !== 'object') throw new Error('No hay datos de operación para compartir.');
  const demo = kind === 'demo-open' || kind === 'demo-closed';
  if (!demo && kind !== 'live-open' && kind !== 'live-closed') throw new Error('Tipo de operación inválido.');
  const open = kind.endsWith('-open');
  let pnl = numeric(demo ? (open ? raw.unrealized_pnl_usdc : raw.pnl_net_usdc) : (open ? raw.unrealized_pnl_usdc : raw.profit));
  const lev = positive(raw.leverage);
  const quantity = positive(raw.quantity || raw.qty || raw.qty_coin_for_log || raw.size);
  const margin = positive(raw.margin_usdc) || (positive(raw.notional_usdc || raw.qty_usdc_for_profit) && lev ? positive(raw.notional_usdc || raw.qty_usdc_for_profit) / lev : null);
  const dir = direction(raw.direction || raw.side);
  const quoteExit = positive(raw.share_quote_exit_price);
  // Solo presentar estimaciones de mercado si recibimos bid/ask reciente y qty fiable.
  const estimateFromBook = !demo && open && quoteExit && quantity && positive(raw.entry_price) && dir !== '—';
  if (estimateFromBook) pnl = (quoteExit - positive(raw.entry_price)) * quantity * (dir === 'LONG' ? 1 : -1);
  const roe = percent(pnl, margin);
  const strategy = text(raw.strategy_id, 48);
  return {
    mode: demo ? 'DEMO' : 'LIVE', status: open ? 'ABIERTA' : 'CERRADA',
    symbol: text(raw.symbol || raw.coin || raw.asset || '—', 32).toUpperCase(),
    side: dir, leverage: lev,
    entry: positive(raw.entry_price),
    price: positive(open ? (quoteExit || raw.mark_price || raw.last_price || raw.current_price) : raw.exit_price),
    pnl, roe, strategy,
    reason: open ? null : text(raw.close_reason || raw.exit_reason || raw.close_source || '', 60),
    occurredAt: iso(open ? (raw.opened_at || raw.started_at || raw.persisted_at) : (raw.closed_at || raw.timestamp)),
    sharedAt: Date.now(),
    currency: 'USDC',
    estimated: Boolean(open && (demo || estimateFromBook)),
    grossEstimate: Boolean(estimateFromBook),
  };
}

export function validateReferral(data) {
  const referralCode = text(data?.referral_code || data?.user_id, 48);
  const input = text(data?.referral_link, 256);
  if (!input || !referralCode) return null;
  try {
    const u = new URL(input);
    const host = u.hostname.toLowerCase();
    if (u.protocol !== 'https:' || u.username || u.password || u.port || !['t.me', 'telegram.me'].includes(host)) return null;
    // No enviar el QR a páginas de terceros ni a parámetros manipulados.
    if (!/^[\w-]{3,64}$/.test(u.pathname.slice(1))) return null;
    if (u.searchParams.get('start') !== referralCode) return null;
    return { code: referralCode, url: u.href };
  } catch { return null; }
}

const money = (value) => value == null ? '—' : `${value >= 0 ? '+' : '−'}${Math.abs(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 })} USDC`;
const percentage = (value) => value == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
const price = (value) => value == null ? '—' : value.toLocaleString('en-US', { maximumFractionDigits: value < 1 ? 9 : value < 100 ? 6 : 4 });

function roundRect(ctx, x, y, w, h, r = 20) {
  ctx.beginPath(); ctx.roundRect(x, y, w, h, r);
}
function label(ctx, txt, x, y, size, color, weight = 600) {
  ctx.font = `${weight} ${size}px Inter, system-ui, sans-serif`;
  ctx.fillStyle = color;
  ctx.fillText(txt, x, y);
}
function fitText(ctx, value, maxWidth) {
  let textValue = String(value);
  while (ctx.measureText(textValue).width > maxWidth && textValue.length > 1) textValue = `${textValue.slice(0, -2)}…`;
  return textValue;
}
function cell(ctx, x, y, title, value, width, color = '#f1f7fd') {
  roundRect(ctx, x, y, width, 102, 18);
  ctx.fillStyle = 'rgba(7,20,34,0.94)'; ctx.fill();
  ctx.strokeStyle = 'rgba(132,168,201,0.20)'; ctx.lineWidth = 2; ctx.stroke();
  label(ctx, title, x + 20, y + 30, 18, '#8da6bc', 600);
  ctx.font = '750 28px Inter, system-ui, sans-serif';
  label(ctx, fitText(ctx, value, width - 38), x + 20, y + 70, 28, color, 750);
}
function loadLogo() {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = '/static/miniapp-logo.png';
  });
}

export async function drawCard(canvas, snapshot, referral, options = {}, logo = null) {
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Tu navegador no puede generar tarjetas PNG.');
  canvas.width = 1080; canvas.height = 1280;
  const ok = (snapshot.pnl ?? 0) >= 0;
  const accent = ok ? '#27e6a8' : '#ff5c7c';
  const bg = ctx.createLinearGradient(0, 0, 1080, 1280);
  bg.addColorStop(0, '#10273a'); bg.addColorStop(.48, '#06101e'); bg.addColorStop(1, '#030a13');
  ctx.fillStyle = bg; ctx.fillRect(0, 0, 1080, 1280);
  ctx.save(); ctx.globalAlpha = .14;
  for (let x = -300; x < 1500; x += 46) { ctx.strokeStyle = '#66c8ed'; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x + 280, 1280); ctx.stroke(); }
  ctx.restore();
  const glow = ctx.createRadialGradient(860, 300, 0, 860, 300, 600);
  glow.addColorStop(0, ok ? 'rgba(39,230,168,.28)' : 'rgba(255,92,124,.25)'); glow.addColorStop(1, 'transparent');
  ctx.fillStyle = glow; ctx.fillRect(0, 0, 1080, 940);
  ctx.strokeStyle = ok ? 'rgba(39,230,168,.65)' : 'rgba(255,92,124,.65)'; ctx.lineWidth = 4;
  roundRect(ctx, 30, 30, 1020, 1220, 38); ctx.stroke();
  if (logo) { ctx.save(); roundRect(ctx, 70, 72, 92, 92, 18); ctx.clip(); ctx.drawImage(logo, 70, 72, 92, 92); ctx.restore(); }
  label(ctx, 'TRADING X', logo ? 184 : 78, 122, 40, '#f2f8ff', 900);
  label(ctx, 'HIPER PRO · HYPERLIQUID', logo ? 184 : 78, 155, 20, '#8cacbd', 700);
  roundRect(ctx, 74, 196, 160, 49, 18); ctx.fillStyle = snapshot.mode === 'DEMO' ? '#12395b' : '#194a37'; ctx.fill();
  label(ctx, snapshot.mode, 98, 228, 25, '#e5f9ff', 800);
  roundRect(ctx, 249, 196, 184, 49, 18); ctx.fillStyle = snapshot.status === 'ABIERTA' ? '#153047' : '#263444'; ctx.fill();
  label(ctx, snapshot.status, 272, 228, 24, '#e5f9ff', 800);
  roundRect(ctx, 450, 196, 160, 49, 18); ctx.fillStyle = snapshot.side === 'SHORT' ? '#64283d' : '#164c3f'; ctx.fill();
  label(ctx, snapshot.side, 476, 228, 25, '#f2fbff', 800);
  label(ctx, snapshot.symbol, 74, 336, 72, '#f5faff', 900);
  label(ctx, `${snapshot.strategy || 'TRADING AUTOMÁTICO'}${snapshot.leverage ? ` · ${snapshot.leverage}x` : ''}`, 78, 383, 24, '#9fb5cc', 650);
  label(ctx, `Compartida: ${new Date(snapshot.sharedAt).toLocaleString('es-ES')}`, 78, 409, 16, '#819bb1', 600);
  ctx.fillStyle = 'rgba(5,15,26,.86)'; roundRect(ctx, 68, 424, 944, 265, 32); ctx.fill();
  ctx.strokeStyle = 'rgba(125,169,196,.24)'; ctx.lineWidth = 2; ctx.stroke();
  if (options.showRoe !== false) {
    label(ctx, 'ROI / ROE', 108, 484, 25, '#a0b5c9', 700);
    label(ctx, percentage(snapshot.roe), 103, 583, 75, accent, 900);
  }
  if (options.showPnl !== false) {
    label(ctx, snapshot.grossEstimate ? 'PnL bruto estimado · sin fees' : snapshot.estimated ? 'PnL flotante estimado' : snapshot.status === 'ABIERTA' ? 'PnL flotante registrado' : 'PnL neto', 108, 637, 23, '#9fb5c9', 700);
    label(ctx, money(snapshot.pnl), 350, 637, 37, accent, 850);
  }
  if (options.showPnl === false && options.showRoe === false) label(ctx, 'DETALLES DE OPERACIÓN', 108, 555, 34, '#ebf3fa', 800);
  cell(ctx, 72, 723, 'Precio de entrada', price(snapshot.entry), 448);
  cell(ctx, 544, 723, snapshot.status === 'ABIERTA' ? 'Precio observado' : 'Precio de salida', price(snapshot.price), 448, accent);
  cell(ctx, 72, 848, 'Tipo de cuenta', snapshot.mode, 448);
  cell(ctx, 544, 848, snapshot.status === 'ABIERTA' ? 'Estado' : 'Motivo de cierre', snapshot.status === 'ABIERTA' ? 'OPERACIÓN ACTIVA' : (snapshot.reason || '—'), 448);
  ctx.strokeStyle = 'rgba(130,170,200,.20)'; ctx.beginPath(); ctx.moveTo(72, 984); ctx.lineTo(1000, 984); ctx.stroke();
  if (referral) {
    label(ctx, 'ÚNETE A TRADING X HIPER PRO', 76, 1030, 25, '#f2fbff', 800);
    label(ctx, `Código de referido: ${referral.code}`, 76, 1071, 23, '#93b9ce', 650);
    ctx.font = '650 19px Inter, system-ui, sans-serif';
    label(ctx, fitText(ctx, referral.url, 628), 76, 1112, 19, '#79e7c9', 650);
    label(ctx, 'Escanea el QR para registrarte', 76, 1154, 19, '#9fb0c5', 600);
    drawQrCode(ctx, referral.url, 780, 991, 195);
  } else {
    label(ctx, 'TRADING X HIPER PRO', 75, 1080, 31, '#eef7ff', 800);
    label(ctx, 'Enlace personal de referido no disponible', 75, 1123, 20, '#8ea5bc', 600);
  }
  label(ctx, 'DEMO = CAPITAL SIMULADO  ·  LOS RESULTADOS NO GARANTIZAN BENEFICIOS', 75, 1216, 16, '#839ab2', 600);
  return canvas;
}

const toBlob = (canvas) => new Promise((resolve, reject) => canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error('No se pudo exportar la imagen.')), 'image/png'));
const download = (blob, name) => {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = name;
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
};

let activeModal = null;
let activeDismiss = null;
export async function openShare(kind, raw, referrals = null) {
  const snapshot = normalizeTrade(kind, raw);
  const referral = validateReferral(referrals);
  if (activeDismiss) activeDismiss();
  const dialog = document.createElement('div');
  dialog.className = 'trade-share-backdrop'; dialog.setAttribute('role', 'presentation');
  const panel = document.createElement('section'); panel.className = 'trade-share-dialog';
  panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true'); panel.setAttribute('aria-label', 'Compartir operación');
  panel.innerHTML = `<header class="trade-share-head"><div><span>TRADING X HIPER PRO</span><h2>Compartir operación</h2></div><button type="button" class="trade-share-close" aria-label="Cerrar ventana">✕</button></header><div class="trade-share-preview"><canvas aria-label="Vista previa de la tarjeta con datos y QR"></canvas></div><div class="trade-share-settings"><label><input type="checkbox" data-option="showRoe" checked> Mostrar ROI / ROE</label><label><input type="checkbox" data-option="showPnl" checked> Mostrar PnL</label></div><p class="trade-share-note" role="status"></p><div class="trade-share-actions"><button type="button" data-action="download">Descargar PNG</button><button type="button" data-action="share" class="primary-button">Compartir tarjeta</button></div>`;
  dialog.append(panel); document.body.append(dialog); activeModal = dialog;
  const previousFocus = document.activeElement;
  const esc = (event) => { if (event.key === 'Escape') dismiss(); };
  const dismiss = () => {
    document.removeEventListener('keydown', esc);
    dialog.remove();
    if (activeModal === dialog) activeModal = null;
    if (activeDismiss === dismiss) activeDismiss = null;
    previousFocus?.focus?.();
  };
  document.addEventListener('keydown', esc);
  activeDismiss = dismiss;
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dismiss(); });
  panel.querySelector('.trade-share-close').addEventListener('click', dismiss);
  panel.querySelector('.trade-share-close').focus();
  const canvas = panel.querySelector('canvas');
  const note = panel.querySelector('.trade-share-note');
  const qrOption = panel.querySelector('[data-option="showRoe"]');
  const pnlOption = panel.querySelector('[data-option="showPnl"]');
  const buttons = Array.from(panel.querySelectorAll('.trade-share-actions button'));
  const logo = await loadLogo();
  if (!dialog.isConnected) return;
  const redraw = () => drawCard(canvas, snapshot, referral, { showRoe: qrOption.checked, showPnl: pnlOption.checked }, logo);
  try { await redraw(); } catch (e) { note.textContent = e.message; }
  [qrOption, pnlOption].forEach(el => el.addEventListener('change', () => { redraw().catch((e) => { note.textContent = e.message; }); }));
  const fileName = `TradingX-${snapshot.mode}-${snapshot.symbol.replace(/[^A-Z0-9-]/gi, '-')}-${snapshot.status}.png`;
  async function action(share) {
    buttons.forEach((b) => { b.disabled = true; }); note.textContent = 'Preparando tarjeta…';
    try {
      await redraw();
      const blob = await toBlob(canvas);
      if (share && typeof navigator.share === 'function') {
        const file = new File([blob], fileName, { type: 'image/png' });
        if (navigator.canShare?.({ files: [file] })) {
          try { await navigator.share({ files: [file], title: 'Trading X Hiper Pro · Operación' }); note.textContent = 'Compartido.'; return; }
          catch (e) { if (e?.name === 'AbortError') { note.textContent = 'Compartir cancelado.'; return; } }
        }
      }
      download(blob, fileName);
      note.textContent = share ? 'Imagen descargada: puedes compartirla desde tu dispositivo.' : 'Tarjeta PNG descargada.';
    } catch (e) { note.textContent = `No se pudo preparar la tarjeta: ${e.message || e}`; }
    finally { buttons.forEach((b) => { b.disabled = false; }); }
  }
  panel.querySelector('[data-action="download"]').addEventListener('click', () => action(false));
  panel.querySelector('[data-action="share"]').addEventListener('click', () => action(true));
}

// La vista principal puede seguir siendo script clásico. El módulo maneja únicamente la tarjeta.
if (typeof window !== 'undefined') {
  window.addEventListener('hyperleague:share-trade', (event) => {
    openShare(event.detail?.kind, event.detail?.trade, event.detail?.referrals).catch((e) => {
      // El aviso es visible para el usuario, sin datos sensibles.
      const notice = document.createElement('div'); notice.className = 'trade-share-error'; notice.setAttribute('role', 'alert');
      notice.textContent = `No fue posible compartir la operación: ${e.message}`;
      document.body.append(notice); setTimeout(() => notice.remove(), 6000);
    });
  });
}
