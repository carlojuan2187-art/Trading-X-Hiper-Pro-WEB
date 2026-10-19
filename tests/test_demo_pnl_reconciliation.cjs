// Regression: 250-USDC exposure must NOT charge the same as 990 USDC.
// All PnL and fees are from the SAME live bid/ask, not a stale ledger mark.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const mod = pathToFileURL(path.resolve(__dirname, '../app/web/static/market/demo-pnl.mjs'));
let reconcile;
async function calc() {
  if (!reconcile) reconcile = (await import(mod.href)).reconcileDemoQuote;
  return reconcile;
}
test('AAVE-like SHORT 250 notional, fees 0.125 + ~0.125', async () => {
  const fn = await calc();
  const p = { direction:'SHORT', entry_price: 100, quantity:2.5, entry_fee_usdc:.125 };
  const rate = {feeBps:5,slippageBps:2};
  const quote = { bid:99.61, ask:99.64/(1+.0002) }; // exit fill precisely 99.64
  const r=fn(p,quote,rate);
  assert.ok(r);
  assert.ok(Math.abs(r.entryNotional-250)<1e-8);
  assert.ok(Math.abs(r.entryFee-.125)<1e-8);
  assert.ok(Math.abs(r.exitFee-.12455)<1e-8);
  assert.ok(Math.abs(r.gross-.90)<1e-7);
  assert.ok(Math.abs(r.net-.65045)<1e-7);
});
test('990-USDC notional pays 3.96x entry commission', async () => {
  const fn=await calc();
  const costs={feeBps:5,slippageBps:2};
  const small=fn({direction:'SHORT',entry_price:100,quantity:2.5,entry_fee_usdc:.125},{bid:99.6,ask:99.7},costs);
  const big=fn({direction:'SHORT',entry_price:100,quantity:9.9,entry_fee_usdc:.495},{bid:99.6,ask:99.7},costs);
  assert.ok(Math.abs(small.entryNotional-250)<1e-9);
  assert.ok(Math.abs(big.entryNotional-990)<1e-9);
  assert.ok(Math.abs(big.feesTotal/small.feesTotal-3.96)<1e-8);
});
test('LONG includes entry and exit fees once and fills on bid minus slippage', async () => {
  const fn=await calc();
  const r=fn({direction:'LONG',entry_price:100,quantity:2.5,entry_fee_usdc:.125},
             {bid:100.5,ask:100.51},{feeBps:5,slippageBps:2});
  const fill=100.5*.9998;
  assert.ok(Math.abs(r.gross-(fill-100)*2.5)<1e-7);
  assert.ok(Math.abs(r.net-(r.gross-r.entryFee-r.exitFee))<1e-8);
});
test('missing quotes/fees fail closed: no invented PnL', async () => {
  const fn=await calc();
  const p={direction:'SHORT',entry_price:100,quantity:2.5,entry_fee_usdc:.125};
  assert.equal(fn(p,{bid:99.6,ask:99.7},{feeBps:null,slippageBps:2}),null);
  assert.equal(fn(p,{bid:99.8,ask:99.7},{feeBps:5,slippageBps:2}),null);
  assert.equal(fn({...p,quantity:0},{bid:99.6,ask:99.7},{feeBps:5,slippageBps:2}),null);
});
