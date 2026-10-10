/* Feeds the shared map section with the active tab's filtered table rows. */
(function () {
    'use strict';
    const TABLES = {current: '#tbl_timers_current', database: '#tbl_manage_recon'};
    const MAP_TABS = {current: 'current', preliminary: 'database'};
    let mode = 'current';
    window.StructureMapSource = {
        get mode() { return mode; },
        rows() {
            const table = TABLES[mode];
            if (!$.fn.dataTable.isDataTable(table)) return [];
            return $(table).DataTable().rows({search: 'applied'}).data().toArray();
        },
    };
    let timer;
    function changed() {
        clearTimeout(timer);
        timer = setTimeout(() => window.StructureRegionalMap?.tableChanged(), 300);
    }
    $(function () {
        $(Object.values(TABLES).join(',')).on('draw.dt', function () {
            if (TABLES[mode] === '#' + this.id) changed();
        });
        function show(paneId) {
            const tab = MAP_TABS[paneId];
            document.getElementById('st-map-panel').hidden = !tab;
            if (tab && tab !== mode) {
                mode = tab;
                window.StructureRegionalMap?.modeChanged();
            }
        }
        $('button[data-bs-toggle="tab"]').on('shown.bs.tab', function (event) {
            show(event.target.getAttribute('data-bs-target').slice(1));
        });
        // The first tab may already have been shown before this ran.
        setTimeout(() => show(document.querySelector('.tab-content > .tab-pane.active')?.id), 0);
    });
})();
