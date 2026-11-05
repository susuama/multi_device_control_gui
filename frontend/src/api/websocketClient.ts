import { decodeEvent } from "./eventDecoder.ts";
import { measurementStore } from "../state/measurementStore.ts";
import { logger } from "../logging/logStore.ts";

type ConnectionStatus = "disconnected" | "connecting" | "connected" | "closing";
type WebSocketState = Readonly<{
    status: ConnectionStatus;
    url: string;
    receivedCount: number;
    latestMessage: string;
    error: string;
    logReceived: boolean;
    dataError: string;
    leaseCount: number;
    manualConnection: boolean;
    lastReceivedAt: number | null;
    retryCount: number;
}>;

function createWebSocketClient(key: string | null, maxsize: number) {
    let socket: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let closing = false;
    let generation = 0;
    const leases = new Set<symbol>();
    let state: WebSocketState = {
        status: "disconnected", url: "", receivedCount: 0, latestMessage: "", error: "", logReceived: false,
        dataError: "", leaseCount: 0, manualConnection: false, lastReceivedAt: null, retryCount: 0,
    };
    const listeners = new Set<() => void>();
    const needed = () => leases.size > 0 || state.manualConnection;

    function update(changes: Partial<WebSocketState>): void {
        state = { ...state, ...changes };
        listeners.forEach((listener) => listener());
    }

    function cancelRetry(): void {
        clearTimeout(retryTimer);
        retryTimer = undefined;
    }

    function scheduleRetry(): void {
        if (!needed() || retryTimer !== undefined) return;
        const delay = Math.min(1000 * 2 ** Math.min(state.retryCount, 5), 30000);
        update({ retryCount: state.retryCount + 1 });
        retryTimer = setTimeout(() => {
            retryTimer = undefined;
            open();
        }, delay);
    }

    function open(): void {
        if (!needed() || socket !== null) return;
        cancelRetry();
        const path = key === null ? "/ws" : `/ws/${encodeURIComponent(key)}`;
        const url = new URL(path, window.location.href);
        if (maxsize !== 100) url.searchParams.set("maxsize", String(maxsize));
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        closing = false;
        update({ status: "connecting", url: url.href, receivedCount: 0, latestMessage: "", error: "", dataError: "" });
        logger.info("websocket", `${url.href} 接続開始`);
        let connection: WebSocket;
        try {
            connection = new WebSocket(url.href);
        } catch (cause) {
            const message = `WebSocket接続を開始できませんでした: ${cause instanceof Error ? cause.message : String(cause)}`;
            update({ status: "disconnected", error: message });
            logger.error("websocket", message);
            scheduleRetry();
            return;
        }
        socket = connection;
        connection.binaryType = "arraybuffer";
        connection.onopen = () => {
            if (socket !== connection || closing) return;
            update({ status: "connected", retryCount: 0 });
            logger.info("websocket", `${url.href} 接続成功`);
        };
        connection.onmessage = (event: MessageEvent<string | ArrayBuffer>) => {
            if (socket !== connection || closing) return;
            let latestMessage = typeof event.data === "string" ? event.data : "画像バイナリ";
            let dataError = "";
            try {
                const received = decodeEvent(event.data);
                measurementStore.receive(received);
                if (received.type === "camera_preview") {
                    const { jpeg, ...metadata } = received;
                    latestMessage = JSON.stringify({ ...metadata, jpeg_bytes: jpeg.size });
                }
            } catch (cause) {
                dataError = `受信データを共有状態へ反映できませんでした: ${cause instanceof Error ? cause.message : String(cause)}`;
                if (dataError !== state.dataError) logger.error("websocket", dataError);
            }
            update({ receivedCount: state.receivedCount + 1, latestMessage, dataError, lastReceivedAt: Date.now() });
            if (state.logReceived) logger.info("websocket", `受信 ${latestMessage}`);
        };
        connection.onerror = () => {
            if (socket !== connection || closing) return;
            const message = "WebSocket通信でエラーが発生しました。バックエンドの起動と接続先を確認してください。";
            update({ error: message });
            logger.error("websocket", message);
        };
        connection.onclose = (event) => {
            if (socket !== connection) return;
            socket = null;
            const requested = closing;
            closing = false;
            const message = `${url.href} 切断 (code=${event.code}${event.reason ? `, reason=${event.reason}` : ""})`;
            if (!requested && !event.wasClean) {
                update({ status: "disconnected", error: state.error ? `${state.error}\n${message}` : message });
                logger.warn("websocket", message);
            } else {
                update({ status: "disconnected" });
                logger.info("websocket", message);
            }
            if (requested && needed()) open();
            else scheduleRetry();
        };
    }

    function stopIfUnused(): void {
        if (needed()) return;
        cancelRetry();
        if (state.retryCount !== 0) update({ retryCount: 0 });
        if (socket === null || closing) return;
        closing = true;
        update({ status: "closing", error: "" });
        logger.info("websocket", `${state.url} 切断要求`);
        socket.close(1000, "Client disconnect");
    }

    return {
        getSnapshot: () => state,
        subscribe(listener: () => void) {
            listeners.add(listener);
            return () => { listeners.delete(listener); };
        },
        acquire() {
            const lease = Symbol();
            const acquiredGeneration = generation;
            leases.add(lease);
            update({ leaseCount: leases.size });
            open();
            return () => {
                if (generation !== acquiredGeneration || !leases.delete(lease)) return;
                update({ leaseCount: leases.size });
                stopIfUnused();
            };
        },
        connect() {
            if (!state.manualConnection) update({ manualConnection: true });
            open();
        },
        disconnect() {
            if (state.manualConnection) update({ manualConnection: false });
            stopIfUnused();
        },
        dispose() {
            generation++;
            leases.clear();
            cancelRetry();
            const connection = socket;
            socket = null;
            closing = false;
            update({ leaseCount: 0, manualConnection: false, status: "disconnected", retryCount: 0 });
            if (connection) connection.close(1000, "Client dispose");
        },
        setLogReceived(enabled: boolean) {
            if (enabled !== state.logReceived) update({ logReceived: enabled });
        },
    };
}

const clients = new Map<string, ReturnType<typeof createWebSocketClient>>();

/** 同じウィンドウの同一購読条件では、接続と受信状態を共有する。 */
export function getWebSocketClient(key: string | null = null, maxsize = 100) {
    if (key === "" || !Number.isSafeInteger(maxsize) || maxsize < 1) throw new Error("購読キーまたはQueue上限が正しくありません。");
    const id = JSON.stringify([key, maxsize]);
    let client = clients.get(id);
    if (!client) {
        client = createWebSocketClient(key, maxsize);
        clients.set(id, client);
    }
    return client;
}

export const websocketClient = getWebSocketClient();

/** 機器操作を行わず、すべての受信要求と再接続待機を終了する。 */
export function disposeWebSocketClients(): void {
    clients.forEach((client) => client.dispose());
}

import.meta.hot?.dispose(disposeWebSocketClients);
