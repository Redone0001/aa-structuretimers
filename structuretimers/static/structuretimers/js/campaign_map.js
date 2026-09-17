/* Region maps share the list's form checkboxes, keeping POST authorization unchanged. */
(function () {
    "use strict";

    function layout(systems, links = [], fixed = null) {
        if (fixed) {
            const all = Object.values(fixed.positions);
            // One uniform scale preserves the source layout while leaving room for labels.
            let scale = fixed.scale || 3;
            for (let i = 0; !fixed.scale && i < all.length; i += 1) {
                for (let j = i + 1; j < all.length; j += 1) {
                    const dx = Math.abs(all[i][0] - all[j][0]), dy = Math.abs(all[i][1] - all[j][1]);
                    if (dx || dy) scale = Math.max(scale, Math.min(dx ? 140 / dx : Infinity, dy ? 66 / dy : Infinity));
                }
            }
            const width = Math.max(160, fixed.width * scale + 160);
            let missing = 0;
            const nodes = systems.map(system => {
                const position = fixed.positions[system.name];
                if (position) return {...system, px: position[0] * scale + 80, py: position[1] * scale + 42};
                const index = missing++, columns = Math.max(1, Math.floor(width / 152));
                return {...system, px: (index % columns) * 152 + 80, py: fixed.height * scale + 140 + Math.floor(index / columns) * 82};
            });
            return {nodes, width, height: Math.max(fixed.height * scale + 84, ...nodes.map(n => n.py + 42)), missing};
        }
        const byId = new Map(systems.map(system => [system.id, system]));
        const adjacency = new Map(systems.map(system => [system.id, new Set()]));
        links.forEach(([a, b]) => {
            if (a !== b && byId.has(a) && byId.has(b)) { adjacency.get(a).add(b); adjacency.get(b).add(a); }
        });
        const visited = new Set(), components = [];
        systems.forEach(system => {
            if (visited.has(system.id)) return;
            const ids = [system.id];
            visited.add(system.id);
            for (let i = 0; i < ids.length; i += 1) {
                adjacency.get(ids[i]).forEach(id => { if (!visited.has(id)) { visited.add(id); ids.push(id); } });
            }
            components.push(ids);
        });
        // Layout each connected component independently using gate attraction and node repulsion.
        // EVE coordinates deliberately have no influence on this schematic.
        const clusters = components.map(ids => {
            const index = new Map(ids.map((id, i) => [id, i]));
            const edges = [];
            ids.forEach((id, i) => adjacency.get(id).forEach(other => { if (index.get(other) > i) edges.push([i, index.get(other)]); }));
            const radius = 90 * Math.sqrt(ids.length);
            const points = ids.map((id, i) => ({...byId.get(id), px: Math.cos(i * 2 * Math.PI / ids.length) * radius, py: Math.sin(i * 2 * Math.PI / ids.length) * radius}));
            for (let iteration = 0; iteration < 220; iteration += 1) {
                const forces = points.map(() => ({x: 0, y: 0}));
                for (let i = 0; i < points.length; i += 1) {
                    for (let j = i + 1; j < points.length; j += 1) {
                        const dx = points[i].px - points[j].px || 0.01;
                        const dy = points[i].py - points[j].py || 0.01;
                        const distance = Math.max(1, Math.hypot(dx, dy));
                        const strength = 2400 / (distance * distance);
                        forces[i].x += dx * strength; forces[i].y += dy * strength;
                        forces[j].x -= dx * strength; forces[j].y -= dy * strength;
                    }
                }
                edges.forEach(([i, j]) => {
                    const dx = points[i].px - points[j].px, dy = points[i].py - points[j].py;
                    const distance = Math.max(1, Math.hypot(dx, dy));
                    const strength = (distance - 155) * 0.35 / distance;
                    forces[i].x -= dx * strength; forces[i].y -= dy * strength;
                    forces[j].x += dx * strength; forces[j].y += dy * strength;
                });
                const temperature = 24 * (1 - iteration / 220) + 0.2;
                points.forEach((point, i) => {
                    const force = forces[i];
                    force.x -= point.px * 0.015; force.y -= point.py * 0.015;
                    const length = Math.max(1, Math.hypot(force.x, force.y));
                    const step = Math.min(temperature, length) / length;
                    point.px += force.x * step; point.py += force.y * step;
                });
            }
            const spanX = Math.max(...points.map(p => p.px)) - Math.min(...points.map(p => p.px));
            const spanY = Math.max(...points.map(p => p.py)) - Math.min(...points.map(p => p.py));
            points.forEach(point => {
                const x = point.px, y = point.py;
                point.px = (spanY > spanX ? y : x) * 0.7;
                point.py = (spanY > spanX ? x : y) * 0.7;
            });
            // Snap to nearby free cells so all names remain readable, even at dense junctions.
            const occupied = new Set();
            points.forEach(point => {
                const wantedX = Math.round(point.px / 152), wantedY = Math.round(point.py / 82);
                let cell;
                for (let radius = 0; !cell; radius += 1) {
                    const candidates = [];
                    for (let dy = -radius; dy <= radius; dy += 1) {
                        for (let dx = -radius; dx <= radius; dx += 1) {
                            if (Math.max(Math.abs(dx), Math.abs(dy)) !== radius) continue;
                            const x = wantedX + dx, y = wantedY + dy;
                            if (!occupied.has(`${x},${y}`)) candidates.push({x, y, distance: Math.hypot(x * 152 - point.px, y * 82 - point.py)});
                        }
                    }
                    candidates.sort((a, b) => a.distance - b.distance);
                    cell = candidates[0];
                }
                occupied.add(`${cell.x},${cell.y}`);
                point.px = cell.x * 152; point.py = cell.y * 82;
            });
            const left = Math.min(...points.map(p => p.px)), top = Math.min(...points.map(p => p.py));
            points.forEach(p => { p.px += 80 - left; p.py += 42 - top; });
            return {nodes: points, width: Math.max(...points.map(p => p.px)) + 80, height: Math.max(...points.map(p => p.py)) + 42};
        }).sort((a, b) => b.nodes.length - a.nodes.length);
        const targetWidth = Math.max(640, Math.sqrt(clusters.reduce((area, c) => area + c.width * c.height, 0) * 1.6));
        let x = 0, y = 0, rowHeight = 0, width = 160;
        const nodes = [];
        clusters.forEach(cluster => {
            if (x > 0 && x + cluster.width > targetWidth) { x = 0; y += rowHeight + 40; rowHeight = 0; }
            cluster.nodes.forEach(node => nodes.push({...node, px: node.px + x, py: node.py + y}));
            width = Math.max(width, x + cluster.width);
            x += cluster.width + 40;
            rowHeight = Math.max(rowHeight, cluster.height);
        });
        return {nodes, width, height: Math.max(84, y + rowHeight)};
    }

    if (typeof module !== "undefined" && module.exports) module.exports = {layout};
    if (typeof document === "undefined") return;
    const source = document.getElementById("campaign-map-data");
    if (!source) return;
    let regions = [];
    const container = document.getElementById("campaign-region-maps");
    const list = document.getElementById("campaign-list-view");
    const map = document.getElementById("campaign-map-view");
    const controls = document.getElementById("campaign-view-controls");
    const inputs = Array.from(list.querySelectorAll('input[name="systems"]'));
    const rendered = new Map();
    const storageKey = `campaign-view:${window.location.pathname}`;
    let initialized = false;
    let loading = false;

    function svgElement(tag, attrs, text) {
        const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
        Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
        if (text !== undefined) element.textContent = text;
        return element;
    }

    function sync() {
        inputs.forEach(input => {
            const node = rendered.get(input.id);
            if (node) node.setAttribute("aria-checked", String(input.checked));
        });
        document.getElementById("campaign-selected-count").textContent = inputs.filter(input => input.checked).length;
    }

    function enableMapPanning(viewport) {
        let pointer = null;
        let suppressClick = false;
        viewport.addEventListener("pointerdown", event => {
            if (event.pointerType !== "mouse" || event.button !== 0) return;
            suppressClick = false;
            pointer = {id: event.pointerId, x: event.clientX, y: event.clientY,
                left: viewport.scrollLeft, top: viewport.scrollTop, dragging: false};
        });
        viewport.addEventListener("pointermove", event => {
            if (!pointer || event.pointerId !== pointer.id) return;
            const dx = event.clientX - pointer.x, dy = event.clientY - pointer.y;
            if (!pointer.dragging && Math.hypot(dx, dy) < 5) return;
            if (!pointer.dragging) {
                pointer.dragging = true;
                suppressClick = true;
                viewport.setPointerCapture(event.pointerId);
                viewport.classList.add("is-panning");
            }
            event.preventDefault();
            viewport.scrollLeft = pointer.left - dx;
            viewport.scrollTop = pointer.top - dy;
        });
        function finish(event) {
            if (!pointer || event.pointerId !== pointer.id) return;
            pointer = null;
            viewport.classList.remove("is-panning");
            if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
        }
        viewport.addEventListener("pointerup", finish);
        viewport.addEventListener("pointercancel", finish);
        viewport.addEventListener("lostpointercapture", finish);
        viewport.addEventListener("pointerleave", event => {
            if (pointer && !pointer.dragging) finish(event);
        });
        // A drag starting on a system must never become a selection click on release.
        viewport.addEventListener("click", event => {
            if (suppressClick && event.detail !== 0) {
                event.preventDefault();
                event.stopPropagation();
                suppressClick = false;
            }
        }, true);
    }

    function renderRegion(region) {
        const drawing = layout(region.systems, region.links, region.layout);
        const section = document.createElement("section");
        section.className = "mb-4";
        const heading = document.createElement("h2");
        heading.className = "h4";
        heading.textContent = region.name;
        section.appendChild(heading);
        const sourceNote = document.createElement("p");
        sourceNote.className = "small text-muted";
        sourceNote.textContent = region.layout ? container.dataset.localLayoutLabel : container.dataset.fallbackLayoutLabel;
        if (drawing.missing) sourceNote.textContent += " " + container.dataset.unmappedLabel;
        section.appendChild(sourceNote);
        const toolbar = document.createElement("div");
        toolbar.className = "d-flex gap-2 mb-2";
        section.appendChild(toolbar);
        const viewport = document.createElement("div");
        viewport.className = "campaign-map-scroll";
        enableMapPanning(viewport);
        const svg = svgElement("svg", {viewBox: `0 0 ${drawing.width} ${drawing.height}`, class: "campaign-map-svg", role: "group", "aria-label": region.name});
        viewport.appendChild(svg);
        section.appendChild(viewport);
        container.appendChild(section);
        let scale = Math.max(0.65, Math.min(1, viewport.clientWidth / drawing.width));
        function zoom(value) {
            scale = Math.max(0.15, Math.min(3, value));
            svg.style.width = `${drawing.width * scale}px`;
            svg.style.height = `${drawing.height * scale}px`;
        }
        [["zoomOutLabel", () => zoom(scale / 1.25)], ["zoomInLabel", () => zoom(scale * 1.25)], ["fitLabel", () => zoom(viewport.clientWidth / drawing.width)]].forEach(([label, action]) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "btn btn-sm btn-outline-secondary";
            button.textContent = container.dataset[label];
            button.addEventListener("click", action);
            toolbar.appendChild(button);
        });
        zoom(scale);
        const positions = new Map(drawing.nodes.map(node => [node.id, node]));
        region.links.forEach(([from, to]) => {
            const a = positions.get(from), b = positions.get(to);
            if (a && b) svg.appendChild(svgElement("line", {x1: a.px, y1: a.py, x2: b.px, y2: b.py, class: "campaign-map-link", "aria-hidden": "true"}));
        });
        if (!region.links.length) {
            const note = document.createElement("p");
            note.className = "small text-muted mt-2";
            note.textContent = container.dataset.noLinksLabel;
            section.appendChild(note);
        }
        drawing.nodes.forEach(system => {
            const input = document.getElementById(`system-${system.entryId}`);
            if (!input) return; // A background import may finish before the status refresh.
            const label = `${system.name}, ${container.dataset.countLabel}: ${system.count}, ${container.dataset[`${system.status}Label`]}`;
            const node = svgElement("g", {transform: `translate(${system.px},${system.py})`, class: "campaign-map-node", "data-status": system.status, "data-entry-id": system.entryId, tabindex: "0", role: "checkbox", "aria-checked": String(input.checked), "aria-label": label});
            node.appendChild(svgElement("title", {}, label));
            node.appendChild(svgElement("rect", {x: -62, y: -25, width: 124, height: 50, rx: 7}));
            node.appendChild(svgElement("text", {x: 0, y: -3, "text-anchor": "middle", class: "map-name"}, system.name));
            node.appendChild(svgElement("text", {x: 0, y: 16, "text-anchor": "middle", class: "map-count"}, String(system.count)));
            node.appendChild(svgElement("text", {x: 47, y: 16, class: "map-check", "aria-hidden": "true"}, "✓"));
            function toggle() { if (input.disabled) return; input.checked = !input.checked; input.dispatchEvent(new Event("change", {bubbles: true})); }
            node.addEventListener("click", toggle);
            node.addEventListener("keydown", event => {
                if (event.key === " " || event.key === "Enter") { event.preventDefault(); toggle(); }
            });
            rendered.set(input.id, node);
            svg.appendChild(node);
        });
    }

    function show(view) {
        list.hidden = view === "map";
        map.hidden = view !== "map";
        controls.querySelectorAll("[data-campaign-view]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.campaignView === view)));
        if (view === "map" && !initialized && !loading) {
            loading = true;
            container.textContent = container.dataset.loadingLabel;
            fetch(source.dataset.url, {credentials: "same-origin", headers: {Accept: "application/json"}})
                .then(response => { if (!response.ok) throw new Error("Map request failed"); return response.json(); })
                .then(data => {
                    regions = data;
                    container.textContent = "";
                    regions.forEach(renderRegion);
                    initialized = true;
                    sync();
                })
                .catch(() => { container.textContent = container.dataset.errorLabel; })
                .finally(() => { loading = false; });
        }
        try { sessionStorage.setItem(storageKey, view); } catch (_) { /* Storage may be unavailable. */ }
        sync();
    }
    controls.hidden = false;
    controls.querySelectorAll("[data-campaign-view]").forEach(button => button.addEventListener("click", () => show(button.dataset.campaignView)));
    // Constellation handlers run first and update all underlying checkboxes.
    list.addEventListener("change", sync);
    window.addEventListener("pageshow", sync);
    let preferred = "list";
    try { preferred = sessionStorage.getItem(storageKey) || preferred; } catch (_) { /* Use list. */ }
    show(preferred === "map" ? "map" : "list");
}());
