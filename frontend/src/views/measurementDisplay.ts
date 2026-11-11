import type { JsonValue, MeasurementEvent } from "../type/event.ts";

export const EMPTY_MEASUREMENTS: readonly MeasurementEvent[] = [];
export const measurementLabel = (key: string) => key === "power_w" ? "パワー [W]" : key;
export const formatMeasurementValue = (value: JsonValue | undefined): string => {
    if (value === undefined || value === null) return "—";
    return typeof value === "object" ? JSON.stringify(value) : String(value);
};
const timeFormatter = new Intl.DateTimeFormat("ja-JP", { hour12: false, fractionalSecondDigits: 3 });
export const formatMeasurementTime = (value: string) => timeFormatter.format(new Date(value));
const rowKeys = new WeakMap<MeasurementEvent, number>();
let nextRowKey = 1;
const orderedCache = new WeakMap<readonly MeasurementEvent[], readonly MeasurementEvent[]>();

/** 同じ時刻の別サンプルも区別し、表示範囲が動いても同じ行を再利用する。 */
export function measurementRowKey(event: MeasurementEvent): number {
    let key = rowKeys.get(event);
    if (key === undefined) { key = nextRowKey++; rowKeys.set(event, key); }
    return key;
}

/** 受信順の履歴を変更せず、経過時間の順に表示する。 */
export function orderedMeasurements(events: readonly MeasurementEvent[]): readonly MeasurementEvent[] {
    let ordered = orderedCache.get(events);
    if (!ordered) {
        ordered = [...events].sort((left, right) => left.elapsed_s - right.elapsed_s);
        orderedCache.set(events, ordered);
    }
    return ordered;
}

export function measurementFields(events: readonly MeasurementEvent[], numericOnly = false): string[] {
    const keys = new Set<string>();
    const numeric = new Set<string>();
    for (const event of events) for (const key of Object.keys(event.data)) {
        keys.add(key);
        if (numericOnly && typeof event.data[key] === "number" && Number.isFinite(event.data[key])) numeric.add(key);
    }
    return [...keys].filter(key => !numericOnly || numeric.has(key));
}

/** 最後の受信点を基準に絞り込み、欠損を0へ置き換えない。 */
export function graphMeasurements(events: readonly MeasurementEvent[], field: string, seconds: number) {
    const ordered = orderedMeasurements(events);
    const end = ordered.at(-1)?.elapsed_s ?? 0;
    return ordered.filter(event => seconds === 0 || event.elapsed_s >= end - seconds).map(event => ({
        elapsed_s: event.elapsed_s, datetime: event.datetime,
        value: typeof event.data[field] === "number" && Number.isFinite(event.data[field]) ? event.data[field] as number : null,
    }));
}
