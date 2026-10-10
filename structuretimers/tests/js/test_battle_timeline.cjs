const {test}=require('node:test');
const assert=require('node:assert/strict');
const {stateAt,onMap,hourKey}=require('../../static/structuretimers/js/battle_timeline.js');
const start=Date.parse('2026-10-10T12:00:00Z');
const timer={date:new Date(start).toISOString(),duration:900,relationship:'hostile',events:[]};
const event=(action,seconds)=>({action,at:new Date(start+seconds*1000).toISOString()});
test('repair windows are half-open at exact boundaries',()=>{
    for(const duration of [900,1800]){
        const t={...timer,duration};
        assert.equal(stateAt(t,start-1).name,'upcoming');
        assert.equal(stateAt(t,start).name,'open');
        assert.equal(stateAt(t,start+duration*1000-1).name,'open');
        assert.equal(stateAt(t,start+duration*1000).name,'repaired');
        assert.equal(onMap(t,start+duration*1000),false);
    }
});
test('pauses freeze remaining repair time and resumes preserve it across days',()=>{
    const t={...timer,events:[event('pause',300),event('resume',86700),event('pause',86800),event('resume',87000)]};
    assert.deepEqual(stateAt(t,start+600000),{name:'paused',seconds:600});
    assert.deepEqual(stateAt(t,start+86700000),{name:'open',seconds:600});
    assert.deepEqual(stateAt(t,start+86900000),{name:'paused',seconds:500});
    assert.equal(stateAt(t,start+87500000).name,'repaired');
    assert.equal(stateAt(t,start+299000).name,'open');
});
test('kill outcomes respect relationship, event time and undo',()=>{
    for(const [relationship,result] of [['hostile','won'],['friendly','lost'],['neutral','killed'],['undefined','killed']]){
        const t={...timer,relationship,events:[event('kill',60),event('restore',120)]};
        assert.equal(stateAt(t,start+59000).name,'open');
        assert.equal(stateAt(t,start+60000).name,result);
        assert.equal(onMap(t,start+60000),false);
        assert.equal(stateAt(t,start+120000).name,'open');
    }
});
test('instant events have no repair window; unknown types are not assigned durations',()=>{
    const t={...timer,duration:0};
    assert.equal(stateAt(t,start).name,'unanchored');
    assert.equal(onMap(t,start),true);
    assert.equal(onMap(t,start+60000),false);
    assert.equal(stateAt({...timer,duration:null},start).name,'occurred');
});
test('hour grouping is UTC even for offset dates',()=>{
    assert.equal(hourKey({...timer,date:'2026-10-11T00:30:00+02:00'}),'2026-10-10T22:00Z');
});
