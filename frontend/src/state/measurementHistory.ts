import type { MeasurementEvent } from "../type/event.ts";

export type MeasurementHistory = Readonly<{
    length: number;
    fields: readonly string[];
    numericFields: readonly string[];
    start: number | undefined;
    end: number | undefined;
    at: (index: number) => MeasurementEvent | undefined;
    read: (start?: number, end?: number) => readonly MeasurementEvent[];
    range: (minimum: number) => readonly MeasurementEvent[];
    latest: (count: number) => readonly MeasurementEvent[];
}>;

/** 受信順に追記し、スナップショット作成時点の件数に読出し範囲を固定する。 */
export function createMeasurementHistory() {
    const events: MeasurementEvent[] = [];
    const keys = new Set<string>(), numeric = new Set<string>();
    let fields: readonly string[] = [], numericFields: readonly string[] = [];
    let ordered = true, first = Infinity, last = -Infinity;
    function snapshot(): MeasurementHistory {
        const length = events.length, chronological = ordered;
        const read = (start = 0, end = length) => events.slice(Math.max(0, start < 0 ? length + start : Math.min(start, length)),
            Math.max(0, end < 0 ? length + end : Math.min(end, length)));
        const range = (minimum: number) => {
            if (!chronological) return read().filter(event => event.elapsed_s >= minimum).sort((a, b) => a.elapsed_s - b.elapsed_s);
            let low = 0, high = length;
            while (low < high) {
                const middle = (low + high) >>> 1;
                if (events[middle].elapsed_s < minimum) low = middle + 1;
                else high = middle;
            }
            return read(low);
        };
        return { length, fields, numericFields, start: length ? first : undefined, end: length ? last : undefined,
            at: index => { const position = index < 0 ? length + index : index; return position >= 0 && position < length ? events[position] : undefined; },
            read, range, latest: count => chronological ? read(Math.max(0, length - count)) : range(-Infinity).slice(-count) };
    }
    let current = snapshot();
    return {
        getSnapshot: () => current,
        append(event: MeasurementEvent) {
            if (events.length && event.elapsed_s < events.at(-1)!.elapsed_s) ordered = false;
            events.push(event);
            first = Math.min(first, event.elapsed_s); last = Math.max(last, event.elapsed_s);
            let changed = false;
            for (const key of Object.keys(event.data)) {
                if (!keys.has(key)) { keys.add(key); changed = true; }
                if (typeof event.data[key] === "number" && Number.isFinite(event.data[key]) && !numeric.has(key)) {
                    numeric.add(key); changed = true;
                }
            }
            if (changed) { fields = [...keys]; numericFields = fields.filter(key => numeric.has(key)); }
            current = snapshot();
            return current;
        },
    };
}

export const EMPTY_HISTORY = createMeasurementHistory().getSnapshot();
