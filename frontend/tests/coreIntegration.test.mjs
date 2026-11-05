import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { createBackendStore } from "../src/state/backendStore.ts";
import { measurementStore } from "../src/state/measurementStore.ts";
import { createApplicationRuntime } from "../src/state/applicationRuntime.ts";
import { getWebSocketClient, disposeWebSocketClients } from "../src/api/websocketClient.ts";

const original = { fetch: globalThis.fetch, WebSocket: globalThis.WebSocket, window: globalThis.window };
afterEach(() => { disposeWebSocketClients(); Object.assign(globalThis, original); });
const settle = async () => { for (let i = 0; i < 15; i++) await new Promise((done) => setImmediate(done)); };

test("将来APIの実ストア・通信・運転を結合し、受信共有・測定切替・終了まで確認する", async () => {
    let server = "integration-server", period = "integration-period";
    let invalidRecording = false;
    const device = { id: "meter-integration", type: "power_meter", manufacturer: "test", model: "test", serial_number: "1", address: "test", status: "connected" };
    globalThis.window = { location: { href: "http://localhost/" } };
    class Socket {
        static instances = [];
        constructor(url) { this.url = url; Socket.instances.push(this); queueMicrotask(() => this.onopen?.()); }
        close() { queueMicrotask(() => this.onclose?.({ code: 1000, wasClean: true })); }
        receive(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
    }
    globalThis.WebSocket = Socket;
    globalThis.fetch = async (path) => {
        if (path === "/api/devices") return Response.json([device]);
        if (path.endsWith("/settings")) return Response.json({ wavelength: 500, range: 1, auto_range: true, bandwidth: "low", averaging: 1 });
        if (path === "/api/measurements/status") return Response.json({ server_instance_id: server, measurement_id: period,
            started_at: "2026-11-02T00:00:00+09:00", sampling_device_ids: [device.id], error: null });
        if (path === "/api/recordings/status" && invalidRecording) return Response.json({ is_recording: false });
        if (path === "/api/recordings/status") return Response.json({ server_instance_id: server, recording_id: null, measurement_id: null,
            is_recording: false, is_stopping: false, directory: null, name: null, error: null, targets: {} });
        throw new Error(`Unexpected request: ${path}`);
    };
    const backend = createBackendStore();
    const runtime = createApplicationRuntime({ backendStore: backend, measurementStore, getWebSocketClient, pollIntervalMs: 60000 });
    try {
        await Promise.all([runtime.start(), runtime.start()]);
        await settle();
        assert.equal(runtime.getSnapshot().phase, "ready");
        assert.equal(Socket.instances.length, 1);
        assert.equal(getWebSocketClient().getSnapshot().leaseCount, 1);
        let first = 0, second = 0;
        const off1 = measurementStore.subscribe(() => first++), off2 = measurementStore.subscribe(() => second++);
        const sample = () => ({ type: "measurement", id: device.id, server_instance_id: server, measurement_id: period,
            timestamp_ns: 1000, elapsed_s: 1, datetime: "2026-11-02T00:00:01+09:00", data: { power_w: 42 } });
        Socket.instances[0].receive(sample());
        assert.equal(measurementStore.getSnapshot().measurements[device.id].at(-1).data.power_w, 42);
        assert.equal(first, second);
        off1(); off2();
        invalidRecording = true;
        await assert.rejects(backend.refreshRecording(), /unsupported/);
        assert.equal(backend.getSnapshot().synchronization.status, "error");
        Socket.instances[0].receive(sample());
        assert.equal(measurementStore.getSnapshot().measurements[device.id].length, 2);
        invalidRecording = false;
        await runtime.refresh();
        assert.equal(Socket.instances.length, 1);
        assert.equal(getWebSocketClient().getSnapshot().leaseCount, 1);
        server = "restarted-server"; period = "next-period";
        await runtime.refresh();
        await settle();
        assert.equal(measurementStore.getSnapshot().measurements[device.id], undefined);
        Socket.instances[0].receive({ ...sample(), server_instance_id: "integration-server", measurement_id: "integration-period" });
        assert.equal(measurementStore.getSnapshot().measurements[device.id], undefined);
        await settle();
        Socket.instances[0].receive(sample());
        assert.equal(measurementStore.getSnapshot().measurements[device.id].length, 1);
        runtime.dispose();
        await settle();
        assert.equal(getWebSocketClient().getSnapshot().leaseCount, 0);
        assert.equal(measurementStore.getSnapshot().streams[device.id].stale, true);
    } finally { runtime.dispose(); }
});

test("現行の不足APIは初期化失敗として明示し、取得や記録の要求を自動送信しない", async () => {
    const requests = [];
    globalThis.fetch = async (path, options) => {
        requests.push([path, options.method ?? "GET"]);
        if (path === "/api/devices") return Response.json([]);
        if (path === "/api/measurements/status") return Response.json({ detail: "Not Found" }, { status: 404 });
        return Response.json({ is_recording: false, is_stopping: false, directory: null, name: null, error: null });
    };
    const runtime = createApplicationRuntime({ backendStore: createBackendStore(), pollIntervalMs: 60000 });
    try {
        await assert.rejects(runtime.start(), /unsupported/);
        assert.equal(runtime.getSnapshot().phase, "error");
        assert.ok(requests.every(([, method]) => method === "GET"));
    } finally { runtime.dispose(); }
});
