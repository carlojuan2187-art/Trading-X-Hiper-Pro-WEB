import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeTrade, validateReferral, drawCard, CARD_WIDTH, CARD_HEIGHT } from '../app/web/static/trade-share/share-trade.js';
import { createQrMatrix } from '../app/web/static/trade-share/qr-code.js';

test('DEMO abierta lleva entrada, capital simulado, beneficio y ROE', () => {
  const item = normalizeTrade('demo-open', {
    symbol:'BTC', direction:'long', entry_price:100, mark_price:101,
    unrealized_pnl_usdc:0.4, margin_usdc:4, leverage:10,
    wallet:'secret', private_key:'secret', strategy_id:'BREAKOUT_RETEST',
  });
  assert.equal(item.mode, 'DEMO'); assert.equal(item.status, 'ABIERTA');
  assert.equal(item.pnl, 0.4); assert.equal(item.roe, 10); assert.equal(item.estimated, true);
  assert.equal(item.price, 101); assert.equal(item.entry, 100);
  assert.ok(!JSON.stringify(item).includes('secret'));
});

test('DEMO abierta resta la comisión de entrada exactamente una vez en tarjeta', () => {
  const item=normalizeTrade('demo-open', {
    symbol:'AAVE-PERP', direction:'short', entry_price:147.830428,
    mark_price:147.10, unrealized_pnl_usdc:0.775,
    entry_fee_usdc:0.125, margin_usdc:25, leverage:10
  });
  assert.ok(Math.abs(item.pnl-0.65)<1e-8);
  assert.ok(Math.abs(item.roe-2.6)<1e-8);
});

test('DEMO cerrada usa neto y precio de salida, no el mark anterior', () => {
  const r = normalizeTrade('demo-closed', {
    symbol: 'ETH', direction:'short', entry_price:1000,
    exit_price:900, mark_price:999, pnl_net_usdc:2.25,
    margin_usdc:15, leverage:10, close_reason:'TP', closed_at:'2026-10-13T06:00:00Z'
  });
  assert.equal(r.side, 'SHORT'); assert.equal(r.roe, 15);
  assert.equal(r.price, 900); assert.equal(r.reason,'TP');
});

test('LIVE cerrada sin margen ni leverage muestra ROE desconocido', () => {
  const r = normalizeTrade('live-closed', { symbol:'SOL',side:'B',entry_price:100,exit_price:102,profit:-0.25 });
  assert.equal(r.mode,'LIVE'); assert.equal(r.pnl,-0.25);
  assert.equal(r.leverage,null); assert.equal(r.roe,null);
});

test('LIVE sin precio actual no inventa precios ni PnL', () => {
  const r=normalizeTrade('live-open',{symbol:'SUI',side:'SELL',entry_price:3});
  assert.equal(r.side,'SHORT'); assert.equal(r.price,null);
  assert.equal(r.pnl,null); assert.equal(r.roe,null);
});

test('La tarjeta LIVE puede estimar PnL con cotización bid/ask reciente, sin atribuir comisiones', () => {
  const p=normalizeTrade('live-open',{symbol:'BTC', direction:'LONG',entry_price:100, share_quote_exit_price:102, qty:2, leverage:10, margin_usdc:20 });
  assert.equal(p.price,102); assert.equal(p.pnl,4); assert.equal(p.roe,20);
  assert.equal(p.grossEstimate,true);
  const noQty=normalizeTrade('live-open',{symbol:'BTC', direction:'LONG',entry_price:100,share_quote_exit_price:102});
  assert.equal(noQty.pnl,null); assert.equal(noQty.grossEstimate,false);
});

test('QR se alimenta solo de enlace Telegram que contenga el código correcto', () => {
  const r=validateReferral({referral_code:'12345',referral_link:'https://t.me/TradingXHiperPro_bot?start=12345'});
  assert.equal(r.code,'12345'); assert.equal(r.url,'https://t.me/TradingXHiperPro_bot?start=12345');
  assert.equal(validateReferral({referral_code:'12345',referral_link:'https://t.me/TradingXHiperPro_bot?start=987'}),null);
  assert.equal(validateReferral({referral_code:'12345',referral_link:'https://evil.example/next?start=12345'}),null);
  assert.equal(validateReferral({referral_code:'12345',referral_link:'javascript:alert(1)'}),null);
  assert.equal(validateReferral({referral_code:'12345',referral_link:'https://t.me.evil.example/bot?start=12345'}),null);
});

test('QR local tiene 37x37 módulos y bitmap binario', () => {
  const m=createQrMatrix('https://t.me/TradingXHiperPro_bot?start=12345');
  assert.equal(m.length,37);
  assert.equal(m[0].length,37);
  assert.ok(m.every(row=>row.every(cell=>typeof cell==='boolean')));
});

test('El QR rechaza enlaces demasiado largos de forma explícita', () => {
  assert.throws(()=>createQrMatrix('https://t.me/'+ 'a'.repeat(120)), /largo/);
});


test('Tarjeta 4:5 dibuja datos completos sin recortar ni depender del AVIF', async () => {
  const painted = [];
  const gradient = { addColorStop() {} };
  const ctx = {
    createLinearGradient: () => gradient,
    beginPath() {}, roundRect() {}, rect() {}, clip() {},
    fill() {}, stroke() {}, fillRect() {}, moveTo() {}, lineTo() {},
    save() {}, restore() {},
    measureText(value) { return { width: String(value).length * 17 }; },
    fillText(value, x, y, maxWidth) { painted.push({ value: String(value), x, y, maxWidth }); },
    drawImage() { throw new Error('El fallback no debe invocar el arte ausente'); },
  };
  const canvas = { width: 0, height: 0, getContext: () => ctx };
  const data = normalizeTrade('demo-closed', {
    symbol: 'XRP-PERP', direction: 'LONG', entry_price: 1.307461,
    exit_price: 1.29984, margin_usdc: 72.79, pnl_net_usdc: -4.9719,
    leverage: 10, strategy_id: 'liquidity_sweep_reversal', close_reason: 'SL',
  });
  await drawCard(canvas, data, null, {}, null);
  assert.equal(canvas.width, CARD_WIDTH);
  assert.equal(canvas.height, CARD_HEIGHT);
  assert.equal(canvas.width / canvas.height, 4 / 5);
  for (const value of ['XRP-PERP', '1.307461', '1.29984', 'SL', '−4.9719 USDC']) {
    assert.ok(painted.some((item) => item.value === value), 'Falta ' + value);
  }
  assert.ok(painted.every((item) => item.y < CARD_HEIGHT), 'Texto fuera del PNG');
  painted.length = 0;
  await drawCard(canvas, data, null, { showRoe: false, showPnl: false }, null);
  assert.ok(!painted.some((item) => item.value.startsWith('RENTABILIDAD')));
  assert.ok(!painted.some((item) => item.value.includes('USDC')));
  assert.ok(painted.some((item) => item.value === '1.307461'));
});
