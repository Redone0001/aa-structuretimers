/* Timer choices and EVE-time defaults shared by the Add information UI. */
(function (root) {
    'use strict';
    root.StructureTimerOptions = {
        choicesFor(event, typeId, rules) {
            const matches = key => (rules[key] || []).some(id => String(id) === String(typeId));
            if (event === 'anchoring') return ['AN'];
            if (event === 'reinforced') {
                return !typeId || matches('large') ? ['AR', 'FI'] : ['FI'];
            }
            return ['', 'NO', 'UA', ...(matches('refineries') ? ['MM'] : []), ...(matches('skyhooks') ? ['TF'] : [])];
        },
        unanchoringDate(current, now = new Date()) {
            if (current.trim()) return current;
            return new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000).toISOString().slice(0, 16).replace('T', ' ');
        },
    };
})(globalThis);
