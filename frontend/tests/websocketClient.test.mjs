import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { websocketClient } from "../src/api/websocketClient.ts";
import { logStore } from "../src/logging/logStore.ts";

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
});

afterEach(() => {
    websocketClient.disconnect();
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
    assert.equal(first, 3);
    assert.equal(second, 3);
    unsubscribeFirst();
    socket.receive('{"value":2}');
    assert.equal(first, 3);
    assert.equal(second, 4);
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
    socket.receive('{"value":1}');
    assert.equal(logStore.getSnapshot().length, 2);
    websocketClient.setLogReceived(true);
    socket.receive('{"value":2}');
    assert.match(logStore.getSnapshot().at(-1).message, /受信 .*"value":2/);
    websocketClient.setLogReceived(false);
    const logCount = logStore.getSnapshot().length;
    socket.receive('{"value":3}');
    assert.equal(logStore.getSnapshot().length, logCount);
    assert.equal(websocketClient.getSnapshot().receivedCount, 3);
});

test("手動切断の完了までは再接続せず、切断後も直前の受信内容を保持する", () => {
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
    assert.equal(websocketClient.getSnapshot().status, "disconnected");
    assert.equal(websocketClient.getSnapshot().latestMessage, "last message");
    assert.equal(websocketClient.getSnapshot().receivedCount, 1);
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
