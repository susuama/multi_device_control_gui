import assert from "node:assert/strict";
import { test } from "node:test";
import { createApplicationRuntime } from "../src/state/applicationRuntime.ts";

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function fixture(options = {}) {
    let snapshot = { devices: [{ id: "camera", type: "camera" }, { id: "meter", type: "power_meter" }],
        measurement: { server_instance_id: "server", measurement_id: "measurement", sampling_device_ids: ["camera", "meter"] },
        synchronization: { status: "ready", error: "" } };
    const backendListeners = new Set();
    const measurementListeners = new Set();
    let data = { session: null, needsResync: false, streams: {} };
    let refreshCount = 0;
    let refreshTask = async () => {};
    const backend = {
        getSnapshot: () => snapshot,
        subscribe(fn) { backendListeners.add(fn); return () => backendListeners.delete(fn); },
        async refresh() { refreshCount++; await refreshTask(); },
    };
    const measurements = {
        getSnapshot: () => data,
        subscribe(fn) { measurementListeners.add(fn); return () => measurementListeners.delete(fn); },
        setSession(server_instance_id, measurement_id) { data = { ...data, session: { server_instance_id, measurement_id }, needsResync: false }; },
        setStreamState(id, value) { data = { ...data, streams: { ...data.streams, [id]: value } }; },
        refreshFreshness(value) { measurements.lastMaxAge = value; },
    };
    const clients = new Map();
    const getClient = (key, maxsize) => {
        if (!clients.has(key)) {
            const listeners = new Set();
            const client = { maxsize, count: 0, status: "connected", getSnapshot: () => ({ status: client.status }),
                subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
                acquire() { client.count++; return () => client.count--; },
                setStatus(value) { client.status = value; listeners.forEach((fn) => fn()); },
            };
            clients.set(key, client);
        }
        return clients.get(key);
    };
    const runtime = createApplicationRuntime({ backendStore: backend, measurementStore: measurements,
        getWebSocketClient: getClient, pollIntervalMs: 10000, ...options });
    return { runtime, clients, measurements, backendListeners, measurementListeners,
        get refreshCount() { return refreshCount; },
        setRefreshTask(task) { refreshTask = task; },
        change(changes) { snapshot = { ...snapshot, ...changes }; backendListeners.forEach((fn) => fn()); },
        resync() { data = { ...data, needsResync: true }; measurementListeners.forEach((fn) => fn()); },
    };
}

test("初期化を共有し、取得中のキーを購読して不要な接続だけを解放する", async (t) => {
    const f = fixture();
    t.after(() => f.runtime.dispose());
    const first = f.runtime.start();
    assert.equal(f.runtime.start(), first);
    assert.equal(f.runtime.refresh(), first);
    await first;
    assert.equal(f.refreshCount, 1);
    assert.equal(f.runtime.getSnapshot().phase, "ready");
    assert.equal(f.clients.get("camera").maxsize, 1);
    assert.equal(f.clients.get(null).maxsize, 100);
    assert.equal(f.clients.get(null).count, 1);
    assert.deepEqual(f.measurements.getSnapshot().session, { server_instance_id: "server", measurement_id: "measurement" });
    assert.equal(f.measurements.lastMaxAge, 5000);
    f.change({ measurement: { server_instance_id: "server", measurement_id: "next", sampling_device_ids: ["camera"] } });
    assert.equal(f.clients.get(null).count, 0);
    assert.equal(f.clients.get("camera").count, 1);
    assert.deepEqual(f.measurements.getSnapshot().streams.meter, { connected: false, sampling: false });
    f.clients.get("camera").setStatus("disconnected");
    assert.deepEqual(f.measurements.getSnapshot().streams.camera, { connected: false, sampling: true });
    f.clients.get("camera").setStatus("connected");
    await f.runtime.refresh();
    assert.equal(f.refreshCount, 2);
    f.resync();
    await f.runtime.refresh();
    assert.equal(f.refreshCount, 3);
    assert.equal(f.measurements.getSnapshot().needsResync, false);
});

test("周期処理中にGETを重ねず、終了後にのみ次の周期を開始する", async (t) => {
    const f = fixture({ pollIntervalMs: 10, staleAfterMs: 50 });
    t.after(() => f.runtime.dispose());
    let finish;
    f.setRefreshTask(() => new Promise((resolve) => { finish = resolve; }));
    const pending = f.runtime.start();
    await delay(35);
    assert.equal(f.refreshCount, 1);
    finish();
    await pending;
    assert.equal(f.measurements.lastMaxAge, 50);
    await delay(35);
    assert.equal(f.refreshCount, 2);
    finish();
    f.runtime.dispose();
});

test("初期化失敗を通知し、明示的な再確認で復旧する。旧APIを成功として扱わない", async (t) => {
    const f = fixture();
    t.after(() => f.runtime.dispose());
    f.setRefreshTask(async () => { throw new Error("測定状態API未対応"); });
    await assert.rejects(f.runtime.start(), /未対応/);
    assert.deepEqual(f.runtime.getSnapshot(), { phase: "error", error: "測定状態API未対応" });
    assert.equal(f.clients.size, 0);
    f.setRefreshTask(async () => {});
    await f.runtime.refresh();
    assert.equal(f.runtime.getSnapshot().phase, "ready");
    f.change({ measurement: null });
    await assert.rejects(f.runtime.refresh(), /契約/);
    assert.equal(f.runtime.getSnapshot().phase, "error");
});

test("dispose後に遅い初期化応答が来ても接続・購読・タイマーを復活させない", async () => {
    const f = fixture({ pollIntervalMs: 5 });
    let finish;
    f.setRefreshTask(() => new Promise((resolve) => { finish = resolve; }));
    const pending = f.runtime.start();
    await Promise.resolve();
    f.runtime.dispose();
    f.runtime.dispose();
    finish();
    await pending;
    await delay(20);
    assert.equal(f.runtime.getSnapshot().phase, "disposed");
    assert.equal(f.clients.size, 0);
    assert.equal(f.backendListeners.size, 0);
    assert.equal(f.measurementListeners.size, 0);
    await f.runtime.start();
    assert.equal(f.refreshCount, 1);
});

test("終了時はruntime自身の購読だけを解放し、機器操作を呼ばない", async () => {
    const f = fixture();
    await f.runtime.start();
    f.runtime.dispose();
    assert.equal(f.clients.get("camera").count, 0);
    assert.equal(f.clients.get(null).count, 0);
    assert.deepEqual(f.measurements.getSnapshot().streams.camera, { connected: false, sampling: true });
});
