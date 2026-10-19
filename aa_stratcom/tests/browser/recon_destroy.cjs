/* Seed a permitted Database record called "Map action repro" with SDE coordinates.
   RECON_SESSION_FILE: {"session":"..."}; RECON_FIXTURE_FILE: {"region":123,"system":456}. */
const fs=require('fs'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
 const base=process.env.MAP_BASE_URL||'http://127.0.0.1:8766';
 const session=JSON.parse(fs.readFileSync(process.env.RECON_SESSION_FILE,'utf8')).session;
 const fixture=JSON.parse(fs.readFileSync(process.env.RECON_FIXTURE_FILE,'utf8'));
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1650,height:1100}});
 await context.addCookies([{name:'sessionid',value:session,domain:new URL(base).hostname,path:'/'}]);
 const page=await context.newPage();const dialogs=[],errors=[];
 page.on('dialog',async dialog=>{dialogs.push(dialog.message());await dialog.dismiss();});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base+'/aa_stratcom/?tab=preliminary');
 await page.waitForFunction(()=>window.jQuery?.fn.dataTable.isDataTable('#tbl_manage_recon'));
 await page.evaluate(()=>$('#tbl_manage_recon').DataTable().search('Map action repro').draw());
 await page.waitForFunction(()=>$('#tbl_manage_recon').DataTable().rows({search:'applied'}).data().length===1);
 await page.locator('#st-map-toggle').click();
 await page.locator('#st-map-region option[value="'+fixture.region+'"]').waitFor({state:'attached'});
 await page.locator('#st-map-region').selectOption(String(fixture.region));
 const node=page.locator('#st-regional-map [data-node="'+fixture.system+'"]').first();
 await node.waitFor();await node.focus();await page.keyboard.press('Enter');
 const destroy=page.locator('#st-map-details').getByRole('button',{name:'Destroyed',exact:true});
 await destroy.waitFor();
 const saved=page.waitForResponse(r=>r.url().includes('/destroy/')&&r.request().method()==='POST');
 await destroy.click();assert.equal((await saved).status(),200);
 await page.waitForFunction(()=>$('#tbl_manage_recon').DataTable().rows({search:'applied'}).data().length===0);
 await page.waitForFunction(()=>!document.querySelector('#st-map-details [data-destroy="true"]'));
 await page.locator('#recon-show-destroyed').check();
 await page.locator('#tbl_manage_recon').getByRole('button',{name:'Restore',exact:true}).click();
 await page.locator('#tbl_manage_recon').getByRole('button',{name:'Destroyed',exact:true}).waitFor();
 await page.locator('#recon-show-destroyed').uncheck();
 await page.locator('#tbl_manage_recon').getByRole('button',{name:'Destroyed',exact:true}).click();
 await page.waitForFunction(()=>$('#tbl_manage_recon').DataTable().rows({search:'applied'}).data().length===0);
 assert.deepEqual(dialogs,[]);assert.deepEqual(errors,[]);
 if(process.env.RECON_SCREENSHOT)await page.screenshot({path:process.env.RECON_SCREENSHOT,fullPage:true});
 await browser.close();console.log('PASS: map Destroyed archives immediately, Show destroyed restores, table Destroyed works; no warning dialogs.');
})().catch(error=>{console.error(error.message);process.exit(1);});
