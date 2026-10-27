import { useState } from "react";
import { apiRawRequest } from "../api/client";
import { logger } from "../logging/logStore";
import { WebSocketPanel } from "./WebSocketPanel";
import "../App.css";

const apiGroups = {
    status: "状態",
    devices: "デバイス",
    windows: "ウィンドウ",
};

type Endpoint = {
    id: string;
    tag: keyof typeof apiGroups;
    method: "GET" | "POST";
    path: string;
    title: string;
    description: string;
    initialBody?: string;
    deviceId?: boolean;
};

const endpoints: Endpoint[] = [
    { id: "status", tag: "status", method: "GET", path: "/api/status", title: "状態取得", description: "バックエンドの状態を取得します。" },
    { id: "devices", tag: "devices", method: "GET", path: "/api/devices", title: "デバイス一覧", description: "登録されているデバイスの情報を取得します。" },
    { id: "scan", tag: "devices", method: "POST", path: "/api/devices/scan", title: "デバイススキャン", description: "デバイスを探索し、更新後の一覧を取得します。" },
    { id: "device", tag: "devices", method: "GET", path: "/api/devices/{device_id}", title: "デバイス個別取得", description: "指定したIDのデバイス情報を取得します。", deviceId: true },
    { id: "connect", tag: "devices", method: "POST", path: "/api/devices/{device_id}/connect", title: "デバイス接続", description: "指定したIDのデバイスに接続します。", deviceId: true },
    { id: "disconnect", tag: "devices", method: "POST", path: "/api/devices/{device_id}/disconnect", title: "デバイス切断", description: "指定したIDのデバイスのサンプリングを停止し、接続を切断します。", deviceId: true },
    { id: "start-sampling", tag: "devices", method: "POST", path: "/api/devices/{device_id}/sampling/start", title: "サンプリング開始", description: "指定したIDのデバイスのデータ取得を開始します。", deviceId: true },
    { id: "stop-sampling", tag: "devices", method: "POST", path: "/api/devices/{device_id}/sampling/stop", title: "サンプリング停止", description: "指定したIDのデバイスのデータ取得を停止します。", deviceId: true },
    { id: "sampling-status", tag: "devices", method: "GET", path: "/api/devices/{device_id}/sampling", title: "サンプリング状態取得", description: "指定したIDのデバイスがデータ取得中か確認します。", deviceId: true },
    { id: "windows", tag: "windows", method: "GET", path: "/api/windows", title: "ウィンドウ一覧", description: "現在表示されているウィンドウの情報を取得します。" },
    {
        id: "create-window", tag: "windows", method: "POST", path: "/api/windows", title: "ウィンドウ作成",
        description: "指定したタイトルとパスでウィンドウを作成します。",
        initialBody: JSON.stringify({ title: "テストウィンドウ", path: "/" }, null, 2),
    },
];

type ApiResponse = {
    status: number;
    statusText: string;
    body: string;
};

