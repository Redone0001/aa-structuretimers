/* Pure UTC timeline calculations, shared by the browser and regression tests. */
(function (root) {
    'use strict';
    function stateAt(timer, instant) {
        const start = Date.parse(timer.date);
        let elapsed = instant - start, paused = null, killed = false, reinforced = false;
        for (const event of timer.events || []) {
            const at = Date.parse(event.at);
            if (at > instant) break;
            if (event.action === 'pause') paused = at;
            if (event.action === 'resume' && paused !== null) { elapsed -= at - paused; paused = null; }
            if (event.action === 'adjust') elapsed -= event.seconds * 1000;
            if (event.action === 'kill') killed = true;
            if (event.action === 'restore') killed = false;
            if (event.action === 'reinforce') reinforced = true;
        }
        if (killed) return {name: timer.relationship === 'hostile' ? 'won' : timer.relationship === 'friendly' ? 'lost' : 'killed'};
        if (reinforced) return {name: 'reinforced'};
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
    function fleetSummary(tokens){
        const groups=new Map();
        for(const token of tokens){
            const name=token.ship_class||'Unknown / unspecified';
            if(!groups.has(name))groups.set(name,{name,dps:0,logi:0,unknownDps:0,unknownLogi:0});
            const row=groups.get(name);
            for(const field of ['dps','logi'])if(token[field]===null||token[field]===undefined)row[field==='dps'?'unknownDps':'unknownLogi']++;else row[field]+=token[field];
        }
        return [...groups.values()].sort((a,b)=>a.name.localeCompare(b.name));
    }
    function hourKey(timer) { return timer.date ? new Date(timer.date).toISOString().slice(0,13)+':00Z' : 'Unscheduled'; }
    function completionActions(timer, status) {
        if (['won', 'lost', 'killed'].includes(status)) return [['restore', 'Undo killed']];
        if (status === 'reinforced') return [];
        if (timer.timer_type === 'AR') return [['reinforce', 'Mark reinforced']];
        // Legacy Hull records describe the same final fight.
        return ['FI', 'HL'].includes(timer.timer_type) ? [['kill', 'Mark killed']] : [];
    }
    async function reinforcementPrompt(loadOptions) {
        const options = await loadOptions();
        return new Promise(resolve => {
            const dialog = document.createElement('dialog');
            dialog.className = 'rounded border p-4';
            dialog.style.maxWidth = 'min(32rem, 95vw)';
            const form = document.createElement('form');
            const title = document.createElement('h3'); title.className = 'h5'; title.textContent = 'Mark armor destroyed / reinforced';
            const message = document.createElement('p');
            message.textContent = options.already_registered ? 'A later timer for this structure is already registered. No duplicate will be created.' : 'Enter the new Final timer in UTC, or record armor destruction without a new timer.';
            form.append(title, message);
            const input = document.createElement('input'); input.type = 'datetime-local'; input.className = 'form-control mb-3'; input.required = true; input.step = '60';
            input.min = new Date(Date.now() + 60000).toISOString().slice(0, 16);
            if (!options.already_registered) {
                const label = document.createElement('label'); label.textContent = 'New reinforcement date and time (UTC)'; input.id = 'reinforcement-date'; label.htmlFor = input.id; form.append(label, input);
            }
            const buttons = document.createElement('div'); buttons.className = 'd-flex flex-wrap gap-2';
            const finish = value => { dialog.close(); dialog.remove(); resolve(value); };
            const addButton = (label, handler) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-outline-secondary'; button.textContent = label; button.onclick = handler; buttons.append(button); };
            const save = document.createElement('button'); save.type = 'submit'; save.className = 'btn btn-primary'; save.textContent = options.already_registered ? 'Mark reinforced' : 'Save with new timer'; buttons.append(save);
            if (!options.already_registered) addButton('Record without new timer', () => finish({}));
            addButton('Cancel', () => finish(null));
            form.addEventListener('submit', event => { event.preventDefault(); finish(options.already_registered ? {} : {reinforcement_date: input.value + ':00Z'}); });
            dialog.addEventListener('cancel', event => { event.preventDefault(); finish(null); });
            form.append(buttons); dialog.append(form); document.body.append(dialog); dialog.showModal();
        });
    }
    const api = {stateAt, onMap, hourKey, signal, fleetRangeOverlay, fleetSummary, completionActions, reinforcementPrompt};
    if (typeof module !== 'undefined') module.exports = api;
    else root.StructureBattleTimeline = api;
})(typeof window === 'undefined' ? globalThis : window);
