// Regression: chart uses native Hyperliquid symbols for DEMO historical trades.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const path = 'app/web/static/market/market-terminal.js';
const source = fs.readFileSync(path, 'utf8');
const start = source.indexOf('const coin = symbol =>');
assert(start >= 0, 'expected shared coin normalizer');
const end = source.indexOf(';', start);
assert(end > start, 'missing end of normalizer');
const normalize = vm.runInNewContext(source.slice(start, end + 1) + '\ncoin;');
for(const [input, expected] of [
  ['FET-PERP', 'FET'],
  ['FET-PERP/USDC', 'FET'],
  ['fet-perp', 'FET'],
  ['BTC-USDC', 'BTC'],
  ['ETH/USDT', 'ETH'],
  ['SOL_USDC', 'SOL'],
  ['xyz:ABC', 'XYZ:ABC'],
  ['BTC', 'BTC'],
]) assert.equal(normalize(input), expected, input);
assert.match(source, /const nativeSymbol=coin\(symbol\)/, 'choose must normalize before chart reload');
assert.match(source, /ticker: nativeSymbol/, 'chart must use normalized native symbol');
assert.match(source, /this\.instrumentCache && !this\.instruments\.has\(nativeSymbol\)/,
             'stale/delisted history must be rejected once instruments loaded');
assert.match(source, /this\.follow=false/, 'must stop following a delisted contract');
console.log('Market terminal legacy FET-PERP/native coin aliases: passed');
