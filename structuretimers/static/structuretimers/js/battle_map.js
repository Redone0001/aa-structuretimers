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
            this.root=root;this.hooks=hooks;this.timers=[];this.tokens=[];this.data=null;this.live=true;this.offset=0;this.selected=null;this.ready=false;
            $('day').value=utc(Date.now()).slice(0,10);
            $('enabled').addEventListener('change',()=>{this.updateControls();hooks.refresh();});
            $('day').addEventListener('change',()=>{if(!$('day').value)return;this.live=false;this.load();});
            $('slider').addEventListener('input',()=>{this.live=false;this.tick();});
            $('time').addEventListener('input',()=>{if(!$('time').value)return;const [h,m]=$('time').value.split(':').map(Number);$('slider').value=h*60+m;this.live=false;this.tick();});
            $('live').addEventListener('click',()=>{this.live=true;const today=utc(this.now()).slice(0,10);if($('day').value!==today){$('day').value=today;this.load();}else this.tick();});
            $('fleets').addEventListener('change',()=>this.hooks.paint());
            $('add').addEventListener('click',()=>this.edit());
            $('close').addEventListener('click',()=>$('editor').close());
            $('form').addEventListener('submit',event=>{event.preventDefault();this.saveToken(false);});
            $('delete').addEventListener('click',()=>this.saveToken(true));
            for(const kind of ['alliance','ship'])this.autocomplete(kind);
            this.interval=setInterval(()=>{if(!document.hidden)this.tick();},1000);
            window.addEventListener('pagehide',event=>{this.abort?.abort();if(!event.persisted)clearInterval(this.interval);});
            document.addEventListener('visibilitychange',()=>{if(!document.hidden&&this.data&&this.now()-(this.loaded||0)>=15*60000)this.load();});
            this.updateControls();
        }
        get enabled(){return $('enabled').checked;}
        now(){return Date.now()+this.offset;}
        instant(){const day=$('day').value||this.loadedDay||utc(this.now()).slice(0,10);return this.live?this.now():Date.parse(day+'T00:00:00Z')+Number($('slider').value)*60000;}
        updateControls(){
            for(const id of ['day','slider','time','live'])$(id).disabled=!this.enabled;
            $('hours-panel').hidden=!this.enabled;
            document.getElementById('st-map-window').disabled=this.enabled;
        }
        reset(){this.abort?.abort();this.abort=null;this.data=null;this.timers=[];this.tokens=[];this.ready=false;this.selected=null;this.signature=null;$('hours').replaceChildren();$('fleet-list').replaceChildren();$('summary').textContent='';$('sync').textContent='';$('editor').close();}
        async setRegion(data){this.data=data;$('token-system').replaceChildren(...data.nodes.slice().sort((a,b)=>a.name.localeCompare(b.name)).map(n=>new Option(n.name,n.id)));await this.load();}
        async api(layer, params={}, options={}){
            const url=new URL(this.root.dataset.battleApi.replace('LAYER',layer),location.origin);
            Object.entries(params).forEach(([k,v])=>url.searchParams.set(k,v));
            const response=await fetch(url,{...options,headers:{Accept:'application/json',...options.headers}});
            let data;try{data=await response.json();}catch(_){throw new Error('Session expired or server unavailable. Reload to reconnect.');}
            if(!response.ok)throw new Error(data.error||`Request failed (${response.status}).`);
            return data;
        }
        async load(){
            if(!this.data||!$('day').value)return;
            this.abort?.abort();const controller=new AbortController();this.abort=controller;
            const region=this.data.region.id, day=$('day').value;
            if(this.loadedDay!==day){this.timers=[];this.ready=false;this.renderHours();this.signature=null;this.tick();}
            $('error').textContent='';
            try{
                const data=await this.api('snapshot',{region,day},{signal:controller.signal});
                if(this.abort!==controller)return;
                this.timers=data.timers;this.tokens=data.tokens;this.offset=Date.parse(data.server_time)-Date.now();this.ready=true;
                this.loaded=Date.parse(data.server_time);this.loadedDay=day;this.signature=null;this.renderHours();this.renderFleets();this.tick();
                if(this.selected!==null)this.hooks.select(this.selected);
            }catch(e){if(e.name!=='AbortError')$('error').textContent=e.message+' Use Refresh to retry.';}
        }
        filtered(){
            const relation=document.getElementById('st-map-relationship').value;
            return this.timers.filter(t=>(relation==='all'||relationshipCodes[t.relationship]===relation)&&this.hooks.inRange(t.system_id));
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
            $('summary').textContent=this.ready?`${active.length} open / paused / instant events · ${new Set(active.map(t=>t.system_id)).size} systems · ${timers.filter(t=>timeline.stateAt(t,instant).name==='upcoming').length} upcoming`:'Loading battle intelligence…';
            const signature=JSON.stringify([this.enabled,this.loadedDay,timers.map(t=>[t.id,timeline.stateAt(t,instant).name,timeline.onMap(t,instant)])]);
            if(signature!==this.signature){this.signature=signature;this.hooks.paint();}
            for(const label of this.root.querySelectorAll('[data-battle-status]')){
                const timer=this.timers.find(t=>t.id===Number(label.dataset.battleStatus));
                if(timer){const status=timeline.stateAt(timer,instant);label.textContent=this.statusText(status);label.dataset.state=status.name;}
            }
            for(const button of this.root.querySelectorAll('[data-battle-action]')){
                const timer=this.timers.find(t=>t.id===Number(button.dataset.timerId));
                if(!timer)continue;
                const status=timeline.stateAt(timer,this.now()).name, action=button.dataset.battleAction;
                button.hidden=!this.live||(action==='pause'&&status!=='open')||(action==='resume'&&status!=='paused')||(action==='kill'&&['won','lost','killed'].includes(status))||(action==='restore'&&!['won','lost','killed'].includes(status));
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
            if($('fleets').checked)for(const t of this.tokens)add(t.system_id,{id:'fleet-'+t.id,tokenId:t.id,category:t.stance==='friend'?'friendly':'hostile',label:this.tokenName(t),symbol:t.stance==='friend'?'F':'H',type_id:t.ship_id,alliance_id:t.alliance_id,tooltip:`${this.tokenName(t)} · ${t.stance} · DPS ${t.dps??'?'} / Logi ${t.logi??'?'} · ${t.mobility} · latest intelligence`,action:'fleet'});
            return result;
        }
        highlights(){return new Set(this.filtered().filter(t=>timeline.onMap(t,this.instant())).map(t=>t.system_id));}
        filtersChanged(){this.renderHours();this.signature=null;this.tick();}
        timerCard(timer){
            const card=el('article',undefined,'st-map-timer');card.dataset.category=timer.relationship;
            const title=el('button',`${clock(Date.parse(timer.date))} · ${timer.system} · ${timer.name}`,'btn btn-link p-0 text-start');title.type='button';title.addEventListener('click',()=>this.hooks.select(timer.system_id));
            const status=el('p',undefined,'st-battle-status small mb-1');status.dataset.battleStatus=timer.id;
            card.append(title,el('p',`${timer.type} · ${timer.timer_label} · ${timer.relationship}`,'small mb-1'),status);
            if(timer.can_edit){const actions=el('div',undefined,'d-flex flex-wrap gap-2');for(const [action,label] of [['pause','Pause'],['resume','Resume'],['kill','Mark killed'],['restore','Undo killed']]){
                const button=el('button',label,'btn btn-sm btn-outline-secondary');button.type='button';button.dataset.battleAction=action;button.dataset.timerId=timer.id;
                button.addEventListener('click',async()=>{button.disabled=true;try{await this.post('timer',{id:timer.id,revision:timer.revision,action});await this.load();}catch(e){$('error').textContent=e.message;}finally{button.disabled=false;}});actions.append(button);
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
            if(this.enabled){const records=this.filtered().filter(t=>t.system_id===id);if(document.getElementById('st-map-structures').checked){for(const timer of records)container.append(this.timerCard(timer));if(!records.length)container.append(el('p','No scheduled timers for this system on the loaded day.'));}}
            const tokens=this.tokens.filter(t=>t.system_id===id);
            if(tokens.length){container.append(el('h3','Latest forces','h6'));tokens.forEach(t=>container.append(this.fleetCard(t)));}
            this.tick();
        }
        tokenName(token){return [token.alliance_name,token.ship_name].filter(Boolean).join(' · ')||'Unknown force';}
        fleetCard(token){
            const card=el('article',undefined,'st-map-timer');card.dataset.category=token.stance==='friend'?'friendly':'hostile';
            const heading=el('div',undefined,'d-flex gap-2 align-items-center');
            for(const [kind,id] of [['alliances',token.alliance_id],['types',token.ship_id]])if(id){const img=el('img');img.src=`https://images.evetech.net/${kind}/${id}/${kind==='alliances'?'logo':'icon'}?size=64`;img.alt=kind==='alliances'?'Alliance logo':'Ship icon';img.width=32;img.height=32;img.addEventListener('error',()=>img.remove());heading.append(img);}
            const title=el('button',this.tokenName(token),'btn btn-link p-0 text-start');title.type='button';title.addEventListener('click',()=>this.hooks.select(token.system_id));heading.append(title);card.append(heading);
            card.append(el('p',`${this.data?.nodes.find(n=>n.id===token.system_id)?.name||token.system_id} · ${token.stance} · DPS ${token.dps??'?'} / Logi ${token.logi??'?'} · ${token.mobility}`,'small mb-1'));
            if(token.note)card.append(el('p',token.note,'small st-battle-note'));
            if(token.dscan){const details=el('details');details.append(el('summary','D-scan text'),el('pre',token.dscan,'st-battle-note'));card.append(details);}
            card.append(el('p',`Updated ${utc(Date.parse(token.updated_at)).replace('T',' ').slice(0,19)} UTC`,'small text-muted mb-1'));
            if(token.can_edit){const edit=el('button','Edit / move','btn btn-sm btn-outline-secondary');edit.type='button';edit.addEventListener('click',()=>this.edit(token));card.append(edit);}
            return card;
        }
        renderFleets(){const list=$('fleet-list');list.replaceChildren(...this.tokens.map(t=>this.fleetCard(t)));if(!this.tokens.length)list.append(el('p','No fleet tokens in this region.','text-muted'));}
        edit(token=null){
            if(!this.data)return;
            this.editing=token;$('form').reset();$('form-error').textContent='';$('delete').hidden=!token;
            for(const field of $('form').elements)if(field.name&&token)field.value=token[field.name]??'';
            if(!token&&this.selected)$('token-system').value=this.selected;
            $('editor-title').textContent=token?'Edit fleet token':'Add fleet token';$('editor').showModal();$('alliance').focus();
        }
        async post(layer,body){return this.api(layer,{}, {method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':this.root.querySelector('[name=csrfmiddlewaretoken]').value},body:JSON.stringify(body)});}
        async saveToken(remove){
            $('save').disabled=true;$('delete').disabled=true;$('form-error').textContent='';
            const body=Object.fromEntries(new FormData($('form')));if(this.editing){body.id=this.editing.id;body.revision=this.editing.revision;}if(remove)body.action='delete';
            try{await this.post('fleet',body);$('editor').close();await this.load();}catch(e){$('form-error').textContent=e.message;}finally{$('save').disabled=false;$('delete').disabled=false;}
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
