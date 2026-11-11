import { Chart } from "chart.js";
import { chartLines } from "../chartInspection";
import { Profiler, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { LogView } from "../../src/views/LogView";
import { MeasurementTableView } from "../../src/views/MeasurementTableView";
import { MeasurementGraphView } from "../../src/views/MeasurementGraphView";
import { Workspace } from "../../src/workspace/Workspace";
import { backendStore } from "../../src/state/backendStore";
import { measurementStore } from "../../src/state/measurementStore";
import { logger, logStore } from "../../src/logging/logStore";
import { applicationRuntime } from "../../src/state/applicationRuntime";
import "../../src/index.css";
import "../../src/workspace/workspace.css";

const errors: string[] = [];
window.addEventListener("error", event => errors.push(event.message));
window.addEventListener("unhandledrejection", event => errors.push(String(event.reason)));
const pause = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
let configure: (value: string) => void;
export function Harness() {
    const [view, setView] = useState("none");
    useEffect(() => { configure = setView; }, []);
    const views = [
        { id: "none", title: "未表示", render: () => <div /> },
        { id: "log", title: "ログ", render: (panelId: string) => <LogView panelId={panelId} /> },
        { id: "table", title: "テーブル", render: (panelId: string) => <MeasurementTableView panelId={panelId} /> },
        { id: "graph", title: "グラフ", render: (panelId: string) => <Profiler id="graph" onRender={(_id, _phase, duration) => {
            if (profile) metrics.reactRenderMs.push(duration);
        }}><MeasurementGraphView panelId={panelId} /></Profiler> },
    ];
    return <div className="application theme-dark"><Workspace key={view} layouts={[{ id: "single", title: "1パネル", panels: [{ id: "P1", viewId: view }], layout: "P1" }]} initialLayoutId="single" views={[...views.filter(item => item.id === view), ...views.filter(item => item.id !== view)]} /></div>;
}
function select(label: string, value: string) {
    const node = document.querySelector<HTMLSelectElement>(`[aria-label="${label}"]`);
    if (!node) throw new Error(`選択欄がありません: ${label}`);
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!.call(node, value);
    node.dispatchEvent(new Event("change", { bubbles: true }));
}
let sequence = 0;
let meterId = "meter-1";
function receive(id = meterId, offset = 0) {
    const index = sequence++;
    const session = measurementStore.getSnapshot().session!;
    measurementStore.receive({ type: "measurement", ...session, measurement_id: session.measurement_id!, id,
        elapsed_s: index / 10 + offset, timestamp_ns: index * 1e8, datetime: new Date(1794000000000 + index * 100).toISOString(),
        data: { power_w: Math.sin(index / 30), wavelength: 500, label: "検証" } });
}
let producer: ReturnType<typeof setInterval> | undefined;
let settingsTimer: ReturnType<typeof setInterval> | undefined;
let profile = false;
function emptyMetrics() {
    return { updates: 0, graphUpdates: 0, graphUpdateAt: [] as number[], reactRenderMs: [] as number[], chartUpdateMs: [] as number[],
        addedRows: 0, removedRows: 0, timeFormats: 0, formatMs: 0, longTasks: [] as number[], settingsMs: [] as number[], settingsErrors: [] as string[] };
}
let metrics = emptyMetrics();
let measureGraph = false;
const chartUpdate = Chart.prototype.update;
Chart.prototype.update = function(...args: Parameters<typeof chartUpdate>) {
    const started = performance.now();
    try { return chartUpdate.apply(this, args); }
    finally { if (profile) metrics.chartUpdateMs.push(performance.now() - started); }
};
Chart.register({ id: "performance-measurement", afterRender() {
    if (measureGraph) { metrics.graphUpdates++; metrics.graphUpdateAt.push(performance.now()); }
} });
const timeString = Date.prototype.toLocaleTimeString;
const dateString = Date.prototype.toLocaleString;
Date.prototype.toLocaleTimeString = function(...args: Parameters<typeof timeString>) {
    if (!profile) return timeString.apply(this, args);
    const started = performance.now(); metrics.timeFormats++;
    const result = timeString.apply(this, args); metrics.formatMs += performance.now() - started; return result;
};
Date.prototype.toLocaleString = function(...args: Parameters<typeof dateString>) {
    if (!profile) return dateString.apply(this, args);
    const started = performance.now(); metrics.timeFormats++;
    const result = dateString.apply(this, args); metrics.formatMs += performance.now() - started; return result;
};
const mutation = new MutationObserver(records => {
    metrics.updates++;
    for (const record of records) for (const [nodes, key] of [[record.addedNodes, "addedRows"], [record.removedNodes, "removedRows"]] as const) {
        for (const node of nodes) if (node instanceof Element) metrics[key] += (node.matches("tbody tr") ? 1 : 0) + node.querySelectorAll("tbody tr").length;
    }
});
const longTasks = new PerformanceObserver(list => { if (profile) metrics.longTasks.push(...list.getEntries().map(entry => entry.duration)); });
longTasks.observe({ type: "longtask", buffered: false });
async function start(view: string, limit: number, instrument: boolean, initial = 1000) {
    clearInterval(producer); clearInterval(settingsTimer); mutation.disconnect(); profile = false;
    configure("none"); await pause(100);
    measurementStore.setSession("reset", "reset");
    const backend = backendStore.getSnapshot().measurement!;
    measurementStore.setSession(backend.server_instance_id, backend.measurement_id!);
    const comparison = view === "graphPair";
    const recent = view === "graphRecent";
    measurementStore.setStreamState(meterId, { connected: true, sampling: true });
    if (comparison) measurementStore.setStreamState("meter-2", { connected: true, sampling: true });
    sequence = 0; logStore.clear();
    const receivePair = () => { receive(); if (comparison) { sequence--; receive("meter-2", .025); } };
    for (let index = 0; index < initial; index++) { receivePair(); logger.info("verification", `同じ入力列 ${index} GET /api/devices/meter-1/settings → HTTP 200`); }
    if (comparison || recent) view = "graph";
    configure(view); await pause(200);
    if (view === "log") select("P1のログ表示件数", String(limit));
    if (view === "table" || view === "graph") { select("P1の対象機器", meterId); await pause(100); }
    if (view === "table") select("P1のテーブル表示件数", String(limit));
    if (view === "graph") select("P1のグラフ時間幅", recent ? "30" : initial >= 72000 ? "7200" : "0");
    if (comparison) select("P1の比較機器", "meter-2");
    if (limit === -1) select("P1のビュー", "none");
    await pause(200);
    metrics = emptyMetrics(); measureGraph = true;
    profile = instrument;
    mutation.observe(document.getElementById("root")!, { childList: true, subtree: true, characterData: true,
        attributes: true, attributeFilter: ["d"] });
    const finite = sequence + 700;
    producer = setInterval(() => {
        if (sequence >= finite) { clearInterval(producer); return; }
        receivePair(); logger.info("verification", `同じ入力列 ${sequence} GET /api/devices/meter-1/settings → HTTP 200`);
    }, 100);
    settingsTimer = setInterval(() => {
        const started = performance.now();
        void backendStore.refreshSettings(meterId).then(() => metrics.settingsMs.push(performance.now() - started), error => metrics.settingsErrors.push(String(error)));
    }, 2000);
}
async function initialize() {
    await fetch("/api/devices/meter-1/connect", { method: "POST" });
    await fetch("/api/devices/meter-1/sampling/start", { method: "POST" });
    await backendStore.refresh();
    return true;
}
function stop() {
    measureGraph = false;
    clearInterval(producer); clearInterval(settingsTimer); mutation.disconnect(); profile = false;
    return { ...metrics, received: sequence, retained: measurementStore.getSnapshot().measurements[meterId]?.length ?? 0,
        oldestElapsed: measurementStore.getSnapshot().measurements[meterId]?.at(0)?.elapsed_s,
        graphRange: document.querySelector<HTMLSelectElement>('[aria-label="P1のグラフ時間幅"]')?.value,
        logCount: logStore.getSnapshot().length, errors, rows: document.querySelectorAll("tbody tr").length,
        graphPoints: chartLines().map(line => line.count), renderedPoints: chartLines().map(line => line.rendered),
        text: document.querySelector<HTMLTextAreaElement>(".log-text")?.value.split("\n").length };
}
async function action(task: () => Promise<unknown>) {
    while (backendStore.getSnapshot().synchronization.status !== "ready") await pause(20);
    await task();
}
async function initializeReal(directory: string) {
    await applicationRuntime.start();
    await backendStore.scanDevices();
    await applicationRuntime.refresh();
    meterId = backendStore.getSnapshot().devices!.find(device => device.type === "power_meter")!.id;
    await action(() => backendStore.connectDevice(meterId));
    await applicationRuntime.refresh();
    await action(() => backendStore.startSampling(meterId));
    await applicationRuntime.refresh();
    await action(() => backendStore.startRecording({ directory, name: `continuous-display-${Date.now()}` }));
    return { meterId, recording: backendStore.getSnapshot().recording };
}
async function showReal(view: string) {
    clearInterval(settingsTimer);
    configure(view); await pause(200);
    if (view === "table" || view === "graph") { select("P1の対象機器", meterId); await pause(100); }
    if (view === "table") select("P1のテーブル表示件数", "1000");
    if (view === "graph") select("P1のグラフ時間幅", "0");
    await action(() => backendStore.updateSettings(meterId, { ...backendStore.getSnapshot().settings[meterId], wavelength: view === "table" ? 600 : 650 }));
    metrics = emptyMetrics(); measureGraph = true;
    settingsTimer = setInterval(() => {
        const started = performance.now();
        void backendStore.refreshSettings(meterId).then(() => metrics.settingsMs.push(performance.now() - started), error => metrics.settingsErrors.push(String(error)));
    }, 2000);
    return true;
}
async function finishReal() {
    clearInterval(settingsTimer);
    await action(() => backendStore.stopSampling(meterId));
    await action(() => backendStore.stopRecording());
    const tail = measurementStore.getSnapshot().measurements[meterId]?.read();
    applicationRuntime.dispose();
    return { tail, errors, recording: backendStore.getSnapshot().recording };
}
let comparisonIds: string[] = [];
async function initializeComparison(directory: string) {
    await applicationRuntime.start(); await backendStore.scanDevices(); await applicationRuntime.refresh();
    comparisonIds = backendStore.getSnapshot().devices!.filter(device => device.id.startsWith("DummyPowerMeter_")).map(device => device.id);
    if (comparisonIds.length !== 2) throw new Error("比較検証にはダミーパワーメーター2台が必要です。");
    for (const id of comparisonIds) {
        await action(() => backendStore.connectDevice(id)); await applicationRuntime.refresh();
        await action(() => backendStore.startSampling(id)); await applicationRuntime.refresh();
    }
    meterId = comparisonIds[0];
    await action(() => backendStore.startRecording({ directory, name: `comparison-${Date.now()}` }));
    return { meterId, recording: backendStore.getSnapshot().recording };
}
async function showComparison() {
    await showReal("graph");
    select("P1の比較機器", comparisonIds[1]);
    await pause(400);
    if (chartLines().length !== 2) throw new Error("2系列を描画できません。");
    return true;
}
async function finishComparison() {
    clearInterval(settingsTimer);
    for (const id of comparisonIds) await action(() => backendStore.stopSampling(id));
    await action(() => backendStore.stopRecording()); await pause(400);
    const tails = Object.fromEntries(comparisonIds.map(id => [id, measurementStore.getSnapshot().measurements[id].read()]));
    const points = chartLines().map(line => line.count);
    if (points.length !== 2 || points.some((count, index) => count !== tails[comparisonIds[index]].length)) throw new Error("最終表示と全履歴の点数が一致しません。");
    applicationRuntime.dispose();
    return { tails, errors, recording: backendStore.getSnapshot().recording };
}
Object.assign(window, { performanceTest: { initialize, start, stop, initializeReal, showReal, finishReal,
    initializeComparison, showComparison, finishComparison,
    comparisonCount: () => Math.min(...comparisonIds.map(id => measurementStore.getSnapshot().measurements[id]?.length ?? 0)),
    realCount: () => measurementStore.getSnapshot().measurements[meterId]?.length ?? 0 } });
createRoot(document.getElementById("root")!).render(<Harness />);
