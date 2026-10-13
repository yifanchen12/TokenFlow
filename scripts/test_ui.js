// Execute the page's real task/history functions with a tiny DOM and fake transport.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const nodes = new Map(), storage = new Map(), timers = new Map();
let timerId = 0;
function node() {
  const classes = new Set();
  return {value: '', textContent: '', disabled: false, files: [], children: [],
    classList: {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x),
      toggle(x, on) {if (on) classes.add(x); else classes.delete(x);}},
    replaceChildren(...children) {this.children = children;}, append() {}, addEventListener() {}, setAttribute() {}};
}
const document = {getElementById(id) {if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);},
  createElement: node, querySelectorAll: () => []};
const context = vm.createContext({document, console, location: {host: '127.0.0.1'},
  localStorage: {setItem: (k,v) => storage.set(k,v), getItem: k => storage.get(k) || null, removeItem: k => storage.delete(k)},
  setTimeout: fn => {timers.set(++timerId,fn); return timerId;}, clearTimeout: id => timers.delete(id)});
const html = fs.readFileSync(path.join(__dirname, '..', 'ui_page.py'), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];
vm.runInContext(script.split("document.querySelectorAll('.nav-item')")[0], context);
const run = code => vm.runInContext(code, context);
let liveStatus = 'running', failNetwork = false;
context.get = async url => {
  if (failNetwork) throw new Error('fixture offline');
  if (url === '/api/tasks') return {runs: [{id:'live', task:'current', status:liveStatus}, {id:'old',task:'past',status:'completed'}]};
  const id = url.split('/').pop();
  return {id, status:id === 'live' ? liveStatus : 'completed', result:id === 'live' ? 'LIVE' : 'HISTORY', calls:[],stages:[]};
};
context.post = async (url, body) => {
  assert.equal(url, '/api/tasks/cancel'); assert.equal(body.id, 'live');
  liveStatus = 'cancelling'; return {id:'live',status:liveStatus};
};
(async () => {
  run("activeRun='live';viewedRun='live';localStorage.setItem('tokenflowActiveRun','live');syncRunControls()");
  document.getElementById('runHistory').value = 'old';
  await run('viewHistory()');
  assert.equal(run('activeRun'), 'live');
  assert.equal(storage.get('tokenflowActiveRun'), 'live');
  assert.equal(nodes.get('result').textContent, 'HISTORY');
  assert.equal(nodes.get('autoRun').disabled, true);
  assert.equal(nodes.get('returnRun').classList.contains('hidden'), false);
  await run('pollRun()');
  assert.equal(nodes.get('result').textContent, 'HISTORY');
  failNetwork = true;
  await run('pollRun()');
  assert.equal(run('activeRun'), 'live');
  assert.equal(nodes.get('autoRun').disabled, true);
  assert.equal(storage.get('tokenflowActiveRun'), 'live');
  failNetwork = false;
  await run('cancelRunningTask()');
  assert.equal(nodes.get('cancelRun').disabled, true);
  assert.equal(nodes.get('result').textContent, 'HISTORY');
  await run('returnToRunningTask()');
  assert.equal(run('viewedRun'), 'live');
  assert.equal(nodes.get('result').textContent, 'LIVE');
  assert.match(nodes.get('resultMeta').textContent, /正在取消/);
  liveStatus = 'cancelled';
  await run('pollRun()');
  assert.equal(run('activeRun'), '');
  assert.equal(storage.has('tokenflowActiveRun'), false);
  assert.equal(nodes.get('autoRun').disabled, false);
  assert.match(nodes.get('resultMeta').textContent, /已取消/);
  console.log('UI task/history checks: PASS');
})().catch(error => {console.error(error);process.exitCode = 1;});
