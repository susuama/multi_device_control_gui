import { useMemo, useSyncExternalStore } from "react";
import { websocketClient } from "../api/websocketClient";

const statusLabels = {
    disconnected: "未接続",
    connecting: "接続中…",
    connected: "接続済み",
    closing: "切断中…",
};

/** 共有WebSocketの接続操作と最新の受信データを表示する。 */
export function WebSocketPanel({ panelId }: { panelId: string }) {
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
                <div className="endpoint-address"><span className="method">WebSocket</span><code>/ws</code></div>
            </header>
            <div className="panel-columns">
                <div className="request-column">
                    <h3>接続</h3>
                    <p className="no-body">接続と受信状態は、このウィンドウ内のAPI画面で共有します。</p>
                    <code className="request-path">{state.url || "/ws"}</code>
                    <label className="websocket-log-toggle" htmlFor={`${inputId}-log-received`}>
                        <input id={`${inputId}-log-received`} type="checkbox" checked={state.logReceived}
                            onChange={(event) => websocketClient.setLogReceived(event.target.checked)} />
                        受信データをログに表示
                    </label>
                    <div className="websocket-controls">
                        <button type="button" onClick={websocketClient.connect} disabled={state.status !== "disconnected"}>接続</button>
                        <button type="button" onClick={websocketClient.disconnect}
                            disabled={state.status !== "connecting" && state.status !== "connected"}>切断</button>
                    </div>
                    {state.error && <pre className="response-body response-error" role="alert">{state.error}</pre>}
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
