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


// Exportación 4:5 (feed social). La vista previa muestra exactamente el mismo canvas.
export const CARD_WIDTH = 1080;
export const CARD_HEIGHT = 1350;

function roundRect(ctx, x, y, w, h, radius = 24) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, radius);
}
function drawText(ctx, value, x, y, size, color, weight = 700, maxWidth = 920) {
  const content = String(value ?? '—');
  ctx.save();
  ctx.textBaseline = 'alphabetic';
  ctx.fillStyle = color;
  let fontSize = size;
  do {
    ctx.font = weight + ' ' + fontSize + 'px Inter, system-ui, sans-serif';
    if (ctx.measureText(content).width <= maxWidth || fontSize <= 26) break;
    fontSize -= 2;
  } while (true);
  // maxWidth es una última protección: nunca se elimina un precio ni un PnL.
  ctx.fillText(content, x, y, maxWidth);
  ctx.restore();
}
function cardPanel(ctx, x, y, w, h) {
  ctx.save();
  roundRect(ctx, x, y, w, h, 24);
  ctx.fillStyle = 'rgba(6, 17, 24, .90)';
  ctx.fill();
  ctx.strokeStyle = 'rgba(238, 193, 108, .35)';
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.restore();
}
function cardRule(ctx, x1, y1, x2, y2, color = 'rgba(232, 197, 131, .24)') {
  ctx.beginPath();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
}
function loadArtwork() {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('No se pudo cargar el fondo de marca.'));
    img.src = '/static/tradingx-share-background.avif';
  });
}

