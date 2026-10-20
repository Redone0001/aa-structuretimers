/* Background data updates never reload the page or replace an edit form. */
(function () {
    'use strict';
    function every(callback, {interval = 60000, active = () => true} = {}) {
        let busy = false;
        async function run() {
            if (document.hidden || busy || !active()) return;
            busy = true;
            try { await callback(); } catch (_) { /* Retry at the next interval. */ }
            finally { busy = false; }
        }
        const timer = setInterval(run, interval);
        document.addEventListener('visibilitychange', run);
        window.addEventListener('pagehide', event => {
            if (!event.persisted) {
                clearInterval(timer);
                document.removeEventListener('visibilitychange', run);
            }
        });
        return run;
    }
    function preference(key, value) {
        try {
            if (value !== undefined) {
                document.cookie = key + '=' + (value ? '1' : '0') + '; Max-Age=31536000; Path=' + location.pathname +
                    '; SameSite=Lax' + (location.protocol === 'https:' ? '; Secure' : '');
                return value;
            }
            return document.cookie.split('; ').includes(key + '=1');
        } catch (_) { return false; }
    }
    window.StratComRefresh = {every, preference};
    every(async () => {
        const visible = [...document.querySelectorAll('[data-stratcom-refresh]')]
            .filter(node => node.getClientRects().length && !node.contains(document.activeElement));
        if (!visible.length) return;
        const response = await fetch(location.href, {credentials: 'same-origin', cache: 'no-store'});
        if (!response.ok || response.redirected) return;
        const page = new DOMParser().parseFromString(await response.text(), 'text/html');
        for (const node of visible) {
            const updated = page.getElementById(node.id);
            if (updated && !node.contains(document.activeElement)) node.replaceChildren(...updated.childNodes);
        }
        document.getElementById('standings-search')?.dispatchEvent(new Event('input'));
    });
})();
