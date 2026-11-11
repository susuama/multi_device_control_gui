import { useState, useSyncExternalStore } from "react";
import { backendStore } from "../state/backendStore";
import { applicationRuntime } from "../state/applicationRuntime";
import { ViewMessage } from "./View";

function useBackendStatus() {
    const backend = useSyncExternalStore(backendStore.subscribe, backendStore.getSnapshot);
    const runtime = useSyncExternalStore(applicationRuntime.subscribe, applicationRuntime.getSnapshot);
    const requests = Object.values(backend.requests);
    const unknown = requests.some((request) => request.outcomeUnknown);
    const errors = [...new Set([backend.synchronization.error, runtime.error, ...requests.map((request) => request.error)].filter(Boolean))];
    const refreshing = backend.synchronization.status === "refreshing" || runtime.phase === "initializing";
    return { lastUpdatedAt: backend.synchronization.lastUpdatedAt, errors, unknown, refreshing };
}

/** ウィンドウ共通フッターへ、最終確認時刻と再取得ボタンを表示する。 */
export function BackendStatusSummary() {
    const { lastUpdatedAt, refreshing } = useBackendStatus();
    const [manualRefreshing, setManualRefreshing] = useState(false);
    return <div className="backend-status-summary">
        {lastUpdatedAt !== null && <time className="backend-status-time" dateTime={new Date(lastUpdatedAt).toISOString()}>
            最終確認: {new Date(lastUpdatedAt).toLocaleTimeString("ja-JP", { hour12: false })}
        </time>}
        <button type="button" disabled={refreshing || manualRefreshing} onClick={() => {
            setManualRefreshing(true);
            // 失敗理由はruntimeと共有ストアから表示する。
            void applicationRuntime.refresh().catch(() => {}).finally(() => setManualRefreshing(false));
        }}>状態を再取得</button>
    </div>;
}

/** エラー・結果不明の詳細を上部に表示する。 */
export function BackendStatus() {
    const { errors, unknown } = useBackendStatus();
    if (!unknown && errors.length === 0) return null;
    return <div className="backend-status">
        {unknown && <ViewMessage tone="warning">結果不明の操作があります。状態を再取得して、現在の機器・記録状態を確認してください。</ViewMessage>}
        {errors.length > 0 && <ViewMessage tone="error">{errors.join("\n")}</ViewMessage>}
    </div>;
}
