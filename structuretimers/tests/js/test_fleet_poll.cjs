const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function setup(){
    const nodes={fleets:{checked:true},'fleet-sync':{textContent:''}};
    const context={window:{StructureBattleTimeline:{}},document:{hidden:false,getElementById:id=>nodes[id.replace('st-battle-','')]},AbortController,Date};
    vm.runInNewContext(fs.readFileSync(require.resolve('../../static/structuretimers/js/battle_map.js'),'utf8'),context);
    const map=Object.create(context.window.StructureBattleMap.prototype);
    Object.assign(map,{data:{region:{id:1}},fleetVersion:0,moving:new Set(),selected:null,hooks:{},applyFleets:tokens=>{map.applied=tokens;}});
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
