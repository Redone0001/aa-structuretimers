const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function setup(){
    const nodes={fleets:{checked:true},'fleet-sync':{textContent:''}};
    const context={window:{StructureBattleTimeline:{}},document:{hidden:false,getElementById:id=>nodes[id.replace('st-battle-','')]},AbortController,Date};
    vm.runInNewContext(fs.readFileSync(require.resolve('../../static/aa_stratcom/js/battle_map.js'),'utf8'),context);
    const map=Object.create(context.window.StructureBattleMap.prototype);
    Object.assign(map,{root:{dataset:{}},forces:true,fleetLoaded:0,data:{region:{id:1}},fleetVersion:0,moving:new Set(),selected:null,hooks:{},applyFleets:tokens=>{map.applied=tokens;}});
    return {map,nodes,context};
}
test('fleet polling is gated by overlay, visibility and local mutations',async()=>{
    const {map,nodes,context}=setup();let requests=0;
    map.api=async()=>{requests++;return {tokens:[{id:1}]};};
    nodes.fleets.checked=false;await map.loadFleets();
    nodes.fleets.checked=true;context.document.hidden=true;await map.loadFleets();
    context.document.hidden=false;map.saving=true;await map.loadFleets();
    map.saving=false;map.moving.add(1);await map.loadFleets();
    assert.equal(requests,0);
    map.moving.clear();await map.loadFleets();assert.equal(requests,1);assert.equal(map.applied[0].id,1);
});
test('obsolete fleet responses cannot replace newer state, and failures release the poll lock',async()=>{
    const {map,nodes}=setup();let resolve;
    map.api=()=>new Promise(r=>{resolve=r;});
    const pending=map.loadFleets();map.fleetVersion++;resolve({tokens:[{id:2}]});await pending;
    assert.equal(map.applied,undefined);assert.equal(map.fleetAbort,null);
    map.api=async()=>{throw Error('Offline');};await map.loadFleets();
    assert.match(nodes['fleet-sync'].textContent,/Retrying within 2 minutes/);assert.equal(map.fleetAbort,null);
});

test('Ops fleet polling uses two seconds; non-Ops polling keeps two minutes',async()=>{
    const {map}=setup();let requests=0;
    map.api=async()=>{requests++;return {tokens:[{id:1}]};};
    map.root.dataset.operation='12';
    assert.equal(map.fleetRefreshMs,2000);assert.equal(map.fleetRefreshLabel,'2 seconds');
    map.fleetLoaded=Date.now()-1500;await map.pollFleets();assert.equal(requests,0);
    map.fleetLoaded=Date.now()-2100;await map.pollFleets();assert.equal(requests,1);
    delete map.root.dataset.operation;
    map.fleetLoaded=Date.now()-2100;await map.pollFleets();assert.equal(requests,1);
    map.fleetLoaded=Date.now()-120100;await map.pollFleets();assert.equal(requests,2);
});
test('Ops polling defers during dragging and resumes without stale positions',async()=>{
    const {map}=setup();map.root.dataset.operation='12';
    let dragging=true,requests=0,resolve;
    map.hooks.isDragging=()=>dragging;
    map.api=()=>{requests++;return new Promise(done=>{resolve=done;});};
    await map.pollFleets();assert.equal(requests,0);
    dragging=false;const pending=map.pollFleets();assert.equal(requests,1);
    await map.pollFleets();assert.equal(requests,1); // No overlapping requests.
    dragging=true;resolve({tokens:[{id:1,system_id:10}]});await pending;
    assert.equal(map.applied,undefined);assert.equal(map.fleetLoaded,0);
    dragging=false;const resumed=map.pollFleets();assert.equal(requests,2);
    resolve({tokens:[{id:1,system_id:20}]});await resumed;
    assert.equal(map.applied[0].system_id,20);
});
test('Ops failures advertise the two-second retry interval',async()=>{
    const {map,nodes}=setup();map.root.dataset.operation='12';
    map.api=async()=>{throw Error('Offline');};await map.loadFleets();
    assert.match(nodes['fleet-sync'].textContent,/Retrying within 2 seconds/);
    assert.equal(map.fleetAbort,null);
});
test('the Ops timer schedules fleet requests at a fixed two-second cadence',async()=>{
    const nodes={},intervals=[],windowEvents={},documentEvents={};
    const context={window:{StructureBattleTimeline:{},addEventListener:(name,fn)=>{windowEvents[name]=fn;}},document:{hidden:false,addEventListener:(name,fn)=>{documentEvents[name]=fn;},getElementById:id=>nodes[id]||(nodes[id]={checked:true,addEventListener(){}})},AbortController,Date,setInterval:(callback,delay)=>{intervals.push({callback,delay});return intervals.length;},clearInterval:id=>{intervals[id-1].cleared=true;}};
    vm.runInNewContext(fs.readFileSync(require.resolve('../../static/aa_stratcom/js/battle_map.js'),'utf8'),context);
    const map=new context.window.StructureBattleMap({dataset:{operation:'12',forces:'on'}},{});
    const fleetTimer=intervals.find(timer=>timer.delay===2000);assert.ok(fleetTimer);
    map.data={region:{id:1}};map.loaded=Date.now();map.applyFleets=()=>{};
    let requests=0;map.api=async()=>{requests++;return {tokens:[]};};
    fleetTimer.callback();await new Promise(resolve=>setImmediate(resolve));
    // A response arriving after a tick must not stretch the next poll to 3 sec.
    fleetTimer.callback();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(requests,2);
    context.document.hidden=true;fleetTimer.callback();assert.equal(requests,2);
    context.document.hidden=false;documentEvents.visibilitychange();await new Promise(resolve=>setImmediate(resolve));assert.equal(requests,3);
    windowEvents.pagehide({persisted:false});assert.equal(fleetTimer.cleared,true);
});
