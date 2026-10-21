// Execute the page's real task/history functions with a tiny DOM and fake transport.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const nodes = new Map(), storage = new Map(), timers = new Map();
let timerId = 0;
function node() {
  const classes = new Set();
  return {value: '', textContent: '', disabled: false, files: [], children: [], attributes: {},
    classList: {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x),
      toggle(x, on) {if (on) classes.add(x); else classes.delete(x);}},
    replaceChildren(...children) {this.children = children;}, append(...children) {this.children.push(...children);}, addEventListener() {}, setAttribute(k,v) {this.attributes[k]=v;}};
}
const document = {getElementById(id) {if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);},
  createElement: node, querySelectorAll: () => []};
const context = vm.createContext({document, console, location: {host: '127.0.0.1'}, URLSearchParams, crypto: require('node:crypto').webcrypto, AbortController,
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
    if(url==='/api/tasks/preview')return {ready:true,routes:[],issues:[],max_calls:6,note:'fixture preflight'};
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
  // Paging/search must not replace the separately tracked running task.
  run("activeRun='live';viewedRun='old';historyOffset=0");
  context.get = async url => {
    if(url === '/api/tasks')return {runs:Array.from({length:20},(_,i)=>({id:'record-'+i,task:'fixture '+i,status:'completed'})),total:25,has_more:true};
    if(url.includes('offset=20'))return {runs:Array.from({length:5},(_,i)=>({id:'older-'+i,task:'older '+i,status:'completed'})),total:25,has_more:false};
    if(url.includes('query=needle'))return {runs:[{id:'match',task:'needle match',status:'failed'}],total:1,has_more:false};
    throw new Error('Unexpected history request '+url);
  };
  await run('loadHistory()');
  assert.equal(nodes.get('historyNext').disabled,false);
  await run('changeHistoryPage(1)');
  assert.equal(run('historyOffset'),20);assert.equal(run('activeRun'),'live');
  assert.match(nodes.get('historyState').textContent,/共 25 条/);
  document.getElementById('historyQuery').value='needle';document.getElementById('historyStatus').value='failed';
  await run('filterHistory()');
  assert.equal(run('historyOffset'),0);assert.equal(nodes.get('runHistory').children[1].value,'match');
  let finishOld;
  context.get = url => url.includes('query=old')?new Promise(resolve=>{finishOld=resolve}):Promise.resolve({runs:[{id:'new',task:'new',status:'completed'}],total:1,has_more:false});
  document.getElementById('historyQuery').value='old';const oldLoad=run('loadHistory()');
  document.getElementById('historyQuery').value='new';await run('loadHistory()');
  finishOld({runs:[{id:'stale',task:'stale',status:'failed'}],total:1,has_more:false});await oldLoad;
  assert.equal(nodes.get('runHistory').children[1].value,'new');
  assert.equal(run('activeRun'),'live');
  run("renderRun({status:'needs_review',result:'REPAIRED',calls:[],stages:[],candidates:[{answer:'ORIGINAL',acceptance:{status:'failed'}},{answer:'REPAIRED',acceptance:{status:'failed'}}]})");
  assert.equal(nodes.get('candidateHistory').classList.contains('hidden'),false);
  assert.match(nodes.get('candidateList').children[0].textContent,/ORIGINAL/);
  run("renderRun({status:'completed',result:'OLD HISTORY',calls:[],stages:[]})");
  assert.equal(nodes.get('candidateHistory').classList.contains('hidden'),true);
  run("renderRun({status:'failed',result:'',partial_result:'PARTIAL_ONLY',calls:[],stages:[],step_results:[{id:'s1',instruction:'提取事实',status:'completed',location:'local',name:'Edge fixture',depends_on:[],expected_output:'事实',output:'<script>fixture</script>',duration_ms:1500},{id:'s2',instruction:'推理',status:'failed',location:'cloud',name:'Cloud fixture',depends_on:['s1'],expected_output:'结论',output:''}]})");
  assert.match(nodes.get('result').textContent,/部分结果（最终任务未完成）/);
  assert.match(nodes.get('result').textContent,/PARTIAL_ONLY/);
  assert.equal(nodes.get('stepBoard').children.length,2);
  assert.match(nodes.get('stepBoard').children[0].children[1].textContent,/端侧/);
  assert.match(nodes.get('stepBoard').children[1].children[1].textContent,/云端.*s1/);
  assert.equal(nodes.get('stepBoard').children[0].children[3].children[1].textContent,'<script>fixture</script>');
  run("activeRun='';startingRun=false;pendingSubmission=null");
  context.post=async url=>{assert.equal(url,'/api/tasks/preview');return {ready:false,issues:['本机模型未配置'],routes:[],max_calls:6,note:'fixture'}};
  await run('orchestrateTask()');
  assert.match(nodes.get('result').textContent,/本机模型未配置/);
  assert.equal(run('activeRun'),'');
  const saved={price:{model:'gpt-6.1-sol'},saved_input_usd:'0.1234',saved_input_tokens:61700,counted_records:3,records:4,uncounted_records:1,since:1760000000,reported_output_tokens:50,output_unknown_records:1};
  context.savedFixture=saved;
  run('renderSavings(savedFixture)');
  assert.equal(nodes.get('savingsAmount').textContent,'$0.1234');
  assert.match(nodes.get('savingsScope').textContent,/1 条口径不完整/);
  run("renderSavings({...savedFixture,saved_input_usd:'-0.000002',saved_input_tokens:-1})");
  assert.equal(nodes.get('savingsAmount').textContent,'$0.000002');
  assert.equal(nodes.get('savingsTitle').textContent,'累计额外输入开销');
  assert.equal(nodes.get('savedTokenLabel').textContent,'累计增加的输入 Token');
  run("renderProgress({id:'live',status:'running',created_at:Date.now()/1000-65,calls:[{purpose:'reviewer',status:'running'}]})");
  assert.equal(nodes.get('progressPhase').textContent,'正在验收结果');
  assert.match(nodes.get('progressTime').textContent,/1 分 5 秒/);
  assert.equal(nodes.get('taskProgress').attributes['aria-busy'],'true');
  assert.equal(run('progressTimer!==null'),true);
  run("renderProgress({...progressRun,status:'cancelling'})");
  assert.match(nodes.get('progressDetail').textContent,/等待当前请求/);
  run("renderProgress({...progressRun,status:'needs_review'})");
  assert.equal(run('progressTimer'),null);
  assert.equal(nodes.get('taskProgress').classList.contains('is-warning'),true);
  assert.equal(nodes.get('taskProgress').attributes['aria-busy'],'false');
  context.get=async()=>{throw Error('fixture offline')};
  await run('loadSavings()');
  assert.equal(nodes.get('savingsAmount').textContent,'—');
  console.log('UI task/history checks: PASS');
})().catch(error => {console.error(error);process.exitCode = 1;});
