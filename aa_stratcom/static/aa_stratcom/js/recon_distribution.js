/* Pure calculations shared by the recon dashboard and its tests. */
const ReconDistribution = (() => {
    const DAY = 24 * 60;
    const DAY_MS = DAY * 60 * 1000;
    function distribution(rows) {
        const bins = Array(48).fill(0);
        let missing = 0;
        rows.forEach(row => {
            if (!row.reinforcement_time) { missing++; return; }
            const [hours, minutes] = row.reinforcement_time.split(':').map(Number);
            const center = hours * 60 + minutes;
            const radius = row.window_minutes;
            // Adjacent days allow windows crossing midnight to wrap around.
            for (let i = 0; i < bins.length; i++) {
                for (const shift of [-DAY, 0, DAY]) {
                    const overlap = Math.max(0, Math.min(i * 30 + 30, center + radius + shift)
                        - Math.max(i * 30, center - radius + shift));
                    bins[i] += overlap / 30;
                }
            }
        });
        return {bins, missing};
    }
    function minutes(value) {
        if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(value || '')) return null;
        const [hours, mins] = value.split(':').map(Number);
        return hours * 60 + mins;
    }
    function overlaps(row, start, end) {
        const center = minutes(row.reinforcement_time);
        if (center === null || !Number.isFinite(row.window_minutes)) return false;
        if (end < start) end += DAY;
        return [-DAY, 0, DAY].some(shift =>
            center + row.window_minutes + shift >= start && center - row.window_minutes + shift <= end);
    }
    function matchesWindow(row, from, to, bins = []) {
        const start = minutes(from), end = minutes(to);
        // Incomplete ranges do not filter. Equal endpoints select that instant.
        if (start !== null && end !== null && !overlaps(row, start, end)) return false;
        return !bins.length || bins.some(bin => overlaps(row, bin * 30, bin * 30 + 30));
    }
    function matchesFreshness(row, filters, now = Date.now()) {
        const updated = Date.parse(row.last_updated_at);
        const age = now - updated;
        if (filters.age === 'stale' && age <= 30 * DAY_MS) return false;
        if (filters.age === 'recent30' && age > 30 * DAY_MS) return false;
        if (filters.age === 'recent7' && age > 7 * DAY_MS) return false;
        if (filters.from && updated < Date.parse(filters.from + 'T00:00:00Z')) return false;
        if (filters.to && updated >= Date.parse(filters.to + 'T00:00:00Z') + DAY_MS) return false;
        return true;
    }
    function summary(rows, now = Date.now()) {
        const dates = rows.map(row => Date.parse(row.last_updated_at));
        const oldest = dates.length ? Math.min(...dates) : null;
        return {count: rows.length, oldest, latest: dates.length ? Math.max(...dates) : null,
            stale: oldest !== null && now - oldest > 30 * DAY_MS};
    }
    return {distribution, matchesFreshness, summary, matchesWindow};
})();
if (typeof module !== 'undefined') module.exports = ReconDistribution;
