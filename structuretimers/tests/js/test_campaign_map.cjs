const assert = require('node:assert/strict');
const {layout} = require('../../static/structuretimers/js/campaign_map.js');
for (const systems of [[], [{id:1, x:0, z:0}], Array.from({length:300}, (_, id) => ({id, x:0, z:0})), Array.from({length:200}, (_, id) => ({id, x:id % 7 ? id * 1e17 : null, z:id % 7 ? id * -2e17 : null}))]) {
    const result = layout(systems);
    assert.equal(result.nodes.length, systems.length);
    assert.equal(new Set(result.nodes.map(n => `${n.px},${n.py}`)).size, systems.length);
    assert(Number.isFinite(result.width) && Number.isFinite(result.height));
    for (const n of result.nodes) {
        assert(n.px >= 62 && n.py >= 25);
        assert(n.px + 62 <= result.width && n.py + 25 <= result.height);
    }
    assert.deepEqual(result, layout(systems));
}
console.log('Map layout: empty/single/dense/missing-coordinate regions remain deterministic and non-overlapping.');
const systems = Array.from({length:40}, (_, id) => ({id, name:String(id), x:id * 1e20, z:-id * 1e20}));
const gates = systems.slice(1).map((s,i) => [i,s.id]);
const graph = layout(systems, gates);
const moved = layout(systems.map(s => ({...s,x:Math.sin(s.id)*1e30,z:0})), gates);
assert.deepEqual(graph.nodes.map(n=>[n.id,n.px,n.py]), moved.nodes.map(n=>[n.id,n.px,n.py]));
assert.notDeepEqual(graph.nodes.map(n=>[n.px,n.py]), layout(systems, []).nodes.map(n=>[n.px,n.py]));
assert.equal(new Set(graph.nodes.map(n => `${n.px},${n.py}`)).size,40);
console.log('Graph checks: stargates drive the layout; changing physical coordinates does not.');
const fs = require('node:fs');
const path = require('node:path');
const bundled = JSON.parse(fs.readFileSync(path.join(__dirname, '../../data/region_layouts.json'))).regions;
const fixed = bundled.Querious;
const fixedSystems = Object.keys(fixed.positions).map((name, id) => ({id, name}));
const fixedResult = layout(fixedSystems, [], fixed);
const subset = layout(fixedSystems.slice(0, 5), [], fixed);
assert.deepEqual(subset.nodes, fixedResult.nodes.slice(0, 5));
assert.equal(subset.width, fixedResult.width);
const changedLinks = layout(fixedSystems, [[0, 1], [2, 3]], fixed);
assert.deepEqual(changedLinks, fixedResult);
const extra = layout([...fixedSystems, {id:999999,name:'New system'}], [], fixed);
assert.equal(extra.missing, 1);
assert(extra.nodes.at(-1).py > Math.max(...fixedResult.nodes.map(n=>n.py)));
for (const region of Object.values(bundled)) {
    const result = layout(Object.keys(region.positions).map((name,id)=>({name,id})), [], region);
    result.nodes.forEach((a,i) => result.nodes.slice(i+1).forEach(b => {
        assert(Math.abs(a.px-b.px) >= 139.9 || Math.abs(a.py-b.py) >= 65.9);
    }));
}
console.log('Bundled layouts: fixed positions, stable subsets, unmapped-system shelf, all-region label separation.');
