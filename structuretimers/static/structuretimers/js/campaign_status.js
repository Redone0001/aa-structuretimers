/* A status change never interrupts map work or an in-progress selection. */
(function () {
    "use strict";
    const status = document.getElementById("campaign-import-status");
    if (!status) return;
    let timer = null;
    function schedule() {
        if (timer === null && status.dataset.import === "pending") {
            timer = window.setTimeout(poll, 5000);
        }
    }
    async function poll() {
        timer = null;
        if (document.hidden) { schedule(); return; }
        try {
            const response = await fetch(status.dataset.url, {credentials: "same-origin"});
            if (!response.ok) throw new Error("Status request failed");
            const data = await response.json();
            if (data.import_status !== status.dataset.import) {
                document.getElementById("campaign-import-changed").hidden = false;
                return;
            }
        } catch (_) { /* Retry without disrupting the current page. */ }
        schedule();
    }
    window.addEventListener("campaign:updated", schedule);
    schedule();
}());
