/* Selection is shared with the map and survives inline campaign actions. */
(function () {
    "use strict";
    function syncSelection() {
        document.querySelectorAll("[data-constellation-group]").forEach(group => {
            const constellation = group.querySelector("[data-constellation-select]");
            const systems = Array.from(group.querySelectorAll('input[name="systems"]'));
            const count = systems.filter(system => system.checked).length;
            constellation.checked = count === systems.length && count > 0;
            constellation.indeterminate = count > 0 && count < systems.length;
        });
    }
    document.addEventListener("change", event => {
        if (event.target.matches("[data-constellation-select]")) {
            event.target.closest("[data-constellation-group]").querySelectorAll('input[name="systems"]:not(:disabled)').forEach(input => {
                input.checked = event.target.checked;
            });
        }
        syncSelection();
        window.dispatchEvent(new Event("campaign:selection"));
    });
    window.addEventListener("pageshow", syncSelection);
    window.addEventListener("campaign:updated", syncSelection);
    syncSelection();
}());
