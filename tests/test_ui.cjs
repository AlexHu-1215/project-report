const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../skills/report/assets/app.js'), 'utf8');
const context = vm.createContext({
  document: {body:{dataset:{root:'./',stage:''}}, querySelector:()=>({textContent:'{"project":{"timezone":"Asia/Shanghai"},"stages":[]}'}), addEventListener(){}},
  location: {protocol:'file:',search:''}, URLSearchParams,
});
vm.runInContext(source.slice(0, source.lastIndexOf('\nrender();')), context);
const metric={key:'score',direction:'up',digits:3};
function evaluate(expression, values) {
  Object.assign(context, values);
  return vm.runInContext(expression, context);
}
const run={id:'result',status:'completed',metrics:{score:0.8},metric_steps:{score:150}};
for (const baseline of [
  undefined, {id:'base',status:'planned',metrics:{}},
  {id:'base',status:'running',metrics:{score:0.5}},
  {id:'base',status:'completed',metrics:{}},
  {id:'base',status:'completed',metrics:{score:null}},
  {id:'base',status:'completed',metrics:{score:NaN}},
]) {
  const html=evaluate('metricCell(run,metric,baseline)', {run,metric,baseline});
  assert.ok(!html.includes('class="delta'), html);
  assert.ok(!html.includes('NaN'), html);
  assert.ok(html.includes('step 150'), html);
}
const baseline={id:'base',status:'completed',metrics:{score:0.5}};
let html=evaluate('metricCell(run,metric,baseline)', {run,metric,baseline});
assert.ok(html.includes('+0.300') && html.includes('delta positive'));
assert.equal(evaluate('comparisonDelta(run,metric,run)', {run,metric}), null);
assert.equal(evaluate('comparisonDelta({...run,status:"running"},metric,baseline)', {run,metric,baseline}), null);
html=evaluate('metricCell(run,{...metric,direction:"down"},baseline)', {run,metric,baseline});
assert.ok(!html.includes('delta positive'));
const stage={id:'stage',title:'Stage',question:'Question',state:'active',summary:'Summary',analysis_at:null,updated_at:null,
  common_config:{},settings_summary:'Settings',baseline:'base',metrics:[metric],notes:'## Results\n\nPending',runs:[baseline,run],media:[]};
Object.assign(baseline,{name:'Baseline',changes:'Default',progress:{step:100,total:100},history:[],sampled_at:null,comment:''});
Object.assign(run,{name:'Result',changes:'Change',progress:{step:200,total:200},history:[{step:100,score:0.7},{step:200,loss:0.2}],sampled_at:null,comment:''});
evaluate('data.stages=[stage]',{stage});
html=evaluate('stagePage(stage)',{stage});
assert.ok(html.includes('+0.300'));
baseline.status='running';
html=evaluate('stagePage(stage)',{stage});
assert.ok(!html.includes('相对基线'), html);
html=evaluate('chart(stage)',{stage});
assert.ok(html.includes('100 step · 0.7'));
assert.ok(!html.includes('200 step · 0.7'));
html=evaluate('runStatus({...run,status:"unknown",status_detail:"监测已中断"})',{run});
assert.ok(html.includes('待确认') && html.includes('监测已中断'));
console.log('UI regression cases passed: completed comparisons, per-metric steps, sparse curves, unknown state.');
