/* Battle timeline and fast fleet editor. All untrusted content uses text nodes. */
(function () {
    'use strict';
    const $ = id => document.getElementById('st-battle-'+id);
    const el = (tag,text,classes) => {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(classes)n.className=classes;return n;};
    const utc = ms => new Date(ms).toISOString();
    const clock = ms => utc(ms).slice(11,19);
    const timeline = window.StructureBattleTimeline;
    const relationshipCodes = {friendly:'FR',hostile:'HO',neutral:'NE',undefined:'UN'};
    class BattleMap {
        constructor(root, hooks) {
            this.root=root;this.hooks=hooks;
            // Fleet tokens are off unless the map section asks for them (data-forces="on").
            this.forces=root.dataset.forces!=='off';
            if(!this.forces){$('fleets').checked=false;$('forces-panel').hidden=true;root.querySelectorAll('[data-forces-only]').forEach(e=>{e.hidden=true;});}
this.timers=[];this.tokens=[];this.data=null;this.live=true;this.offset=0;this.selected=null;this.ready=false;this.moving=new Set();this.rangeSelected=new Set();this.rangeCache=new Map();this.rangeGeneration=0;this.fleetVersion=0;this.fleetLoaded=0;
            $('day').value=utc(Date.now()).slice(0,10);
            $('enabled').addEventListener('change',()=>{this.updateControls();hooks.refresh();});
            $('day').addEventListener('change',()=>{if(!$('day').value)return;this.live=false;this.load();});
            $('slider').addEventListener('input',()=>{this.live=false;this.tick();});
            $('time').addEventListener('input',()=>{if(!/^([01][0-9]|2[0-3]):[0-5][0-9]$/.test($('time').value))return;const [h,m]=$('time').value.split(':').map(Number);$('slider').value=h*60+m;this.live=false;this.tick();});
            $('live').addEventListener('click',()=>{this.live=true;const today=utc(this.now()).slice(0,10);if($('day').value!==today){$('day').value=today;this.load();}else this.tick();});
            $('fleets').addEventListener('change',()=>{this.hooks.paint();if($('fleets').checked)this.loadFleets();else{this.fleetAbort?.abort();this.fleetAbort=null;this.fleetVersion++;$('fleet-sync').textContent='Fleet background updates paused while overlay is off.';}});
            $('add').addEventListener('click',()=>this.edit());
            $('close').addEventListener('click',()=>$('editor').close());
            $('form').addEventListener('submit',event=>{event.preventDefault();this.saveToken(false);setTimeout(()=>{if(!this.saving)$('form').classList.remove('is-submitting');},0);});
            $('delete').addEventListener('click',()=>this.saveToken(true));
            this.systemAutocomplete();
            for(const kind of ['alliance','ship'])this.autocomplete(kind);
            this.interval=setInterval(()=>{if(!document.hidden){this.tick();if($('fleets').checked&&Date.now()-this.fleetLoaded>=120000)this.loadFleets();}},1000);
            window.addEventListener('pagehide',event=>{this.abort?.abort();this.fleetAbort?.abort();if(!event.persisted)clearInterval(this.interval);});
            document.addEventListener('visibilitychange',()=>{if(!document.hidden&&this.data&&this.now()-(this.loaded||0)>=15*60000)this.load();});
            this.updateControls();
        }
        get enabled(){return $('enabled').checked;}
        now(){return Date.now()+this.offset;}
        instant(){const day=$('day').value||this.loadedDay||utc(this.now()).slice(0,10);return this.live?this.now():Date.parse(day+'T00:00:00Z')+Number($('slider').value)*60000;}
        updateControls(){
            for(const id of ['day','slider','time','live'])$(id).disabled=!this.enabled;
            $('hours-panel').hidden=!this.enabled;
        }
        reset(){this.fleetVersion++;this.fleetAbort?.abort();this.fleetAbort=null;this.fleetLoaded=0;this.rangeGeneration++;this.rangeSelected.clear();this.rangeCache.clear();this.abort?.abort();this.abort=null;this.data=null;this.timers=[];this.tokens=[];this.ready=false;this.selected=null;this.signature=null;$('hours').replaceChildren();$('fleet-list').replaceChildren();$('summary').textContent='';$('sync').textContent='';$('editor').close();}
        async setRegion(data){this.data=data;await this.load();}
        async api(layer, params={}, options={}){
            const url=new URL(this.root.dataset.battleApi.replace('LAYER',layer),location.origin);
            Object.entries(params).forEach(([k,v])=>url.searchParams.set(k,v));
            const response=await fetch(url,{...options,headers:{Accept:'application/json',...options.headers}});
            let data;try{data=await response.json();}catch(_){throw new Error('Session expired or server unavailable. Reload to reconnect.');}
            if(!response.ok)throw new Error(data.error||`Request failed (${response.status}).`);
            return data;
        }
        applyFleets(tokens){
            if(!this.forces)return;
            this.tokens=tokens;this.fleetLoaded=Date.now();this.syncForceRanges();this.renderFleets();this.hooks.paint();
            $('fleet-sync').textContent=`Forces updated ${clock(this.now())} UTC · updates every 2 minutes while fleet overlay is on.`;
        }
        async loadFleets(){
            if(!this.data||!$('fleets').checked||document.hidden||this.fleetAbort||this.saving||this.moving.size)return;
            const controller=new AbortController(), version=++this.fleetVersion;
            this.fleetAbort=controller;this.fleetLoaded=Date.now();
            try{
                const data=await this.api('fleets',{region:this.data.region.id},{signal:controller.signal});
                if(version!==this.fleetVersion)return;
                this.applyFleets(data.tokens);
                if(this.selected!==null)this.hooks.select(this.selected);
            }catch(error){if(error.name!=='AbortError')$('fleet-sync').textContent=`Fleet update failed: ${error.message} Retrying within 2 minutes.`;}
            finally{if(this.fleetAbort===controller)this.fleetAbort=null;}
        }
        async load(forceFleets=false){
            if(!this.data||!$('day').value)return;
            this.abort?.abort();const controller=new AbortController();this.abort=controller;
            this.fleetAbort?.abort();this.fleetAbort=null;const fleetVersion=++this.fleetVersion;
            const region=this.data.region.id, day=$('day').value;
            if(this.loadedDay!==day){this.timers=[];this.ready=false;this.renderHours();this.signature=null;this.tick();}
            $('error').textContent='';
            try{
                const data=await this.api('snapshot',{region,day,fleets:forceFleets||$('fleets').checked?'1':'0'},{signal:controller.signal});
                if(this.abort!==controller)return;
                this.timers=data.timers;if(data.tokens&&fleetVersion===this.fleetVersion)this.applyFleets(data.tokens);this.offset=Date.parse(data.server_time)-Date.now();this.ready=true;
                this.loaded=Date.parse(data.server_time);this.loadedDay=day;this.syncForceRanges();this.signature=null;this.renderHours();this.renderFleets();this.tick();
                if(this.selected!==null)this.hooks.select(this.selected);
            }catch(e){if(e.name!=='AbortError')$('error').textContent=e.message+' Use Refresh to retry.';}
        }
        filtered(){
            return this.timers.filter(t=>this.hooks.allowed(t)&&this.hooks.inRange(t.system_id));
        }
        tick(){
            if(!this.data)return;
            const instant=this.instant();
            if(this.live&&utc(instant).slice(0,10)!==$('day').value){$('day').value=utc(instant).slice(0,10);this.load();return;}
            if(this.live)$('slider').value=new Date(instant).getUTCHours()*60+new Date(instant).getUTCMinutes();
            if(document.activeElement!==$('time'))$('time').value=clock(instant).slice(0,5);
            $('clock').textContent=(this.live?'LIVE · ':'Inspecting · ')+clock(instant)+' UTC';
            $('live').setAttribute('aria-pressed',String(this.live));$('live').classList.toggle('btn-primary',this.live);$('live').classList.toggle('btn-outline-primary',!this.live);
            if(this.loaded)$('sync').textContent=`Loaded ${clock(this.loaded)} UTC · next server refresh within 15 min`;
            const timers=this.filtered(), active=timers.filter(t=>timeline.onMap(t,instant));
            $('summary').textContent=this.ready?`${active.length} opening soon / open / paused / instant events · ${new Set(active.map(t=>t.system_id)).size} systems · ${timers.filter(t=>timeline.stateAt(t,instant).name==='upcoming').length} upcoming`:'Loading battle intelligence…';
            const signature=JSON.stringify([this.enabled,this.loadedDay,timers.map(t=>[t.id,timeline.stateAt(t,instant).name,timeline.onMap(t,instant),timeline.signal(t,instant)])]);
            if(signature!==this.signature){this.signature=signature;this.hooks.paint();}
            for(const label of this.root.querySelectorAll('[data-battle-status]')){
                const timer=this.timers.find(t=>t.id===Number(label.dataset.battleStatus));
                if(timer){const status=timeline.stateAt(timer,instant);label.textContent=this.statusText(status);label.dataset.state=status.name;}
            }
            for(const button of this.root.querySelectorAll('[data-battle-action]')){
                const timer=this.timers.find(t=>t.id===Number(button.dataset.timerId));
                if(!timer)continue;
                const status=timeline.stateAt(timer,this.now()).name, action=button.dataset.battleAction;
                button.hidden=!this.live||(action==='pause'&&status!=='open')||(action==='resume'&&status!=='paused')||(action==='adjust'&&status!=='paused')||(action==='kill'&&['won','lost','killed'].includes(status))||(action==='restore'&&!['won','lost','killed'].includes(status));
            }
        }
        statusText(status){
            const names={open:'OPEN',paused:'PAUSED',won:'WON · hostile killed',lost:'LOST · friendly killed',killed:'KILLED',repaired:'Auto-repaired (estimated)',unanchored:'Unanchored · instant',occurred:'Occurred',upcoming:'Upcoming'};
            return (names[status.name]||status.name)+(status.seconds!==undefined?` · ${Math.floor(status.seconds/60)}m ${status.seconds%60}s`:'');
        }
        overlays(base){
            const result=new Map();
            const add=(id,item)=>{if(!result.has(id))result.set(id,[]);result.get(id).push(item);};
            if(this.enabled&&document.getElementById('st-map-structures').checked){
                for(const t of this.filtered())if(timeline.onMap(t,this.instant()))add(t.system_id,{id:'battle-'+t.id,type_id:t.type_id,category:t.relationship,label:t.name,symbol:timeline.stateAt(t,this.instant()).name==='paused'?'Ⅱ':'!',tooltip:`${t.name} · ${t.timer_label} · ${timeline.stateAt(t,this.instant()).name}`,action:'timer'});
            }else for(const [id,items] of base)result.set(id,[...items]);
            if($('fleets').checked)for(const t of this.tokens)add(t.system_id,{id:'fleet-'+t.id,tokenId:t.id,revision:t.revision,large:true,draggable:t.can_edit&&!this.moving.has(t.id),category:t.stance==='friend'?'friendly':'hostile',label:this.tokenName(t),symbol:t.stance==='friend'?'F':'H',type_id:t.ship_id,alliance_id:t.alliance_id,tooltip:`${this.tokenName(t)} · ${t.stance} · DPS ${t.dps??'?'} / Logi ${t.logi??'?'} · ${t.mobility} · latest intelligence`,action:'fleet'});
            return result;
        }
        nodeStates(){
            const states=new Map(), priority={warning:1,info:2,danger:3};
            if(!this.enabled||!document.getElementById('st-map-structures').checked)return states;
            for(const timer of this.filtered()){
                const next=timeline.signal(timer,this.instant()), old=states.get(timer.system_id);
                if(!next)continue;
                if(!old||priority[next.category]>priority[old.category])states.set(timer.system_id,next);
                else if(next.category===old.category&&next.pulse)old.pulse=true;
            }
            return states;
        }
        highlights(){return new Set(this.filtered().filter(t=>timeline.onMap(t,this.instant())).map(t=>t.system_id));}
        filtersChanged(){this.renderHours();this.signature=null;this.tick();}
        timerCard(timer){
            const card=el('article',undefined,'st-map-timer');card.dataset.category=timer.relationship;
            const title=el('button',`${clock(Date.parse(timer.date))} · ${timer.system} · ${timer.name}`,'btn btn-link p-0 text-start');title.type='button';title.addEventListener('click',()=>this.hooks.select(timer.system_id));
            const status=el('p',undefined,'st-battle-status small mb-1');status.dataset.battleStatus=timer.id;
            card.append(title,el('p',`${timer.type} · ${timer.timer_label} · ${timer.relationship}`,'small mb-1'),status);
            if(timer.can_edit){const actions=el('div',undefined,'d-flex flex-wrap gap-2');for(const [action,label] of [['pause','Pause'],['resume','Resume'],['adjust','−1 min'],['adjust','+1 min'],['kill','Mark killed'],['restore','Undo killed']]){
                const button=el('button',label,'btn btn-sm btn-outline-secondary');button.type='button';button.dataset.battleAction=action;button.dataset.timerId=timer.id;if(action==='adjust'){button.title='Adjust paused remaining repair time';button.setAttribute('aria-label',label+' remaining repair time');}
                button.addEventListener('click',async()=>{button.disabled=true;try{await this.post('timer',{id:timer.id,revision:timer.revision,action,...(action==='adjust'?{seconds:label.startsWith('+')?60:-60}:{})});await this.load();}catch(e){$('error').textContent=e.message;}finally{button.disabled=false;}});actions.append(button);
            }card.append(actions);}
            return card;
        }
        renderHours(){
            const list=$('hours');list.replaceChildren();const groups=new Map();
            for(const timer of this.filtered()){
                const day=utc(Date.parse(timer.date)).slice(0,10);
                if(day!==$('day').value&&!['open','paused'].includes(timeline.stateAt(timer,Date.parse($('day').value+'T00:00:00Z')).name))continue;
                const key=day===$('day').value?timeline.hourKey(timer):'Carried over from earlier timers';
                if(!groups.has(key))groups.set(key,[]);groups.get(key).push(timer);
            }
            for(const [hour,timers] of groups){const section=el('section',undefined,'st-battle-hour');const heading=el('h3',`${hour.replace('T',' ').replace('Z',' UTC')} · ${timers.length} timer(s)`,'h6');section.append(heading);for(const timer of timers)section.append(this.timerCard(timer));list.append(section);}
            if(!groups.size)list.append(el('p','No scheduled timers match this day and the selected relationship/range.','text-muted'));
        }
        sidebar(id,container){
            this.selected=id;
            if(this.enabled){const records=this.filtered().filter(t=>t.system_id===id);if(document.getElementById('st-map-structures').checked){for(const timer of records)container.append(this.timerCard(timer));if(!records.length)container.append(el('p',this.mapNodes().find(n=>n.id===id)?.external?'Outside-region force location. Timer loading is limited to the displayed region.':'No scheduled timers for this system on the loaded day.'));}}
            const tokens=this.tokens.filter(t=>t.system_id===id);
            if(tokens.length){container.append(el('h3','Latest forces','h6'));tokens.forEach(t=>container.append(this.fleetCard(t)));}
            this.updateRangeButtons();this.tick();
        }
        mapNodes(){
            const nodes=this.data?.nodes||[], known=new Set(nodes.map(n=>n.id));
            const locations=new Map(this.tokens.filter(t=>!known.has(t.system_id)).map(t=>[t.system_id,t]));
            const positioned=nodes.filter(n=>n.position);
            const edge=Math.max(0,...positioned.map(n=>n.position[0]))+350;
            const top=Math.min(0,...positioned.map(n=>n.position[1]));
            return [...nodes,...[...locations.values()].sort((a,b)=>a.system_name.localeCompare(b.system_name)).map((t,index)=>({
                id:t.system_id,name:t.system_name,constellation:`${t.region_name} · Outside region (schematic placement)`,
                position:[edge+Math.floor(index/6)*400,top+(index%6)*180],external:true
            }))];
        }
        systemAutocomplete(){
            let timeout,controller;this.systemMatches=new Map();
            $('token-system-name').addEventListener('input',()=>{
                clearTimeout(timeout);controller?.abort();
                const q=$('token-system-name').value.trim();
                $('token-system').value=this.systemMatches.get(q)||'';
                if(q.length<2){$('system-options').replaceChildren();return;}
                timeout=setTimeout(async()=>{
                    controller=new AbortController();
                    try{
                        const data=await this.api('lookup',{kind:'system',q},{signal:controller.signal});
                        if($('token-system-name').value.trim()!==q)return;
                        this.systemMatches=new Map(data.results.map(s=>[s.name,s.id]));
                        $('system-options').replaceChildren(...data.results.map(s=>{const option=new Option(s.region,s.name);return option;}));
                        const exact=data.results.find(s=>s.name.toLowerCase()===q.toLowerCase());
                        $('token-system').value=exact?.id||'';
                        $('lookup-status').textContent=data.results.length?'Choose a system; suggestions cover all regions.':'No matching solar system.';
                    }catch(error){if(error.name!=='AbortError')$('lookup-status').textContent='System search unavailable. Type again to retry.';}
                },200);
            });
        }
        forceRanges(){
            return timeline.fleetRangeOverlay(this.tokens.filter(t=>this.rangeSelected.has(t.id)).map(t=>({
                ...t,systems:this.rangeCache.get(this.rangeKey(t))?.systems
            })));
        }
        rangeKey(token){return `${this.data?.region.id}:${token.system_id}:${token.mobility}`;}
        updateRangeButtons(){
            for(const button of this.root.querySelectorAll('[data-force-range]')){
                const token=this.tokens.find(t=>t.id===Number(button.dataset.forceRange));
                if(!token)continue;
                const selected=this.rangeSelected.has(token.id), entry=this.rangeCache.get(this.rangeKey(token));
                button.setAttribute('aria-pressed',String(selected));
                button.textContent=selected?(entry?.error?'Hide ranges (failed)':entry?.systems?'Hide ranges':'Loading ranges…'):'Show ranges';
                button.classList.toggle('btn-outline-secondary',!selected);button.classList.toggle('btn-primary',selected);
            }
        }
        async syncForceRanges(){
            const generation=++this.rangeGeneration;
            const tokens=this.tokens.filter(t=>this.rangeSelected.has(t.id)&&t.mobility!=='gate');
            this.rangeSelected=new Set(tokens.map(t=>t.id));
            this.updateRangeButtons();this.hooks.paint();
            await Promise.all(tokens.map(async token=>{
                const key=this.rangeKey(token);
                if(this.rangeCache.get(key)?.systems)return;
                try{
                    const preset=token.mobility==='conduit'?'command':token.mobility;
                    const data=await this.hooks.fetchRange(token,preset);
                    if(generation!==this.rangeGeneration||!data)return;
                    this.rangeCache.set(key,{systems:new Set(data.systems.filter(s=>s.distance_ly!==null&&s.distance_ly<=data.limit_ly).map(s=>s.id))});
                }catch(error){
                    if(generation!==this.rangeGeneration)return;
                    this.rangeCache.set(key,{error:true});
                    $('move-status').textContent=`Range unavailable: ${error.message} Toggle the range off and on to retry.`;
                }
            }));
            if(generation!==this.rangeGeneration)return;
            this.updateRangeButtons();this.hooks.paint();
            if(tokens.length&&!tokens.some(t=>this.rangeCache.get(this.rangeKey(t))?.error)){
                $('move-status').textContent=`${this.forceRanges().size} systems ${tokens.length>1?'in the intersection of '+tokens.length+' force ranges':'in force range'}. Dashed ${tokens.length>1?'yellow':tokens[0].stance==='friend'?'blue':'red'} boxes; geometric jump distance.`;
            }else if(!tokens.length)$('move-status').textContent='Force range overlay cleared.';
        }
        toggleForceRange(token){
            if(this.rangeSelected.has(token.id))this.rangeSelected.delete(token.id);
            else {this.rangeSelected.add(token.id);this.rangeCache.delete(this.rangeKey(token));}
            this.syncForceRanges();
        }
        tokenName(token){return [token.alliance_name,token.ship_name].filter(Boolean).join(' · ')||'Unknown force';}
        fleetCard(token){
            const card=el('article',undefined,'st-map-timer');card.dataset.category=token.stance==='friend'?'friendly':'hostile';
            const heading=el('div',undefined,'d-flex gap-2 align-items-center');
            for(const [kind,id] of [['alliances',token.alliance_id],['types',token.ship_id]])if(id){const img=el('img');img.src=`https://images.evetech.net/${kind}/${id}/${kind==='alliances'?'logo':'icon'}?size=64`;img.alt=kind==='alliances'?'Alliance logo':'Ship icon';img.width=32;img.height=32;img.addEventListener('error',()=>img.remove());heading.append(img);}
            const title=el('button',this.tokenName(token),'btn btn-link p-0 text-start');title.type='button';title.addEventListener('click',()=>this.hooks.select(token.system_id));heading.append(title);card.append(heading);
            card.append(el('p',`${token.system_name||token.system_id} · ${token.region_name?'('+token.region_name+') · ':''}${token.stance} · DPS ${token.dps??'?'} / Logi ${token.logi??'?'} · ${token.mobility}`,'small mb-1'));
            if(token.note)card.append(el('p',token.note,'small st-battle-note'));
            if(token.dscan){const details=el('details');details.append(el('summary','D-scan text'),el('pre',token.dscan,'st-battle-note'));card.append(details);}
            card.append(el('p',`Updated ${utc(Date.parse(token.updated_at)).replace('T',' ').slice(0,19)} UTC`,'small text-muted mb-1'));
            const actions=el('div',undefined,'d-flex gap-2 flex-wrap');
            if(token.can_edit){const edit=el('button','Edit / move','btn btn-sm btn-outline-secondary');edit.type='button';edit.addEventListener('click',()=>this.edit(token));actions.append(edit);}
            const ranges=el('button',this.rangeSelected.has(token.id)?'Hide ranges':'Show ranges','btn btn-sm btn-outline-secondary');
            ranges.type='button';ranges.dataset.forceRange=token.id;ranges.setAttribute('aria-pressed',String(this.rangeSelected.has(token.id)));
            ranges.disabled=token.mobility==='gate';ranges.title=token.mobility==='gate'?'Gate mobility has no jump-range filter':'Show systems within this force’s mobility range';
            ranges.addEventListener('click',()=>this.toggleForceRange(token));actions.append(ranges);card.append(actions);
            return card;
        }
        renderFleets(){
            const list=$('fleet-list');list.replaceChildren();
            for(const [stance,label] of [['friend','Friend'],['foe','Foe']]){
                const tokens=this.tokens.filter(t=>t.stance===stance), section=el('section');
                section.append(el('h3',`${label} · ${tokens.length} force(s)`,'h5'));
                const rows=timeline.fleetSummary(tokens);
                if(rows.length){
                    const table=el('table',undefined,'table table-sm');table.append(el('caption',`${label}: reported ship counts by ship class. Unknown counts are not treated as zero.`,'small'));
                    const head=el('thead'), header=el('tr');for(const title of ['Ship class','DPS','Logi']){const cell=el('th',title);cell.scope='col';header.append(cell);}head.append(header);table.append(head);
                    const body=el('tbody');
                    for(const row of rows){const tr=el('tr');tr.append(el('th',row.name));for(const [value,unknown] of [[row.dps,row.unknownDps],[row.logi,row.unknownLogi]])tr.append(el('td',`${value}${unknown?' + ? ('+unknown+' force'+(unknown>1?'s':'')+')':''}`));body.append(tr);}table.append(body);section.append(table);
                }
                const cards=el('div',undefined,'st-battle-fleet-list');cards.append(...tokens.map(t=>this.fleetCard(t)));section.append(cards);
                if(!tokens.length)section.append(el('p','No forces reported.','text-muted'));list.append(section);
            }
            this.updateRangeButtons();
        }

        edit(token=null){
            if(!this.data)return;
            this.editing=token;$('form').reset();if(!this.saving)$('form').classList.remove('is-submitting');$('form-error').textContent='';$('delete').hidden=!token;
            for(const field of $('form').elements)if(field.name&&token)field.value=token[field.name]??'';
            const node=this.mapNodes().find(n=>n.id===(token?.system_id||this.selected));
            $('token-system').value=token?.system_id||node?.id||'';
            $('token-system-name').value=token?.system_name||node?.name||'';
            this.systemMatches=new Map(node?[[node.name,node.id]]:token?[[token.system_name,token.system_id]]:[]);
            $('system-options').replaceChildren();
            $('editor-title').textContent=token?'Edit fleet token':'Add fleet token';$('editor').showModal();$('alliance').focus();
        }
        async post(layer,body){return this.api(layer,{}, {method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':this.root.querySelector('[name=csrfmiddlewaretoken]').value},body:JSON.stringify(body)});}
        async moveToken(source,destination,item){
            const token=this.tokens.find(t=>t.id===item.tokenId);
            if(!token?.can_edit||this.moving.has(token.id)||source===destination)return;
            const region=this.data?.region.id;
            this.fleetVersion++;this.fleetAbort?.abort();this.fleetAbort=null;
            this.moving.add(token.id);this.hooks.paint();
            $('move-status').textContent=`Moving ${this.tokenName(token)}…`;
            try{
                const result=await this.post('fleet',{action:'move',id:token.id,revision:item.revision,system_id:destination});
                if(this.data?.region.id!==region)return;
                // Cancel an older snapshot before applying the server-confirmed move.
                this.abort?.abort();this.abort=null;
                this.tokens=this.tokens.map(t=>t.id===token.id?result.token:t);
                this.renderFleets();this.syncForceRanges();this.hooks.select(destination);
                $('move-status').textContent=`${this.tokenName(result.token)} moved to ${result.token.system_name||destination}.`;
            }catch(e){
                if(this.data?.region.id===region)$('move-status').textContent=`Move not saved: ${e.message} Use Refresh to check the latest position.`;
            }finally{this.moving.delete(token.id);this.hooks.paint();}
        }
        async saveToken(remove){
            if(this.saving)return;
            if(!remove&&!$('token-system').value){$('form-error').textContent='Choose a valid solar system from the suggestions.';return;}
            this.fleetVersion++;this.fleetAbort?.abort();this.fleetAbort=null;
            this.saving=true;$('form').setAttribute('aria-busy','true');
            $('save').disabled=true;$('delete').disabled=true;$('form-error').textContent='';
            const body=Object.fromEntries(new FormData($('form')));if(this.editing){body.id=this.editing.id;body.revision=this.editing.revision;}if(remove)body.action='delete';
            try{await this.post('fleet',body);$('editor').close();await this.load(true);}catch(e){$('form-error').textContent=e.message;}finally{this.saving=false;$('form').classList.remove('is-submitting');$('form').removeAttribute('aria-busy');$('save').disabled=false;$('delete').disabled=false;}
        }
        autocomplete(kind){
            let timeout,controller;
            $(kind).addEventListener('input',()=>{clearTimeout(timeout);controller?.abort();$(kind+'-options').replaceChildren();const q=$(kind).value.trim();if(q.length<2)return;
                timeout=setTimeout(async()=>{controller=new AbortController();try{const data=await this.api('lookup',{kind,q},{signal:controller.signal});if($(kind).value.trim()!==q)return;$(kind+'-options').replaceChildren(...data.results.map(item=>new Option(item.name||item.alliance_name)));$('lookup-status').textContent=data.results.length?'Suggestions available; free text is also accepted.':'No local matches. You can still save this name.';}catch(e){if(e.name!=='AbortError')$('lookup-status').textContent='Suggestions unavailable; you can still save any name.';}},200);
            });
        }
    }
    window.StructureBattleMap=BattleMap;
})();
