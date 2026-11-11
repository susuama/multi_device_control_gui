import { useMemo, useState, useSyncExternalStore } from "react";
import { MeasurementChart } from "./MeasurementChart";
import { backendStore } from "../state/backendStore";
import { measurementStore } from "../state/measurementStore";
import { createFrameDisplayStore } from "../state/displayStore";
import { DeviceSelector } from "../components/DeviceInfo";
import { ViewLayout, ViewMessage } from "../components/View";
import { measurementLabel } from "./measurementDisplay";
import { EMPTY_HISTORY } from "../state/measurementHistory";
import { createGraphSelection, createGraphSeries } from "./graphSeries";
import { getDeviceViewStatus } from "./viewStatus";
import "./measurement.css";

const colors = ["var(--focus)", "#e89528", "#b278df", "#36a994", "#df728b"];
type GraphSettings = { field: string; seconds: number; compared: string[]; hidden: string[] };
const defaults: GraphSettings = { field: "power_w", seconds: 30, compared: [], hidden: [] };

function GraphStatus({ ids, names }: { ids: readonly string[]; names: Readonly<Record<string, string>> }) {
    const backend = useSyncExternalStore(backendStore.subscribe, backendStore.getSnapshot);
    const data = useSyncExternalStore(measurementStore.subscribe, measurementStore.getSnapshot);
    return (ids.length ? ids : [""]).map(id => {
        const status = getDeviceViewStatus(backend, data, id || null, "measurement");
        return status ? <ViewMessage key={id} tone={status.tone}>{names[id] && `${names[id]}: `}{status.message}
            {status.lastReceivedAt != null && <span className="view-last-received">
                最終受信: {new Date(status.lastReceivedAt).toLocaleTimeString("ja-JP", { hour12: false })}
            </span>}
        </ViewMessage> : null;
    });
}

export function MeasurementGraphView({ panelId }: { panelId: string }) {
    const devices = useSyncExternalStore(backendStore.subscribe, () => backendStore.getSnapshot().devices);
    const [deviceId, setDeviceId] = useState("");
    const [settings, setSettings] = useState<Record<string, GraphSettings>>({});
    const selected = settings[deviceId] ?? defaults;
    const idsKey = JSON.stringify(deviceId ? [deviceId, ...selected.compared.filter(id => id !== deviceId)] : []);
    const ids = useMemo<string[]>(() => JSON.parse(idsKey), [idsKey]);
    const display = useMemo(() => {
        const select = createGraphSelection(ids);
        return createFrameDisplayStore({ subscribe: measurementStore.subscribe,
            getSnapshot: () => select(measurementStore.getSnapshot().measurements) });
    }, [ids]);
    const histories = useSyncExternalStore(display.subscribe, display.getSnapshot);
    const primary = histories.find(item => item.id === deviceId)?.events ?? EMPTY_HISTORY;
    const fields = primary.numericFields;
    const field = String(settings[deviceId]?.field ?? (devices?.some(device => device.id === deviceId && device.type === "power_meter")
        || fields.includes("power_w") ? "power_w" : fields[0] ?? ""));
    const comparable = field === "power_w" && (selected.compared.length > 0
        || devices?.some(device => device.id === deviceId && device.type === "power_meter"));
    const selectedIds = comparable ? ids : ids.slice(0, 1);
    const prepare = useMemo(() => createGraphSeries(), []);
    const graph = useMemo(() => prepare(histories, ids.filter(id => !selected.hidden.includes(id)
        && (comparable || id === deviceId)), field, selected.seconds), [prepare, histories, ids, selected.hidden, comparable, deviceId, field, selected.seconds]);
    const { series } = graph;
    const names = Object.fromEntries(ids.map(id => { const device = devices?.find(item => item.id === id);
        return [id, device ? `${device.model} / ${device.serial_number || id}` : id]; }));
    function change(value: Partial<GraphSettings>) {
        setSettings(current => ({ ...current, [deviceId]: { ...selected, field, ...value } }));
    }
    return <ViewLayout toolbar={<>
        <DeviceSelector panelId={panelId} devices={(devices ?? []).filter(device => device.type !== "camera")} value={deviceId} onChange={setDeviceId} />
        {comparable && <label>比較 <select aria-label={`${panelId}の比較機器`} value="" onChange={event => {
            if (event.target.value) change({ compared: [...selected.compared, event.target.value] });
        }}><option value="">系列を追加</option>{(devices ?? []).filter(device => device.type === "power_meter" && !ids.includes(device.id))
            .map(device => <option key={device.id} value={device.id}>{device.model} / {device.serial_number || device.id}</option>)}</select></label>}
        <label>項目 <select aria-label={`${panelId}のグラフ項目`} value={field} onChange={event => change({ field: event.target.value })}>
            {!fields.includes(field) && <option value={field}>{field || "数値項目なし"}</option>}
            {fields.map(key => <option key={key} value={key}>{measurementLabel(key)}</option>)}
        </select></label>
        <label>範囲 <select aria-label={`${panelId}のグラフ時間幅`} value={selected.seconds} onChange={event => change({ seconds: Number(event.target.value) })}>
            {[10, 30, 60, 600, 1800, 3600, 7200].map(seconds => <option key={seconds} value={seconds}>
                直近{seconds < 60 ? `${seconds}秒` : seconds < 3600 ? `${seconds / 60}分` : `${seconds / 3600}時間`}
            </option>)}<option value={0}>保持全範囲</option>
        </select></label>
    </>}>
        <div className="measurement-graph-content">
        <GraphStatus ids={selectedIds} names={names} />
        {deviceId && <div className="graph-series-list" aria-label={`${panelId}のグラフ系列`}>{selectedIds.map((id, index) => <span key={id}>
            <label><input type="checkbox" aria-label={`${panelId}の系列${id}`} checked={!selected.hidden.includes(id)} onChange={event => change({
                hidden: event.target.checked ? selected.hidden.filter(key => key !== id) : [...selected.hidden, id],
            })} /><i style={{ background: colors[index % colors.length] }} aria-hidden="true" />{names[id]}</label>
            {id !== deviceId && <button type="button" aria-label={`${panelId}の系列${id}を削除`} onClick={() => change({
                compared: selected.compared.filter(key => key !== id), hidden: selected.hidden.filter(key => key !== id),
            })}>×</button>}</span>)}</div>}
        {!deviceId ? null : !series.length ? <ViewMessage>表示する系列を選択してください。</ViewMessage> : !series.some(item => item.numericCount) ? <ViewMessage>選択項目の数値データがありません。</ViewMessage> : <div className="measurement-display measurement-graph" aria-label={`${panelId}の測定グラフ`}>
            <div className="measurement-display-options"><span>保持: {graph.retainedStart}–{graph.retainedEnd} s / {graph.retainedCount}件</span>
                <span>表示: {graph.displayStart}–{graph.displayEnd} s</span><span>{measurementLabel(field)}</span></div>
            <div className="measurement-chart-area"><MeasurementChart series={series} field={field} names={names} ids={selectedIds} /></div>
        </div>}
        </div>
    </ViewLayout>;
}
