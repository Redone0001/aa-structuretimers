const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const {test} = require('node:test');
function fixture() {
    const timers = [], cleared = [], events = {}, cookies = new Map();
    const document = {hidden: false, querySelectorAll: () => [],
        addEventListener(type, callback) { (events[type] ||= []).push(callback); }, removeEventListener() {},
        get cookie() { return [...cookies].map(([k,v]) => k+'='+v).join('; '); },
        set cookie(text) { const [key,value] = text.split(';')[0].split('='); cookies.set(key,value); this.lastCookie=text; }};
    const window = {addEventListener(type, callback) { (events[type] ||= []).push(callback); }};
    vm.runInNewContext(fs.readFileSync(require.resolve('../../static/aa_stratcom/js/data_refresh.js'),'utf8'), {
        document, window, location: {pathname:'/aa_stratcom/', protocol:'https:'},
        setInterval(callback,ms) { timers.push({callback,ms}); return timers.length; }, clearInterval(id) { cleared.push(id); },
    });
    return {api:window.StratComRefresh,document,timers,cleared,events};
}
test('regular requests run every minute; Ops can retain two seconds; hidden tabs pause',async()=>{
    const f=fixture();let requests=0,active=true;
    f.api.every(()=>requests++,{active:()=>active});assert.equal(f.timers.at(-1).ms,60000);
    await f.timers.at(-1).callback();assert.equal(requests,1);
    f.document.hidden=true;await f.timers.at(-1).callback();assert.equal(requests,1);
    f.document.hidden=false;active=false;await f.timers.at(-1).callback();assert.equal(requests,1);
    f.api.every(()=>requests++,{interval:2000});assert.equal(f.timers.at(-1).ms,2000);
});
test('overlapping async requests are skipped and failures allow the next retry',async()=>{
    const f=fixture();let calls=0,resolve;
    const run=f.api.every(()=>{calls++;return new Promise(r=>resolve=r);});
    const pending=run();await run();assert.equal(calls,1);resolve();await pending;
    const retry=f.api.every(()=>{calls++;throw Error('offline');});await retry();await retry();assert.equal(calls,3);
});
test('past destroyed preference persists both choices with a scoped secure cookie',()=>{
    const f=fixture(),key='asc-past-destroyed-5';
    assert.equal(f.api.preference(key),false);
    f.api.preference(key,true);assert.equal(f.api.preference(key),true);
    assert.match(f.document.lastCookie,/Max-Age=31536000; Path=\/aa_stratcom\/; SameSite=Lax; Secure/);
    f.api.preference(key,false);assert.equal(f.api.preference(key),false);
});
test('leaving the page stops timers, while browser history caching preserves them',()=>{
    const f=fixture();f.events.pagehide.forEach(fn=>fn({persisted:true}));assert.equal(f.cleared.length,0);
    f.events.pagehide.forEach(fn=>fn({persisted:false}));assert.equal(f.cleared.length,1);
});
