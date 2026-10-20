/* OPS_FLEET_FIXTURE: {region,system,destination,operation,writer_session,observer_session}.
   The fixture must use two distinct users, an operation visible to both and two
   SDE systems in the same region. All mutations use the real CSRF-protected API. */
const fs=require('fs'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
 const base=process.env.MAP_BASE_URL||'http://127.0.0.1:8766';
 const fixture=JSON.parse(fs.readFileSync(process.env.OPS_FLEET_FIXTURE,'utf8'));
 const browser=await chromium.launch({headless:true});
 const contexts=[],pages=[];
 for(const key of ['writer_session','observer_session']){
  const context=await browser.newContext({viewport:{width:1650,height:1100}});contexts.push(context);
  await context.addCookies([{name:'sessionid',value:fixture[key],domain:new URL(base).hostname,path:'/'}]);
  const page=await context.newPage();pages.push(page);
  const response=await page.goto(base+'/aa_stratcom/ops/'+fixture.operation+'/');
  assert.equal(response.status(),200);
 }
 const [writer,observer]=pages;
 await observer.locator('#st-map-toggle').click();
 await observer.locator('#st-map-region option[value="'+fixture.region+'"]').waitFor({state:'attached'});
 await observer.locator('#st-map-region').selectOption(String(fixture.region));
 await observer.locator('#st-battle-fleets').check();
 await observer.waitForFunction(()=>document.querySelector('#st-battle-fleet-sync').textContent.includes('updates every 2 seconds'));
 let navigations=0;observer.on('framenavigated',frame=>{if(frame===observer.mainFrame())navigations++;});
 const csrf=await writer.locator('#st-map-csrf input').inputValue();
 async function update(body){
  const response=await contexts[0].request.post(base+'/aa_stratcom/map/battle/fleet/?operation='+fixture.operation,{headers:{'X-CSRFToken':csrf},data:body});
  assert.equal(response.status(),200,await response.text());return (await response.json()).token;
 }
 const marker='Fleet polling '+Date.now();
 const timings=[];
 let start=Date.now();
 let token=await update({system_id:fixture.system,ship_name:'Polling test fleet',dps:10,logi:2,mobility:'gate',stance:'foe',note:'Added '+marker});
 await observer.waitForFunction(marker=>document.querySelector('#st-battle-fleet-list').textContent.includes('Added '+marker),marker,{timeout:7000});timings.push(Date.now()-start);
 start=Date.now();token=await update({id:token.id,revision:token.revision,system_id:fixture.system,ship_name:'Polling test fleet',dps:25,logi:4,mobility:'gate',stance:'foe',note:'Edited '+marker});
 await observer.waitForFunction(marker=>Array.from(document.querySelectorAll('#st-battle-fleet-list article')).some(card=>card.textContent.includes('Edited '+marker)&&card.textContent.includes('DPS 25 / Logi 4')),marker,{timeout:7000});timings.push(Date.now()-start);
 start=Date.now();await update({id:token.id,revision:token.revision,action:'move',system_id:fixture.destination});
 await observer.waitForFunction(marker=>Array.from(document.querySelectorAll('#st-battle-fleet-list article')).some(card=>card.textContent.includes(marker)&&card.textContent.includes('Fleet test destination')),marker,{timeout:7000});timings.push(Date.now()-start);
 assert.equal(navigations,0);
 console.log('PASS: coordinator add/edit/move reached a separate viewer without page navigation; latencies (ms): '+timings.join(', '));
 await browser.close();
})().catch(error=>{console.error(error.message);process.exit(1);});
