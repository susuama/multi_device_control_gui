import { Chart } from "chart.js";

export function chartLines(container: ParentNode = document) {
    const canvas = container.querySelector<HTMLCanvasElement>(".measurement-chart-area canvas");
    const chart = canvas && Chart.getChart(canvas);
    const lines = new Map<string, { count: number; rendered: number; color: unknown }>();
    for (const dataset of chart?.data.datasets ?? []) {
        const raw = (dataset as typeof dataset & { _data?: unknown[] })._data ?? dataset.data;
        const key = dataset.label ?? "";
        const line = lines.get(key) ?? { count: 0, rendered: 0, color: dataset.borderColor };
        line.count += raw.length; line.rendered += dataset.data.length;
        lines.set(key, line);
    }
    return [...lines.values()];
}
