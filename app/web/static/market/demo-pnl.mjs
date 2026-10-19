/** Quote-consistent DEMO financial reconciliation, without LIVE order logic.
 * Currency values always use the actual quantity and the SAME live bid/ask.
 * Entry fee has already been debited from DEMO balance, but a trade's all-in
 * PnL must subtract it exactly once. This module never places orders.
 */
export function reconcileDemoQuote(position, quote, costs) {
  const finite = x => x !== null && x !== undefined && x !== '' && Number.isFinite(Number(x));
  const entry = Number(position?.entry_price);
  const qty = Number(position?.quantity);
  const bid = Number(quote?.bid);
  const ask = Number(quote?.ask);
  const entryFee = Number(position?.entry_fee_usdc);
  const feeBps = Number(costs?.feeBps);
  const slippageBps = Number(costs?.slippageBps);
  const side = String(position?.direction || '').toUpperCase();
  if (![position?.entry_price, position?.quantity, quote?.bid, quote?.ask,
       position?.entry_fee_usdc, costs?.feeBps, costs?.slippageBps].every(finite)
      || ![entry, qty, bid, ask, entryFee, feeBps, slippageBps].every(Number.isFinite)
      || entry <= 0 || qty <= 0 || bid <= 0 || ask < bid || entryFee < 0
      || feeBps < 0 || feeBps > 50 || slippageBps < 0 || slippageBps > 50
      || !['LONG', 'SHORT'].includes(side)) return null;
  const rate = feeBps / 10000;
  const slip = slippageBps / 10000;
  const exit = side === 'LONG' ? bid * (1 - slip) : ask * (1 + slip);
  if (!Number.isFinite(exit) || exit <= 0) return null;
  const gross = (side === 'LONG' ? 1 : -1) * (exit - entry) * qty;
  const exitFee = exit * qty * rate;
  const net = gross - entryFee - exitFee;
  return {
    entryNotional: entry * qty, exitNotional: exit * qty,
    entryFee, exitFee, feesTotal: entryFee + exitFee,
    gross, net, exitPrice: exit,
    slippageBps, feeBps
  };
}
