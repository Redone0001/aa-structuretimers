/* Campaign-specific adapter: selection/posts remain the existing campaign form's responsibility. */
(function () {
    'use strict';
    const endpoint=document.getElementById('campaign-map-data');if(!endpoint)return;
    const $=id=>document.getElementById('campaign-map-'+id);
    const prefs=window.StructureMapPreferences, saved=prefs.load('st_campaign_map');
    let regions=[], current=null, detailId=null, request=null, generation=0, view='list';
    const map=new window.StructureSystemMap(document.getElementById('st-map-canvas'),{onSelect:toggle});
    if(['1','1.6','2.2'].includes(saved.spacing))$('spacing').value=saved.spacing;
    for(const id of ['gates','structures','labels'])if(typeof saved[id]==='boolean')$(id).checked=saved[id];
    map.spacing=Number($('spacing').value);map.showGates=$('gates').checked;map.showLabels=$('labels').checked;
    function remember(){prefs.save('st_campaign_map',{region:$('region').value,spacing:$('spacing').value,gates:$('gates').checked,structures:$('structures').checked,labels:$('labels').checked});}
    function input(node){return document.getElementById('system-'+node.entryId);}
    function sync(){
        const selected=Array.from(document.querySelectorAll('#campaign-list-view input[name="systems"]:checked'));
        document.getElementById('campaign-selected-count').textContent=selected.length;
        map.setSelection((current?.nodes||[]).filter(n=>input(n)?.checked).map(n=>n.id));
    }
    function details(id){
        detailId=id;$('details').replaceChildren();const node=current?.nodes.find(n=>n.id===id);
        if(!node){$('details').textContent='Select a system to see its recon and permitted actions.';return;}
        const source=document.querySelector('#campaign-list-view [data-system-entry="'+node.entryId+'"]');
        if(!source)return;
        const clone=source.cloneNode(true);clone.removeAttribute('data-system-entry');clone.removeAttribute('id');
        clone.querySelectorAll('input[name="systems"]').forEach(el=>el.remove());
        clone.querySelectorAll('[id]').forEach(el=>el.removeAttribute('id'));
        clone.querySelectorAll('label[for]').forEach(el=>el.removeAttribute('for'));
        if(!node.position){const warning=document.createElement('p');warning.className='text-warning';warning.textContent='Schematic position unavailable in the SDE. You can still select and work on this system.';$('details').append(warning);}
        $('details').append(clone);
        if(source.dataset.addUrl){const add=document.createElement('a');add.className='btn btn-primary';add.href=source.dataset.addUrl;add.textContent='Add recon';$('details').append(add);}
    }
    function toggle(id){const node=current?.nodes.find(n=>n.id===id);if(!node)return;const checkbox=input(node);if(checkbox&&!checkbox.disabled){checkbox.checked=!checkbox.checked;checkbox.dispatchEvent(new Event('change',{bubbles:true}));}details(id);$('system').value=id;}
    function draw(refit=true){
        current=regions.find(r=>String(r.id)===$('region').value)||null;
        if(!current){map.setData({nodes:[],edges:[]});$('status').textContent='No campaign regions available.';return;}
        map.setOverlays($('structures').checked?new Map(current.nodes.map(n=>[n.id,n.indicators||[]])):new Map(),new Set());
        const viewport=map.box?{...map.box}:null;
        const symbols={available:'○',reserved:'◐',completed:'✓'};
        map.setData({...current,nodes:current.nodes.map(n=>({...n,category:{available:'neutral',reserved:'warning',completed:'success'}[n.status],name:`${symbols[n.status]} ${n.name} (${n.count})`}))});
        if(!refit)map.setViewport(viewport);
        $('system').replaceChildren(new Option('Select / deselect a system',''),...current.nodes.map(n=>new Option(n.name+(n.position?'':' — position unavailable'),n.id)));
        if(current.nodes.some(n=>n.id===detailId))$('system').value=detailId;
        sync();details(detailId);
        const missing=current.nodes.filter(n=>!n.position).length;
        $('status').textContent=`${current.nodes.length} campaign systems${missing?` · ${missing} without SDE schematic positions`:''}`;
        remember();
    }
    async function load(){
        request?.abort();const controller=new AbortController();request=controller;const version=++generation;
        $('status').textContent='Loading SDE campaign map…';
        try{
            const response=await fetch(endpoint.dataset.url,{signal:controller.signal,headers:{Accept:'application/json'}});
            if(!response.ok)throw new Error(response.status===503?'Enable eve_sde and import its data. Use List view until ready.':'Could not load the map. Use Refresh map to retry.');
            const data=await response.json();if(version!==generation)return;
            const previous=$('region').value||String(saved.region||'');const same=current&&data.some(r=>String(r.id)===previous);
            regions=data;$('region').replaceChildren(...regions.map(r=>new Option(r.name,r.id)));
            if(regions.some(r=>String(r.id)===previous))$('region').value=previous;
            draw(!same);
        }catch(error){if(error.name!=='AbortError'&&version===generation)$('status').textContent=error.message;}
    }
    function switchView(next){view=next;document.getElementById('campaign-list-view').hidden=view==='map';document.getElementById('campaign-map-view').hidden=view!=='map';document.querySelectorAll('[data-campaign-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.campaignView===view)));if(view==='map'){if(!regions.length)load();else map.fit();}remember();}
    document.getElementById('campaign-view-controls').hidden=false;
    document.querySelectorAll('[data-campaign-view]').forEach(button=>button.addEventListener('click',()=>switchView(button.dataset.campaignView)));
    $('region').addEventListener('change',()=>{detailId=null;draw();});
    $('system').addEventListener('change',()=>toggle(Number($('system').value)));
    $('spacing').addEventListener('change',()=>{map.setSpacing(Number($('spacing').value));remember();});
    $('gates').addEventListener('change',()=>{map.setLayers($('gates').checked,$('labels').checked);remember();});
    $('structures').addEventListener('change',()=>draw(false));$('labels').addEventListener('change',()=>{map.setLayers($('gates').checked,$('labels').checked);remember();});
    $('zoom-in').addEventListener('click',()=>map.zoom(.8));$('zoom-out').addEventListener('click',()=>map.zoom(1.25));$('fit').addEventListener('click',()=>map.fit());$('refresh').addEventListener('click',load);
    $('clear').addEventListener('click',()=>{document.querySelectorAll('#campaign-list-view input[name="systems"]').forEach(el=>el.checked=false);window.dispatchEvent(new Event('campaign:updated'));});
    window.addEventListener('campaign:selection',sync);
    window.addEventListener('campaign:updated',()=>{sync();details(detailId);if(view==='map')load();else regions=[];});
    switchView('map');sync();
})();