function ApiPanel({ endpoint, panelId }: { endpoint: Endpoint; panelId: string }) {
    const inputId = `${panelId}-${endpoint.id}`;
    const [body, setBody] = useState(endpoint.initialBody ?? "");
    const [deviceId, setDeviceId] = useState("power_meter_1");
    const [sending, setSending] = useState(false);
    const [response, setResponse] = useState<ApiResponse | null>(null);
    const [error, setError] = useState("");
    const requestPath = endpoint.deviceId
        ? endpoint.path.replace("{device_id}", encodeURIComponent(deviceId))
        : endpoint.path;

    async function sendRequest() {
        setError("");
        setResponse(null);

        if (endpoint.deviceId && !deviceId.trim()) {
            setError("デバイスIDを入力してください。リクエストは送信されていません。");
            logger.warn("frontend", "デバイスIDが未入力のため送信を中止しました。");
            return;
        }

        if (endpoint.initialBody !== undefined) {
            try {
                JSON.parse(body);
            } catch {
                setError("JSONの構文が正しくありません。修正してから送信してください。");
                logger.warn("frontend", "JSON本文の構文が正しくないため送信を中止しました。");
                return;
            }
        }

        setSending(true);
        try {
            const result = await apiRawRequest(requestPath, {
                method: endpoint.method,
                body: endpoint.initialBody !== undefined ? body : undefined,
            });
            const responseBody = await result.text();
            setResponse({ status: result.status, statusText: result.statusText, body: responseBody });
        } catch (cause) {
            const message = cause instanceof Error ? cause.message : String(cause);
            setError(`通信に失敗しました。バックエンドの起動と接続先を確認してください。\n${message}`);
        } finally {
            setSending(false);
        }
    }

    return (
        <section className="api-panel" aria-labelledby={`${inputId}-title`}>
            <header className="panel-header">
                <div>
                    <h2 id={`${inputId}-title`}>{endpoint.title}</h2>
                    <p>{endpoint.description}</p>
                </div>
                <div className="endpoint-address">
                    <span className={`method method-${endpoint.method.toLowerCase()}`}>{endpoint.method}</span>
                    <code>{endpoint.path}</code>
                </div>
            </header>

            <div className="panel-columns">
                <div className="request-column">
                    <h3>リクエスト</h3>
                    {endpoint.deviceId && (
                        <label className="field" htmlFor={`${inputId}-device-id`}>
                            デバイスID
                            <input
                                id={`${inputId}-device-id`}
                                value={deviceId}
                                onChange={(event) => setDeviceId(event.target.value)}
                                disabled={sending}
                                spellCheck={false}
                            />
                        </label>
                    )}
                    {endpoint.initialBody !== undefined ? (
                        <label className="field" htmlFor={`${inputId}-body`}>
                            JSON本文
                            <textarea
                                id={`${inputId}-body`}
                                value={body}
                                onChange={(event) => setBody(event.target.value)}
                                disabled={sending}
                                spellCheck={false}
                                rows={7}
                            />
                        </label>
                    ) : (
                        <p className="no-body">このAPIにはリクエスト本文を送信しません。</p>
                    )}
                    <div className="request-footer">
                        <code className="request-path">{requestPath}</code>
                        <button type="button" onClick={sendRequest} disabled={sending}>
                            {sending ? "送信中…" : "送信"}
                        </button>
                    </div>
                </div>

                <div className="response-column" aria-busy={sending}>
                    <div className="response-heading">
                        <h3>応答本文</h3>
                        <span
                            className={`response-status ${response ? response.status < 400 ? "status-success" : "status-error" : ""}`}
                            role="status"
                        >
                            {sending ? "送信中…" : response
                                ? `HTTP ${response.status} ${response.statusText}`
                                : error ? "送信失敗" : "未送信"}
                        </span>
                    </div>
                    {error ? (
                        <pre className="response-body response-error" role="alert">{error}</pre>
                    ) : response ? (
                        <pre className="response-body">{response.body || "（応答本文なし）"}</pre>
                    ) : (
                        <div className="response-body response-empty">
                            {sending ? "応答を待っています。" : "送信すると、受け取った応答本文をここに表示します。"}
                        </div>
                    )}
                </div>
            </div>
        </section>
    );
}

/** APIをtagsごとの折り畳み領域にまとめて表示する。 */
export function ApiTestView({ panelId }: { panelId: string }) {
    return (
        <div className="api-list">
            {Object.entries(apiGroups).map(([tag, title]) => {
                const groupEndpoints = endpoints.filter((endpoint) => endpoint.tag === tag);
                return (
                    <details className="api-group" key={tag}>
                        <summary>
                            {title}<code className="api-group-tag">{tag}</code>
                            <span className="api-group-count">{groupEndpoints.length}件</span>
                        </summary>
                        <div className="api-group-content">
                            {groupEndpoints.map((endpoint) => <ApiPanel key={endpoint.id} endpoint={endpoint} panelId={panelId} />)}
                        </div>
                    </details>
                );
            })}
            <details className="api-group">
                <summary>
                    WebSocket<code className="api-group-tag">websocket</code>
                    <span className="api-group-count">1件</span>
                </summary>
                <div className="api-group-content"><WebSocketPanel panelId={panelId} /></div>
            </details>
        </div>
    );
}

