/* Pure UTC timeline calculations, shared by the browser and regression tests. */
(function (root) {
    'use strict';
    function stateAt(timer, instant) {
        const start = Date.parse(timer.date);
        let elapsed = instant - start, paused = null, killed = false;
        for (const event of timer.events || []) {
            const at = Date.parse(event.at);
            if (at > instant) break;
            if (event.action === 'pause') paused = at;
            if (event.action === 'resume' && paused !== null) { elapsed -= at - paused; paused = null; }
            if (event.action === 'adjust') elapsed -= event.seconds * 1000;
            if (event.action === 'kill') killed = true;
            if (event.action === 'restore') killed = false;
        }
        if (killed) return {name: timer.relationship === 'hostile' ? 'won' : timer.relationship === 'friendly' ? 'lost' : 'killed'};
        if (instant < start) return {name: 'upcoming', seconds: Math.ceil((start-instant)/1000)};
        if (paused !== null) elapsed -= instant-paused;
        const seconds = timer.duration === null ? null : Math.max(0, Math.ceil(timer.duration-elapsed/1000));
        if (paused !== null) return {name:'paused', seconds};
        if (timer.duration === null) return {name:'occurred'};
        if (timer.duration === 0) return {name:'unanchored'};
        return {name: seconds > 0 ? 'open' : 'repaired', seconds};
    }
    function onMap(timer, instant) {
        const status = stateAt(timer, instant);
        return ['open','paused'].includes(status.name) || (status.name==='upcoming' && status.seconds<=900) ||
            (['unanchored','occurred'].includes(status.name) && instant-Date.parse(timer.date)<60000);
    }
    function signal(timer, instant) {
        const state=stateAt(timer,instant);
        if(state.name==='open')return {category:'danger',pulse:state.seconds<=300};
        if(state.name==='paused')return {category:'info',pulse:false};
        if(state.name==='upcoming'&&state.seconds<=900)return {category:'warning',pulse:false};
        return null;
    }
    // Gate forces do not restrict a jump-range intersection.
    function fleetRangeOverlay(entries) {
        const active=entries.filter(entry=>entry.mobility!=='gate');
        if(!active.length||active.some(entry=>!entry.systems))return new Map();
        const color=active.length>1?'intersection':active[0].stance==='friend'?'friendly':'hostile';
        return new Map([...active[0].systems].filter(id=>active.every(entry=>entry.systems.has(id))).map(id=>[id,color]));
    }
    function hourKey(timer) { return timer.date ? new Date(timer.date).toISOString().slice(0,13)+':00Z' : 'Unscheduled'; }
    const api = {stateAt, onMap, hourKey, signal, fleetRangeOverlay};
    if (typeof module !== 'undefined') module.exports = api;
    else root.StructureBattleTimeline = api;
})(typeof window === 'undefined' ? globalThis : window);
