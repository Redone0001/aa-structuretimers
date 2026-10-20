/* Fixture: TAB_SESSION_FILE session; TAB_FIXTURE_FILE {campaign,entry}.
   Seed one unreserved campaign entry and an owner named "Minute refresh owner". */
const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{
 const base=process.env.MAP_BASE_URL||'http://127.0.0.1:8766',f=JSON.parse(fs.readFileSync(process.env.TAB_FIXTURE_FILE));
 const browser=await chromium.launch(),context=await browser.newContext(),errors=[];
 await context.addCookies([{name:'sessionid',value:JSON.parse(fs.readFileSync(process.env.TAB_SESSION_FILE)).session,domain:new URL(base).hostname,path:'/'}]);
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const start=new Date('2026-10-20T12:00:00Z');await page.clock.install({time:start});await page.clock.pauseAt(start);
 await page.goto(base+'/aa_stratcom/campaigns/'+f.campaign+'/');await page.clock.runFor(100);
 await page.evaluate(id=>{const c=document.getElementById('system-'+id);c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}));},f.entry);
 const editor=await context.newPage();await editor.goto(page.url());
 await editor.evaluate(id=>{document.getElementById('system-'+id).checked=true;},f.entry);
 await editor.locator('#campaign-bulk button[value="reserve"]').click();
 await editor.waitForFunction(()=>document.getElementById('campaign-action-status').textContent==='Saved');
 assert.equal(await page.locator('[data-system-entry="'+f.entry+'"]').getAttribute('data-status'),'available');
 const changed=page.waitForResponse(r=>r.url()===page.url()&&r.request().resourceType()==='fetch');
 await page.clock.runFor(60000);assert.equal((await changed).status(),200);
 assert.equal(await page.locator('[data-system-entry="'+f.entry+'"]').getAttribute('data-status'),'reserved');
 assert.equal(await page.locator('#system-'+f.entry).isChecked(),true);
 const standings=await context.newPage();standings.on('pageerror',e=>errors.push(e.message));
 await standings.clock.install({time:start});await standings.clock.pauseAt(start);
 await standings.goto(base+'/aa_stratcom/?tab=standings');await standings.clock.runFor(100);
 await standings.locator('#standings-search').fill('Minute refresh owner');
 const request=await standings.locator('#standings-rows form').first().evaluate(form=>({url:form.action,csrf:form.querySelector('[name=csrfmiddlewaretoken]').value}));
 await editor.request.post(request.url,{form:{standing:'5',csrfmiddlewaretoken:request.csrf}});
 const refreshed=standings.waitForResponse(r=>r.url()===standings.url()&&r.request().resourceType()==='fetch');
 await standings.clock.runFor(60000);assert.equal((await refreshed).status(),200);
 assert.equal(await standings.locator('#standings-rows strong').first().textContent(),'+5');
 assert.equal(await standings.locator('#standings-search').inputValue(),'Minute refresh owner');
 assert.deepEqual(errors,[]);await browser.close();console.log('PASS: campaign reservations and standings refresh at one minute; selections and search persist, no JS errors.');
})().catch(e=>{console.error(e);process.exit(1)});
