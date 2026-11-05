import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { disposeWebSocketClients, getWebSocketClient, websocketClient } from "../src/api/websocketClient.ts";
import { logStore } from "../src/logging/logStore.ts";
import { measurementStore } from "../src/state/measurementStore.ts";

const sample = (value, id = "meter-test") => JSON.stringify({ type: "measurement", id, server_instance_id: "server-test", measurement_id: "measurement-test",
    timestamp_ns: 1000 + value, elapsed_s: value, datetime: "2026-11-02T00:00:00+09:00", data: { power_w: value } });

const originalWebSocket = globalThis.WebSocket;
const originalWindow = globalThis.window;

class FakeWebSocket {
    static instances = [];
    static nextError = null;
    onopen = null;
    onmessage = null;
    onerror = null;
    onclose = null;
    closeCalls = [];

    constructor(url) {
        if (FakeWebSocket.nextError) {
            const error = FakeWebSocket.nextError;
            FakeWebSocket.nextError = null;
            throw error;
        }
        this.url = url;
        FakeWebSocket.instances.push(this);
    }

    close(code, reason) { this.closeCalls.push({ code, reason }); }
    open() { this.onopen?.(); }
    receive(data) { this.onmessage?.({ data }); }
    fail() { this.onerror?.(); }
    finishClose({ code = 1000, reason = "", wasClean = true } = {}) {
        this.onclose?.({ code, reason, wasClean });
    }
}

beforeEach(() => {
    FakeWebSocket.instances = [];
    FakeWebSocket.nextError = null;
    globalThis.WebSocket = FakeWebSocket;
    globalThis.window = { location: { href: "http://127.0.0.1:5173/workspace" } };
    logStore.clear();
    measurementStore.setSession("server-test", "measurement-test");
});

afterEach(() => {
    disposeWebSocketClients();
    FakeWebSocket.instances.forEach((socket) => socket.finishClose());
    websocketClient.setLogReceived(false);
    globalThis.WebSocket = originalWebSocket;
    if (originalWindow === undefined) delete globalThis.window;
    else globalThis.window = originalWindow;
});

test("同じホストの /ws に接続し、接続中・接続済みの重複接続を防ぐ", () => {
    websocketClient.connect();
    const socket = FakeWebSocket.instances[0];
    assert.equal(socket.url, "ws://127.0.0.1:5173/ws");
    assert.equal(websocketClient.getSnapshot().status, "connecting");
    websocketClient.connect();
    socket.open();
    websocketClient.connect();
    assert.equal(FakeWebSocket.instances.length, 1);
    assert.equal(websocketClient.getSnapshot().status, "connected");
    assert.match(logStore.getSnapshot().at(-1).message, /接続成功/);
});

test("HTTPSの画面ではwssを使用する", () => {
    globalThis.window.location.href = "https://example.test:8443/workspace";
    websocketClient.connect();
    assert.equal(FakeWebSocket.instances[0].url, "wss://example.test:8443/ws");
});

test("複数の表示先へ状態を通知し、表示先がなくても最新の受信状態を保持する", (t) => {
    let first = 0;
    let second = 0;
    const unsubscribeFirst = websocketClient.subscribe(() => first++);
    const unsubscribeSecond = websocketClient.subscribe(() => second++);
    t.after(unsubscribeFirst);
    t.after(unsubscribeSecond);
    websocketClient.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive('{"value":1}');
    assert.equal(first, 4);
    assert.equal(second, 4);
    unsubscribeFirst();
    socket.receive('{"value":2}');
    assert.equal(first, 4);
    assert.equal(second, 5);
    unsubscribeSecond();
    socket.receive('{"value":3}');
    const state = websocketClient.getSnapshot();
    assert.equal(state.receivedCount, 3);
    assert.equal(state.latestMessage, '{"value":3}');
    assert.equal(state, websocketClient.getSnapshot());
    assert.equal(socket.closeCalls.length, 0);
});

test("受信データのログ出力は明示的に有効化した間だけ行う", () => {
    websocketClient.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive(sample(1));
    assert.equal(logStore.getSnapshot().length, 2);
    websocketClient.setLogReceived(true);
    socket.receive(sample(2));
    assert.match(logStore.getSnapshot().at(-1).message, /受信 .*"power_w":2/);
    websocketClient.setLogReceived(false);
    const logCount = logStore.getSnapshot().length;
    socket.receive(sample(3));
    assert.equal(logStore.getSnapshot().length, logCount);
    assert.equal(websocketClient.getSnapshot().receivedCount, 3);
});

