import { useState, useSyncExternalStore } from "react";
import { Card } from "@astryxdesign/core/Card";
import { Text } from "@astryxdesign/core/Text";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { apiRequest } from "../api/client";
import { backendStore } from "../state/backendStore";
import { preferencesStore } from "../state/preferencesStore";
import { getOperationAvailability } from "../state/operationPolicy";
import type { OperationRequest } from "../state/operationPolicy";
import { OperationButton } from "../components/OperationButton";
import { ViewLayout, ViewMessage } from "../components/View";
import { buildRecordingRequest, recordingStateLabel, targetStateLabels, videoFormats } from "./measurementRecording";
import "./devices.css";
import "./measurementRecording.css";

/** 共通の取得・記録を操作する。表示の終了では取得・記録を止めない。 */
export function MeasurementRecordingView({ panelId }: { panelId: string }) {
    const backend = useSyncExternalStore(backendStore.subscribe, backendStore.getSnapshot);
    const preferences = useSyncExternalStore(preferencesStore.subscribe, preferencesStore.getSnapshot);
    const directory = preferences.defaultDirectory;
    const [name, setName] = useState("");
    const [videoFormat, setVideoFormat] = useState("");
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const devices = backend.devices ?? [];
    const ids = backend.measurement?.sampling_device_ids ?? [];
    const recording = backend.recording;
    const recordingPending = Object.entries(backend.requests).some(([key, value]) => key.startsWith("recording/") && value.pending > 0 && value.writing);
    const conditionsLocked = busy || recordingPending || !!recording?.is_recording || !!recording?.is_stopping;
    let inputError = "";
    try { buildRecordingRequest(directory, name, devices, ids, Object.fromEntries(ids.map(id => [id, videoFormat]))); }
    catch (cause) { inputError = cause instanceof Error ? cause.message : String(cause); }

    async function perform(action: () => Promise<unknown>) {
        if (busy) return;
        setBusy(true); setError("");
        try { await action(); }
        catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
        finally { setBusy(false); }
    }
    function availability(request: OperationRequest, localReason = "") {
        return localReason || getOperationAvailability(backend, request).reason;
    }
    const startReason = availability({ operation: "startRecording" }, inputError);
    const stopReason = availability({ operation: "stopRecording" });
    const hideStopReason = stopReason === "停止または再試行する記録がありません。";
    const recordingReason = startReason || (hideStopReason ? "" : stopReason);
    const recordingLabel = recordingStateLabel(recording);

    return <ViewLayout>
        <div className="recording-view">
            {error && <ViewMessage tone="error">{error}</ViewMessage>}
            {preferences.error && <ViewMessage tone="error">{preferences.error}</ViewMessage>}
            {backend.measurement?.error && <ViewMessage tone="error">取得: {backend.measurement.error}</ViewMessage>}
            <Card className="recording-card" variant="transparent" padding={2} role="region" aria-label="記録条件">
                <div className="recording-heading"><Text as="h3" type="label" weight="semibold">記録条件</Text></div>
                <fieldset className="device-setting-fields recording-fields" disabled={conditionsLocked}>
                    <label className="recording-field"><span>保存先の親フォルダ</span><input aria-label={`${panelId}の保存先`} value={directory} onChange={event => preferencesStore.update({ defaultDirectory: event.target.value })} /></label>
                    <label className="recording-field"><span>記録名（省略可）</span><input aria-label={`${panelId}の記録名`} value={name} onChange={event => setName(event.target.value)} /></label>
                    <label className="recording-field"><span>動画形式</span><select aria-label={`${panelId}の動画形式`} value={videoFormat} onChange={event => setVideoFormat(event.target.value)}>
                        <option value="">動画形式を選択</option>{videoFormats.map(format => <option key={format} value={format}>{format}</option>)}
                    </select></label>
                </fieldset>
                <div className="recording-controls">
                    <small className={`recording-hint${recordingReason && !busy && !recordingPending ? " recording-hint-warning" : ""}`}>{busy || recordingPending ? "操作中" : recordingReason}</small>
                    <div className="recording-actions recording-main-actions">
                        <OperationButton variant="primary" request={{ operation: "startRecording" }} disabledReason={busy ? "操作中です。" : inputError || undefined}
                            onClick={() => { void perform(() => {
                                const latest = backendStore.getSnapshot();
                                const latestIds = latest.measurement?.sampling_device_ids ?? [];
                                return backendStore.startRecording(buildRecordingRequest(directory, name, latest.devices ?? [], latestIds, Object.fromEntries(latestIds.map(id => [id, videoFormat]))));
                            }); }}>記録開始</OperationButton>
                        <OperationButton variant="destructive" hideReason={hideStopReason} request={{ operation: "stopRecording" }} disabledReason={busy ? "操作中です。" : undefined}
                            onClick={() => { void perform(() => backendStore.stopRecording()); }}>{recording?.error && !recording.is_recording ? "停止を再試行" : "記録停止"}</OperationButton>
                    </div>
                </div>
            </Card>
            <Card className="recording-card" variant="transparent" padding={2} role="region" aria-label="取得中の機器">
                <details className="recording-collapsible" open>
                <summary className="recording-heading"><Text as="h3" type="label" weight="semibold">取得中の機器</Text><Badge variant="neutral" label={`取得中 ${backend.measurement ? ids.length : "—"}台・記録対象 ${recording ? Object.keys(recording.targets).length : "—"}台`} /><span className="recording-chevron" aria-hidden="true" /></summary>
                {ids.length === 0 ? <p className="recording-empty">{backend.measurement ? "取得中の機器はありません。" : "取得対象を確認しています。"}</p> : <ul className="recording-target-list">{ids.map(id => {
                    const device = devices.find(item => item.id === id);
                    const target = recording?.targets[id];
                    const state = !recording || backend.synchronization.status !== "ready" ? "記録状態未確認"
                        : recording.is_recording || recording.is_stopping ? (target ? targetStateLabels[target.state] : "記録対象外")
                        : "記録していません";
                    const type = device ? ({ camera: "カメラ", power_meter: "パワーメーター" } as Record<string, string>)[device.type] ?? device.type : "種類未確認";
                    return <li key={id}><div className="recording-device-name"><strong>{device?.model ?? id}</strong><small>{type}・{id}</small></div>
                        <span className={state === "記録中" ? "recording-active" : state === "保存完了" ? "recording-success" : "recording-target-state"}>{state}</span>
                    </li>;
                })}</ul>}
                </details>
            </Card>
            <Card className="recording-card" variant="transparent" padding={2} role="region" aria-label="現在の記録">
                    <details className="recording-collapsible" open>
                    <summary className="recording-heading"><Text as="h3" type="label" weight="semibold">現在の記録</Text>
                        <span className={`recording-state${recordingLabel === "記録中" ? " recording-active" : recordingLabel === "停止確認済み" ? " recording-success" : recording?.error ? " recording-error" : ""}`}>
                            {recordingLabel}
                        </span>
                        <span className="recording-chevron" aria-hidden="true" />
                    </summary>
                    {recording?.error && <ViewMessage tone="error">{recording.error}</ViewMessage>}
                    {Object.keys(recording?.targets ?? {}).length > 0 && <div className="recording-results-scroll">
                        <table className="measurement-table recording-results"><thead><tr><th>開始時の対象</th><th>保存形式</th><th>保存状態</th><th>取得エラー</th><th>保存エラー</th></tr></thead>
                            <tbody>{Object.entries(recording?.targets ?? {}).map(([id, target]) => <tr key={id} data-recording-target={id}>
                                <td>{id}</td><td>{target.video_options?.format ?? "CSV"}</td><td className={target.state === "completed" ? "recording-success" : undefined}>{targetStateLabels[target.state]}</td><td>{target.acquisition_error ?? "—"}</td><td>{target.error ?? "—"}</td>
                            </tr>)}</tbody>
                        </table>
                    </div>}
                    <div className="recording-summary">
                        <div className="recording-period"><span>記録期間</span><span>{formatRecordingTime(recording?.recording_started_at)} ～ {formatRecordingTime(recording?.recording_stopped_at)}</span></div>
                        <Button className="ui-button" variant="secondary" size="sm" label="フォルダを開く"
                            isDisabled={busy || !recording?.directory || !recording.recording_id}
                            onClick={() => {
                                if (!recording?.recording_id) return;
                                void perform(() => apiRequest("/api/windows/open-recording-folder", {
                                    method: "POST", body: JSON.stringify({ recording_id: recording.recording_id, server_instance_id: recording.server_instance_id }),
                                }));
                            }} />
                    </div>
                    </details>
            </Card>
        </div>
    </ViewLayout>;
}

function formatRecordingTime(value: string | null | undefined): string {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("ja-JP", { hour12: false });
}
