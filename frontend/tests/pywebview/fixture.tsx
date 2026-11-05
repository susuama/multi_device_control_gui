import { StrictMode, useState, useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import { backendStore } from "../../src/state/backendStore";
import { measurementStore } from "../../src/state/measurementStore";
import { applicationRuntime } from "../../src/state/applicationRuntime";
import { preferencesStore } from "../../src/state/preferencesStore";
import { getOperationAvailability, type Operation } from "../../src/state/operationPolicy";
import { disposeWebSocketClients, getWebSocketClient } from "../../src/api/websocketClient";
import type { DeviceSettings } from "../../src/type/devices";

let notifications = 0;
measurementStore.subscribe(() => { notifications++; });
const errors: string[] = [];
const reportError = (cause: unknown) => {
    errors.push(cause instanceof Error ? cause.message : String(cause));
    if (errors.length > 20) errors.shift();
};

/** 操作は画面・自動検証のどちらからも同じ共有ストアを通す。 */
async function action(operation: Operation, id = "camera", settings?: DeviceSettings) {
    switch (operation) {
        case "connect": return backendStore.connectDevice(id);
        case "disconnect": return backendStore.disconnectDevice(id);
        case "startSampling": return backendStore.startSampling(id);
        case "stopSampling": return backendStore.stopSampling(id);
        case "updateSettings": return backendStore.updateSettings(id, settings ?? backendStore.getSnapshot().settings[id]);
        case "startRecording": return backendStore.startRecording({
            directory: preferencesStore.getSnapshot().defaultDirectory || "mock-recordings",
            video_options: { camera: { format: "ffv1" } },
        });
        case "stopRecording": return backendStore.stopRecording();
    }
}

function snapshot() {
    const data = measurementStore.getSnapshot();
    const backend = backendStore.getSnapshot();
    const keys = new Set<string | null>([null, ...(backend.devices ?? []).filter((device) => device.type === "camera").map((device) => device.id)]);
    return {
        backend, runtime: applicationRuntime.getSnapshot(), preferences: preferencesStore.getSnapshot(),
        session: data.session, streams: data.streams, needsResync: data.needsResync, notifications, errors,
        counts: Object.fromEntries(Object.entries(data.measurements).map(([id, values]) => [id, values.length])),
        latest: Object.fromEntries(Object.entries(data.measurements).map(([id, values]) => [id, values.at(-1)])),
        previews: Object.fromEntries(Object.entries(data.previews).map(([id, { jpeg, ...metadata }]) => [id, { ...metadata, bytes: jpeg.size }])),
        sockets: [...keys].map((key) => getWebSocketClient(key, key === null ? 100 : 1).getSnapshot()),
        panels: [...document.querySelectorAll("[data-panel]")].map((panel) => panel.textContent),
        heapBytes: (performance as Performance & { memory?: { usedJSHeapSize: number } }).memory?.usedJSHeapSize ?? null,
    };
}

function dispose() {
    applicationRuntime.dispose();
    disposeWebSocketClients();
}

declare global {
    interface Window {
        coreTest: {
            snapshot: typeof snapshot;
            action: typeof action;
            refresh: typeof applicationRuntime.refresh;
            dispose: typeof dispose;
            setPreferences: typeof preferencesStore.update;
            decodePreview: () => Promise<{ width: number; height: number }>;
        };
    }
}

window.coreTest = {
    snapshot, action, refresh: applicationRuntime.refresh, dispose, setPreferences: preferencesStore.update,
    async decodePreview() {
        const preview = measurementStore.getSnapshot().previews.camera;
        const bitmap = await createImageBitmap(preview.jpeg);
        const size = { width: bitmap.width, height: bitmap.height };
        bitmap.close();
        return size;
    },
};
window.addEventListener("pagehide", dispose);
window.addEventListener("error", (event) => reportError(event.message));
window.addEventListener("unhandledrejection", (event) => reportError(event.reason));

const operations: [Operation, string][] = [
    ["connect", "接続"], ["disconnect", "切断"], ["startSampling", "取得開始"], ["stopSampling", "取得停止"],
    ["updateSettings", "設定適用"], ["startRecording", "記録開始"], ["stopRecording", "記録停止"],
];

export function CorePanel({ number }: { number: number }) {
    const backend = useSyncExternalStore(backendStore.subscribe, backendStore.getSnapshot);
    const data = useSyncExternalStore(measurementStore.subscribe, measurementStore.getSnapshot);
    const [id, setId] = useState("camera");
    const [draft, setDraft] = useState("");
    const [error, setError] = useState("");
    let settings = backend.settings[id];
    let draftError = "";
    try { if (draft) settings = JSON.parse(draft) as DeviceSettings; }
    catch { draftError = "設定JSONの構文を確認してください。"; }
    return <section data-panel={number}>
        <h2>パネル {number}</h2>
        <label>機器 <select value={id} onChange={(event) => { setId(event.target.value); setDraft(""); }}>
            {(backend.devices ?? []).map((device) => <option key={device.id}>{device.id}</option>)}
        </select></label>
        <p>同期: {backend.synchronization.status} / 取得: {String(backend.sampling[id] ?? false)} / 記録: {String(backend.recording?.is_recording ?? false)}</p>
        <textarea aria-label={`パネル${number}の設定`} value={draft || JSON.stringify(backend.settings[id] ?? {}, null, 2)}
            onChange={(event) => setDraft(event.target.value)} />
        <nav>{operations.map(([operation, label]) => {
            const availability = getOperationAvailability(backend, { operation, deviceId: id, settings });
            const reason = operation === "updateSettings" && draftError || availability.reason;
            return <button key={operation} data-operation={operation} disabled={!availability.allowed || !!reason} title={reason ?? "操作可能"}
                onClick={async () => { setError(""); try { await action(operation, id, settings); } catch (cause) { setError(String(cause)); } }}>{label}</button>;
        })}</nav>
        {(error || draftError) && <p role="alert">{error || draftError}</p>}
        <pre>{JSON.stringify({ settings: backend.settings[id], recording: backend.recording,
            count: data.measurements[id]?.length ?? 0, image: data.previews[id]?.frame_index ?? null, stream: data.streams[id] }, null, 2)}</pre>
    </section>;
}

export function Fixture() {
    const runtime = useSyncExternalStore(applicationRuntime.subscribe, applicationRuntime.getSnapshot);
    const preferences = useSyncExternalStore(preferencesStore.subscribe, preferencesStore.getSnapshot);
    const [second, setSecond] = useState(true);
    return <main data-theme={preferences.theme}>
        <h1>pywebview 中核検証</h1>
        <p role="status">中核: {runtime.phase} {runtime.error}</p>
        <header>
            <label>配色 <select value={preferences.theme} onChange={(event) => preferencesStore.update({ theme: event.target.value as "dark" | "light" })}>
                <option value="dark">ダーク</option><option value="light">ライト</option>
            </select></label>
            <label>標準保存先 <input value={preferences.defaultDirectory} onChange={(event) => preferencesStore.update({ defaultDirectory: event.target.value })} /></label>
            <button data-testid="toggle-panel" onClick={() => setSecond(!second)}>パネル2の表示切替</button>
            <button onClick={() => { void applicationRuntime.refresh().catch(reportError); }}>再確認</button>
        </header>
        {preferences.error && <p role="alert">{preferences.error}</p>}
        <div className="panels"><CorePanel number={1} />{second && <CorePanel number={2} />}</div>
    </main>;
}

createRoot(document.getElementById("root")!).render(<StrictMode><Fixture /></StrictMode>);
void applicationRuntime.start().catch(reportError);
