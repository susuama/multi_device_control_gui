import { useMemo, useState, useSyncExternalStore } from "react";
import { getWebSocketClient } from "../api/websocketClient";

const statusLabels = {
    disconnected: "未接続",
    connecting: "接続中…",
    connected: "接続済み",
    closing: "切断中…",
};

/** 共有WebSocketの接続操作と最新の受信データを表示する。 */
export function WebSocketPanel({ panelId }: { panelId: string }) {
    const [key, setKey] = useState("");
    const [queueSize, setQueueSize] = useState("100");
    const [subscription, setSubscription] = useState({ key: null as string | null, maxsize: 100 });
    const websocketClient = getWebSocketClient(subscription.key, subscription.maxsize);
    const state = useSyncExternalStore(websocketClient.subscribe, websocketClient.getSnapshot);
    const inputId = `${panelId}-websocket`;
    const receivedText = useMemo(() => {
        if (!state.latestMessage) return "";
        try {
            return JSON.stringify(JSON.parse(state.latestMessage), null, 2);
        } catch {
            return state.latestMessage;
        }
    }, [state.latestMessage]);
    const statusText = state.status === "disconnected" && state.error ? "接続エラー" : statusLabels[state.status];
    const statusClass = state.error ? "status-error" : state.status === "connected" ? "status-success" : "";

    return (
        <section className="api-panel" aria-labelledby={`${inputId}-title`}>
            <header className="panel-header">
                <div>
                    <h2 id={`${inputId}-title`}>イベント配信</h2>
                    <p>サンプリング中のデバイスの測定データを受信します。</p>
                </div>
                <div className="endpoint-address"><span className="method">WebSocket</span><code>/ws / /ws/キー</code></div>
            </header>
            <div className="panel-columns">
                <div className="request-column">
                    <h3>接続</h3>
                    <p className="no-body">接続と受信状態は、このウィンドウ内のAPI画面で共有します。</p>
                    <label className="field" htmlFor={`${inputId}-key`}>購読キー（空欄は /ws）
                        <input id={`${inputId}-key`} value={key} onChange={(event) => setKey(event.target.value)} />
                    </label>
                    <label className="field" htmlFor={`${inputId}-queue`}>Queue上限（画像は1を推奨）
                        <input id={`${inputId}-queue`} type="number" min="1" step="1" value={queueSize}
                            onChange={(event) => setQueueSize(event.target.value)} />
                    </label>
                    <button type="button" disabled={!Number.isSafeInteger(Number(queueSize)) || Number(queueSize) < 1}
                        onClick={() => setSubscription({ key: key || null, maxsize: Number(queueSize) })}>表示する購読条件を選択</button>
                    <p className="no-body">中核からの購読 {state.leaseCount} 件。手動接続の解除では、中核が利用中の接続を切断しません。</p>
                    <code className="request-path">{state.url || "/ws"}</code>
                    <label className="websocket-log-toggle" htmlFor={`${inputId}-log-received`}>
                        <input id={`${inputId}-log-received`} type="checkbox" checked={state.logReceived}
                            onChange={(event) => websocketClient.setLogReceived(event.target.checked)} />
                        受信データをログに表示
                    </label>
                    <div className="websocket-controls">
                        <button type="button" onClick={websocketClient.connect} disabled={state.manualConnection}>接続</button>
                        <button type="button" onClick={websocketClient.disconnect}
                            disabled={!state.manualConnection}>手動接続を解除</button>
                    </div>
                    {state.error && <pre className="response-body response-error" role="alert">{state.error}</pre>}
                    {state.dataError && <pre className="response-body response-error" role="alert">{state.dataError}</pre>}
                </div>
                <div className="response-column">
                    <div className="response-heading">
                        <h3>最新の受信データ</h3>
                        <span className={`response-status ${statusClass}`} role="status">{statusText} / {state.receivedCount}件</span>
                    </div>
                    {state.receivedCount > 0 ? (
                        <pre className="response-body">{receivedText || "（本文なし）"}</pre>
                    ) : (
                        <div className="response-body response-empty">接続後、測定データを受信するとここに表示します。</div>
                    )}
                </div>
            </div>
        </section>
    );
}
