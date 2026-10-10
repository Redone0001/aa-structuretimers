/* Map section of the Current and Database tabs. It draws the active tab's filtered
   table rows (window.StructureMapSource); the renderer only receives data and callbacks. */
(function () {
    'use strict';
    const root = document.getElementById('st-regional-map');
    if (!root) return;
    const $ = id => document.getElementById('st-map-'+id);
    const el = (tag, text, className) => { const node=document.createElement(tag); if(text!==undefined)node.textContent=text; if(className)node.className=className;return node; };
    const state = {data:null, structures:new Map(), distances:new Map(), selected:null, rangeReady:false, structuresReady:false};
    const RELATIONSHIPS={FR:'friendly',NE:'neutral',HO:'hostile',UN:'undefined'};
    const SYMBOLS={FR:'F',NE:'N',HO:'H',UN:'?'};
    const source=()=>window.StructureMapSource||{mode:'current',rows:()=>[]};
    const rows=()=>source().rows().filter(row=>row.map&&row.map.system_id);
    const html=text=>{const div=document.createElement('div');div.innerHTML=text||'';return div.textContent.trim();};
    const preferences=window.StructureMapPreferences;
    const saved=preferences.load('st_regional_map');
    let remembering=false, saveTimer;
    function savePreferences(){if(!remembering)return;preferences.save('st_regional_map',{
        region:$('region').value,range:$('range').value,spacing:$('spacing').value,
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
    function filters(){return {region:$('region').value,range:$('range').value,source:$('source').value};}
    function error(message){$('status').textContent=message+' Use Refresh to retry.';$('status').className='text-danger';}
    const map=new window.StructureSystemMap($('canvas'),{onSelect:select,onViewport:remember,onIndicatorDrop:(source,destination,item)=>battle.moveToken(source,destination,item),onIndicator:(_id,item)=>{if(item.tokenId){const token=battle.tokens.find(t=>t.id===item.tokenId);if(token?.can_edit)battle.edit(token);}}});
    const battle=new window.StructureBattleMap(root,{paint,select,refresh,allowed:timer=>{
        // The battle timeline shows the same timers as the Current table.
        if(source().mode!=='current')return false;
        return rows().some(row=>row.id===timer.id);
    },fetchRange:(token,preset)=>request('range',{region:state.data.region.id,source:token.system_id,range:preset},'fleet-range-'+token.id),inRange:id=>{
        if($('range').value==='none')return true;
        const d=state.distances.get(id);return state.rangeReady&&d!==null&&d!==undefined&&d<=state.limit;
    }});
    function paint(){
        let refit=false;
        const highlighted=battle.enabled&&$('structures').checked?battle.highlights():new Set();const needsRange=$('range').value!=='none';
        if(state.structuresReady){for(const id of state.structures.keys()) {const distance=state.distances.get(id);if(!needsRange||(state.rangeReady&&distance!==null&&distance!==undefined&&distance<=state.limit))highlighted.add(id);}}
        if(state.data){
            const nodes=battle.mapNodes();
            refit=JSON.stringify((map.data?.nodes||[]).filter(n=>n.external).map(n=>n.id))!==JSON.stringify(nodes.filter(n=>n.external).map(n=>n.id));
            map.data={...state.data,nodes};
            const external=nodes.filter(n=>n.external), previous=$('system').value;
            for(const option of [...$('system').options])if(option.dataset.external)option.remove();
            for(const node of external){const option=new Option(node.name+' — outside region',node.id);option.dataset.external='1';$('system').add(option);}
            $('system').value=previous;
        }
        map.nodeStates=battle.nodeStates();
        map.forceRanges=battle.forceRanges();
        map.setOverlays(battle.overlays($('structures').checked?state.structures:new Map()),battle.enabled&&$('structures').checked?battle.highlights():highlighted);
        if(refit)map.fit();
        const missing=state.data?.nodes.filter(n=>!n.position).length||0;
        $('status').className='text-muted';
        $('status').textContent=`${state.data?.nodes.length||0} systems · ${highlighted.size} match${missing?` · ${missing} without schematic positions (use selector)`:''}${!$('structures').checked?' · Structure layer off; timer matching paused.':''}${needsRange&&!$('source').value?' · Choose a range origin.':''}${state.rangeReady?` · Range ≤ ${state.limit} LY`:''}`;
    }
    function indicators(){
        const inRegion=new Set(state.data.nodes.map(n=>n.id)), groups=new Map();
        for(const row of rows()){
            const m=row.map;if(!inRegion.has(m.system_id))continue;
            const key=`${m.objective}-${m.structure_type_id||'unknown'}`;
            const systems=groups.get(m.system_id)||new Map();groups.set(m.system_id,systems);
            const item=systems.get(key)||{id:`structure-${m.system_id}-${key}`,category:RELATIONSHIPS[m.objective]||'undefined',label:row.structure_type_name||'Unknown structure',type_id:m.structure_type_id,count:0,symbol:SYMBOLS[m.objective]||'?'};
            item.count++;item.tooltip=`${item.label} · ${item.category} · ${item.count} ${source().mode==='current'?'timer':'record'}(s)`;systems.set(key,item);
        }
        return new Map([...groups].map(([id,items])=>[id,[...items.values()]]));
    }
    async function overlays(){
        if(!state.data)return;
        const version=++revision;const params=filters();
        cancel('structures');cancel('range');
        state.structuresReady=false;state.rangeReady=false;state.structures=new Map();state.distances=new Map();paint();$('status').textContent='Loading selected layers…';
        const jobs=[];
        if($('structures').checked&&!battle.enabled){state.structures=indicators();state.structuresReady=true;}
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
        const node=battle.mapNodes().find(n=>n.id===id);$('detail-title').textContent=node?.name||'Select a system';$('details').replaceChildren();
        if(!node){$('details').textContent='Select a system on the map or in the selector.';return;}
        $('details').append(el('p',node.constellation,'text-muted small'));
        if($('range').value!=='none'){const origin=el('button','Use as range origin','btn btn-sm btn-outline-secondary mb-2');origin.type='button';origin.addEventListener('click',()=>{$('source').replaceChildren(new Option(node.name,node.id,true,true));$('source-search').value=node.name;refresh();});$('details').append(origin);}
        if(!node.position)$('details').append(el('p','Schematic position unavailable in the SDE. Timers remain accessible here.','text-warning'));
        if($('range').value!=='none'&&state.rangeReady){const distance=state.distances.get(id);$('details').append(el('p',distance===null||distance===undefined?'Distance unavailable: geographic coordinates are missing.':`${distance.toFixed(2)} LY from range origin`));}
        battle.sidebar(id,$('details'));
        if(battle.enabled||node.external)return;
        if(!$('structures').checked){$('details').append(el('p','Enable Structure indicators to load permitted timer details.'));return;}
        const matches=rows().filter(row=>row.map.system_id===id);
        if(!matches.length){$('details').append(el('p','No rows of the table below are in this system.'));return;}
        for(const row of matches){
            const m=row.map, card=el('article',undefined,'st-map-timer');card.dataset.category=RELATIONSHIPS[m.objective]||'undefined';
            card.append(el('h3',m.structure_name||'Unnamed structure','h6'),el('p',`${row.structure_type_name||'Unknown structure'} · ${row.objective_name}`,'small mb-1'));
            if(row.date)card.append(el('p',`${m.timer_type_name} · ${new Date(row.date).toISOString().replace('T',' ').slice(0,16)} UTC`,'small mb-1'));
            if(row.owner_name)card.append(el('p',`Owner: ${row.owner_name}`,'small mb-1'));if(m.location_details)card.append(el('p',m.location_details,'small'));
            // The row's own buttons: rendered by the server with escaped content.
            const actions=el('div',undefined,'mt-1');actions.innerHTML=row.actions||'';card.append(actions);$('details').append(card);
        }
        if(focusedAction)Array.from($('details').querySelectorAll('[data-timer-action]')).find(e=>e.dataset.timerAction===focusedAction)?.focus({preventScroll:true});
    }
    async function refresh(){cancel('details');if(!state.data){await region();return;}await Promise.all([overlays(),battle.load()]);if(state.selected!==null)await select(state.selected);}
    $('region').addEventListener('change',region);
    $('structures').addEventListener('change',async()=>{await overlays();if(state.selected!==null)select(state.selected);});
    $('range').addEventListener('change',()=>{$('source-controls').hidden=$('range').value==='none';refresh();});
    $('source').addEventListener('change',refresh);
    let searchTimer;
    $('source-search').addEventListener('input',()=>{clearTimeout(searchTimer);cancel('search');cancel('range');$('source').replaceChildren(new Option('Choose an origin',''));state.rangeReady=false;state.distances=new Map();paint();const query=$('source-search').value.trim();$('source-status').textContent=query.length<2?'Enter at least two characters.':'Searching…';if(query.length<2)return;searchTimer=setTimeout(async()=>{try{const data=await request('search',{q:query});if(!data)return;data.systems.forEach(s=>$('source').add(new Option(s.name,s.id)));$('source-status').textContent=data.systems.length?`${data.systems.length} result(s); choose an origin.`:'No matching systems.';}catch(e){$('source-status').textContent=e.message;}},250);});
    $('system').addEventListener('change',()=>select($('system').value?Number($('system').value):null));
    $('spacing').addEventListener('change',()=>map.setSpacing(Number($('spacing').value)));
    ['gates','labels'].forEach(id=>$(id).addEventListener('change',()=>map.setLayers($('gates').checked,$('labels').checked)));
    $('zoom-in').addEventListener('click',()=>map.zoom(.8));$('zoom-out').addEventListener('click',()=>map.zoom(1.25));$('fit').addEventListener('click',()=>map.fit());$('clear').addEventListener('click',()=>select(null));$('refresh').addEventListener('click',refresh);
    async function init(restore=false){try{
        const previous=restore?(saved.region||root.dataset.defaultRegion):$('region').value;
        const data=await request('regions',{include_test:$('test-regions').checked?'1':'0'});if(!data)return;
        $('region').replaceChildren(...data.regions.map(r=>{const option=new Option(r.name,r.id);option.dataset.regions=(r.regions||[r.id]).join(',');return option;}));
        // Staging in Delve or Querious opens the merged Delve + Querious map.
        const merged=!restore||saved.region?null:[...$('region').options].find(o=>o.dataset.regions.includes(',')&&o.dataset.regions.split(',').includes(String(previous)));
        if(merged)$('region').value=merged.value;
        else if(data.regions.some(r=>String(r.id)===String(previous)))$('region').value=previous;
        await region();
        if(restore && String(saved.region)===$('region').value){map.setViewport(saved.viewport);if(state.data?.nodes.some(n=>n.id===saved.selected))await select(saved.selected);}
        remembering=true;remember();
    }catch(e){error(e.message);}}
    $('refresh').addEventListener('click',()=>{if(!$('region').options.length)init();});
    const interval=setInterval(()=>{if(!document.hidden&&state.data)refresh();},15*60000);
    window.addEventListener('pagehide',event=>{requests.forEach(controller=>controller.abort());if(!event.persisted){clearInterval(interval);map.destroy();}});
    for(const id of ['range','spacing']){if(Array.from($(id).options).some(o=>o.value===saved[id]))$(id).value=saved[id];}
    for(const [id,key] of [['structures','structures'],['gates','gates'],['labels','labels'],['test-regions','testRegions']])if(typeof saved[key]==='boolean')$(id).checked=saved[key];
    if(/^\d+$/.test(saved.source||'')){$('source').replaceChildren(new Option(String(saved.sourceName||saved.source).slice(0,250),saved.source,true,true));$('source-search').value=saved.sourceName||'';}
    $('source-controls').hidden=$('range').value==='none';map.spacing=Number($('spacing').value);map.setLayers($('gates').checked,$('labels').checked);
    root.addEventListener('change',remember);root.addEventListener('input',remember);
    $('test-regions').addEventListener('change',()=>init());

    // Load nothing until the section is opened, so the tabs stay fast.
    let started=false;
    function regionFollowsTable(){
        // Follow the table when all its rows are in one region the map is not showing.
        const regions=new Set(rows().map(row=>String(row.map.region_id)));
        const shown=($('region').selectedOptions[0]?.dataset.regions||'').split(',');
        if(!regions.size||[...regions].every(id=>shown.includes(id))||regions.size!==1)return false;
        const [id]=regions, option=[...$('region').options].find(o=>o.value===id);
        if(!option)return false;
        $('region').value=option.value;region();return true;
    }
    async function tableChanged(){
        if(!started||!state.data)return;
        if(regionFollowsTable())return;
        await overlays();if(state.selected!==null)select(state.selected);
    }
    function modeChanged(){
        const database=source().mode==='database';
        $('mode-label').textContent=database?'· Database records':'· Current timers';
        // The battle timeline is about timers; the Database shows structures.
        root.querySelector('.st-battle-controls').hidden=database;
        if(database&&battle.enabled){document.getElementById('st-battle-enabled').checked=false;battle.updateControls();}
        if(started&&state.data)refresh();
    }
    document.getElementById('st-map-collapse').addEventListener('shown.bs.collapse',()=>{
        if(!started){started=true;init(true);}else map.fit();
    });
    window.StructureRegionalMap={tableChanged,modeChanged};
    modeChanged();
})();
