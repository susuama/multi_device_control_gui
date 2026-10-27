import { logger } from "../logging/logStore.ts";

type ConnectionStatus = "disconnected" | "connecting" | "connected" | "closing";
type WebSocketState = Readonly<{
    status: ConnectionStatus;
    url: string;
    receivedCount: number;
    latestMessage: string;
    error: string;
    logReceived: boolean;
}>;

// 接続と受信状態はビューの外で保持し、現在のウィンドウ内で共有する。
let socket: WebSocket | null = null;
let disconnectRequested = false;
let state: WebSocketState = {
    status: "disconnected", url: "", receivedCount: 0, latestMessage: "", error: "", logReceived: false,
};
const listeners = new Set<() => void>();

function update(changes: Partial<WebSocketState>): void {
    state = { ...state, ...changes };
    listeners.forEach((listener) => listener());
}

/** 現在の画面と同じホストの /ws へ接続する。接続中・切断待ちの再接続は行わない。 */
function connect(): void {
    if (socket !== null) return;

    const url = new URL("/ws", window.location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    disconnectRequested = false;
    update({ status: "connecting", url: url.href, receivedCount: 0, latestMessage: "", error: "" });
    logger.info("websocket", `${url.href} 接続開始`);

    let connection: WebSocket;
    try {
        connection = new WebSocket(url.href);
    } catch (cause) {
        const message = `WebSocket接続を開始できませんでした: ${cause instanceof Error ? cause.message : String(cause)}`;
        update({ status: "disconnected", error: message });
        logger.error("websocket", message);
        return;
    }
    socket = connection;

    connection.onopen = () => {
        if (socket !== connection || disconnectRequested) return;
        update({ status: "connected" });
        logger.info("websocket", `${url.href} 接続成功`);
    };

    connection.onmessage = (event: MessageEvent<string>) => {
        // 再接続前のソケットから届いたイベントは、現在の受信状態へ反映しない。
        if (socket !== connection) return;
        update({ receivedCount: state.receivedCount + 1, latestMessage: event.data });
        if (state.logReceived) logger.info("websocket", `受信 ${event.data}`);
    };

    connection.onerror = () => {
        if (socket !== connection || disconnectRequested) return;
        const message = "WebSocket通信でエラーが発生しました。バックエンドの起動と接続先を確認してください。";
        update({ error: message });
        logger.error("websocket", message);
    };

    connection.onclose = (event) => {
        if (socket !== connection) return;
        socket = null;
        const message = `${url.href} 切断 (code=${event.code}${event.reason ? `, reason=${event.reason}` : ""})`;
        if (!disconnectRequested && !event.wasClean) {
            update({ status: "disconnected", error: state.error ? `${state.error}\n${message}` : message });
            logger.warn("websocket", message);
        } else {
            // 直前の通信エラーを消さず、接続状態だけを未接続へ更新する。
            update({ status: "disconnected" });
            logger.info("websocket", message);
        }
    };
}

/** 接続中のソケット、または接続試行を終了する。機器のサンプリングは操作しない。 */
function disconnect(): void {
    if (socket === null || state.status === "closing") return;
    disconnectRequested = true;
    update({ status: "closing", error: "" });
    logger.info("websocket", `${state.url} 切断要求`);
    socket.close(1000, "Client disconnect");
}

export const websocketClient = {
    getSnapshot: () => state,
    subscribe(listener: () => void) {
        listeners.add(listener);
        return () => { listeners.delete(listener); };
    },
    connect,
    disconnect,
    setLogReceived(enabled: boolean) {
        update({ logReceived: enabled });
    },
};

// 開発中に通信モジュールを差し替える際は、古い接続を閉じる。
import.meta.hot?.dispose(disconnect);
