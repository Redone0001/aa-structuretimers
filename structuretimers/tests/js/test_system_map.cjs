const assert=require('node:assert/strict');
const {boundary,connectionPath,indicatorSlots}=require('../../static/structuretimers/js/system_map.js');
const a={x:0,y:0,width:160,height:34},b={x:300,y:0,width:90,height:34};
assert.deepEqual(boundary(a,b),{x:85,y:0});
assert.deepEqual(boundary(b,a,9),{x:246,y:0});
assert.match(connectionPath(a,b),/^M 85 0 Q 150 0 246 0$/);
assert.notEqual(connectionPath(a,b,1),connectionPath(a,b,-1));
assert.ok(!connectionPath(a,a).includes('NaN'));
const indicators=Array.from({length:17},(_,id)=>({id,category:id<6?'friendly':'hostile'}));
const slots=indicatorSlots(indicators);
assert.equal(slots.length,13);assert.equal(slots.at(-1).item.label,'+5');
assert.deepEqual(slots.slice(0,4).map(s=>s.x),[-43,-21,1,23]);
assert.deepEqual(slots.slice(4,6).map(s=>s.x),[-21,1]);
assert.equal(slots[0].y,35);assert.equal(slots[4].y,59);assert.equal(slots[6].y,83);
assert.deepEqual(indicatorSlots([]),[]);
console.log('Rectangle boundary intersections, parallel curves, self loops, category rows and overflow passed.');

const {tokenSlots,dropTarget}=require('../../static/structuretimers/js/system_map.js');
const tokens=tokenSlots(Array.from({length:5},(_,id)=>({id})),90);
assert.equal(tokens.length,5);assert.ok(tokens.every(t=>t.size===45&&t.y+t.size < -31));
for(let i=0;i<tokens.length;i++)for(let j=i+1;j<tokens.length;j++){
    const a=tokens[i],b=tokens[j];
    assert.ok(a.x+a.size<=b.x||b.x+b.size<=a.x||a.y+a.size<=b.y||b.y+b.size<=a.y);
}
assert.equal(tokenSlots([{id:1}],180)[0].size,90);
assert.deepEqual(tokenSlots([],90),[]);
const destinations=new Map([[1,a],[2,b]]);
assert.equal(dropTarget(destinations,{x:0,y:0}),1);
assert.equal(dropTarget(destinations,{x:300,y:20}),2);
assert.equal(dropTarget(destinations,{x:300,y:32}),null);
assert.equal(dropTarget(destinations,{x:150,y:0}),null);
assert.equal(dropTarget(new Map(),{x:0,y:0}),null);
console.log('Large fleet placement and ellipse drop targets passed.');
