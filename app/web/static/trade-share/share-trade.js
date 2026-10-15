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
// Trade facts are plain text over the user-supplied artwork; no stat tiles.
function drawText(ctx, value, x, y, size, color, weight = 750, maxWidth = 930) {
  ctx.save();
  ctx.font = `${weight} ${size}px Inter, system-ui, sans-serif`;
  ctx.textBaseline = 'alphabetic';
  ctx.lineJoin = 'round';
  ctx.lineWidth = Math.max(3, size / 12);
  ctx.strokeStyle = 'rgba(0,0,0,.9)';
  ctx.shadowColor = 'rgba(0,0,0,.95)';
  ctx.shadowBlur = 11;
  const txt = fitText(ctx, String(value), maxWidth);
  ctx.strokeText(txt, x, y);
  ctx.fillStyle = color;
  ctx.fillText(txt, x, y);
  ctx.restore();
}
function loadArtwork() {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('No se pudo cargar el fondo de la tarjeta.'));
    img.src = '/static/tradingx-share-background.avif';
  });
}

export async function drawCard(canvas, snapshot, referral, options = {}, artwork = null) {
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Tu navegador no puede generar tarjetas PNG.');
  if (!artwork?.naturalWidth || !artwork?.naturalHeight) throw new Error('Fondo de marca no disponible.');
  canvas.width = 1080; canvas.height = 1280;
  // Square artwork: center crop, never stretch the golden X.
  const w = artwork.naturalWidth, h = artwork.naturalHeight;
  const cropW = Math.min(w, h * canvas.width / canvas.height);
  const cropH = Math.min(h, w * canvas.height / canvas.width);
  ctx.drawImage(artwork, (w-cropW)/2, (h-cropH)/2, cropW, cropH, 0, 0, 1080, 1280);
  // Gradient tint is transparent; there are no opaque cards over the logo.
  const shade = ctx.createLinearGradient(0, 0, 0, 1280);
  shade.addColorStop(0, 'rgba(0,0,0,.78)');
  shade.addColorStop(.30, 'rgba(0,0,0,.28)');
  shade.addColorStop(.54, 'rgba(0,0,0,.09)');
  shade.addColorStop(.72, 'rgba(0,0,0,.3)');
  shade.addColorStop(1, 'rgba(0,0,0,.83)');
  ctx.fillStyle = shade; ctx.fillRect(0, 0, 1080, 1280);
  const accent = (snapshot.pnl ?? 0) >= 0 ? '#66ffab' : '#ff788a';
  const white = '#fff8e8';
  drawText(ctx, 'TRADING X HIPER PRO', 65, 94, 44, white, 900);
  drawText(ctx, `${snapshot.mode}   ·   ${snapshot.status}   ·   ${snapshot.side}`,
           67, 149, 26, '#ffe8b3', 800);
  drawText(ctx, snapshot.symbol, 65, 265, 75, white, 900);
  drawText(ctx, `${snapshot.strategy || 'TRADING AUTOMÁTICO'}${snapshot.leverage ? ` · ${snapshot.leverage}x` : ''}`,
           67, 317, 27, '#eaf3ec', 720);
  if (options.showRoe !== false) {
    drawText(ctx, 'ROE', 65, 388, 28, '#ffebbd', 800);
    drawText(ctx, percentage(snapshot.roe), 64, 479, 88, accent, 900);
  }
  if (options.showPnl !== false) {
    const caption = snapshot.grossEstimate ? 'PnL BRUTO ESTIMADO'
      : snapshot.estimated ? 'PnL FLOTANTE ESTIMADO'
      : snapshot.status === 'ABIERTA' ? 'PnL FLOTANTE' : 'PnL NETO REALIZADO';
    const posY = options.showRoe !== false ? 553 : 445;
    drawText(ctx, caption, 67, posY, 26, '#ffe8b3', 800);
    drawText(ctx, money(snapshot.pnl), 65, posY + 80, 62, accent, 900, 950);
  }
  const rows = [
    ['PRECIO DE ENTRADA', price(snapshot.entry), 66, 865],
    [snapshot.status === 'ABIERTA' ? 'PRECIO OBSERVADO' : 'PRECIO DE SALIDA',
     price(snapshot.price), 555, 865],
    ['DIRECCIÓN', snapshot.side, 66, 972],
    ['ESTADO / CIERRE', snapshot.status === 'ABIERTA' ? 'ABIERTA' : snapshot.reason || '—',
     555, 972],
  ];
  for (const [label, val, x, y] of rows) {
    drawText(ctx, label, x, y, 20, '#ffe7a7', 800, 440);
    drawText(ctx, val, x, y + 42, 32, white, 850, 440);
  }
  if (referral) {
    drawText(ctx, 'CÓDIGO DE REFERIDO', 66, 1098, 20, '#ffe7a7', 800);
    drawText(ctx, referral.code, 66, 1137, 31, white, 850, 575);
    // QR is functional, not a boxed trading-data tile.
    drawQrCode(ctx, referral.url, 862, 1039, 144);
  } else {
    drawText(ctx, 'AUTOMATED TRADING SYSTEM · 24/7', 66, 1137, 29, white, 850);
  }
  drawText(ctx, snapshot.mode === 'DEMO'
    ? 'CAPITAL SIMULADO · LOS RESULTADOS NO GARANTIZAN BENEFICIOS'
    : 'TRADING CON RIESGO · LOS RESULTADOS NO GARANTIZAN BENEFICIOS',
    65, 1230, 18, '#f4e9d5', 650, 950);
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
  const artwork = await loadArtwork();
  if (!dialog.isConnected) return;
  const redraw = () => drawCard(canvas, snapshot, referral, { showRoe: qrOption.checked, showPnl: pnlOption.checked }, artwork);
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
