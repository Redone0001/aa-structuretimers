$(document).ready(function () {
    const root = document.getElementById('recon-dashboard');
    if (!root) return;
    const exported = document.getElementById('dataExport');
    const messages = JSON.parse(document.getElementById('recon-translations').textContent);
    const formatMessage = (template, values) => template.replace(/%\((\w+)\)s/g, (_, key) => values[key]);
    const number = value => Number(value.toFixed(2)).toLocaleString(document.documentElement.lang || undefined);
    const status = $('#recon-status');
    const filters = () => ({age: $('#recon-age').val(), from: $('#recon-from').val(), to: $('#recon-to').val()});
    const stateKey = 'st-recon-settings-v2-' + root.dataset.userId;
    let saved = {}, ready = false;
    try {
        const cookie = document.cookie.split('; ').find(value => value.startsWith(stateKey + '='));
        saved = cookie ? JSON.parse(decodeURIComponent(cookie.slice(stateKey.length + 1))) : {};
        if (!saved || typeof saved !== 'object') saved = {};
    } catch (_) { /* Invalid or unavailable cookies use defaults. */ }
    $('#recon-age').val(saved.age || 'all');
    $('#recon-from').val(saved.from || '');
    $('#recon-to').val(saved.to || '');
    $('#recon-window-from').val(saved.windowFrom || '');
    $('#recon-window-to').val(saved.windowTo || '');
    const selectedBins = new Set((Array.isArray(saved.bins) ? saved.bins : []).filter(i => Number.isInteger(i) && i >= 0 && i < 48));
    function saveSettings() {
        if (!ready) return;
        const columns = {};
        for (let i = 8; i <= 12; i++) {
            columns[i] = $('#tbl_manage_recon_filterSelect' + i + '_menu input:checked').map(function () { return this.value; }).get();
        }
        const state = {...filters(), windowFrom: $('#recon-window-from').val(), windowTo: $('#recon-window-to').val(),
            bins: [...selectedBins], columns, search: table.search(), order: table.order(), length: table.page.len(), page: table.page()};
        try {
            document.cookie = stateKey + '=' + encodeURIComponent(JSON.stringify(state)) +
                '; Max-Age=31536000; Path=' + location.pathname + '; SameSite=Lax' + (location.protocol === 'https:' ? '; Secure' : '');
        } catch (_) { /* Filtering still works with cookies disabled. */ }
    }
    $.fn.dataTable.ext.search.push((settings, data, index, row) =>
        settings.nTable.id !== 'tbl_manage_recon' || (ReconDistribution.matchesFreshness(row, filters()) &&
            ReconDistribution.matchesWindow(row, $('#recon-window-from').val(), $('#recon-window-to').val(), [...selectedBins])));

    function timeLabel(minutes) {
        return String(Math.floor(minutes / 60)).padStart(2, '0') + ':' + String(minutes % 60).padStart(2, '0');
    }
    function updateDashboard(rows) {
        const summary = ReconDistribution.summary(rows);
        $('#recon-count').text(summary.count);
        for (const [id, date] of [['oldest', summary.oldest], ['latest', summary.latest]]) {
            $('#recon-' + id).text(date === null ? messages.noMatches : moment(date).utc().format('YYYY-MM-DD HH:mm') + ' UTC');
            $('#recon-' + id + '-age').text(date === null ? '' : moment(date).fromNow());
        }
        $('#recon-oldest').removeClass('text-danger text-success').addClass(
            summary.oldest === null ? '' : summary.stale ? 'text-danger' : 'text-success');
        if (summary.stale) $('#recon-oldest-age').append(' · ' + messages.stale);
        const {bins, missing} = ReconDistribution.distribution(rows);
        const peak = Math.max(...bins);
        const heatmap = $('#recon-heatmap');
        bins.forEach((count, i) => {
            const label = formatMessage(messages.overlap, {start: timeLabel(i * 30), end: timeLabel((i + 1) * 30), count: number(count)});
            let cell = heatmap.children().eq(i);
            if (!cell.length) {
                cell = $('<button>', {type: 'button', class: 'recon-heat-cell', 'data-bin': i}).appendTo(heatmap);
            }
            cell.attr({title: label, 'aria-label': label, 'aria-pressed': String(selectedBins.has(i))})
                .text(selectedBins.has(i) ? '✓' : '')
                .css('background-color', count ? `rgba(var(--bs-info-rgb), ${0.15 + 0.85 * count / peak})` : 'var(--bs-body-bg)');
        });
        $('#recon-peak').text(formatMessage(messages.peak, {count: number(peak)}));
        $('#recon-missing').text(formatMessage(messages.missing, {count: number(missing)}));
    }
    const table = $('#tbl_manage_recon').DataTable({
        language: messages.table,
        ajax: {url: root.dataset.url, dataSrc: '', cache: false,
            error: () => status.addClass('text-danger').text(messages.loadError)},
        columns: [
            {data: 'location'}, {data: 'distance', render: {_: 'display', sort: 'sort'}}, {data: 'structure_details'}, {data: 'name_objective'},
            {data: 'owner'},
            {data: 'reinforcement_time', className: 'text-nowrap', render: (data, type) => {
                if (type === 'sort' || type === 'type') {
                    return data ? Number(data.slice(0, 2)) * 60 + Number(data.slice(3, 5)) : 1440;
                }
                return data || '—';
            }},
            {data: 'last_updated_at', render: (data, type) => type === 'display' ? moment(data).utc().format('YYYY-MM-DD HH:mm') : data},
            {data: 'actions', orderable: false, searchable: false},
            {data: 'system_name', visible: false}, {data: 'region_name', visible: false},
            {data: 'structure_type_name', visible: false}, {data: 'owner_name', visible: false},
            {data: 'objective_name', visible: false}
        ],
        order: saved.order || [[6, 'asc']], pageLength: saved.length || 25,
        search: {search: saved.search || ''},
        lengthMenu: [[10, 25, 50, 100, -1], [10, 25, 50, 100, messages.all]],
        drawCallback: function () { updateDashboard(this.api().rows({search: 'applied'}).data().toArray()); saveSettings(); },
        initComplete: function () {
            initializeMultiSelectFilters(this.api(), {columns: [
                {idx: 8, title: messages.solarSystem}, {idx: 9, title: messages.region},
                {idx: 10, title: messages.structureType}, {idx: 11, title: messages.owner}, {idx: 12, title: messages.objective}
            ]}, exported.getAttribute('data-titleFilterBy'), exported.getAttribute('data-titleAll'),
            saved.columns || {});
            this.api().page(Math.min(saved.page || 0, Math.max(0, this.api().page.info().pages - 1))).draw(false);
            ready = true;
        }
    });
    $('#recon-age, #recon-from, #recon-to, #recon-window-from, #recon-window-to').on('change', () => table.draw());
    $('#recon-heatmap').on('click', 'button', function () {
        const bin = Number(this.dataset.bin);
        if (selectedBins.has(bin)) selectedBins.delete(bin); else selectedBins.add(bin);
        table.draw();
    });
    $('#recon-window-clear').on('click', () => {
        $('#recon-window-from, #recon-window-to').val('');
        selectedBins.clear();
        table.draw();
    });
    $('#recon-reset').on('click', function () {
        $('#recon-age').val('all');
        $('#recon-from, #recon-to, #recon-window-from, #recon-window-to').val('');
        selectedBins.clear();
        $('#tbl_manage_recon_wrapper .timer-filter-clear:not(:disabled)').trigger('click');
        table.search('').columns().search('');
        $('#recon-age').trigger('change');
    });
    $('#tbl_manage_recon').on('click', '.recon-action', async function () {
        const button = $(this);
        const destroy = button.attr('data-destroy') === 'true';
        if (destroy && !window.confirm(messages.confirmDestroy)) return;
        const buttons = button.closest('tr').find('.recon-action').prop('disabled', true);
        status.removeClass('text-danger').text(messages.saving);
        try {
            const response = await fetch(button.attr('data-url'), {
                method: 'POST', credentials: 'same-origin',
                headers: {'X-CSRFToken': $('#recon-csrf input').val(), 'Accept': 'application/json'}
            });
            if (!response.ok) throw new Error('Request failed');
            await response.json();
            status.text(destroy ? messages.removed : messages.refreshed);
            table.ajax.reload(null, false);
        } catch (_) {
            status.addClass('text-danger').text(messages.saveError);
        } finally { buttons.prop('disabled', false); }
    });
    $('button[data-bs-toggle="tab"]').on('shown.bs.tab', function (event) {
        const tab = event.target.getAttribute('data-bs-target').slice(1);
        const url = new URL(window.location.href);
        url.searchParams.set('tab', tab);
        window.history.replaceState(null, '', url);
        if (tab === 'preliminary') table.columns.adjust();
    });
});
