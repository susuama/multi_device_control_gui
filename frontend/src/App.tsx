import { useState } from "react";
import { apiRawRequest } from "./api/client";
import "./App.css";

type Endpoint = {
    id: string;
    method: "GET" | "POST";
    path: string;
    title: string;
    description: string;
    initialBody?: string;
    deviceId?: boolean;
};

const endpoints: Endpoint[] = [
    { id: "status", method: "GET", path: "/api/status", title: "状態取得", description: "バックエンドの状態を取得します。" },
    { id: "devices", method: "GET", path: "/api/devices", title: "デバイス一覧", description: "登録されているデバイスの情報を取得します。" },
    { id: "scan", method: "POST", path: "/api/devices/scan", title: "デバイススキャン", description: "デバイスを探索し、更新後の一覧を取得します。" },
    { id: "device", method: "GET", path: "/api/devices/{device_id}", title: "デバイス個別取得", description: "指定したIDのデバイス情報を取得します。", deviceId: true },
    { id: "windows", method: "GET", path: "/api/windows", title: "ウィンドウ一覧", description: "現在表示されているウィンドウの情報を取得します。" },
    {
        id: "create-window", method: "POST", path: "/api/windows", title: "ウィンドウ作成",
        description: "指定したタイトルとパスでウィンドウを作成します。",
        initialBody: JSON.stringify({ title: "テストウィンドウ", path: "/" }, null, 2),
    },
];

type ApiResponse = {
    status: number;
    statusText: string;
    body: string;
};

function ApiPanel({ endpoint }: { endpoint: Endpoint }) {
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
            return;
        }

        if (endpoint.initialBody !== undefined) {
            try {
                JSON.parse(body);
            } catch {
                setError("JSONの構文が正しくありません。修正してから送信してください。");
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
        <section className="api-panel" aria-labelledby={`${endpoint.id}-title`}>
            <header className="panel-header">
                <div>
                    <h2 id={`${endpoint.id}-title`}>{endpoint.title}</h2>
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
                        <label className="field" htmlFor={`${endpoint.id}-device-id`}>
                            デバイスID
                            <input
                                id={`${endpoint.id}-device-id`}
                                value={deviceId}
                                onChange={(event) => setDeviceId(event.target.value)}
                                disabled={sending}
                                spellCheck={false}
                            />
                        </label>
                    )}
                    {endpoint.initialBody !== undefined ? (
                        <label className="field" htmlFor={`${endpoint.id}-body`}>
                            JSON本文
                            <textarea
                                id={`${endpoint.id}-body`}
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

function App() {
    return (
        <main className="app">
            <header className="app-header">
                <div>
                    <h1>API通信テスト</h1>
                    <p>リクエストを送信し、HTTPステータスと応答本文を確認します。</p>
                </div>
                <span className="api-count">{endpoints.length} API</span>
            </header>
            <div className="api-list">
                {endpoints.map((endpoint) => <ApiPanel key={endpoint.id} endpoint={endpoint} />)}
            </div>
        </main>
    );
}

export default App;