test("購読条件ごとに接続を共有し、キーのエンコードとQueue上限をURLへ反映する", () => {
    const first = getWebSocketClient("camera /日本語", 1);
    const second = getWebSocketClient("camera /日本語", 1);
    const other = getWebSocketClient("camera-B", 1);
    assert.equal(first, second);
    assert.notEqual(first, other);
    first.connect();
    second.connect();
    other.connect();
    assert.equal(FakeWebSocket.instances.length, 2);
    assert.equal(FakeWebSocket.instances[0].url, "ws://127.0.0.1:5173/ws/camera%20%2F%E6%97%A5%E6%9C%AC%E8%AA%9E?maxsize=1");
    assert.equal(FakeWebSocket.instances[0].binaryType, "arraybuffer");
    first.disconnect();
    other.disconnect();
    assert.throws(() => getWebSocketClient("camera", 0));
});

test("数値を共有データへ反映し、不正データを保持せず次の正常受信で復帰する", () => {
    websocketClient.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive("{broken");
    assert.match(websocketClient.getSnapshot().dataError, /共有状態へ反映/);
    socket.receive(sample(42, "shared-meter"));
    assert.equal(websocketClient.getSnapshot().dataError, "");
    assert.equal(measurementStore.getSnapshot().measurements["shared-meter"].at(-1).data.power_w, 42);
    websocketClient.disconnect();
    socket.receive(sample(99, "shared-meter"));
    assert.equal(measurementStore.getSnapshot().measurements["shared-meter"].at(-1).data.power_w, 42);
});

test("購読キーと画像の取得元IDを独立して扱い、JPEGを共有する", () => {
    const client = getWebSocketClient("group-key", 1);
    client.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    const meta = { server_instance_id: "server-test", measurement_id: "measurement-test", type: "camera_preview", id: "origin-camera", frame_index: 3,
        timestamp_ns: "1000000000000000003", width: 10, height: 10 };
    const header = new TextEncoder().encode(JSON.stringify(meta));
    const buffer = new Uint8Array(4 + header.length + 2);
    new DataView(buffer.buffer).setUint32(0, header.length, false);
    buffer.set(header, 4);
    buffer.set([0xff, 0xd8], 4 + header.length);
    socket.receive(buffer.buffer);
    assert.equal(measurementStore.getSnapshot().previews["origin-camera"].jpeg.size, 2);
    assert.equal(measurementStore.getSnapshot().previews["group-key"], undefined);
    assert.equal(JSON.parse(client.getSnapshot().latestMessage).jpeg_bytes, 2);
    assert.equal(client.getSnapshot().dataError, "");
    client.disconnect();
});

test("手動切断中の新しい要求は切断完了後に接続する", () => {
    websocketClient.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive("last message");
    websocketClient.disconnect();
    websocketClient.disconnect();
    websocketClient.connect();
    assert.equal(websocketClient.getSnapshot().status, "closing");
    assert.equal(FakeWebSocket.instances.length, 1);
    assert.deepEqual(socket.closeCalls, [{ code: 1000, reason: "Client disconnect" }]);
    socket.finishClose();
    assert.equal(websocketClient.getSnapshot().status, "connecting");
    assert.equal(FakeWebSocket.instances.length, 2);
    assert.equal(websocketClient.getSnapshot().latestMessage, "");
    assert.equal(websocketClient.getSnapshot().receivedCount, 0);
    assert.equal(websocketClient.getSnapshot().error, "");
});

test("接続試行の手動キャンセルを通信失敗として表示しない", () => {
    websocketClient.connect();
    const socket = FakeWebSocket.instances[0];
    websocketClient.disconnect();
    socket.open();
    socket.fail();
    assert.equal(websocketClient.getSnapshot().status, "closing");
    socket.finishClose({ code: 1006, wasClean: false });
    assert.equal(websocketClient.getSnapshot().status, "disconnected");
    assert.equal(websocketClient.getSnapshot().error, "");
    assert.equal(logStore.getSnapshot().some((entry) => entry.level === "ERROR"), false);
});

test("通信エラーと異常切断のコード・理由を残し、再接続時に受信状態を初期化する", () => {
    websocketClient.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive("last message");
    socket.fail();
    socket.finishClose({ code: 1006, reason: "connection lost", wasClean: false });
    assert.match(websocketClient.getSnapshot().error, /通信でエラー/);
    assert.match(websocketClient.getSnapshot().error, /1006.*connection lost/);
    assert.equal(websocketClient.getSnapshot().status, "disconnected");
    assert.equal(logStore.getSnapshot().at(-1).level, "WARN");
    websocketClient.connect();
    assert.equal(FakeWebSocket.instances.length, 2);
    assert.equal(websocketClient.getSnapshot().receivedCount, 0);
    assert.equal(websocketClient.getSnapshot().latestMessage, "");
    assert.equal(websocketClient.getSnapshot().error, "");
});

