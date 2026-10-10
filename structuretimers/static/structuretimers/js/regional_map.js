/* Timer-specific controller. The renderer only receives data and callbacks. */
(function () {
    'use strict';
    const root = document.getElementById('st-regional-map');
    if (!root) return;
    const $ = id => document.getElementById('st-map-'+id);
    const el = (tag, text, className) => { const node=document.createElement(tag); if(text!==undefined)node.textContent=text; if(className)node.className=className;return node; };
    const state = {data:null, structures:new Map(), distances:new Map(), selected:null, rangeReady:false, structuresReady:false};
    const preferences=window.StructureMapPreferences;
    const saved=preferences.load('st_regional_map');
    let remembering=false, saveTimer;
    function savePreferences(){if(!remembering)return;preferences.save('st_regional_map',{
        region:$('region').value,relationship:$('relationship').value,window:$('window').value,range:$('range').value,spacing:$('spacing').value,
        structures:$('structures').checked,gates:$('gates').checked,labels:$('labels').checked,testRegions:$('test-regions').checked,
        source:$('source').value,sourceName:$('source').selectedOptions[0]?.textContent||'',selected:state.selected,viewport:map.box
    });}
    function remember(){if(!remembering)return;clearTimeout(saveTimer);saveTimer=setTimeout(savePreferences,150);}
    window.addEventListener('pagehide',()=>{clearTimeout(saveTimer);savePreferences();});
    const requests = new Map();
    let revision=0;
    function cancel(key) {const old=requests.get(key);old?.abort();requests.delete(key);}
    async function request(layer, params={}, key=layer, options={}) {
        cancel(key);const controller=new AbortController();requests.set(key,controller);
        const url=new URL(root.dataset.api.replace('LAYER',layer),window.location.origin);
        Object.entries(params).forEach(([k,v])=>url.searchParams.set(k,v));
        try {const response=await fetch(url,{...options,signal:controller.signal,headers:{Accept:'application/json',...options.headers}});if(!response.ok){let message=`Request failed (${response.status}).`;try{message=(await response.json()).error||message;}catch(_){/* login/HTML error */}throw new Error(message);}const data=await response.json();return requests.get(key)===controller?data:null;}
        catch(error){if(error.name==='AbortError'||requests.get(key)!==controller)return null;throw error;}
    }
    function filters(){return {region:$('region').value,relationship:$('relationship').value,window:$('window').value,range:$('range').value,source:$('source').value};}
    function error(message){$('status').textContent=message+' Use Refresh to retry.';$('status').className='text-danger';}
    const map=new window.StructureSystemMap($('canvas'),{onSelect:select,onViewport:remember,onIndicatorDrop:(source,destination,item)=>battle.moveToken(source,destination,item),onIndicator:(_id,item)=>{if(item.tokenId){const token=battle.tokens.find(t=>t.id===item.tokenId);if(token?.can_edit)battle.edit(token);}}});
    const battle=new window.StructureBattleMap(root,{paint,select,refresh,fetchRange:(token,preset)=>request('range',{region:state.data.region.id,source:token.system_id,range:preset},'fleet-range-'+token.id),inRange:id=>{
        if($('range').value==='none')return true;
        const d=state.distances.get(id);return state.rangeReady&&d!==null&&d!==undefined&&d<=state.limit;
    }});
    function paint(){
        const highlighted=battle.enabled&&$('structures').checked?battle.highlights():new Set();const needsRange=$('range').value!=='none';
        if(state.structuresReady){for(const id of state.structures.keys()) {const distance=state.distances.get(id);if(!needsRange||(state.rangeReady&&distance!==null&&distance!==undefined&&distance<=state.limit))highlighted.add(id);}}
        map.nodeStates=battle.nodeStates();
        map.forceRanges=battle.forceRanges();
        map.setOverlays(battle.overlays($('structures').checked?state.structures:new Map()),battle.enabled&&$('structures').checked?battle.highlights():highlighted);
        const missing=state.data?.nodes.filter(n=>!n.position).length||0;
        $('status').className='text-muted';
        $('status').textContent=`${state.data?.nodes.length||0} systems · ${highlighted.size} match${missing?` · ${missing} without schematic positions (use selector)`:''}${!$('structures').checked?' · Structure layer off; timer matching paused.':''}${needsRange&&!$('source').value?' · Choose a range origin.':''}${state.rangeReady?` · Range ≤ ${state.limit} LY`:''}`;
    }
    async function overlays(){
        if(!state.data)return;
        const version=++revision;const params=filters();
        cancel('structures');cancel('range');
        state.structuresReady=false;state.rangeReady=false;state.structures=new Map();state.distances=new Map();paint();$('status').textContent='Loading selected layers…';
        const jobs=[];
        if($('structures').checked&&!battle.enabled)jobs.push(request('structures',params).then(data=>{if(!data)return;state.structures=new Map(data.systems.map(s=>[s.id,s.indicators]));state.structuresReady=true;paint();}));
        if(params.range!=='none'&&params.source)jobs.push(request('range',params).then(data=>{if(!data)return;state.distances=new Map(data.systems.map(s=>[s.id,s.distance_ly]));state.limit=data.limit_ly;state.rangeReady=true;paint();}));
        const outcomes=await Promise.allSettled(jobs);
        if(version!==revision)return;
        const failure=outcomes.find(result=>result.status==='rejected');
        if(failure)error(failure.reason.message);else if(!jobs.length)paint();battle.filtersChanged();
    }
    async function region(){
        battle.reset();revision++;['geography','structures','range','details'].forEach(cancel);state.data=null;state.selected=null;state.structures=new Map();state.distances=new Map();map.setOverlays(new Map(),new Set());map.select(null,false);map.setData({nodes:[],edges:[]});$('details').textContent='Select a system.';$('detail-title').textContent='Select a system';$('system').replaceChildren(new Option('Select a system',''));$('status').textContent='Loading regional geography…';
        if(!$('region').value){$('status').textContent='No regions available. Import the SDE to populate the map.';return;}
        try {const data=await request('geography',filters());if(!data)return;state.data=data;map.setData(data);data.nodes.slice().sort((a,b)=>a.name.localeCompare(b.name)).forEach(n=>$('system').add(new Option(n.name+(n.position?'':' — position unavailable'),n.id)));if(!data.nodes.length){$('status').textContent='This region has no systems in the installed SDE.';return;}await Promise.all([overlays(),battle.setRegion(data)]);map.fit();}catch(e){error(e.message);}
    }
    async function select(id){
        const focusedAction=document.activeElement?.dataset.timerAction;
        state.selected=id;battle.selected=id;map.select(id,false);remember();$('system').value=id||'';cancel('details');
        const node=state.data?.nodes.find(n=>n.id===id);$('detail-title').textContent=node?.name||'Select a system';$('details').replaceChildren();
        if(!node){$('details').textContent='Select a system on the map or in the selector.';return;}
        $('details').append(el('p',node.constellation,'text-muted small'));
        if($('range').value!=='none'){const origin=el('button','Use as range origin','btn btn-sm btn-outline-secondary mb-2');origin.type='button';origin.addEventListener('click',()=>{$('source').replaceChildren(new Option(node.name,node.id,true,true));$('source-search').value=node.name;refresh();});$('details').append(origin);}
        if(!node.position)$('details').append(el('p','Schematic position unavailable in the SDE. Timers remain accessible here.','text-warning'));
        if($('range').value!=='none'&&state.rangeReady){const distance=state.distances.get(id);$('details').append(el('p',distance===null||distance===undefined?'Distance unavailable: geographic coordinates are missing.':`${distance.toFixed(2)} LY from range origin`));}
        battle.sidebar(id,$('details'));
        if(battle.enabled)return;
        if(!$('structures').checked){$('details').append(el('p','Enable Structure indicators to load permitted timer details.'));return;}
        const loading=el('p','Loading permitted timers…');$('details').append(loading);
        try {const data=await request('details',{...filters(),system:id});if(!data||state.selected!==id)return;loading.remove();if(!data.timers.length){$('details').append(el('p','No visible timers match these filters.'));return;}
            data.timers.forEach(timer=>{
                const card=el('article',undefined,'st-map-timer');card.dataset.category=timer.relationship;
                card.append(el('h3',timer.name,'h6'),el('p',`${timer.type} · ${timer.relationship}`,'small mb-1'),el('p',`${timer.timer_type} · ${timer.date?new Date(timer.date).toISOString().replace('T',' ').slice(0,16)+' UTC':'No scheduled date'}`,'small mb-1'));
                if(timer.owner)card.append(el('p',`Owner: ${timer.owner}`,'small mb-1'));if(timer.location)card.append(el('p',timer.location,'small'));
                const actions=el('div',undefined,'d-flex flex-wrap gap-2');
                for(const [key,label] of [['edit_url','Edit'],['delete_url','Delete…']])if(timer[key]){const a=el('a',label,'btn btn-sm btn-outline-secondary');a.href=timer[key];a.dataset.timerAction=`${timer.id}-${key}`;actions.append(a);}
                if(timer.refresh_url){const button=el('button','Still there','btn btn-sm btn-outline-primary');button.type='button';button.dataset.timerAction=`${timer.id}-refresh`;button.addEventListener('click',async()=>{button.disabled=true;try{const response=await fetch(timer.refresh_url,{method:'POST',headers:{'X-CSRFToken':root.querySelector('[name=csrfmiddlewaretoken]').value}});if(!response.ok)throw new Error(`Update failed (${response.status}).`);await refresh();if(state.selected===id&&(document.activeElement===document.body||document.activeElement===button))Array.from($('details').querySelectorAll('[data-timer-action]')).find(e=>e.dataset.timerAction===button.dataset.timerAction)?.focus({preventScroll:true});}catch(e){const notice=el('p',e.message,'text-danger');card.append(notice);button.disabled=false;}});actions.append(button);}
                card.append(actions);$('details').append(card);
            });
            if(focusedAction)Array.from($('details').querySelectorAll('[data-timer-action]')).find(e=>e.dataset.timerAction===focusedAction)?.focus({preventScroll:true});
        }catch(e){if(state.selected===id)loading.textContent=e.message+' Select the system again to retry.';}
    }
    async function refresh(){cancel('details');if(!state.data){await region();return;}await Promise.all([overlays(),battle.load()]);if(state.selected!==null)await select(state.selected);}
    $('region').addEventListener('change',region);
    ['relationship','window','structures'].forEach(id=>$(id).addEventListener('change',async()=>{await overlays();if(state.selected!==null)select(state.selected);}));
    $('range').addEventListener('change',()=>{$('source-controls').hidden=$('range').value==='none';refresh();});
    $('source').addEventListener('change',refresh);
    let searchTimer;
    $('source-search').addEventListener('input',()=>{clearTimeout(searchTimer);cancel('search');cancel('range');$('source').replaceChildren(new Option('Choose an origin',''));state.rangeReady=false;state.distances=new Map();paint();const query=$('source-search').value.trim();$('source-status').textContent=query.length<2?'Enter at least two characters.':'Searching…';if(query.length<2)return;searchTimer=setTimeout(async()=>{try{const data=await request('search',{q:query});if(!data)return;data.systems.forEach(s=>$('source').add(new Option(s.name,s.id)));$('source-status').textContent=data.systems.length?`${data.systems.length} result(s); choose an origin.`:'No matching systems.';}catch(e){$('source-status').textContent=e.message;}},250);});
    $('system').addEventListener('change',()=>select($('system').value?Number($('system').value):null));
    $('spacing').addEventListener('change',()=>map.setSpacing(Number($('spacing').value)));
    ['gates','labels'].forEach(id=>$(id).addEventListener('change',()=>map.setLayers($('gates').checked,$('labels').checked)));
    $('zoom-in').addEventListener('click',()=>map.zoom(.8));$('zoom-out').addEventListener('click',()=>map.zoom(1.25));$('fit').addEventListener('click',()=>map.fit());$('clear').addEventListener('click',()=>select(null));$('refresh').addEventListener('click',refresh);
    async function init(restore=false){try{
        const previous=restore?saved.region:$('region').value;
        const data=await request('regions',{include_test:$('test-regions').checked?'1':'0'});if(!data)return;
        $('region').replaceChildren(...data.regions.map(r=>new Option(r.name,r.id)));
        if(data.regions.some(r=>String(r.id)===String(previous)))$('region').value=previous;
        await region();
        if(restore && String(saved.region)===$('region').value){map.setViewport(saved.viewport);if(state.data?.nodes.some(n=>n.id===saved.selected))await select(saved.selected);}
        remembering=true;remember();
    }catch(e){error(e.message);}}
    $('refresh').addEventListener('click',()=>{if(!$('region').options.length)init();});
    const interval=setInterval(()=>{if(!document.hidden&&state.data)refresh();},15*60000);
    window.addEventListener('pagehide',event=>{requests.forEach(controller=>controller.abort());if(!event.persisted){clearInterval(interval);map.destroy();}});
    for(const id of ['relationship','window','range','spacing']){if(Array.from($(id).options).some(o=>o.value===saved[id]))$(id).value=saved[id];}
    for(const [id,key] of [['structures','structures'],['gates','gates'],['labels','labels'],['test-regions','testRegions']])if(typeof saved[key]==='boolean')$(id).checked=saved[key];
    if(/^\d+$/.test(saved.source||'')){$('source').replaceChildren(new Option(String(saved.sourceName||saved.source).slice(0,250),saved.source,true,true));$('source-search').value=saved.sourceName||'';}
    $('source-controls').hidden=$('range').value==='none';map.spacing=Number($('spacing').value);map.setLayers($('gates').checked,$('labels').checked);
    root.addEventListener('change',remember);root.addEventListener('input',remember);
    $('test-regions').addEventListener('change',()=>init());
    init(true);
})();
