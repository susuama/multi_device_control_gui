import type { MeasurementEvent } from "../type/event.ts";
import { graphValue } from "./graphSeries.ts";

export type ChartPoint = { x: number; y: number };

/** 既存点の変換結果を再利用し、欠損で系列を分割して間引き後も線を分断する。 */
export function createChartData() {
    let previousField = "";
    let cache = new WeakMap<MeasurementEvent, ChartPoint>();
    return (events: readonly MeasurementEvent[], field: string) => {
        if (field !== previousField) { cache = new WeakMap(); previousField = field; }
        const segments: ChartPoint[][] = [];
        let segment: ChartPoint[] = [];
        for (const event of events) {
            const y = graphValue(event, field);
            if (y === null) {
                if (segment.length) segments.push(segment);
                segment = [];
                continue;
            }
            let point = cache.get(event);
            if (!point) { point = { x: event.elapsed_s, y }; cache.set(event, point); }
            segment.push(point);
        }
        if (segment.length) segments.push(segment);
        return segments;
    };
}
