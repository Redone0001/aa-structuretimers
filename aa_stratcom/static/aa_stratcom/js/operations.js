/* Operation selection and conditional live updates; timer permissions stay server-side. */
(function () {
    'use strict';
    const root = document.getElementById('operation-board');
    if (!root) {
        const index = document.getElementById('operation-index');
        if (index) watchIndex(index);
        return;
    }
    const editor = root.dataset.editor === 'true';
    const selected = new Set(JSON.parse(document.getElementById('operation-selected-ids').textContent).map(Number));
    const table = () => $.fn.dataTable.isDataTable('#tbl_timers_current') ? $('#tbl_timers_current').DataTable() : null;
    const status = document.getElementById('operation-sync-status');
    const busyTimers = new Set();
    let latest = null, etag = '', schema = '', pending = null, offset = 0, stopped = false;
    const state = timer => window.StructureBattleTimeline.stateAt(timer, Date.now() + offset);
    const names = {reinforced: 'REINFORCED · armor destroyed', upcoming: 'Upcoming', open: 'OPEN', paused: 'PAUSED', won: 'WON · hostile killed', lost: 'LOST · friendly killed', killed: 'KILLED', repaired: 'Repaired (estimated)', unanchored: 'Unanchored', occurred: 'Occurred'};
    function syncSelection() {
        if (!editor) return;
        const rows = table()?.rows({search: 'applied'}).data().toArray() || [];
        const checked = rows.filter(row => selected.has(Number(row.id))).length;
        const all = document.getElementById('operation-select-all');
        if (all) { all.checked = rows.length > 0 && checked === rows.length; all.indeterminate = checked > 0 && checked < rows.length; all.disabled = rows.length === 0; }
        $('#tbl_timers_current .operation-select').each(function () { this.checked = selected.has(Number(this.dataset.timerId)); });
        document.getElementById('operation-selection-count').textContent = `${selected.size} timer(s) selected`;
    }
    window.OperationSelection = {
        enabled: editor,
        has: id => selected.has(Number(id)),
    };
    window.OperationLive = {
        enabled: !editor,
        timerHtml(timer) {
            const current = state(timer);
            const seconds = current.seconds;
            const clock = seconds === undefined || seconds === null ? '' : ` · ${Math.floor(seconds / 60)}m ${seconds % 60}s`;
            return `<span class="badge ${current.name === 'paused' ? 'text-bg-info' : 'text-bg-secondary'}">${names[current.name] || 'Updated'}${clock}</span>`;
        },
        controls(timer) {
            if (!timer.can_edit) return '';
            const current = state(timer).name;
            const actions = [];
            if (current === 'open') actions.push(['pause', 'Pause']);
            if (current === 'paused') actions.push(['resume', 'Resume'], ['adjust', '−1 min', -60], ['adjust', '+1 min', 60]);
            actions.push(...window.StructureBattleTimeline.completionActions(timer, current));
            return '<div class="d-flex flex-wrap gap-1 mt-1">' + actions.map(([action, label, seconds]) =>
                `<button type="button" class="btn btn-sm btn-outline-secondary" data-op-action="${action}" data-timer-id="${Number(timer.id)}"${busyTimers.has(Number(timer.id)) ? ' disabled' : ''}${seconds ? ` data-seconds="${seconds}"` : ''}>${label}</button>`).join('') + '</div>';
        },
        controlKey(timer) { return [state(timer).name, timer.can_edit, timer.revision].join(':'); },
    };
    function applyObservations() {
        const current = table();
        if (!current || !latest) return;
        const byId = new Map(latest.timers.map(timer => [timer.id, timer]));
        current.rows().every(function () {
            const row = this.data(), timer = byId.get(row.id);
            if (timer) {
                row.battle = timer;
                current.cell(this.index(), 1).data(row.local_time);
                current.cell(this.index(), 8).data(row.actions);
                row._controlKey = window.OperationLive.controlKey(timer);
            }
        });
    }
    function synchronized() {
        status.className = 'small text-muted';
        status.textContent = `Live · checked ${new Date(Date.now() + offset).toISOString().slice(11, 19)} UTC · updates every 2 seconds`;
    }
    function clearDenied() {
        stopped = true;
        table()?.clear().draw();
        window.OperationRoster?.render({roles: [], participants: [], people: {}});
        document.getElementById('operation-name').textContent = 'Operation unavailable';
        document.getElementById('operation-description').textContent = '';
        document.getElementById('operation-edit-link').hidden = true;
        window.dispatchEvent(new CustomEvent('st:timer-sync', {detail: {timers: []}}));
    }
    async function poll(force = false) {
        if (editor || stopped || document.hidden || pending) return;
        const controller = new AbortController(); pending = controller;
        try {
            const response = await fetch(root.dataset.liveUrl, {credentials: 'same-origin', signal: controller.signal,
                headers: {Accept: 'application/json', ...(!force && etag ? {'If-None-Match': etag} : {})}});
            if ([401, 403, 404].includes(response.status)) { clearDenied(); throw new Error('Operation no longer available to this account.'); }
            if (response.status === 304) { synchronized(); return; }
            if (!response.ok || response.redirected) throw new Error('Live updates unavailable. Reconnecting…');
            const data = await response.json();
            etag = response.headers.get('ETag') || '';
            offset = Date.parse(data.server_time) - Date.now();
            const nextSchema = JSON.stringify([data.operation.revision, data.operation.can_edit, data.timers.map(timer => [timer.id, timer.updated_at, timer.destroyed_at, timer.date, timer.system_id, timer.name, timer.type_id, timer.relationship, timer.timer_type])]);
            latest = data;
            window.OperationRoster?.render(data.operation.roster || {roles: [], participants: [], people: {}});
            document.getElementById('operation-name').textContent = data.operation.name;
            document.getElementById('operation-description').textContent = data.operation.description;
            document.getElementById('operation-visibility').textContent = data.operation.visibility;
            document.getElementById('operation-edit-link').hidden = !data.operation.can_edit;
            if (schema && schema !== nextSchema) table()?.ajax.reload(() => $('#tbl_timers_current').trigger('st:rebuild-filters'), false);
            schema = nextSchema;
            applyObservations();
            window.dispatchEvent(new CustomEvent('st:timer-sync', {detail: data}));
            synchronized();
        } catch (error) {
            if (error.name !== 'AbortError') { status.className = 'small text-danger'; status.textContent = error.message; }
        } finally { if (pending === controller) pending = null; }
    }
    $(function () {
        $('#tbl_timers_current').on('draw.dt', () => { syncSelection(); applyObservations(); });
        if (editor) {
            $('#tbl_timers_current').on('change', '.operation-select', function () {
                if (this.checked) selected.add(Number(this.dataset.timerId)); else selected.delete(Number(this.dataset.timerId));
                syncSelection();
            });
            function selectFiltered(value) {
                for (const row of table()?.rows({search: 'applied'}).data().toArray() || []) {
                    if (value) selected.add(Number(row.id)); else selected.delete(Number(row.id));
                }
                syncSelection();
            }
            $('#operation-select-filtered').on('click', () => selectFiltered(true));
            $('#operation-clear-filtered').on('click', () => selectFiltered(false));
            $('#operation-select-all').on('change', function () { selectFiltered(this.checked); });
            document.getElementById('operation-form').addEventListener('submit', event => {
                if (!selected.size) { event.preventDefault(); document.getElementById('operation-selection-count').textContent = 'Select at least one timer.'; return; }
                const inputs = document.getElementById('operation-timer-inputs'); inputs.replaceChildren();
                for (const id of selected) { const input = document.createElement('input'); input.type = 'hidden'; input.name = 'timers'; input.value = id; inputs.append(input); }
                document.getElementById('operation-save').disabled = true;
            });
            window.addEventListener('pageshow', () => { document.getElementById('operation-save').disabled = false; });
            syncSelection(); return;
        }
        $('#tbl_timers_current').on('click', '[data-op-action]', async function () {
            const button = this, id = Number(button.dataset.timerId);
            const timer = table()?.rows().data().toArray().find(row => row.id === id)?.battle;
            if (!timer || busyTimers.has(id)) return;
            busyTimers.add(id);
            $(`#tbl_timers_current [data-timer-id="${id}"]`).prop('disabled', true);
            try {
                let reinforcement = {};
                if (button.dataset.opAction === 'reinforce') {
                    reinforcement = await window.StructureBattleTimeline.reinforcementPrompt(async () => {
                        const url = new URL(root.dataset.reinforcementUrl, location.origin);
                        url.searchParams.set('id', id); url.searchParams.set('operation', root.dataset.operationId);
                        const response = await fetch(url, {credentials: 'same-origin', headers: {Accept: 'application/json'}});
                        if (!response.ok || response.redirected) throw new Error('Unable to check for an existing reinforcement timer.');
                        return response.json();
                    });
                    if (reinforcement === null) return;
                }
                const observed = window.StructureBattleTimeline.observationForAction(timer, latest?.timers || []);
                const response = await fetch(root.dataset.battleUrl, {method: 'POST', credentials: 'same-origin',
                    headers: {'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('#timer-assignment-csrf input').value},
                    body: JSON.stringify({operation: Number(root.dataset.operationId), id, revision: observed.revision,
                        action: button.dataset.opAction, ...reinforcement, ...(button.dataset.seconds ? {seconds: Number(button.dataset.seconds)} : {})})});
                if (!response.ok) { const error = await response.json(); throw new Error(error.error || 'Could not update the timer.'); }
                pending?.abort(); pending = null; await poll(true);
            } catch (error) { status.className = 'small text-danger'; status.textContent = error.message; }
            finally {
                busyTimers.delete(id);
                $(`#tbl_timers_current [data-timer-id="${id}"]`).prop('disabled', false);
            }
        });
        poll(true);
        const interval = setInterval(poll, 2000);
        document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(true); else { pending?.abort(); pending = null; } });
        window.addEventListener('pagehide', event => { pending?.abort(); if (!event.persisted) clearInterval(interval); });
        window.addEventListener('pageshow', event => { if (event.persisted) poll(true); });
    });
    function watchIndex(index) {
        let tag = '', busy = false, controller;
        const message = document.getElementById('operation-index-status');
        async function refreshIndex() {
            if (document.hidden || !document.getElementById('ops').classList.contains('active') || busy) return;
            busy = true; controller = new AbortController();
            try {
                const response = await fetch(index.dataset.liveUrl, {credentials: 'same-origin', signal: controller.signal,
                    headers: {Accept: 'application/json', ...(tag ? {'If-None-Match': tag} : {})}});
                if (response.status === 304) { message.textContent = 'Live · updates every 2 seconds'; return; }
                if (!response.ok || response.redirected) {
                    if ([401, 403, 404].includes(response.status)) document.getElementById('operation-index-rows').replaceChildren();
                    throw new Error('Operations unavailable. Retrying…');
                }
                const data = await response.json(); tag = response.headers.get('ETag') || '';
                const body = document.getElementById('operation-index-rows'); body.replaceChildren();
                for (const operation of data.operations) {
                    const row = body.insertRow();
                    const link = document.createElement('a'); link.href = operation.url; link.textContent = operation.name; row.insertCell().append(link);
                    row.insertCell().textContent = operation.visibility;
                    row.insertCell().textContent = new Date(operation.updated_at).toISOString().slice(0, 16).replace('T', ' ');
                    const action = row.insertCell();
                    if (operation.edit_url) { const edit = document.createElement('a'); edit.href = operation.edit_url; edit.className = 'btn btn-sm btn-outline-secondary'; edit.textContent = 'Edit'; action.append(edit); }
                }
                if (!data.operations.length) { const cell = body.insertRow().insertCell(); cell.colSpan = 4; cell.textContent = 'No operations available.'; }
                const create = document.getElementById('operation-create'); if (create) create.hidden = !data.can_edit;
                message.textContent = 'Live · updates every 2 seconds';
            } catch (error) { if (error.name !== 'AbortError') message.textContent = error.message; }
            finally { busy = false; }
        }
        $(function () { setTimeout(refreshIndex, 0); });
        $(document).on('shown.bs.tab', refreshIndex);
        document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshIndex(); });
        const interval = setInterval(refreshIndex, 2000);
        window.addEventListener('pagehide', event => { controller?.abort(); if (!event.persisted) clearInterval(interval); });
    }
})();
