const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('app/web/static/index.html', 'utf8');
const js = fs.readFileSync('app/web/static/app.js', 'utf8');
const css = fs.readFileSync('app/web/static/brand-premium.css', 'utf8');

test('premium design loads after terminal styling', () => {
  const baseline = html.indexOf('/static/styles.css?');
  const market = html.indexOf('/static/market/market-terminal.css?');
  const premium = html.indexOf('/static/brand-premium.css?');
  assert(baseline >= 0 && baseline < market && market < premium);
  assert.match(css, /@media\s*\(max-width:860px\)/);
});

test('mobile menu keeps existing sections and admin visibility', () => {
  for (const tab of ['overview','demo','control','operations','billing','system','admin']) {
    assert.match(html, new RegExp('data-tab="' + tab + '"'));
  }
  assert.match(html, /id="mobileMoreMenu" hidden/);
  assert.match(html, /class="tab hidden" data-tab="admin"/);
  assert.match(css, /\.mobile-more-menu \.tab\.hidden\{display:none!important\}/);
  assert.match(js, /moreButton\?\.classList\.toggle\('is-active'/);
  assert.match(js, /closeMoreMenu\(\)/);
});

test('existing trading controls remain mapped to actual handlers', () => {
  for (const id of [
    'demoStartButton','demoPauseButton','demoResetButton',
    'demoCapitalForm','activateTradingButton','pauseTradingButton',
    'privateKeyInput','configurationForm','adminTabButton','demoEquity',
  ]) assert(html.includes('id="' + id + '"'), 'missing control: ' + id);
  assert.match(js, /mutateDemo\('\/api\/v1\/demo\/start'\)/);
  assert.match(js, /activateTradingAction/);
});

test('dashboard hero is data-bound rather than mock trading performance', () => {
  for (const id of ['brandHeroEquity','brandHeroStatus','brandHeroPnl','brandHeroTrades']) {
    assert(html.includes('id="' + id + '"'));
  }
  assert.match(js, /function renderBrandHero\(\)/);
  assert.match(js, /state\.performance/);
  assert.match(js, /state\.dashboard/);
  assert.match(html, /Cuenta LIVE · datos reales/);
  assert.match(html, /Cuenta DEMO · capital simulado/i);
});
