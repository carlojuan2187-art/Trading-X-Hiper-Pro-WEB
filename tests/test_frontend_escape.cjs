// Run selected production renderers with an inert DOM; injected markup must be escaped.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const src = fs.readFileSync('app/web/static/app.js', 'utf8');
function source(name) {
  const start = src.indexOf(`function ${name}(`);
  assert(start >= 0);
  const end = src.indexOf('\n}', start);
  return src.slice(start, end + 2);
}
const context = { document: { createElement: () => ({}) } };
vm.createContext(context);
vm.runInContext(['escapeHtml', 'pillClass', 'formatDate', 'renderActivityList'].map(source).join('\n'), context);
const nodes = [];
const target = { appendChild: node => nodes.push(node) };
const payload = '<img src=x onerror=alert(1)>';
context.renderActivityList(target, [{ title: payload, username: payload, detail: payload, target_username: payload }], '');
assert(!nodes[0].innerHTML.includes('<img'));
assert(nodes[0].innerHTML.includes('&lt;img'));
console.log('Frontend escape regression: passed');
