const root = document.body.dataset.root;
const pageId = document.body.dataset.stage;
let data = JSON.parse(document.querySelector('#report-snapshot').textContent);
const state = {filter: 'all', query: '', expanded: new Set(), sharedOpen: false};
const labels = {running: '运行中', completed: '已完成', planned: '待运行', failed: '失败'};
const colors = ['#7c8a9c', '#1a8068', '#6b74cc', '#c38d39'];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = (name, size=18) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${{
  book: '<path d="M4 4h13a3 3 0 0 1 3 3v14H7a3 3 0 0 1-3-3V4Z"/><path d="M4 17h16M8 4v13M12 8h4M12 11h3"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  layers: '<path d="m12 3 10 6-10 6L2 9l10-6Zm-9 11 9 5 9-5M3 18l9 5 9-5"/>',
  code: '<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16"/>',
  file: '<path d="M14 2H5v20h14V7l-5-5Zm0 0v6h5M8 13h8M8 17h6"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>'
}[name]}</svg>`;

function time(value) {
  return value ? new Date(value).toLocaleString('zh-CN', {timeZone:data.project.timezone, month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}) : '等待更新';
}
function date(value) {
  return value ? new Date(value).toLocaleDateString('zh-CN', {timeZone:data.project.timezone,year:'numeric',month:'long',day:'numeric'}) : '';
}
const stageHref = s => `${root}stages/${encodeURIComponent(s.id)}/index.html`;
const badge = (status, demo=false) => `<span class="badge ${status}"><i></i>${demo ? '示例 · ' : ''}${labels[status]}</span>`;
const progress = run => run.progress.total ? Math.round(run.progress.step / run.progress.total * 100) : 0;
const stageRuns = () => data.stages.flatMap(s => s.runs.map(r => ({...r, stage:s})));
const activeStage = () => data.stages.find(s => s.id === data.project.current_stage);
const fmt = (value, digits=3) => value == null ? '—' : Number(value).toFixed(digits);
function bestRun(stage) {
  const metric = stage.metrics[0];
  return stage.runs.filter(r => r.status === 'completed' && r.metrics[metric.key] != null)
    .sort((a,b) => (b.metrics[metric.key] - a.metrics[metric.key]) * (metric.direction === 'up' ? 1 : -1))[0];
}
function demoNotice() {
  return `<div class="demo-notice"><span>示例数据</span><p>以下实验、指标、配置与运行状态用于展示报告效果。</p></div>`;
}
function sidebar() {
  return `<aside class="sidebar">
    <a href="${root}index.html" class="brand"><span class="brand-mark">${icon('book',24)}</span><span>Project Report<small>实验记录工作台</small></span></a>
    <div class="project-label">当前项目</div><div class="project-name"><span class="project-dot"></span>${esc(data.project.title)}</div>
    <nav aria-label="报告导航"><a class="nav-item ${!pageId ? 'active' : ''}" href="${root}index.html">${icon('grid')}<span>实验总览</span></a>
    <div class="nav-label">研究阶段 <span>${data.stages.length.toString().padStart(2,'0')}</span></div>
    ${data.stages.map((s,i) => `<a class="nav-item stage-nav ${s.id === pageId ? 'active' : ''}" href="${stageHref(s)}"><span class="stage-index">${String(i+1).padStart(2,'0')}</span><span>${esc(s.title)}${s.demo ? '<small>示例阶段</small>' : ''}</span></a>`).join('')}</nav>
    <div class="sidebar-bottom"><span class="local-indicator"></span> 本地项目报告<small>手动启用 <code>/report</code></small></div>
  </aside>`;
}
function frame(content, stage) {
  return `${sidebar()}<div class="workspace"><header class="topbar"><div class="breadcrumbs"><a href="${root}index.html">工作区</a><span>/</span>${esc(stage ? stage.title : '实验总览')}</div><div class="sync"><span class="sync-dot"></span><span id="sync-text">${location.protocol === 'file:' ? '离线快照' : '每 5 秒更新'}</span><button class="icon-button" data-action="refresh" aria-label="刷新报告">${icon('refresh',16)}</button></div></header><main>${content}</main><footer>Project Report <span>·</span> 让每一轮探索都有迹可循</footer></div>`;
}
function home() {
  const runs = stageRuns(), running = runs.filter(r=>r.status==='running');
  const complete = runs.filter(r=>r.status==='completed'), stage = activeStage();
  return `<div class="page-heading"><div><p class="eyebrow">RESEARCH WORKSPACE</p><h1>实验总览</h1><p class="subtitle">${esc(data.project.description)}</p></div><span class="date">${date(data.updated_at)}</span></div>
    ${data.stages.some(s=>s.demo) ? demoNotice() : ''}
    <div class="stats-grid">${[
      ['研究阶段',data.stages.length,'layers','按问题归档'],['实验记录',runs.length,'book','设置与结果集中保存'],['进行中',running.length,'clock','查看当前进度'],['已完成',complete.length,'check','保留最终评估结果']
    ].map(([name,value,symbol,hint])=>`<div class="stat"><div class="stat-label">${name}${icon(symbol,18)}</div><strong>${String(value).padStart(2,'0')}</strong><small>${hint}</small></div>`).join('')}</div>
    ${stage ? `<section class="current-stage"><div class="current-content"><p class="eyebrow">当前阶段 <span>STAGE ${String(data.stages.indexOf(stage)+1).padStart(2,'0')}</span></p><h2>${esc(stage.title)}</h2><p>${esc(stage.question)}</p><div class="current-summary">${icon('check',18)}<span>${esc(stage.summary)}</span></div><a class="primary-button" href="${stageHref(stage)}">打开实验表 ${icon('arrow',17)}</a></div><div class="current-side"><span class="tiny-label">完成进度</span><div class="completion-number">${stage.runs.filter(r=>r.status==='completed').length}<span>/ ${stage.runs.length}</span></div><div class="progress-track large"><span style="width:${stage.runs.length ? stage.runs.filter(r=>r.status==='completed').length/stage.runs.length*100 : 0}%"></span></div><p>${stage.runs.filter(r=>r.status==='running').length} 项实验进行中</p><small>采集更新 ${time(data.updated_at)}</small></div></section>` : `<div class="empty"><h2>开始记录第一轮探索</h2><p>在 Codex 中调用 /report，创建阶段并登记实验。</p></div>`}
    ${running.length ? `<section><div class="section-heading"><h2><span class="live-dot"></span>正在进行</h2><span>最新进度与评估</span></div><div class="running-list">${running.map(r=>`<a class="running-card" href="${stageHref(r.stage)}?run=${encodeURIComponent(r.id)}"><div><div class="run-name"><code>${esc(r.id)}</code>${badge(r.status,r.stage.demo)}</div><h3>${esc(r.name)}</h3><p>${esc(r.changes)}</p></div><div class="running-progress"><strong>${progress(r)}<span>%</span></strong><div class="progress-track"><span style="width:${progress(r)}%"></span></div><small>${r.progress.step.toLocaleString()} / ${r.progress.total.toLocaleString()} steps</small></div><div class="running-metric"><span>${esc(r.stage.metrics[0].label)} · 当前值</span><strong>${fmt(r.metrics[r.stage.metrics[0].key],r.stage.metrics[0].digits)}</strong><small>${time(r.updated_at)} 更新</small></div>${icon('arrow',20)}</a>`).join('')}</div></section>` : ''}
    <section><div class="section-heading"><h2>阶段记录</h2><span>每个问题，一份持续更新的记录</span></div><div class="stage-list">${data.stages.map((s,i)=>`<a class="stage-card" href="${stageHref(s)}"><div class="stage-number">${String(i+1).padStart(2,'0')}</div><div><div class="stage-title"><h3>${esc(s.title)}</h3><span class="stage-state">${s.state === 'completed' ? '阶段完成' : '探索中'}</span></div><p>${esc(s.summary)}</p><small>${s.runs.length} 次运行 <span>·</span> ${s.demo ? '示例阶段' : '实验记录'} <span>·</span> 解释更新 ${time(s.analysis_at)}</small></div>${icon('arrow',20)}</a>`).join('')}</div></section>`;
}

function metricCell(run, metric, baseline) {
  const value = run.metrics[metric.key];
  if (value == null) return '<span class="muted">—</span>';
  const delta = value - baseline?.metrics[metric.key];
  const comparable = run.status === 'completed' && baseline && run.id !== baseline.id;
  const good = delta * (metric.direction === 'up' ? 1 : -1) > 0;
  return `<strong class="metric-number ${run.status === 'running' ? 'provisional' : ''}">${fmt(value,metric.digits)}</strong>${comparable ? `<small class="delta ${good ? 'positive' : ''}">${delta >= 0 ? '+' : ''}${fmt(delta,metric.digits)}</small>` : `<small class="metric-caption">${run.status==='running'?'当前值':'最终值'}</small>`}`;
}
function runDetail(run, stage) {
  return `<tr class="detail-row"><td colspan="${5+stage.metrics.slice(0,2).length}"><div class="run-detail"><div class="detail-toolbar"><span>${icon('file',17)} ${esc(run.id)} · 运行详情</span><small>采集 ${time(run.updated_at)} <span>·</span> 解释 ${time(run.analysis_at)}</small></div>
    <div class="detail-grid"><section><h4>设置与运行命令</h4><p class="detail-note">${esc(run.changes)}</p><div class="code-label"><span>完整运行配置</span><span>JSON</span></div><pre>${esc(JSON.stringify(run.config,null,2))}</pre><div class="code-label"><span>启动命令${stage.demo ? ' · 示例' : ''}</span><button class="text-button" data-copy="${esc(run.id)}">${icon('copy',14)} 复制</button></div><pre class="command">${esc(run.command)}</pre></section>
    <section><h4>具体实现</h4>${run.implementation.map(item=>`<div class="implementation"><code>${esc(item.file)}</code><p>${esc(item.description)}</p>${item.code ? `<pre>${esc(item.code)}</pre>` : ''}</div>`).join('')}
    <h4 class="detail-subheading">资源与性能${stage.demo ? ' · 示例' : ''}</h4><dl class="performance">${Object.entries(run.performance).map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('') || '<p class="muted">等待采集</p>'}</dl>
    <h4 class="detail-subheading">产物与日志${stage.demo ? ' · 示例路径' : ''}</h4><ul class="artifact-list">${run.artifacts.map(item=>`<li>${icon('file',14)}<span>${esc(item.label)}<code>${esc(item.path)}</code></span></li>`).join('') || '<li class="muted">等待产物</li>'}</ul></section></div>
    <div class="detail-conclusion"><span class="basis">${stage.demo ? '示例说明' : {measured:'实测记录',inferred:'分析推断',pending:'待验证'}[run.basis]}</span><p>${esc(run.comment)}</p></div></div></td></tr>`;
}
function rows(stage) {
  const baseline = stage.runs.find(r=>r.id===stage.baseline), best = bestRun(stage);
  const runs = stage.runs.filter(r=>(state.filter==='all'||r.status===state.filter) && `${r.id} ${r.name} ${r.changes}`.toLowerCase().includes(state.query.toLowerCase()));
  return runs.map(r=>`<tr data-row-run="${esc(r.id)}" class="experiment-row ${state.expanded.has(r.id)?'expanded':''}"><td><button class="run-toggle" data-run="${esc(r.id)}" aria-expanded="${state.expanded.has(r.id)}"><span class="chevron">${icon('down',15)}</span><span><code>${esc(r.id)}</code><strong>${esc(r.name)}</strong></span></button></td><td><div class="change-text">${esc(r.changes)}</div>${best?.id===r.id && r.id!==stage.baseline ? '<span class="best-label">已完成最佳</span>' : ''}</td><td>${badge(r.status)}${r.status==='running'?`<div class="table-progress"><div class="progress-track"><span style="width:${progress(r)}%"></span></div><small>${progress(r)}% · ${r.progress.step.toLocaleString()}</small></div>`:''}</td>${stage.metrics.slice(0,2).map(m=>`<td class="metric-cell">${metricCell(r,m,baseline)}</td>`).join('')}<td class="comment-cell">${esc(r.comment)}</td><td><button class="icon-button expand-button" data-run="${esc(r.id)}" aria-label="${state.expanded.has(r.id)?'收起':'展开'} ${esc(r.id)} 详情">${icon('down',17)}</button></td></tr>${state.expanded.has(r.id)?runDetail(r,stage):''}`).join('') || '<tr><td colspan="7" class="empty-table">当前筛选条件下暂无实验。</td></tr>';
}
function table(stage) {
  return `<section class="experiments"><div class="section-heading"><div><h2>实验对比</h2><p>点击实验行展开设置、命令与实现。</p></div><span>${stage.runs.length} 次运行</span></div><div class="table-shell"><div class="table-toolbar"><div class="filters" role="group" aria-label="实验状态筛选">${[['all','全部'],['running','运行中'],['completed','已完成'],['planned','待运行'],['failed','失败']].filter(([s])=>s==='all'||stage.runs.some(r=>r.status===s)).map(([s,name])=>`<button data-filter="${s}" class="filter ${state.filter===s?'selected':''}">${name}<span>${s==='all'?stage.runs.length:stage.runs.filter(r=>r.status===s).length}</span></button>`).join('')}</div><label class="search">${icon('search',16)}<input id="run-search" type="search" placeholder="搜索实验或设置…" value="${esc(state.query)}" aria-label="搜索实验"></label></div><div class="table-scroll"><table><thead><tr><th>实验</th><th>关键改动</th><th>状态 / 进度</th>${stage.metrics.slice(0,2).map(m=>`<th class="metric-cell">${esc(m.label)} ${m.direction==='up'?'↑':'↓'}</th>`).join('')}<th>当前结论</th><th></th></tr></thead><tbody id="run-rows">${rows(stage)}</tbody></table></div><div class="table-footnote"><span class="footnote-dot"></span>已完成实验展示最终指标与基线差值；运行中实验展示最近评估值。</div></div></section>`;
}

function chart(stage) {
  const metric=stage.metrics[0], runs=stage.runs.filter(r=>r.history.some(p=>p[metric.key]!=null));
  if (!runs.length) return '';
  const points=runs.flatMap(r=>r.history.filter(p=>p[metric.key]!=null));
  const min=Math.floor(Math.min(...points.map(p=>p[metric.key]))*20)/20, max=Math.ceil(Math.max(...points.map(p=>p[metric.key]))*20)/20;
  const xmax=Math.max(...points.map(p=>p.step)), span=max-min || 1;
  const x=v=>52+(v/(xmax||1))*680, y=v=>198-(v-min)/span*157;
  return `<section class="chart-section"><div class="section-heading"><div><h2>关键结果</h2><p>${esc(metric.label)}随训练进度的变化${stage.demo?' · 示例曲线':''}</p></div><span>${metric.direction==='up'?'数值越高越好':'数值越低越好'}</span></div><div class="chart-card"><div class="chart-legend">${runs.map((r,i)=>`<span><i style="background:${colors[i%colors.length]}"></i>${esc(r.name)}${r.status==='running'?'<small>进行中</small>':''}</span>`).join('')}</div><svg class="line-chart" viewBox="0 0 780 242" role="img" aria-label="${esc(metric.label)}随训练步数变化的曲线">${Array.from({length:5},(_,i)=>{const val=min+span*i/4;return `<line x1="52" y1="${y(val)}" x2="732" y2="${y(val)}" stroke="#e9edf1" stroke-dasharray="3 5"/><text x="39" y="${y(val)+4}" text-anchor="end">${val.toFixed(2)}</text>`}).join('')}${Array.from({length:6},(_,i)=>`<text x="${x(xmax*i/5)}" y="223" text-anchor="middle">${Math.round(xmax*i/5).toLocaleString()}</text>`).join('')}${runs.map((r,i)=>{const pts=r.history.filter(p=>p[metric.key]!=null);return `<polyline points="${pts.map(p=>`${x(p.step)},${y(p[metric.key])}`).join(' ')}" fill="none" stroke="${colors[i%colors.length]}" stroke-width="2.7" stroke-linecap="round" stroke-linejoin="round" ${r.status==='running'?'stroke-dasharray="5 5"':''}/>${pts.map(p=>`<circle cx="${x(p.step)}" cy="${y(p[metric.key])}" r="3.5" fill="white" stroke="${colors[i%colors.length]}" stroke-width="2"><title>${esc(r.name)} · ${p.step} step · ${p[metric.key]}</title></circle>`).join('')}`}).join('')}<text x="750" y="223">step</text></svg></div></section>`;
}

function inline(text) { return esc(text).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>'); }
function notes(markdown) {
  return markdown.trim().split(/\n\n+/).map(block=>{
    if(block.startsWith('## ')) return `<h3>${inline(block.slice(3))}</h3>`;
    if(block.startsWith('- ')) return `<ul>${block.split('\n').map(line=>`<li>${inline(line.slice(2))}</li>`).join('')}</ul>`;
    return `<p>${inline(block).replace(/\n/g,'<br>')}</p>`;
  }).join('');
}
function stagePage(stage) {
  const best=bestRun(stage), baseline=stage.runs.find(r=>r.id===stage.baseline), metric=stage.metrics[0];
  const gain=best && baseline && baseline.metrics[metric.key] != null ? best.metrics[metric.key]-baseline.metrics[metric.key] : null;
  return `<div class="page-heading"><div><p class="eyebrow">STAGE ${String(data.stages.indexOf(stage)+1).padStart(2,'0')} <span class="eyebrow-separator">/</span> ${stage.state==='completed'?'COMPLETED':'IN PROGRESS'}</p><h1>${esc(stage.title)}</h1><p class="subtitle">${esc(stage.question)}</p></div></div>
    ${stage.demo?demoNotice():''}
    <section class="conclusion-card"><div class="conclusion-copy"><p class="eyebrow">当前结论</p><h2>${esc(stage.summary)}</h2><p>解释更新 ${time(stage.analysis_at)} <span>·</span> 采集更新 ${time(stage.updated_at)}</p></div>${best?`<div class="best-metric"><span>已完成最佳 · ${esc(metric.label)}</span><strong>${fmt(best.metrics[metric.key],metric.digits)}</strong>${gain!=null?`<small>${gain>=0?'+':''}${fmt(gain,metric.digits)} <span>相对基线</span></small>`:''}</div>`:''}</section>
    <details class="shared-settings" ${state.sharedOpen ? 'open' : ''}><summary>${icon('layers',16)} 共用实验设置 <span>${esc(stage.settings_summary)}</span>${icon('down',15)}</summary><pre>${esc(JSON.stringify(stage.common_config,null,2))}</pre></details>
    ${table(stage)}${chart(stage)}
    <section class="notes-section"><div class="section-heading"><h2>解释与后续</h2><span>${stage.demo?'示例分析':'阶段记录'}</span></div><article class="notes-card">${notes(stage.notes)}</article></section>
    ${(stage.media||[]).length?`<section class="media-grid">${stage.media.map(m=>`<figure>${m.type==='video'?`<video src="${esc(root+m.path)}" controls preload="metadata"></video>`:`<img src="${esc(root+m.path)}" alt="${esc(m.caption)}" loading="lazy">`}<figcaption>${esc(m.caption)}</figcaption></figure>`).join('')}</section>`:''}`;
}
function render() {
  const searchFocused=document.activeElement?.id==='run-search';
  const selection=searchFocused ? [document.activeElement.selectionStart,document.activeElement.selectionEnd] : null;
  const stage=data.stages.find(s=>s.id===pageId);
  document.title=`${stage?stage.title:'实验总览'} · Project Report`;
  document.querySelector('#app').innerHTML=frame(stage?stagePage(stage):home(),stage);
  if(searchFocused) {const input=document.querySelector('#run-search');input.focus({preventScroll:true});input.setSelectionRange(...selection);}
}
async function refresh() {
  const response=await fetch(`${root}api/report`,{cache:'no-store'});
  if(!response.ok) throw new Error(`HTTP ${response.status}`);
  const next=await response.json();
  if(JSON.stringify(next)!==JSON.stringify(data)) { data=next; render(); }
  document.querySelector('#sync-text').textContent='每 5 秒更新';
  document.querySelector('.sync').classList.remove('error');
}
function refreshView() {
  refresh().catch(()=>{document.querySelector('#sync-text').textContent='连接已断开 · 点击重连';document.querySelector('.sync').classList.add('error');});
}

document.addEventListener('click',async event=>{
  const toggle=event.target.closest('[data-run], [data-row-run]');
  if(toggle) {const id=toggle.dataset.run || toggle.dataset.rowRun;state.expanded.has(id)?state.expanded.delete(id):state.expanded.add(id);document.querySelector('#run-rows').innerHTML=rows(data.stages.find(s=>s.id===pageId));}
  const filter=event.target.closest('[data-filter]');
  if(filter) {state.filter=filter.dataset.filter;render();}
  const copy=event.target.closest('[data-copy]');
  if(copy) {const run=stageRuns().find(r=>r.id===copy.dataset.copy && r.stage.id===pageId);await navigator.clipboard.writeText(run.command);copy.textContent='已复制';}
  if(event.target.closest('[data-action="refresh"]')) {if(location.protocol==='file:') location.reload(); else refreshView();}
});
document.addEventListener('input',event=>{
  if(event.target.id==='run-search') {state.query=event.target.value;document.querySelector('#run-rows').innerHTML=rows(data.stages.find(s=>s.id===pageId));}
});
document.addEventListener('toggle',event=>{if(event.target.classList.contains('shared-settings')) state.sharedOpen=event.target.open;},true);
const initialRun=new URLSearchParams(location.search).get('run');
if(initialRun) state.expanded.add(initialRun);
render();
if(location.protocol!=='file:') {refreshView();setInterval(refreshView,5000);}
