/* Constellation selection is a shortcut; only individual systems are submitted. */
(function () {
    "use strict";

    document.querySelectorAll("[data-constellation-group]").forEach(function (group) {
        const constellation = group.querySelector("[data-constellation-select]");
        const systems = Array.from(group.querySelectorAll('input[name="systems"]'));

        function syncSelection() {
            const selected = systems.filter(function (system) { return system.checked; }).length;
            constellation.checked = selected === systems.length && selected > 0;
            constellation.indeterminate = selected > 0 && selected < systems.length;
        }

        constellation.addEventListener("change", function () {
            systems.forEach(function (system) { system.checked = constellation.checked; });
            syncSelection();
        });
        systems.forEach(function (system) {
            system.addEventListener("change", syncSelection);
        });
        window.addEventListener("pageshow", syncSelection);
        syncSelection();
    });
}());
