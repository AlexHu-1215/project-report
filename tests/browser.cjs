// Optional browser regression: requires Playwright and an installed Chrome.
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const assert=require('node:assert/strict');
const {spawn,execFileSync}=require('node:child_process');
const {chromium}=require('playwright');
const repository=path.resolve(__dirname,'..');
const python=process.env.PYTHON || 'python3';
const script=path.join(repository,'skills/report/scripts/report.py');
const project=fs.mkdtempSync(path.join(os.tmpdir(),'report-browser-'));
function py(code,...args){return execFileSync(python,['-B','-c',`import sys;sys.path.insert(0,sys.argv[1]);${code}`,path.dirname(script),...args],{encoding:'utf8'});}
py(`from pathlib import Path; import report; p=Path(sys.argv[2]); report.init_project(p); report.demo(p/'report'); s=p/'report/data/stages/01-temporal.json'; d=report.read_json(s);d['media']=[{'type':'video','path':'assets/clip.webm','caption':'播放连续性验证'}];report.write_json(s,d);report.conclude(p/'report','01-temporal',d['summary'],['exp-001','exp-002'])`,project);
const server=spawn(python,['-u','-B',script,'serve','--project',project,'--port','0'],{stdio:['ignore','pipe','pipe']});
const errors=[];
async function main(){
 let browser;
 try {
  const url=await new Promise((resolve,reject)=>{let output='';server.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(match)resolve(match[0]);});server.once('exit',code=>reject(new Error(`Server exit ${code}`)));});
  browser=await chromium.launch({headless:true,channel:'chrome'});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',error=>errors.push(error.message));
  const requests=[];page.on('request',r=>{if(r.url().includes('/api/report'))requests.push(r.url());});
  await page.goto(url);
  const video=await page.evaluate(async()=>{
    const canvas=document.createElement('canvas');canvas.width=320;canvas.height=180;
    const ctx=canvas.getContext('2d'),chunks=[];
    const recorder=new MediaRecorder(canvas.captureStream(10),{mimeType:'video/webm;codecs=vp8'});
    recorder.ondataavailable=event=>chunks.push(event.data);
    const stopped=new Promise(resolve=>{recorder.onstop=resolve;});recorder.start();
    for(let i=0;i<25;i++){ctx.fillStyle=`hsl(${i*10},45%,65%)`;ctx.fillRect(0,0,320,180);ctx.fillStyle='#fff';ctx.fillText('Report playback '+i,30,90);await new Promise(resolve=>setTimeout(resolve,100));}
    recorder.stop();await stopped;const bytes=new Uint8Array(await new Blob(chunks).arrayBuffer());
    return btoa(Array.from(bytes,b=>String.fromCharCode(b)).join(''));
  });
  fs.writeFileSync(path.join(project,'report/assets/clip.webm'),Buffer.from(video,'base64'));
  await page.getByRole('link',{name:'打开实验表'}).click();
  await page.locator('tr[data-row-run="exp-003"]').waitFor();
  assert.equal(await page.locator('tr[data-row-run]').count(),4);
  assert.equal(await page.locator('.evidence-links button').count(),2);
  assert.ok((await page.locator('tr[data-row-run="exp-002"]').innerText()).includes('temporal_weight'));
  await page.locator('tr[data-row-run="exp-003"] .run-toggle').click();
  await page.locator('.planned-settings summary').click();
  await page.getByRole('button',{name:'运行中',exact:false}).click();
  assert.equal(await page.locator('tr[data-row-run]').count(),1);
  py(`from report import Run; Run(sys.argv[2]+'/report','01-temporal','exp-003').update(step=7000,metrics={'consistency':0.85,'quality':71.0})`,project);
  await page.waitForFunction(()=>document.querySelector('tr[data-row-run="exp-003"]').textContent.includes('0.850'),null,{timeout:12000});
  assert.equal(await page.locator('.detail-row').count(),1);
  assert.equal(await page.locator('.planned-settings[open]').count(),1);
  assert.ok(await page.getByText('有新结果，结论待更新',{exact:true}).count());
  assert.ok(requests.some(url=>url.includes('stage=01-temporal')));
  await page.locator('video').scrollIntoViewIfNeeded();
  const selected=await page.evaluate(async()=>{
    const video=document.querySelector('video');video.muted=true;video.loop=true;video.currentTime=0.7;await video.play();
    window.savedVideo=video;window.savedScroll=scrollY;
    const text=document.querySelector('.notes-card p').firstChild;
    const selection=getSelection();selection.setBaseAndExtent(text,0,text,Math.min(5,text.length));
    return selection.toString();
  });
  py(`from report import Run; Run(sys.argv[2]+'/report','01-temporal','exp-003').update(metrics={'consistency':0.851},metric_step=7000)`,project);
  await page.evaluate(()=>refresh());
  const preserved=await page.evaluate(()=>({same:document.querySelector('video')===window.savedVideo,playing:!window.savedVideo.paused,time:window.savedVideo.currentTime,scroll:Math.abs(scrollY-window.savedScroll),selected:getSelection().toString()}));
  assert.ok(preserved.same && preserved.playing && preserved.time>=0.7,JSON.stringify(preserved));
  assert.ok(preserved.scroll<=2,JSON.stringify(preserved));assert.equal(preserved.selected,selected);
  py(`from report import Run; Run(sys.argv[2]+'/report','01-temporal','exp-001').update(step=10001,metrics={'consistency':0.79})`,project);
  await page.evaluate(()=>refresh());
  assert.ok(!(await page.locator('.best-metric').innerText()).includes('相对基线'));
  await page.getByRole('button',{name:'全部',exact:false}).click();
  assert.ok((await page.locator('tr[data-row-run="exp-001"]').innerText()).includes('等待最终评估'));
  assert.ok(!(await page.locator('body').innerText()).includes('NaN'));
  await page.locator('[data-evidence="exp-002"]').click();
  assert.equal(await page.locator('tr[data-row-run="exp-002"] .run-toggle').getAttribute('aria-expanded'),'true');
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  py(`from pathlib import Path;import report;report.build(Path(sys.argv[2])/'report')`,project);
  await page.goto('file://'+path.join(project,'report/stages/01-temporal/index.html'));
  await page.getByText('离线快照',{exact:true}).waitFor();
  assert.equal(await page.locator('tr[data-row-run]').count(),4);
  assert.deepEqual(errors,[]);
  console.log('Browser regressions passed: final evaluation, config diff, evidence links, stale conclusions, scoped refresh, stable video/selection/scroll, mobile and offline.');
 } finally {if(browser)await browser.close();server.kill('SIGTERM');await new Promise(resolve=>server.once('exit',resolve));fs.rmSync(project,{recursive:true,force:true});}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
