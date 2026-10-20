const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');

function fixture() {
    const handlers = new Map(), pageHandlers = [], queued = [];
    const document = {querySelector: () => ({id: 'past'}), getElementById: () => null};
    const $ = target => ({
        ready() {},
        on(event, callback) {
            if (!handlers.has(target)) handlers.set(target, {});
            handlers.get(target)[event] = callback;
        },
    });
    const context = {document, $, window: {addEventListener: (event, callback) => pageHandlers.push(callback)},
        setTimeout: callback => queued.push(callback)};
    vm.runInNewContext(fs.readFileSync(require.resolve('../../static/aa_stratcom/js/timer_list.js'), 'utf8'), context);
    const node = {}, request = {readyState: 4};
    let loads = 0, rebuilds = 0, adjusts = 0, callback;
    const table = {
        table: () => ({node: () => node}), settings: () => [{jqXHR: request}],
        ajax: {reload(done, resetPaging) {
            assert.equal(resetPaging, false);
            loads++; request.readyState = 1; callback = done;
        }},
        columns: {adjust: () => adjusts++},
    };
    context.refreshTableOnTab(table, ['past'], () => rebuilds++);
    return {
        show(pane) { handlers.get('button[data-bs-toggle="tab"]')['shown.bs.tab']({target: {getAttribute: () => '#' + pane}}); },
        pageshow(persisted) { pageHandlers.forEach(handler => handler({persisted})); },
        finish() {
            request.readyState = 4;
            handlers.get(node)['xhr.dt']();
            if (callback) { const done = callback; callback = null; done(); }
            while (queued.length) queued.shift()();
        },
        busy() { request.readyState = 1; },
        aborted() { request.readyState = 0; },
        counts: () => ({loads, rebuilds, adjusts}),
    };
}

test('switching to the table tab reloads rows and filters without resetting pagination', () => {
    const f = fixture();
    f.show('current'); assert.equal(f.counts().loads, 0);
    f.show('past'); assert.equal(f.counts().loads, 1);
    f.finish(); assert.deepEqual(f.counts(), {loads: 1, rebuilds: 1, adjusts: 1});
    f.show('past'); f.finish(); assert.equal(f.counts().loads, 2);
});

test('a tab change during an earlier request queues one fresh load after it', () => {
    const f = fixture(); f.busy(); f.show('past'); f.show('past');
    assert.equal(f.counts().loads, 0);
    f.finish(); assert.equal(f.counts().loads, 1);
    f.finish(); assert.equal(f.counts().rebuilds, 1);
});

test('returning from browser history refreshes the active tab only', () => {
    const f = fixture(); f.pageshow(false); assert.equal(f.counts().loads, 0);
    f.pageshow(true); assert.equal(f.counts().loads, 1);
    f.finish(); assert.equal(f.counts().rebuilds, 1);
});

test('an aborted request does not block a fresh load after browser Back', () => {
    const f = fixture(); f.aborted(); f.pageshow(true);
    assert.equal(f.counts().loads, 1);
    f.finish(); assert.equal(f.counts().rebuilds, 1);
});
