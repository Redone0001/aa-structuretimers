/* Browser fixture: permitted user session in TAB_SESSION_FILE; TAB_FIXTURE_FILE contains
   {record, past_timer, owner}. Seed the record named "Map action repro" and a linked expired
   Final timer. The owner must be a remembered corporation. This test edits both records. */
const {chromium}=require('playwright');
const fs=require('fs'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext();
 const base=process.env.MAP_BASE_URL||'http://127.0.0.1:8766';
 const session=JSON.parse(fs.readFileSync(process.env.TAB_SESSION_FILE)).session;
 await context.addCookies([{name:'sessionid',value:session,domain:new URL(base).hostname,path:'/'}]);
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/aa_stratcom/?tab=preliminary');
 await page.waitForFunction(()=>window.jQuery?.fn.dataTable.isDataTable('#tbl_manage_recon')&&$('#tbl_manage_recon').DataTable().rows().count()>0);
 const fixture=JSON.parse(fs.readFileSync(process.env.TAB_FIXTURE_FILE));
 await page.evaluate(()=>$('#tbl_manage_recon').DataTable().search('Map action repro').draw());
 const editor=await context.newPage();
 await editor.goto(base+'/aa_stratcom/structure/'+fixture.record+'/edit/');
 await editor.evaluate(owner=>$('#id_owner_2').append(new Option('Preview Owner',String(owner),true,true)).trigger('change'),fixture.owner);
 await editor.locator('#id_structure_name').fill('Map action repro UPDATED');
 await editor.locator('#add-timer-form button[type="submit"]').click();
 await editor.waitForLoadState('domcontentloaded');
 const formErrors=await editor.locator('.invalid-feedback, .alert-danger').evaluateAll(nodes=>nodes.map(n=>n.parentElement.textContent));
 if(formErrors.length)throw new Error(formErrors.join(' | '));
 await editor.waitForURL(url=>!url.pathname.endsWith('/edit/'));
 // The original page still contains the old snapshot until the tab is revisited.
 assert.equal(await page.evaluate(()=>$('#tbl_manage_recon').DataTable().rows().data().toArray().some(r=>r.name_objective.includes('UPDATED'))),false);
 await page.locator('#tab-past').click();
 await page.locator('#tab-preliminary').click();
 await page.waitForFunction(()=>$('#tbl_manage_recon').DataTable().rows().data().toArray().some(r=>r.name_objective.includes('UPDATED')));
 assert.equal(await page.evaluate(()=>$('#tbl_manage_recon').DataTable().search()),'Map action repro');
 assert.equal(await page.locator('#recon-window-filter').count(),1);
 assert.equal(await page.locator('#recon-window-filter').isVisible(),true);
 assert.equal(await page.locator('#tbl_manage_recon_wrapper .timer-filter-row').count(),1);
 const pastRequest=page.waitForResponse(r=>r.url().includes('/list_data/past'));
 await page.locator('#tab-past').click();assert.equal((await pastRequest).status(),200);
 await editor.goto(base+'/aa_stratcom/edit/'+fixture.past_timer);
 await editor.locator('#timer-date-field').fill('2030-10-24 03:49');
 await editor.locator('#add-timer-form button[type="submit"]').click();
 await editor.waitForURL(url=>!url.pathname.includes('/edit/'));
 const currentRequest=page.waitForResponse(r=>r.url().includes('/list_data/current'));
 await page.locator('#tab-current').click();assert.equal((await currentRequest).status(),200);
 await page.waitForFunction(id=>$('#tbl_timers_current').DataTable().rows().data().toArray().some(row=>row.id===id),fixture.past_timer);
 const pastReload=page.waitForResponse(r=>r.url().includes('/list_data/past'));
 await page.locator('#tab-past').click();await pastReload;
 await page.waitForFunction(id=>!$('#tbl_timers_past').DataTable().rows().data().toArray().some(row=>row.id===id),fixture.past_timer);
 assert.deepEqual(errors,[]);
 await browser.close();console.log('PASS: editing in another page, switching tabs, updated Database rows, retained search/window controls, Past-to-Current timer edit, fresh tab requests; no JS errors.');
})().catch(e=>{console.error(e);process.exit(1)});
