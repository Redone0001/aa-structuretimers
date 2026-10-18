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
    function observationForAction(timer, observations) {
        const observed = observations.find(item => item.id === timer.id);
        if (observed && observed.date !== timer.date) throw new Error('The timer schedule changed. Reopen the timer before recording its outcome.');
        return observed && observed.revision >= timer.revision ? observed : timer;
    }
    async function reinforcementPrompt(loadOptions) {
        const options = await loadOptions();
        return new Promise(resolve => {
            const element = (tag, classes, text) => {const node = document.createElement(tag);node.className = classes;if (text) node.textContent = text;return node;};
            const modal = element('div', 'modal fade st-page');modal.tabIndex = -1;modal.setAttribute('aria-labelledby', 'reinforcement-prompt-title');
            const dialog = element('div', 'modal-dialog modal-dialog-centered modal-dialog-scrollable');
            const form = element('form', 'modal-content');
            const header = element('div', 'modal-header');
            const title = element('h3', 'modal-title h5', 'Mark armor destroyed / reinforced');title.id = 'reinforcement-prompt-title';
            const close = element('button', 'btn-close');close.type = 'button';close.setAttribute('aria-label', 'Cancel reinforcement');
            header.append(title, close);
            const body = element('div', 'modal-body');
            const message = element('p', '', options.already_registered ? 'A later timer for this structure is already registered. No duplicate will be created.' : 'Enter the new Final timer in UTC, or record armor destruction without a new timer.');
            body.append(message);
            const input = element('input', 'form-control');input.type = 'datetime-local';input.required = true;input.step = '60';
            input.min = new Date(Date.now() + 60000).toISOString().slice(0, 16);
            if (!options.already_registered) {
                const label = element('label', 'form-label', 'New reinforcement date and time (UTC)');input.id = 'reinforcement-date';label.htmlFor = input.id;body.append(label, input);
            }
            const buttons = element('div', 'modal-footer');
            let result = null;
            let instance;
            const finish = value => {result = value;instance.hide();};
            close.onclick = () => finish(null);
            const addButton = (label, handler) => {const button = element('button', 'btn btn-outline-secondary', label);button.type = 'button';button.onclick = handler;buttons.append(button);};
            addButton('Cancel', () => finish(null));
            if (!options.already_registered) addButton('Record without new timer', () => finish({}));
            const save = element('button', 'btn btn-primary', options.already_registered ? 'Mark reinforced' : 'Save with new timer');save.type = 'submit';buttons.append(save);
            form.addEventListener('submit', event => {event.preventDefault();finish(options.already_registered ? {} : {reinforcement_date: input.value + ':00Z'});});
            modal.addEventListener('hidden.bs.modal', () => {instance.dispose();modal.remove();resolve(result);}, {once: true});
            modal.addEventListener('shown.bs.modal', () => {if (!options.already_registered) input.focus();}, {once: true});
            form.append(header, body, buttons);dialog.append(form);modal.append(dialog);document.body.append(modal);instance = new root.bootstrap.Modal(modal);instance.show();
        });
    }
    const api = {stateAt, onMap, hourKey, signal, fleetRangeOverlay, fleetSummary, completionActions, reinforcementPrompt, observationForAction};
    if (typeof module !== 'undefined') module.exports = api;
    else root.StructureBattleTimeline = api;
})(typeof window === 'undefined' ? globalThis : window);