test("通信エラー後に正常な切断通知が届いてもエラー内容を残す", () => {
    websocketClient.connect();
    const socket = FakeWebSocket.instances[0];
    socket.fail();
    const error = websocketClient.getSnapshot().error;
    socket.finishClose();
    assert.equal(websocketClient.getSnapshot().error, error);
});

test("再接続前のソケットの遅れた通知で現在の接続を変更しない", () => {
    websocketClient.connect();
    const previous = FakeWebSocket.instances[0];
    previous.finishClose();
    websocketClient.connect();
    const current = FakeWebSocket.instances[1];
    current.open();
    current.receive("current message");
    const state = websocketClient.getSnapshot();
    const logs = logStore.getSnapshot();
    previous.open();
    previous.receive("old message");
    previous.fail();
    previous.finishClose({ code: 1006, wasClean: false });
    assert.equal(websocketClient.getSnapshot(), state);
    assert.equal(logStore.getSnapshot(), logs);
});

test("接続開始時の例外を表示とログへ記録し、再試行を可能にする", () => {
    FakeWebSocket.nextError = new Error("connection blocked");
    websocketClient.connect();
    assert.equal(websocketClient.getSnapshot().status, "disconnected");
    assert.match(websocketClient.getSnapshot().error, /connection blocked/);
    assert.equal(logStore.getSnapshot().at(-1).level, "ERROR");
    websocketClient.connect();
    assert.equal(websocketClient.getSnapshot().status, "connecting");
    assert.equal(FakeWebSocket.instances.length, 1);
});


test("acquireの参照数と手動要求を分け、最後の解除だけで接続を閉じる", () => {
    const client = getWebSocketClient("leases", 1);
    const first = client.acquire();
    const second = client.acquire();
    const socket = FakeWebSocket.instances[0];
    client.connect();
    assert.equal(client.getSnapshot().leaseCount, 2);
    assert.equal(client.getSnapshot().manualConnection, true);
    client.disconnect();
    assert.equal(socket.closeCalls.length, 0);
    first();
    first();
    assert.equal(client.getSnapshot().leaseCount, 1);
    assert.equal(socket.closeCalls.length, 0);
    second();
    assert.equal(client.getSnapshot().leaseCount, 0);
    assert.equal(socket.closeCalls.length, 1);
});

test("必要な間だけ間隔を延ばして再接続し、解除時に待機を回収する", (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const client = getWebSocketClient("retries", 1);
    const release = client.acquire();
    FakeWebSocket.instances[0].finishClose({ code: 1006, wasClean: false });
    assert.equal(client.getSnapshot().retryCount, 1);
    t.mock.timers.tick(999);
    assert.equal(FakeWebSocket.instances.length, 1);
    t.mock.timers.tick(1);
    assert.equal(FakeWebSocket.instances.length, 2);
    FakeWebSocket.instances[1].finishClose();
    assert.equal(client.getSnapshot().retryCount, 2);
    t.mock.timers.tick(1999);
    assert.equal(FakeWebSocket.instances.length, 2);
    t.mock.timers.tick(1);
    assert.equal(FakeWebSocket.instances.length, 3);
    FakeWebSocket.instances[2].open();
    assert.equal(client.getSnapshot().retryCount, 0);
    FakeWebSocket.instances[2].finishClose();
    release();
    t.mock.timers.tick(60000);
    assert.equal(FakeWebSocket.instances.length, 3);
    assert.equal(client.getSnapshot().retryCount, 0);
});

test("全解放後は古い受信と再試行を無視し、古い解除関数で新規登録を減らさない", (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const first = getWebSocketClient("dispose-A", 1);
    const second = getWebSocketClient("dispose-B", 1);
    const releaseOld = first.acquire();
    second.acquire();
    const previous = FakeWebSocket.instances[0];
    FakeWebSocket.instances[1].finishClose();
    disposeWebSocketClients();
    assert.equal(first.getSnapshot().leaseCount, 0);
    assert.equal(second.getSnapshot().leaseCount, 0);
    const state = first.getSnapshot();
    previous.open();
    previous.receive(sample(1));
    previous.finishClose();
    t.mock.timers.tick(60000);
    assert.equal(first.getSnapshot(), state);
    assert.equal(FakeWebSocket.instances.length, 2);
    const releaseNew = first.acquire();
    releaseOld();
    assert.equal(first.getSnapshot().leaseCount, 1);
    FakeWebSocket.instances[2].receive(sample(2));
    assert.equal(typeof first.getSnapshot().lastReceivedAt, "number");
    releaseNew();
});
