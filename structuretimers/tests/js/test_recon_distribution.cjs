const assert = require('node:assert/strict');
const {distribution, summary, matchesFreshness} = require('../../static/structuretimers/js/recon_distribution.js');
const timer = (time, window = 180) => ({reinforcement_time: time, window_minutes: window});
// User's example: 10 midday timers and 2 evening timers.
let result = distribution([...Array(10).fill(timer('12:00')), ...Array(2).fill(timer('21:00'))]);
assert.equal(result.bins.length, 48);
result.bins.forEach((count, i) => assert.equal(count, i >= 18 && i < 30 ? 10 : i >= 36 ? 2 : 0));
// Midnight wrapping and narrow windows, including half-hour centers.
result = distribution([timer('00:00', 30)]);
assert.equal(result.bins[47], 1); assert.equal(result.bins[0], 1);
assert.equal(result.bins.reduce((a,b) => a+b), 2);
result = distribution([timer('12:30', 30)]);
assert.equal(result.bins[24], 1); assert.equal(result.bins[25], 1);
assert.equal(result.bins.reduce((a,b) => a+b), 2);
// Quarter-hour boundaries contribute proportional coverage.
result = distribution([timer('12:15', 30), timer(null)]);
assert.equal(result.bins[23], .5); assert.equal(result.bins[24], 1); assert.equal(result.bins[25], .5);
assert.equal(result.missing, 1);
assert.deepEqual(summary([]), {count: 0, oldest: null, latest: null, stale: false});
const now = Date.parse('2026-09-16T12:00:00Z');
const rows = ['2026-08-01T00:00:00Z', '2026-09-16T11:00:00Z'].map(last_updated_at => ({last_updated_at}));
const filtered = rows.filter(row => matchesFreshness(row, {age: 'stale'}, now));
assert.equal(filtered.length, 1); assert.equal(summary(filtered, now).stale, true);
assert.equal(summary(rows, now).latest, Date.parse(rows[1].last_updated_at));
assert.equal(matchesFreshness(rows[1], {from:'2026-09-16', to:'2026-09-16'}, now), true);
assert.equal(matchesFreshness(rows[1], {to:'2026-09-15'}, now), false);
const boundary = {last_updated_at: new Date(now - 30 * 86400000).toISOString()};
assert.equal(matchesFreshness(boundary, {age:'stale'}, now), false);
assert.equal(summary([boundary], now).stale, false);
assert.equal(matchesFreshness(boundary, {age:'recent30'}, now), true);
console.log('Distribution, midnight wrapping, special windows, freshness and summary checks passed.');
