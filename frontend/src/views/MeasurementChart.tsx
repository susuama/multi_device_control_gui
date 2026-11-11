import { useEffect, useMemo, useRef } from "react";
import { Chart, LineController, LineElement, PointElement, LinearScale, Decimation } from "chart.js";
import { createChartData } from "./chartData";
import type { MeasurementEvent } from "../type/event";
import { measurementLabel } from "./measurementDisplay";

Chart.register(LineController, LineElement, PointElement, LinearScale, Decimation);
const colors = ["var(--focus)", "#e89528", "#b278df", "#36a994", "#df728b"];
type Props = { series: readonly { id: string; points: readonly MeasurementEvent[] }[]; field: string;
    names: Readonly<Record<string, string>>; ids: readonly string[] };

export function MeasurementChart({ series, field, names, ids }: Props) {
    const canvas = useRef<HTMLCanvasElement>(null);
    const chart = useRef<Chart<"line"> | null>(null);
    const convert = useMemo(() => createChartData(), []);
    useEffect(() => {
        const instance = new Chart(canvas.current!, { type: "line", data: { datasets: [] }, options: {
            responsive: true, maintainAspectRatio: false, animation: false, parsing: false, events: [],
            plugins: { legend: { display: false }, tooltip: { enabled: false }, decimation: { enabled: true, algorithm: "min-max" } },
            scales: { x: { type: "linear", title: { display: true, text: "経過時間 [s]" } },
                y: { type: "linear", title: { display: true } } },
        } });
        chart.current = instance;
        return () => { instance.destroy(); chart.current = null; };
    }, []);
    useEffect(() => {
        const instance = chart.current!;
        const style = getComputedStyle(canvas.current!);
        instance.data.datasets = series.flatMap(item => convert(item.points, field).map(points => ({
            label: names[item.id], data: points, borderColor: colors[ids.indexOf(item.id) % colors.length].replace("var(--focus)", style.getPropertyValue("--focus").trim()),
            borderWidth: 1.5, pointRadius: points.length === 1 ? 2 : 0, pointHitRadius: 0, spanGaps: false, tension: 0,
        })));
        const updateTheme = () => {
            const theme = getComputedStyle(canvas.current!);
            const muted = theme.getPropertyValue("--muted").trim(), border = theme.getPropertyValue("--border").trim();
            for (const axis of [instance.options.scales!.x!, instance.options.scales!.y!]) {
                axis.ticks!.color = muted; axis.grid!.color = border; axis.title!.color = muted;
            }
            instance.options.scales!.y!.title!.text = measurementLabel(field);
            instance.data.datasets.forEach(dataset => {
                if (dataset.label === names[ids[0]]) dataset.borderColor = theme.getPropertyValue("--focus").trim();
            });
            const first = series.flatMap(item => item.points.length ? [item.points[0].elapsed_s, item.points.at(-1)!.elapsed_s] : []);
            instance.options.plugins!.decimation!.enabled = Math.min(...first) !== Math.max(...first);
            instance.update("none");
        };
        updateTheme();
        const observer = new MutationObserver(updateTheme);
        const application = canvas.current!.closest(".application");
        if (application) observer.observe(application, { attributes: true, attributeFilter: ["class"] });
        return () => observer.disconnect();
    }, [series, field, names, ids, convert]);
    return <canvas ref={canvas} role="img" aria-label={`${measurementLabel(field)}の時系列グラフ`} />;
}
