/* Keep the map, selection and scroll in place while the server authorizes each action. */
(function () {
    "use strict";
    const status = document.getElementById("campaign-action-status");
    if (!status) return;
    let busy = false;
    document.addEventListener("submit", async function (event) {
        const form = event.target;
        if (!form.matches("[data-campaign-action]")) return;
        event.preventDefault();
        if (busy) return;
        busy = true;
        const data = new FormData(form);
        if (event.submitter && event.submitter.name) data.set(event.submitter.name, event.submitter.value);
        const viewport = document.querySelector("main > .nav-padding");
        const scrollTop = viewport ? viewport.scrollTop : 0;
        const selected = new Set(Array.from(document.querySelectorAll('input[name="systems"]:checked'), el => el.value));
        const buttons = Array.from(document.querySelectorAll('[data-campaign-action] button:not(:disabled)'));
        buttons.forEach(button => { button.disabled = true; });
        status.classList.remove("text-danger");
        status.textContent = status.dataset.saving;
        try {
            const response = await fetch(form.getAttribute("action") || window.location.href, {method: "POST", body: data, credentials: "same-origin"});
            if (!response.ok) throw new Error("Request failed");
            const page = new DOMParser().parseFromString(await response.text(), "text/html");
            const updatedList = page.getElementById("campaign-list-view");
            if (!updatedList) throw new Error("Session expired or unexpected response");
            document.getElementById("campaign-list-view").replaceChildren(...updatedList.childNodes);
            for (const id of ["campaign-progress", "campaign-import-status"]) {
                const updated = page.getElementById(id);
                if (updated) {
                    const current = document.getElementById(id);
                    current.replaceChildren(...updated.childNodes);
                    Object.assign(current.dataset, updated.dataset);
                }
            }
            document.querySelectorAll('input[name="systems"]').forEach(input => { input.checked = selected.has(input.value); });
            window.dispatchEvent(new Event("campaign:updated"));
            const error = page.querySelector(".alert-danger");
            status.classList.toggle("text-danger", Boolean(error));
            status.textContent = error ? error.textContent.trim() : status.dataset.saved;
            if (viewport) viewport.scrollTop = scrollTop;
            if (event.submitter && !event.submitter.isConnected) {
                status.tabIndex = -1;
                status.focus({preventScroll: true});
            }
        } catch (_) {
            status.classList.add("text-danger");
            status.textContent = status.dataset.error;
        } finally {
            buttons.forEach(button => { button.disabled = false; });
            busy = false;
        }
    });
}());
