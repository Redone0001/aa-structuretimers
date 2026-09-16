$(document).ready(function () {
    const root = document.getElementById('recon-dashboard');
    if (!root) return;
    const exported = document.getElementById('dataExport');
    const status = $('#recon-status');
    const filters = () => ({age: $('#recon-age').val(), from: $('#recon-from').val(), to: $('#recon-to').val()});
    const stateKey = 'structuretimers-recon-freshness';
    try {
        const saved = JSON.parse(sessionStorage.getItem(stateKey) || '{}');
        $('#recon-age').val(saved.age || 'all');
        $('#recon-from').val(saved.from || '');
        $('#recon-to').val(saved.to || '');
    } catch (_) { /* Storage may be disabled. */ }
    $.fn.dataTable.ext.search.push((settings, data, index, row) =>
        settings.nTable.id !== 'tbl_manage_recon' || ReconDistribution.matchesFreshness(row, filters()));

    function timeLabel(minutes) {
        return String(Math.floor(minutes / 60)).padStart(2, '0') + ':' + String(minutes % 60).padStart(2, '0');
    }
    function updateDashboard(rows) {
        const summary = ReconDistribution.summary(rows);
        $('#recon-count').text(summary.count);
        for (const [id, date] of [['oldest', summary.oldest], ['latest', summary.latest]]) {
            $('#recon-' + id).text(date === null ? 'No matching recon' : moment(date).utc().format('YYYY-MM-DD HH:mm') + ' UTC');
            $('#recon-' + id + '-age').text(date === null ? '' : moment(date).fromNow());
        }
        $('#recon-oldest').removeClass('text-danger text-success').addClass(
            summary.oldest === null ? '' : summary.stale ? 'text-danger' : 'text-success');
        if (summary.stale) $('#recon-oldest-age').append(' · Older than 30 days');
        const {bins, missing} = ReconDistribution.distribution(rows);
        const peak = Math.max(...bins);
        const heatmap = $('#recon-heatmap').empty();
        bins.forEach((count, i) => {
            const label = timeLabel(i * 30) + '–' + timeLabel((i + 1) * 30) + ' UTC: ' +
                Number(count.toFixed(2)) + ' overlapping timers (average over this half-hour)';
            $('<div>', {class: 'recon-heat-cell', tabindex: 0, role: 'img', title: label, 'aria-label': label})
                .css('background-color', count ? `hsl(210, 80%, ${86 - 56 * count / peak}%)` : 'var(--bs-secondary-bg, #e9ecef)')
                .appendTo(heatmap);
        });
        $('#recon-peak').text('Peak overlap: ' + Number(peak.toFixed(2)));
        $('#recon-missing').text(missing + ' without a reinforcement time · excluded from scale');
    }
    const table = $('#tbl_manage_recon').DataTable({
        ajax: {url: root.dataset.url, dataSrc: '', cache: false,
            error: () => status.addClass('text-danger').text('Could not load recon. Reload the page to try again.')},
        columns: [
            {data: 'location'}, {data: 'structure_details'}, {data: 'name_objective'},
            {data: 'owner'},
            {data: 'last_updated_at', render: (data, type) => type === 'display' ? moment(data).utc().format('YYYY-MM-DD HH:mm') : data},
            {data: 'actions', orderable: false, searchable: false},
            {data: 'system_name', visible: false}, {data: 'region_name', visible: false},
            {data: 'structure_type_name', visible: false}, {data: 'owner_name', visible: false},
            {data: 'objective_name', visible: false}
        ],
        order: [[4, 'asc']], pageLength: 25,
        lengthMenu: [[10, 25, 50, 100, -1], [10, 25, 50, 100, 'All']],
        drawCallback: function () { updateDashboard(this.api().rows({search: 'applied'}).data().toArray()); },
        initComplete: function () {
            initializeMultiSelectFilters(this.api(), {columns: [
                {idx: 6, title: 'Solar System'}, {idx: 7, title: 'Region'},
                {idx: 8, title: 'Structure Type'}, {idx: 9, title: 'Owner'}, {idx: 10, title: 'Objective'}
            ]}, exported.getAttribute('data-titleFilterBy'), exported.getAttribute('data-titleAll'),
            exported.getAttribute('data-isNightMode') === 'true');
        }
    });
    $('#recon-age, #recon-from, #recon-to').on('change', function () {
        try { sessionStorage.setItem(stateKey, JSON.stringify(filters())); } catch (_) { /* optional persistence */ }
        table.draw();
    });
    $('#recon-reset').on('click', function () {
        $('#recon-age').val('all');
        $('#recon-from, #recon-to').val('');
        $('#tbl_manage_recon_filterWrapper select').val(null).trigger('change');
        table.search('').columns().search('');
        $('#recon-age').trigger('change');
    });
    $('#tbl_manage_recon').on('click', '.recon-action', async function () {
        const button = $(this);
        const destroy = button.attr('data-destroy') === 'true';
        if (destroy && !window.confirm('Mark this structure as destroyed and remove its recon timer?')) return;
        const buttons = button.closest('tr').find('.recon-action').prop('disabled', true);
        status.removeClass('text-danger').text('Saving…');
        try {
            const response = await fetch(button.attr('data-url'), {
                method: 'POST', credentials: 'same-origin',
                headers: {'X-CSRFToken': $('#recon-csrf input').val(), 'Accept': 'application/json'}
            });
            if (!response.ok) throw new Error('Request failed');
            await response.json();
            status.text(destroy ? 'Recon removed.' : 'Recon refreshed to today.');
            table.ajax.reload(null, false);
            $('#tbl_preliminary').DataTable().ajax.reload(null, false);
        } catch (_) {
            status.addClass('text-danger').text('Could not save this change. Reload the page and try again.');
        } finally { buttons.prop('disabled', false); }
    });
    $('button[data-bs-toggle="tab"]').on('shown.bs.tab', function (event) {
        const tab = event.target.getAttribute('data-bs-target').slice(1);
        const url = new URL(window.location.href);
        url.searchParams.set('tab', tab);
        window.history.replaceState(null, '', url);
        if (tab === 'manage-recon') table.columns.adjust();
    });
});
