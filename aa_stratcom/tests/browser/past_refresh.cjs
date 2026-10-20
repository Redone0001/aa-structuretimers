/* TAB_SESSION_FILE contains a permitted session; TAB_FIXTURE_FILE contains
   {timer, operation}. Seed a destroyed structure with that future final timer. */
const {chromium}=require('playwright');
const fs=require('fs'),assert=require('node:assert/strict');
(async()=>{
 const base=process.env.MAP_BASE_URL||'http://127.0.0.1:8766';
 const session=JSON.parse(fs.readFileSync(process.env.TAB_SESSION_FILE)).session;
 const fixture=JSON.parse(fs.readFileSync(process.env.TAB_FIXTURE_FILE));
 const browser=await chromium.launch({headless:true}),context=await browser.newContext();
 await context.addCookies([{name:'sessionid',value:session,domain:new URL(base).hostname,path:'/'}]);
 const page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const start=new Date('2026-10-20T12:00:00Z');
 await page.clock.install({time:start});await page.clock.pauseAt(start);
 await page.goto(base+'/aa_stratcom/?tab=past');await page.clock.runFor(100);
 await page.waitForFunction(()=>window.jQuery?.fn.dataTable.isDataTable('#tbl_timers_past')&&$('#tbl_timers_past').DataTable().settings()[0].jqXHR?.readyState===4);
 assert.equal(await page.locator('#past-show-destroyed').isChecked(),false);
 assert.equal(await page.evaluate(id=>$('#tbl_timers_past').DataTable().rows().data().toArray().some(row=>row.id===id),fixture.timer),false);
 const shown=page.waitForResponse(r=>r.url().includes('/list_data/past')&&r.url().includes('include_destroyed=1'));
 await page.locator('#past-show-destroyed').check();await shown;
 assert.equal(await page.evaluate(id=>$('#tbl_timers_past').DataTable().rows().data().toArray().some(row=>row.id===id&&row.structure_details.includes('Destroyed')),fixture.timer),true);
 await page.reload();await page.clock.runFor(100);await page.waitForFunction(()=>$('#tbl_timers_past').DataTable().settings()[0].jqXHR?.readyState===4);
 assert.equal(await page.locator('#past-show-destroyed').isChecked(),true);
 const hidden=page.waitForResponse(r=>r.url().includes('/list_data/past')&&r.url().includes('include_destroyed=0'));
 await page.locator('#past-show-destroyed').uncheck();await hidden;
 await page.reload();await page.clock.runFor(100);await page.waitForFunction(()=>$('#tbl_timers_past').DataTable().settings()[0].jqXHR?.readyState===4);
 assert.equal(await page.locator('#past-show-destroyed').isChecked(),false);
 let requests=0;page.on('request',r=>{if(r.url().includes('/list_data/past'))requests++;});
 await page.clock.runFor(59000);assert.equal(requests,0);
 const minute=page.waitForResponse(r=>r.url().includes('/list_data/past'));
 await page.clock.runFor(1000);assert.equal((await minute).status(),200);assert.equal(requests,1);
 if(fixture.operation){
  const op=await context.newPage();op.on('pageerror',e=>errors.push(e.message));
  await op.clock.install({time:start});await op.clock.pauseAt(start);
  await op.goto(base+'/aa_stratcom/ops/'+fixture.operation+'/');await op.clock.runFor(100);
  await op.waitForFunction(()=>$('#tbl_timers_current').DataTable().settings()[0].jqXHR?.readyState===4);
  let polls=0;op.on('request',r=>{if(r.url().includes('/list_data')||r.url().includes('/timers/'))polls++;});
  const update=op.waitForResponse(r=>r.url().includes('/ops/'+fixture.operation+'/')&&r.request().resourceType()==='xhr');
  await op.clock.runFor(2000);assert.equal((await update).status(),200);
 }
 assert.deepEqual(errors,[]);await browser.close();
 console.log('PASS: destroyed future timer is shown only on request; both cookie choices survive reload; regular data refresh at 60 seconds, Ops at 2 seconds.');
})().catch(e=>{console.error(e);process.exit(1)});
