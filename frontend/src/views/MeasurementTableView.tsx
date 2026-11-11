import { memo, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { MeasurementEvent } from "../type/event";
import { backendStore } from "../state/backendStore";
import { measurementStore } from "../state/measurementStore";
import { createDisplayStore } from "../state/displayStore";
import { DeviceSelector } from "../components/DeviceInfo";
import { DeviceDataView } from "../components/View";
import { formatMeasurementTime, formatMeasurementValue, measurementLabel, measurementRowKey } from "./measurementDisplay";
import { EMPTY_HISTORY } from "../state/measurementHistory";
import "./measurement.css";

type TableSettings = { limit: number; follow: boolean; hidden: string[] };
const defaults: TableSettings = { limit: 100, follow: true, hidden: [] };

const MeasurementRow = memo(function MeasurementRow({ row, fields }: { row: MeasurementEvent; fields: readonly string[] }) {
    const time = useMemo(() => formatMeasurementTime(row.datetime), [row.datetime]);
    return <tr><td><time dateTime={row.datetime}>{time}</time></td><td>{row.elapsed_s}</td>
        {fields.map(field => <td key={field}>{formatMeasurementValue(row.data[field])}</td>)}</tr>;
});

export function MeasurementTableView({ panelId }: { panelId: string }) {
    const devices = useSyncExternalStore(backendStore.subscribe, () => backendStore.getSnapshot().devices);
    const [deviceId, setDeviceId] = useState("");
    const [settings, setSettings] = useState<Record<string, TableSettings>>({});
    const selected = settings[deviceId] ?? defaults;
    const display = useMemo(() => createDisplayStore({ subscribe: measurementStore.subscribe,
        getSnapshot: () => measurementStore.getSnapshot().measurements[deviceId] ?? EMPTY_HISTORY,
    }), [deviceId]);
    const events = useSyncExternalStore(display.subscribe, display.getSnapshot);
    const fields = events.fields;
    const visibleFieldsKey = JSON.stringify(fields.filter(field => !selected.hidden.includes(field)));
    const visibleFields = useMemo<string[]>(() => JSON.parse(visibleFieldsKey), [visibleFieldsKey]);
    const rows = useMemo(() => events.latest(selected.limit), [events, selected.limit]);
    const scroll = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (selected.follow && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
    }, [rows, selected.follow]);
    function change(value: Partial<TableSettings>) {
        setSettings(current => ({ ...current, [deviceId]: { ...selected, ...value } }));
    }
    return <DeviceDataView deviceId={deviceId || null} source="measurement" toolbar={<>
        <DeviceSelector panelId={panelId} devices={(devices ?? []).filter(device => device.type !== "camera")} value={deviceId} onChange={setDeviceId} />
        <label>表示件数 <select aria-label={`${panelId}のテーブル表示件数`} value={selected.limit} onChange={event => change({ limit: Number(event.target.value) })}>
            {[100, 500, 1000].map(limit => <option key={limit} value={limit}>{limit}</option>)}
        </select></label>
        <label><input aria-label={`${panelId}のテーブル追従`} type="checkbox" checked={selected.follow} onChange={event => change({ follow: event.target.checked })} />追従</label>
    </>}>
        <div className="measurement-display">
            <div className="measurement-display-options">{fields.map(field => <label key={field}>
                <input type="checkbox" aria-label={`${panelId}の列${field}`} checked={!selected.hidden.includes(field)} onChange={event => change({
                    hidden: event.target.checked ? selected.hidden.filter(key => key !== field) : [...selected.hidden, field],
                })} />{measurementLabel(field)}
            </label>)}<span>{rows.length}件表示 / {events.length}件保持</span></div>
            <div ref={scroll} className="measurement-table-scroll" tabIndex={0} aria-label={`${panelId}の測定テーブル`}>
                <table className="measurement-data-table"><thead><tr><th>取得日時</th><th>経過時間 [s]</th>
                    {visibleFields.map(field => <th key={field}>{measurementLabel(field)}</th>)}
                </tr></thead><tbody>{rows.map(row => <MeasurementRow key={measurementRowKey(row)} row={row} fields={visibleFields} />)}</tbody></table>
            </div>
        </div>
    </DeviceDataView>;
}
