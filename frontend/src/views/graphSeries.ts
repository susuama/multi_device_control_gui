import type { MeasurementEvent } from "../type/event.ts";
import { EMPTY_HISTORY, type MeasurementHistory } from "../state/measurementHistory.ts";

type History = { id: string; events: MeasurementHistory };
type Series = { id: string; points: readonly MeasurementEvent[]; numericCount: number };
type Prepared = History & { minimum: number; field: string; series: Series };
const EMPTY_HISTORIES: readonly History[] = [];

/** 対象外の更新を無視し、機器別の履歴を結合せずに参照する。 */
export function createGraphSelection(ids: readonly string[]) {
    let previous: readonly History[] = EMPTY_HISTORIES;
    let snapshot = EMPTY_HISTORIES;
    return (histories: Readonly<Record<string, MeasurementHistory>>) => {
        if (ids.some((id, index) => (histories[id] ?? EMPTY_HISTORY) !== previous[index]?.events)) {
            previous = ids.map((id, index) => {
                const events = histories[id] ?? EMPTY_HISTORY;
                return previous[index]?.events === events ? previous[index] : { id, events };
            });
            snapshot = previous.some(item => item.events.length) ? previous : EMPTY_HISTORIES;
        }
        return snapshot;
    };
}

export function graphValue(event: MeasurementEvent, field: string): number | null {
    const value = event.data[field];
    return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** 共通の右端時刻で絞り、変更のない系列と元のイベントを再利用する。 */
export function createGraphSeries() {
    const cache = new Map<string, Prepared>();
    return (histories: readonly History[], ids: readonly string[], field: string, seconds: number) => {
        for (const id of cache.keys()) if (!histories.some(item => item.id === id)) cache.delete(id);
        let retainedStart = Infinity, retainedEnd = -Infinity, retainedCount = 0, end = -Infinity;
        for (const item of histories) {
            retainedCount += item.events.length;
            if (item.events.length) {
                retainedStart = Math.min(retainedStart, item.events.start!);
                retainedEnd = Math.max(retainedEnd, item.events.end!);
                if (ids.includes(item.id)) end = Math.max(end, item.events.end!);
            }
        }
        let displayStart = Infinity, displayEnd = -Infinity;
        const series: Series[] = [];
        const minimum = seconds === 0 ? -Infinity : end - seconds;
        for (const item of histories) {
            if (!ids.includes(item.id)) continue;
            const previous = cache.get(item.id);
            let line: Series;
            if (previous && previous.events === item.events && previous.field === field
                && (previous.minimum === minimum || previous.series.points[0]?.elapsed_s >= minimum && minimum >= previous.minimum)) line = previous.series;
            else {
                const points = item.events.range(minimum);
                let numericCount = 0;
                for (const event of points) if (graphValue(event, field) !== null) numericCount++;
                line = { id: item.id, points, numericCount };
            }
            cache.set(item.id, { ...item, minimum, field, series: line });
            series.push(line);
            if (line.points.length) {
                displayStart = Math.min(displayStart, line.points[0].elapsed_s);
                displayEnd = Math.max(displayEnd, line.points.at(-1)!.elapsed_s);
            }
        }
        return { series, retainedCount, retainedStart: retainedCount ? retainedStart : undefined,
            retainedEnd: retainedCount ? retainedEnd : undefined, displayStart: Number.isFinite(displayStart) ? displayStart : undefined,
            displayEnd: Number.isFinite(displayEnd) ? displayEnd : undefined };
    };
}
