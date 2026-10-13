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
const context = vm.createContext({document, console, location: {host: '127.0.0.1'}, crypto: require('node:crypto').webcrypto, AbortController,
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
  const receipts = new Map(), sentIds = [];
  let sends = 0, lostResponse = true, recoveryOffline = true;
  const originalGet = context.get;
  context.get = async url => {
    if(url.startsWith('/api/tasks/request/')) {
      if(recoveryOffline) throw new Error('fixture offline');
      return {id:'submitted',status:'running'};
    }
    if(url === '/api/tasks/submitted') return {id:'submitted',status:'completed',result:'ONE TASK',calls:[],stages:[]};
    return originalGet(url);
  };
  context.post = async (url, body) => {
    assert.equal(url, '/api/tasks/start');
    sentIds.push(body.request_id);
    if(!receipts.has(body.request_id)) {receipts.set(body.request_id,'submitted');sends++;}
    if(lostResponse) throw new Error('response lost');
    return {id:'submitted',status:'completed',reused:true};
  };
  document.getElementById('task').value = 'fixture task'; document.getElementById('content').value = 'PRIVATE SOURCE';
  await run('orchestrateTask()');
  assert.equal(sends,1); assert.ok(storage.get('tokenflowPendingRequest'));
  assert.ok(![...storage.values()].some(value => value.includes('PRIVATE SOURCE')));
  lostResponse=false; recoveryOffline=false;
  await run('orchestrateTask()');
  assert.equal(sends,1); assert.equal(sentIds[0],sentIds[1]);
  assert.equal(storage.has('tokenflowPendingRequest'),false);
  assert.equal(nodes.get('result').textContent,'ONE TASK');
  // A reload can recover just the receipt ID; source data need not be in browser storage.
  storage.set('tokenflowPendingRequest','receipt-after-refresh');
  storage.set('tokenflowPendingSince',String(Date.now()));
  await run('recoverPending()');
  assert.equal(storage.has('tokenflowPendingRequest'),false);
  run("renderRun({status:'failed',error:'budget exceeded',result:'',calls:[],stages:[],metrics:{model_calls:0,local_calls:0,cloud_or_harness_calls:0,estimated_cloud_input_tokens:0,estimated_input_difference:0}})");
  assert.match(nodes.get('runMetrics').textContent,/未调用模型/);
  assert.doesNotMatch(nodes.get('runMetrics').textContent,/确定性任务/);
  assert.doesNotMatch(nodes.get('result').textContent,/正在完成/);
  console.log('UI task/history checks: PASS');
})().catch(error => {console.error(error);process.exitCode = 1;});