export async function drawCard(canvas, snapshot, referral, options = {}, artwork = null) {
  if (!canvas?.getContext) throw new Error('Tu navegador no puede generar tarjetas PNG.');
  canvas.width = CARD_WIDTH;
  canvas.height = CARD_HEIGHT;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Tu navegador no puede generar tarjetas PNG.');

  // Fondo definido por vectores: nítido a 1080 px aunque la imagen AVIF falte.
  const bg = ctx.createLinearGradient(0, 0, CARD_WIDTH, CARD_HEIGHT);
  bg.addColorStop(0, '#101b27');
  bg.addColorStop(.48, '#0a1920');
  bg.addColorStop(1, '#060e17');
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, CARD_WIDTH, CARD_HEIGHT);

  if (artwork?.naturalWidth && artwork?.naturalHeight) {
    // El arte dorado queda como identidad secundaria, sin texto superpuesto.
    ctx.save();
    ctx.beginPath();
    ctx.rect(480, 190, 550, 520);
    ctx.clip();
    ctx.globalAlpha = .45;
    const side = Math.min(artwork.naturalWidth, artwork.naturalHeight);
    ctx.drawImage(artwork, (artwork.naturalWidth - side) / 2,
      (artwork.naturalHeight - side) / 2, side, side, 480, 190, 550, 520);
    ctx.restore();
  }

  const gold = '#f3c977';
  const cream = '#fff8e9';
  const muted = '#c7d7d5';
  const accent = snapshot.pnl == null ? '#f3c977'
    : snapshot.pnl >= 0 ? '#4cf3ac' : '#ff758f';

  ctx.save();
  roundRect(ctx, 28, 28, 1024, 1294, 32);
  ctx.strokeStyle = 'rgba(247, 202, 121, .60)';
  ctx.lineWidth = 3;
  ctx.stroke();
  ctx.restore();
  cardRule(ctx, 72, 203, 1008, 203, 'rgba(243,201,119,.48)');

  drawText(ctx, 'TRADING X HIPER PRO', 72, 117, 52, cream, 900, 940);
  drawText(ctx, snapshot.mode + '   ·   ' + snapshot.status + '   ·   ' + snapshot.side,
    74, 173, 31, gold, 800, 925);
  drawText(ctx, snapshot.symbol, 72, 324, 102, cream, 900, 928);
  drawText(ctx, (snapshot.strategy || 'TRADING AUTOMÁTICO') +
    (snapshot.leverage ? '  ·  ' + snapshot.leverage + 'x' : ''),
    74, 382, 32, muted, 700, 915);

  const showRoe = options.showRoe !== false;
  const showPnl = options.showPnl !== false;
  if (showRoe || showPnl) {
    cardPanel(ctx, 60, 425, 960, 239);
    if (showRoe && showPnl) cardRule(ctx, 520, 460, 520, 628);
    if (showRoe) {
      drawText(ctx, 'RENTABILIDAD · ROE', 88, 487, 30, gold, 800, showPnl ? 400 : 880);
      drawText(ctx, percentage(snapshot.roe), 86, 592, showPnl ? 101 : 113,
        snapshot.roe == null ? cream : accent, 900, showPnl ? 406 : 880);
    }
    if (showPnl) {
      const caption = snapshot.grossEstimate ? 'PnL BRUTO ESTIMADO'
        : snapshot.estimated ? 'PnL FLOTANTE ESTIMADO'
        : snapshot.status === 'ABIERTA' ? 'PnL FLOTANTE' : 'PnL NETO REALIZADO';
      const x = showRoe ? 548 : 88;
      const max = showRoe ? 439 : 870;
      drawText(ctx, caption, x, 487, 28, gold, 800, max);
      drawText(ctx, money(snapshot.pnl), x, 590, showRoe ? 64 : 88,
        snapshot.pnl == null ? cream : accent, 900, max);
    }
  }

  // Campos completos con zonas de lectura independientes del fondo y del logo.
  cardPanel(ctx, 60, 716, 960, 323);
  drawText(ctx, 'DATOS DE LA OPERACIÓN', 88, 768, 28, gold, 800, 870);
  cardRule(ctx, 540, 799, 540, 1010);
  cardRule(ctx, 89, 903, 989, 903);
  const fields = [
    ['PRECIO DE ENTRADA', price(snapshot.entry), 89, 827],
    [snapshot.status === 'ABIERTA' ? 'PRECIO OBSERVADO' : 'PRECIO DE SALIDA',
      price(snapshot.price), 561, 827],
    ['DIRECCIÓN', snapshot.side, 89, 949],
    ['ESTADO / CIERRE', snapshot.status === 'ABIERTA' ? 'ABIERTA' : snapshot.reason || '—',
      561, 949],
  ];
  for (const [heading, value, x, y] of fields) {
    drawText(ctx, heading, x, y, 27, gold, 750, 414);
    drawText(ctx, value, x, y + 55, 48, cream, 850, 420);
  }

  cardPanel(ctx, 60, 1070, 960, 204);
  if (referral) {
    drawText(ctx, 'TU CÓDIGO DE REFERIDO', 88, 1134, 28, gold, 800, 675);
    drawText(ctx, referral.code, 88, 1210, 54, cream, 900, 660);
    // 200px: QR local con margen blanco suficiente para escanear.
    drawQrCode(ctx, referral.url, 800, 1073, 200);
  } else {
    drawText(ctx, 'AUTOMATED TRADING SYSTEM', 88, 1150, 39, cream, 850, 866);
    drawText(ctx, 'OPERACIONES 24/7', 89, 1213, 31, gold, 750, 850);
  }
  drawText(ctx, snapshot.mode === 'DEMO'
    ? 'CAPITAL SIMULADO · LOS RESULTADOS NO GARANTIZAN BENEFICIOS'
    : 'TRADING CON RIESGO · LOS RESULTADOS NO GARANTIZAN BENEFICIOS',
    67, 1305, 23, muted, 650, 948);
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
  panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true'); panel.setAttribute('aria-labelledby', 'tradeShareTitle');
  panel.innerHTML = `<header class="trade-share-head"><div><span>TRADING X HIPER PRO</span><h2 id="tradeShareTitle">Compartir operación</h2></div><button type="button" class="trade-share-close" aria-label="Cerrar ventana">✕</button></header><div class="trade-share-preview"><canvas role="img" aria-label="Vista previa completa de la tarjeta con datos y QR"></canvas></div><div class="trade-share-settings"><label><input type="checkbox" data-option="showRoe" checked> Mostrar ROI / ROE</label><label><input type="checkbox" data-option="showPnl" checked> Mostrar PnL</label></div><p class="trade-share-note" role="status"></p><div class="trade-share-actions"><button type="button" data-action="download">Descargar PNG</button><button type="button" data-action="share" class="primary-button">Compartir tarjeta</button></div>`;
  dialog.append(panel); document.body.append(dialog); activeModal = dialog;
  const previousFocus = document.activeElement;
  const esc = (event) => {
    if (event.key === 'Escape') { dismiss(); return; }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(panel.querySelectorAll('button, input')).filter((el) => !el.disabled);
    if (!focusable.length) return;
    if (event.shiftKey && document.activeElement === focusable[0]) {
      event.preventDefault(); focusable[focusable.length - 1].focus();
    } else if (!event.shiftKey && document.activeElement === focusable[focusable.length - 1]) {
      event.preventDefault(); focusable[0].focus();
    }
  };
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
  // Un fallo del asset no impide que el usuario genere una tarjeta nítida.
  const artwork = await loadArtwork().catch(() => null);
  if (!dialog.isConnected) return;
  if (!artwork) note.textContent = 'Fondo de marca no disponible; se usó el fondo alternativo.';
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
