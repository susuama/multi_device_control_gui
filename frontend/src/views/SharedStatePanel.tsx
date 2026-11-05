import { useState, useSyncExternalStore } from "react";
import { backendStore } from "../state/backendStore";
import { measurementStore } from "../state/measurementStore";
import { applicationRuntime } from "../state/applicationRuntime";
import { preferencesStore } from "../state/preferencesStore";

/** APIの確定値と受信データの共有状況を、APIテスト画面から確認する。 */
export function SharedStatePanel({ panelId }: { panelId: string }) {
    const backend = useSyncExternalStore(backendStore.subscribe, backendStore.getSnapshot);
    const data = useSyncExternalStore(measurementStore.subscribe, measurementStore.getSnapshot);
    const runtime = useSyncExternalStore(applicationRuntime.subscribe, applicationRuntime.getSnapshot);
    const preferences = useSyncExternalStore(preferencesStore.subscribe, preferencesStore.getSnapshot);
    const [error, setError] = useState("");
    const summary = {
        ...backend,
        runtime, preferences, session: data.session, streams: data.streams, needsResync: data.needsResync,
        measurements: Object.fromEntries(Object.entries(data.measurements).map(([id, values]) =>
            [id, { count: values.length, latest: values.at(-1) }])),
        previews: Object.fromEntries(Object.entries(data.previews).map(([id, { jpeg, ...metadata }]) =>
            [id, { ...metadata, jpeg_bytes: jpeg.size }])),
    };
    return (
        <details className="api-group">
            <summary>共有データ<code className="api-group-tag">同一ウィンドウの全パネルで共有</code></summary>
            <div className="api-group-content">
                <p className="no-body">APIの結果と受信データを保持します。null・未登録は未取得です。失敗時は直前の値とrequestsのエラーを併記します。</p>
                <p role="status">中核状態: {runtime.phase} {runtime.error}</p>
                <label className="field" htmlFor={`${panelId}-default-directory`}>標準保存先
                    <input id={`${panelId}-default-directory`} value={preferences.defaultDirectory}
                        onChange={(event) => preferencesStore.update({ defaultDirectory: event.target.value })} />
                </label>
                {preferences.error && <p role="alert">{preferences.error}</p>}
                <button type="button" disabled={Object.values(backend.requests).some((request) => request.pending > 0)}
                    onClick={async () => {
                        setError("");
                        try { await applicationRuntime.refresh(); }
                        catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
                    }}>状態を再取得</button>
                {error && <pre className="response-body response-error" role="alert">{error}</pre>}
                <pre className="response-body" aria-label={`${panelId}の共有データ`}>{JSON.stringify(summary, null, 2)}</pre>
            </div>
        </details>
    );
}
