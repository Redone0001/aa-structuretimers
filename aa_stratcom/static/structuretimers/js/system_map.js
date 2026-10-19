/* Reusable SVG renderer. No timer models, endpoints or permissions belong here. */
(function (root) {
    'use strict';
    const NS = 'http://www.w3.org/2000/svg';
    let instance = 0;
    function svg(tag, attrs = {}, text) {
        const el = document.createElementNS(NS, tag);
        Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value));
        if (text !== undefined) el.textContent = text;
        return el;
    }
    function boundary(node, toward, gap = 5) {
        const dx = toward.x - node.x, dy = toward.y - node.y;
        if (!dx && !dy) return {x: node.x, y: node.y};
        const t = 1 / Math.max(Math.abs(dx) / (node.width / 2), Math.abs(dy) / (node.height / 2));
        const length = Math.hypot(dx, dy);
        return {x: node.x + dx * t + dx / length * gap, y: node.y + dy * t + dy / length * gap};
    }
    function connectionPath(a, b, lane = 0) {
        if (a.x === b.x && a.y === b.y) {
            return `M ${a.x} ${a.y-a.height/2-5} C ${a.x+80} ${a.y-100} ${a.x+100} ${a.y+80} ${a.x+a.width/2+5} ${a.y}`;
        }
        const dx = b.x-a.x, dy = b.y-a.y, length = Math.hypot(dx, dy);
        const control = {x: (a.x+b.x)/2-dy/length*lane*24, y: (a.y+b.y)/2+dx/length*lane*24};
        const start = boundary(a, control), end = boundary(b, control, 9);
        return `M ${start.x} ${start.y} Q ${control.x} ${control.y} ${end.x} ${end.y}`;
    }
    function indicatorSlots(indicators, limit = 12) {
        const visible = indicators.slice(0, limit), result = [];
        if (indicators.length > limit) visible.push({id: 'overflow', category: 'overflow', label: `+${indicators.length-limit}`, overflow: true});
        let row = 0;
        const categories = [...new Set(visible.map(i => i.category))];
        categories.forEach(category => {
            const items = visible.filter(i => i.category === category);
            for (let start = 0; start < items.length; start += 4) {
                const part = items.slice(start, start+4);
                part.forEach((item, column) => result.push({item, x: (column-(part.length-1)/2)*22-10, y: 35+row*24}));
                row++;
            }
        });
        return result;
    }
    function tokenSlots(items, width) {
        const size = width / 2, gap = 8, rows = Math.ceil(items.length / 2);
        return items.map((item, index) => {
            const row = Math.floor(index / 2), count = Math.min(2, items.length - row * 2);
            return {item, size, x: (index % 2 - (count - 1) / 2) * (size + gap) - size / 2,
                y: -41 - (rows - row) * (size + gap)};
        });
    }
    function dropTarget(nodes, point) {
        let target = null, nearest = Infinity;
        for (const [id, node] of nodes) {
            const distance = ((point.x-node.x)/(node.width/2+14))**2 + ((point.y-node.y)/(node.height/2+14))**2;
            if (distance <= 1 && distance < nearest) {target = id; nearest = distance;}
        }
        return target;
    }
    class SystemMap {
        constructor(host, options = {}) {
            this.host = host; this.options = options; this.spacing = 1; this.selected = null; this.highlighted = new Set(); this.overlays = new Map(); this.showGates = true; this.showLabels = false; this.uid = `system-map-${++instance}`;
            this.svg = svg('svg', {class: 'st-system-map', role: 'group', 'aria-label': 'Regional system map', tabindex: 0});
            this.tooltip = document.createElement('div'); this.tooltip.className = 'st-map-tooltip'; this.tooltip.hidden = true;
            this.announcement = document.createElement('div'); this.announcement.className = 'visually-hidden'; this.announcement.setAttribute('role', 'status');
            host.append(this.svg, this.tooltip, this.announcement);
            this.svg.addEventListener('wheel', event => {event.preventDefault(); if(!this.tokenDrag)this.zoom(event.deltaY < 0 ? 0.85 : 1/0.85, this.point(event));}, {passive: false});
            this.svg.addEventListener('pointerdown', event => {
                if (event.button !== 0 || event.target.closest('[data-indicator-action]')) return;
                this.drag = {x: event.clientX, y: event.clientY, start: this.point(event), box: {...this.box}, moved: false, node: event.target.closest('[data-node]')?.dataset.node};
                this.svg.setPointerCapture(event.pointerId);
            });
            this.svg.addEventListener('pointermove', event => {
                if (this.tokenDrag) {this.moveTokenDrag(event); return;}
                if (!this.drag) return;
                if (Math.hypot(event.clientX-this.drag.x, event.clientY-this.drag.y) > 5) this.drag.moved = true;
                if (!this.drag.moved) return;
                const point = this.point(event);
                this.box.x += this.drag.start.x-point.x; this.box.y += this.drag.start.y-point.y; this.applyView();
                this.host.classList.add('is-panning');
            });
            this.svg.addEventListener('pointerup', event => {
                if (this.tokenDrag) {if(event.pointerId===this.tokenDrag.pointerId)this.finishTokenDrag(false);return;}
                const drag = this.drag; this.drag = null; this.host.classList.remove('is-panning');
                if (this.svg.hasPointerCapture(event.pointerId)) this.svg.releasePointerCapture(event.pointerId);
                if (drag && !drag.moved && drag.node) this.select(Number(drag.node));
            });
            this.svg.addEventListener('lostpointercapture', () => {if(this.tokenDrag)this.finishTokenDrag(true);});
            this.svg.addEventListener('pointercancel', () => {if(this.tokenDrag)this.finishTokenDrag(true);this.drag = null; this.host.classList.remove('is-panning');});
            this.svg.addEventListener('keydown', event => {
                if(event.key==='Escape' && this.tokenDrag){event.preventDefault();this.finishTokenDrag(true);return;}
                if (event.target !== this.svg || this.tokenDrag) return;
                if (event.key === '+' || event.key === '=') this.zoom(.85);
                else if (event.key === '-') this.zoom(1/.85);
                else if (event.key === 'Home') this.fit();
                else if (event.key.startsWith('Arrow')) { const step = this.box.width*.08; this.box.x += event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0; this.box.y += event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0; this.applyView(); }
                else return;
                event.preventDefault();
            });
            this.resizeObserver = new ResizeObserver(() => { if (this.fitted && this.nodes) this.fit(); });
            this.resizeObserver.observe(host);
        }
        startTokenDrag(event, node, item, icon, size) {
            if(event.button!==0 || !event.isPrimary || !item.draggable || this.tokenDrag || this.drag)return;
            event.stopPropagation(); event.preventDefault();
            this.tokenDrag={pointerId:event.pointerId, x:event.clientX, y:event.clientY, source:node.id, item, icon, size, moved:false, target:null};
            this.svg.setPointerCapture(event.pointerId);
        }
        moveTokenDrag(event) {
            const drag=this.tokenDrag;
            if(event.pointerId!==drag.pointerId)return;
            if(!drag.moved && Math.hypot(event.clientX-drag.x,event.clientY-drag.y)<=5)return;
            if(!drag.moved){
                drag.moved=true; drag.ghost=drag.icon.cloneNode(true);
                drag.ghost.removeAttribute('tabindex');drag.ghost.removeAttribute('data-focus-key');
                drag.ghost.setAttribute('aria-hidden','true');drag.ghost.classList.add('st-map-token-ghost');
                this.svg.append(drag.ghost);this.host.classList.add('is-token-dragging');
            }
            this.tooltip.hidden=true;
            const point=this.point(event);drag.ghost.setAttribute('transform',`translate(${point.x-drag.size/2} ${point.y-drag.size/2})`);
            const target=dropTarget(this.nodes,point);
            if(target!==drag.target){
                drag.target=target;this.svg.querySelectorAll('[data-drop-target]').forEach(el=>el.classList.toggle('is-drop-target',Number(el.dataset.dropTarget)===target));
                this.announcement.textContent=target===null?'Drop on a system to move; Escape cancels.':`Move to ${this.data.nodes.find(n=>n.id===target)?.name}`;
            }
        }
        finishTokenDrag(cancelled) {
            const drag=this.tokenDrag;if(!drag)return;
            this.tokenDrag=null;drag.ghost?.remove();this.host.classList.remove('is-token-dragging');
            if(this.svg.hasPointerCapture(drag.pointerId))this.svg.releasePointerCapture(drag.pointerId);
            this.svg.querySelectorAll('.is-drop-target').forEach(el=>el.classList.remove('is-drop-target'));
            if(drag.moved || cancelled)this.suppressClick={id:drag.item.id,until:Date.now()+500};
            if(this.pendingRender){this.pendingRender=false;this.render();}
            if(!cancelled && drag.moved && drag.target!==null && drag.target!==drag.source){
                this.options.onIndicatorDrop?.(drag.source,drag.target,drag.item);
            }else if(!cancelled && !drag.moved){
                this.suppressClick={id:drag.item.id,until:Date.now()+500};
                this.select(drag.source);this.options.onIndicator?.(drag.source,drag.item);
            }else if(drag.moved || cancelled){this.announcement.textContent='Move cancelled; token stays in its original system.';}
        }
        point(event) { const p = this.svg.createSVGPoint(); p.x = event.clientX; p.y = event.clientY; return p.matrixTransform(this.svg.getScreenCTM().inverse()); }
        applyView() { this.fitted = false; if (this.box) this.svg.setAttribute('viewBox', `${this.box.x} ${this.box.y} ${this.box.width} ${this.box.height}`); this.options.onViewport?.(this.box); }
        zoom(factor, point) {
            if(this.tokenDrag)return;
            if (!this.box) return;
            const width = this.box.width*factor;
            if (width < 80 || width > (this.bounds?.width || 1000)*8) return;
            point = point || {x: this.box.x+this.box.width/2, y: this.box.y+this.box.height/2};
            this.box = {x: point.x-(point.x-this.box.x)*factor, y: point.y-(point.y-this.box.y)*factor, width, height: this.box.height*factor}; this.applyView();
        }
        fit() {
            if(this.tokenDrag)return;
            if (!this.bounds) return;
            const ratio = Math.max(this.host.clientWidth, 1)/Math.max(this.host.clientHeight, 1);
            const width = Math.max(this.bounds.width, this.bounds.height*ratio), height = width/ratio;
            this.box = {x: this.bounds.x-(width-this.bounds.width)/2, y: this.bounds.y-(height-this.bounds.height)/2, width, height}; this.applyView(); this.fitted = true;
        }
        setData(data) {this.finishTokenDrag(true);this.data = data; this.render(); this.fit();}
        setSpacing(value) {this.finishTokenDrag(true);this.spacing = value; this.render(); this.fit();}
        setOverlays(overlays, highlighted) {this.overlays = overlays; this.highlighted = highlighted; this.render();}
        setLayers(gates, labels) {const changed = this.showLabels !== labels; this.showGates = gates; this.showLabels = labels; this.render(); if(changed)this.fit();}
        setViewport(box) {
            if(box && ['x','y','width','height'].every(k=>Number.isFinite(box[k])&&Math.abs(box[k])<1e9) && box.width>0 && box.height>0){this.box={...box};this.applyView();}
        }
        setSelection(ids) {this.selectedIds=new Set(ids);this.select(this.selected,false);}
        select(id, notify = true) {
            this.selected = id;
            this.svg.querySelectorAll('[data-node]').forEach(el => el.setAttribute('aria-pressed', String(this.selectedIds ? this.selectedIds.has(Number(el.dataset.node)) : Number(el.dataset.node) === id)));
            this.svg.querySelectorAll('[data-selection]').forEach(el => el.classList.toggle('is-selected', this.selectedIds ? this.selectedIds.has(Number(el.dataset.selection)) : Number(el.dataset.selection) === id));
            if (notify) this.options.onSelect?.(id);
        }
        hint(el, text) {
            el.append(svg('title', {}, text));
            const show = () => {this.tooltip.textContent = text; this.tooltip.hidden = false;};
            const hide = () => {this.tooltip.hidden = true;};
            el.addEventListener('mouseenter', show); el.addEventListener('mouseleave', hide); el.addEventListener('focus', show); el.addEventListener('blur', hide);
        }
        render() {
            if (!this.data) return;
            if(this.tokenDrag){this.pendingRender=true;return;}
            const focused = document.activeElement?.getAttribute('data-focus-key');
            this.tooltip.hidden = true; this.svg.replaceChildren();
            const defs = svg('defs'); this.svg.append(defs);
            const layers = ['gates', 'connections', 'outlines', 'nodes', 'indicators'].map(name => svg('g', {'data-layer': name})); this.svg.append(...layers);
            this.nodes = new Map(); let xmin=Infinity, ymin=Infinity, xmax=-Infinity, ymax=-Infinity;
            this.data.nodes.forEach(node => {
                if (!node.position) return;
                const x=node.position[0]*this.spacing, y=node.position[1]*this.spacing;
                const group=svg('g', {transform: `translate(${x} ${y})`, 'data-node': node.id, 'data-external':node.external?'true':'false', 'data-category': this.nodeStates?.get(node.id)?.category || node.category || 'default', 'data-region': node.region_id ?? '', 'data-focus-key': `node-${node.id}`, tabindex: 0, role: 'button', 'aria-label': node.name, 'aria-pressed': String(this.selectedIds ? this.selectedIds.has(node.id) : this.selected===node.id), class: 'st-map-node'+(this.nodeStates?.get(node.id)?.pulse?' st-map-timer-pulse':'')});
                const label=svg('text', {x:0, y:4, 'text-anchor':'middle', class:'st-map-name'}, node.name); group.append(label); layers[3].append(group);
                const width=Math.max(90, label.getComputedTextLength()+24), height=34;
                const box=svg('rect', {x:-width/2, y:-17, width, height, rx:6, class:'st-map-box'});group.prepend(box);
                const outlines=svg('g', {transform:`translate(${x} ${y})`}); layers[2].append(outlines);
                const highlight=svg('rect', {x:-width/2-8,y:-25,width:width+16,height:50,rx:10,class:'st-map-highlight'}); highlight.classList.toggle('is-highlighted', this.highlighted.has(node.id));
                const forceRange=this.forceRanges?.get(node.id);
                if(forceRange)outlines.append(svg('rect',{x:-width/2-17,y:-34,width:width+34,height:68,rx:12,class:'st-map-highlight st-map-force-range is-highlighted','data-force-color':forceRange}));
                const selected=svg('rect', {x:-width/2-4,y:-21,width:width+8,height:42,rx:8,class:'st-map-selection','data-selection':node.id}); selected.classList.toggle('is-selected',this.selectedIds ? this.selectedIds.has(node.id) : this.selected===node.id);
                const focus=svg('rect', {x:-width/2-12,y:-29,width:width+24,height:58,rx:12,class:'st-map-focus'});outlines.append(highlight,selected,focus);
                ['mouseenter','focus'].forEach(name=>group.addEventListener(name,()=>focus.classList.add('is-focused')));
                ['mouseleave','blur'].forEach(name=>group.addEventListener(name,()=>focus.classList.remove('is-focused')));
                group.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();event.stopPropagation();this.select(node.id);}});
                if(node.external)group.append(svg('text',{x:0,y:32,'text-anchor':'middle',class:'st-map-name'},'Outside region'));
                const indicators=this.overlays.get(node.id)||[];
                this.hint(group, `${node.name}${this.highlighted.has(node.id)?' · Matches filters':''}${forceRange?' · '+(forceRange==='intersection'?'In selected force range intersection':'In force range'):''}`);
                const grid=svg('g',{transform:`translate(${x} ${y})`}); layers[4].append(grid);
                let bottom=30, top=32, extent=width/2+16;
                const tokens=indicators.filter(item=>item.large);
                if(tokens.length){
                    [...new Set(tokens.map(item=>item.category))].forEach((category,index)=>{
                        outlines.prepend(svg('ellipse',{cx:0,cy:0,rx:width/2+14+index*5,ry:height/2+14+index*5,class:'st-map-fleet-ring','data-category':category}));
                        extent=Math.max(extent,width/2+20+index*5);bottom=Math.max(bottom,height/2+20+index*5);
                    });
                }
                outlines.append(svg('ellipse',{cx:0,cy:0,rx:width/2+14,ry:height/2+14,class:'st-map-drop-target','data-drop-target':node.id}));
                const slots=[...indicatorSlots(indicators.filter(item=>!item.large)),...tokenSlots(tokens,width)];
                slots.forEach(({item,x:ix,y:iy,size=20})=>{
                    const icon=svg('g',{transform:`translate(${ix} ${iy})`,class:item.large?'st-map-indicator st-map-fleet-token':'st-map-indicator','data-category':item.category,tabindex:0,'data-focus-key':`${node.id}-${item.id}`,role:item.overflow||item.action?'button':'img','aria-label':item.tooltip||item.label});
                    const artwork=svg('g',{transform:`scale(${size/20})`});icon.append(artwork);
                    artwork.append(svg('rect',{width:20,height:20,rx:3,class:'st-map-icon-bg'}));
                    const fallback=svg('text',{x:10,y:14,'text-anchor':'middle',class:'st-map-icon-fallback'},item.overflow?item.label:(item.symbol||'?'));artwork.append(fallback);
                    if(item.alliance_id){const image=svg('image',{x:1,y:1,width:18,height:18,href:`https://images.evetech.net/alliances/${Number(item.alliance_id)}/logo?size=64`});image.addEventListener('error',()=>image.remove());artwork.append(image);}
                    if(item.type_id){const image=svg('image',{x:item.alliance_id?10:1,y:item.alliance_id?10:1,width:item.alliance_id?12:18,height:item.alliance_id?12:18,href:`https://images.evetech.net/types/${Number(item.type_id)}/icon?size=64`});image.addEventListener('error',()=>image.remove());artwork.append(image);}
                    if(item.count>1){artwork.append(svg('rect',{x:9,y:11,width:Math.max(12,String(item.count).length*6+4),height:12,rx:3,class:'st-map-count-bg'}),svg('text',{x:11,y:20,class:'st-map-count'},item.count));}
                    if(item.overflow||item.action){icon.setAttribute('data-indicator-action','true'); const act=()=>{this.select(node.id);this.options.onIndicator?.(node.id,item);};icon.addEventListener('click',e=>{e.stopPropagation();if(this.suppressClick?.id===item.id && Date.now()<this.suppressClick.until){this.suppressClick=null;return;}act();});icon.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();e.stopPropagation();act();}});}
                    if(item.draggable){icon.classList.add('is-draggable');icon.addEventListener('pointerdown',event=>this.startTokenDrag(event,node,item,icon,size));}
                    this.hint(icon,(item.tooltip||item.label)+(item.draggable?' · Drag to another system; click or Enter to edit.':''));grid.append(icon);
                    bottom=Math.max(bottom,iy+size+8);top=Math.max(top,-iy+8);extent=Math.max(extent,Math.abs(ix)+size+8);
                });
                if(this.showLabels){slots.forEach(({item},i)=>{const text=svg('text',{x:0,y:bottom+14+i*16,'text-anchor':'middle',class:'st-map-indicator-label'},`${item.label}${item.count?' ×'+item.count:''}`);grid.append(text);extent=Math.max(extent,text.getComputedTextLength()/2+8);});bottom+=slots.length*16+16;}
                this.nodes.set(node.id,{x,y,width,height});xmin=Math.min(xmin,x-extent);xmax=Math.max(xmax,x+extent);ymin=Math.min(ymin,y-top);ymax=Math.max(ymax,y+bottom);
            });
            const pairs=new Map();
            (this.data.edges||[]).filter(e=>e.kind!=='gate'||this.showGates).forEach(edge=>{const key=[edge.source,edge.target].sort((a,b)=>a-b).join('-');if(!pairs.has(key))pairs.set(key,[]);pairs.get(key).push(edge);});
            pairs.forEach(edges=>edges.sort((a,b)=>String(a.id).localeCompare(String(b.id))).forEach((edge,index)=>{
                const a=this.nodes.get(edge.source),b=this.nodes.get(edge.target);if(!a||!b)return;
                const lane=(edge.curve||0)+(index-(edges.length-1)/2)*(edge.source<edge.target?1:-1);
                const color=edge.color||'var(--st-map-gate, var(--bs-secondary-color))';
                const path=svg('path',{d:connectionPath(a,b,lane),fill:'none',stroke:color,'stroke-width':edge.width||1,'vector-effect':'non-scaling-stroke',class:`st-map-edge${edge.crossing?' st-map-edge-'+edge.crossing:''}`});
                if(edge.dashed)path.setAttribute('stroke-dasharray','6 4');
                if(edge.directed){const id=`${this.uid}-arrow-${defs.childElementCount}`;const marker=svg('marker',{id,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:8,markerHeight:8,orient:'auto-start-reverse',markerUnits:'userSpaceOnUse'});marker.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:color}));defs.append(marker);path.setAttribute('marker-end',`url(#${id})`);}
                if(edge.tooltip)this.hint(path,edge.tooltip);layers[edge.kind==='gate'?0:1].append(path);
                if(edge.label)layers[1].append(svg('text',{x:(a.x+b.x)/2,y:(a.y+b.y)/2+lane*12,class:'st-map-edge-label'},edge.label));
            }));
            this.bounds=this.nodes.size?{x:xmin-24,y:ymin-24,width:Math.max(100,xmax-xmin+48),height:Math.max(100,ymax-ymin+48)}:{x:0,y:0,width:400,height:300};
            if(!this.box)this.fit();else this.applyView();
            if(focused){const target=Array.from(this.svg.querySelectorAll('[data-focus-key]')).find(e=>e.dataset.focusKey===focused);target?.focus({preventScroll:true});}
        }
        destroy(){this.finishTokenDrag(true);this.announcement.remove();this.resizeObserver.disconnect();this.svg.remove();this.tooltip.remove();}
    }
    if(typeof module!=='undefined'&&module.exports)module.exports={boundary,connectionPath,indicatorSlots,tokenSlots,dropTarget};
    else root.StructureSystemMap=SystemMap;
})(typeof window!=='undefined'?window:globalThis);
