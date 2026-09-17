/* Poll a tiny status endpoint, rather than reloading a page while a user works. */
(function () {
    "use strict";
    const status = document.getElementById("campaign-import-status");
    if (!status || (status.dataset.import !== "pending" && status.dataset.gates !== "pending")) return;
    async function poll() {
        if (document.hidden) { window.setTimeout(poll, 5000); return; }
        try {
            const response = await fetch(status.dataset.url, {credentials: "same-origin"});
            if (!response.ok) throw new Error("Status request failed");
            const data = await response.json();
            if (data.import_status !== status.dataset.import || data.gates_status !== status.dataset.gates) {
                if (!document.querySelector('input[name="systems"]:checked')) window.location.reload();
                else document.getElementById("campaign-import-changed").hidden = false;
                return;
            }
        } catch (_) { /* Transient errors must not interrupt work. */ }
        window.setTimeout(poll, 5000);
    }
    window.setTimeout(poll, 5000);
}());
