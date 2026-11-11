import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { createBackendStore } from "../src/state/backendStore.ts";
import { getOperationAvailability } from "../src/state/operationPolicy.ts";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
const device = (status = "connected") => ({ id: "camera", type: "camera", manufacturer: "Dummy", model: "Camera", serial_number: "1", address: "dummy", status });
const settings = (gain = 1) => ({ width: 640, height: 480, fps: 10, exposure: 0.01, gain, gain_boost: false, pixel_format: "mono8", roi: null });
const recording = (changes = {}) => ({ server_instance_id: "server", recording_id: null, measurement_id: null, targets: {}, is_recording: false, is_stopping: false, directory: null, name: null, error: null, ...changes });
const measurement = (changes = {}) => ({ server_instance_id: "server", measurement_id: "measurement", started_at: "2026-11-02T00:00:00Z", sampling_device_ids: [], error: null, ...changes });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };

test("停止待ちのエラー機器も状態として受け入れ、切断で復旧できる", async () => {
    const store = createBackendStore();
    let failed = true;
    globalThis.fetch = async (url, options) => {
        const path = new URL(String(url), "http://test").pathname;
        if (path.endsWith("/disconnect") && options?.method === "POST") {
            failed = false;
            return Response.json({ success: true });
        }
        const info = { ...device(failed ? "error" : "disconnected"), error: failed ? "USB read failed" : null };
        if (path === "/api/devices") return Response.json([info]);
        if (path === "/api/devices/camera") return Response.json(info);
        if (path === "/api/measurements/status") return Response.json(measurement({ sampling_device_ids: failed ? ["camera"] : [] }));
        if (path === "/api/recordings/status") return Response.json(recording());
        throw new Error(`Unexpected request: ${path}`);
    };
    await store.refresh();
    assert.equal(store.getSnapshot().synchronization.status, "ready");
    assert.equal(store.getSnapshot().devices[0].error, "USB read failed");
    await store.disconnectDevice("camera");
    assert.equal(store.getSnapshot().devices[0].status, "disconnected");
    assert.equal(store.getSnapshot().devices[0].error, null);
});

test("探索応答を一覧へ直接格納せず、管理用の機器一覧を再取得する", async () => {
    const store = createBackendStore();
    const calls = [];
    const discovered = { driver: "UC480", type: "camera", manufacturer: "Thorlabs", model: "C1285R12M", serial_number: "123", address: "7" };
    const listed = { ...device("disconnected"), model: "C1285R12M" };
    globalThis.fetch = async (url, options) => {
        const path = new URL(String(url), "http://test").pathname;
        calls.push([path, options?.method ?? "GET"]);
        if (path === "/api/devices/scan") return Response.json([discovered]);
        if (path === "/api/devices") return Response.json([listed]);
        throw new Error(`Unexpected request: ${path}`);
    };
    await store.scanDevices();
    assert.deepEqual(calls, [["/api/devices/scan", "POST"], ["/api/devices", "GET"]]);
    assert.deepEqual(store.getSnapshot().devices, [listed]);
});

test("確認済み状態は再取得中も維持し、操作可否を切り替えない", async () => {
    const store = createBackendStore(); globalThis.fetch = server();
    await store.refresh();
    const confirmed = store.getSnapshot();
    const gate = deferred();
    globalThis.fetch = server({ "/api/measurements/status": async () => { await gate.promise; return Response.json(measurement()); } });
    const states = [];
    const off = store.subscribe(() => states.push(store.getSnapshot().synchronization.status));
    const pending = store.refresh();
    assert.equal(store.getSnapshot(), confirmed);
    assert.equal(getOperationAvailability(store.getSnapshot(), { operation: "updateSettings", deviceId: "camera", settings: settings(2) }).allowed, true);
    gate.resolve(); await pending; off();
    assert.ok(states.every(status => status === "ready"));
});

test("再取得中に設定を変更しても古い一括GETで適用済み値へ戻さない", async () => {
    const store = createBackendStore(); globalThis.fetch = server(); await store.refresh();
    const gate = deferred(), started = deferred(), applied = deferred();
    let gain = 1, reads = 0;
    globalThis.fetch = server({ "/api/devices/camera/settings": async options => {
        if (options.method === "PUT") { gain = 2; applied.resolve(); return Response.json(settings(gain)); }
        const captured = settings(gain);
        if (++reads === 1) { started.resolve(); await gate.promise; }
        return Response.json(captured);
    } });
    const observed = [];
    store.subscribe(() => observed.push(store.getSnapshot().settings.camera.gain));
    const refresh = store.refresh(); await started.promise;
    const write = store.updateSettings("camera", settings(2)); await applied.promise;
    gate.resolve(); await Promise.all([refresh, write]);
    assert.equal(store.getSnapshot().settings.camera.gain, 2);
    assert.ok(reads >= 2);
    assert.ok(observed.slice(observed.indexOf(2)).every(value => value === 2));
});

test("同期エラーは再取得の成功まで保持し、再試行中も操作を禁止する", async () => {
    const store = createBackendStore(); globalThis.fetch = server(); await store.refresh();
    globalThis.fetch = server({ "/api/measurements/status": () => { throw new Error("試験用の通信断"); } });
    await assert.rejects(store.refresh(), /通信断/);
    const error = store.getSnapshot().synchronization.error;
    const gate = deferred();
    globalThis.fetch = server({ "/api/measurements/status": async () => { await gate.promise; return Response.json(measurement()); } });
    const retry = store.refresh();
    assert.equal(store.getSnapshot().synchronization.status, "error");
    assert.equal(store.getSnapshot().synchronization.error, error);
    assert.equal(getOperationAvailability(store.getSnapshot(), { operation: "updateSettings", deviceId: "camera", settings: settings(2) }).allowed, false);
    gate.resolve(); await retry;
    assert.equal(store.getSnapshot().synchronization.status, "ready");
    assert.equal(store.getSnapshot().synchronization.error, "");
});
function server(overrides = {}) {
    return async (path, options) => {
        if (overrides[path]) return overrides[path](options);
        if (path === "/api/devices") return Response.json([device()]);
        if (path === "/api/measurements/status") return Response.json(measurement());
        if (path === "/api/recordings/status") return Response.json(recording());
        if (path.endsWith("/settings")) return Response.json(settings());
        return Response.json({ is_sampling: false, success: true });
    };
}

test("全体取得は同じPromiseを共有し、設定と測定の取得対象を確定する", async () => {
    const store = createBackendStore();
    globalThis.fetch = server({ "/api/measurements/status": () => Response.json(measurement({ sampling_device_ids: ["camera"] })) });
    const first = store.refresh();
    assert.equal(first, store.refresh());
    await first;
    assert.equal(store.getSnapshot().synchronization.status, "ready");
    assert.equal(store.getSnapshot().sampling.camera, true);
    assert.equal(store.getSnapshot().settings.camera.fps, 10);
});

test("未登録の接続機器は同期エラーとし、他機器の最後の確定設定を保持する", async () => {
    const store = createBackendStore();
    globalThis.fetch = server();
    await store.refresh();
    const confirmed = store.getSnapshot().settings.camera;
    globalThis.fetch = server({ "/api/devices": () => Response.json([device(), { ...device(), id: "other", type: "unknown" }]) });
    await assert.rejects(store.refresh(), /未対応の機器種類/);
    assert.equal(store.getSnapshot().synchronization.status, "error");
    assert.equal(store.getSnapshot().settings.camera, confirmed);
    assert.equal(store.getSnapshot().settings.other, undefined);
});

test("個別の設定GETとPUT応答にも機器別検証を適用し、不正な成功応答を確定しない", async () => {
    const store = createBackendStore();
    globalThis.fetch = server();
    await store.refresh();
    globalThis.fetch = server({ "/api/devices/camera/settings": () => Response.json({ wrong: true }) });
    await assert.rejects(store.refreshSettings("camera"), /カメラ設定/);
    assert.equal(store.getSnapshot().settings.camera.gain, 1);
    await assert.rejects(store.updateSettings("camera", settings(2)), /カメラ設定/);
    assert.equal(store.getSnapshot().settings.camera.gain, 1);
    assert.equal(store.getSnapshot().requests["device:camera/settings"].outcomeUnknown, true);
});

test("初期化前の書込みを拒否し、個別読み取りは旧APIでも維持する", async () => {
    const store = createBackendStore();
    globalThis.fetch = server();
    await assert.rejects(store.connectDevice("camera"), /未確認/);
    await store.refreshDevices();
    await store.refreshSettings("camera");
    assert.equal(store.getSnapshot().settings.camera.gain, 1);
    assert.equal(store.getSnapshot().synchronization.status, "uninitialized");
});

test("旧APIの不足と起動ID不一致をunsupportedで明示し、最後の確定値を保持する", async () => {
    const store = createBackendStore();
    globalThis.fetch = server();
    await store.refresh();
    const previous = store.getSnapshot().measurement;
    globalThis.fetch = server({ "/api/measurements/status": () => Response.json({ detail: "Not Found" }, { status: 404 }) });
    await assert.rejects(store.refresh(), /unsupported/);
    assert.equal(store.getSnapshot().measurement, previous);
    assert.equal(store.getSnapshot().synchronization.status, "error");
    globalThis.fetch = server({ "/api/recordings/status": () => Response.json(recording({ server_instance_id: "other" })) });
    await assert.rejects(store.refresh(), /起動ID/);
    globalThis.fetch = server({ "/api/recordings/status": () => Response.json({ is_recording: false, is_stopping: false, directory: null, name: null, error: null }) });
    await assert.rejects(store.refresh(), /unsupported/);
});

test("操作の結果不明は再送せず保持し、全体再取得が成功してから解除する", async () => {
    const store = createBackendStore();
    globalThis.fetch = server();
    await store.refresh();
    let calls = 0;
    globalThis.fetch = server({ "/api/devices/camera/settings": () => { calls++; throw Object.assign(new Error("通信断"), { outcomeUnknown: true }); } });
    await assert.rejects(store.updateSettings("camera", settings(2)), /通信断/);
    assert.equal(calls, 1);
    assert.equal(store.getSnapshot().requests["device:camera/settings"].outcomeUnknown, true);
    assert.equal(store.getSnapshot().settings.camera.gain, 1);
    globalThis.fetch = server();
    await store.refreshSettings("camera");
    assert.equal(store.getSnapshot().requests["device:camera/settings"].outcomeUnknown, true);
    await store.refresh();
    assert.equal(store.getSnapshot().requests["device:camera/settings"].outcomeUnknown, false);
});

test("同じ機器の重複操作を抑え、操作後の全体状態を確認する", async () => {
    const store = createBackendStore();
    globalThis.fetch = server();
    await store.refresh();
    const pending = deferred();
    let gain = 1;
    globalThis.fetch = server({ "/api/devices/camera/settings": async (options) => {
        if (options.method === "PUT") { await pending.promise; gain = 2.5; }
        return Response.json(settings(gain));
    } });
    const write = store.updateSettings("camera", settings(2));
    await assert.rejects(store.updateSettings("camera", settings(3)), /進行中/);
    pending.resolve();
    await write;
    assert.equal(store.getSnapshot().settings.camera.gain, 2.5);
    assert.equal(store.getSnapshot().synchronization.status, "ready");
    assert.equal(store.getSnapshot().requests["device:camera/settings"].pending, 0);
});

test("遅い個別GETで操作の適用値を上書きしない", async () => {
    const store = createBackendStore();
    globalThis.fetch = server();
    await store.refresh();
    const old = deferred();
    globalThis.fetch = server({ "/api/devices/camera/settings": () => old.promise });
    const read = store.refreshSettings("camera");
    await Promise.resolve();
    globalThis.fetch = server({ "/api/devices/camera/settings": () => Response.json(settings(2)) });
    await store.updateSettings("camera", settings(2));
    old.resolve(Response.json(settings(1)));
    await read;
    assert.equal(store.getSnapshot().settings.camera.gain, 2);
});

test("個別の記録状態でも必須項目と起動IDを検証し、最後の確定値を保持する", async () => {
    for (const invalid of [{ is_recording: false }, recording({ server_instance_id: "restarted" }),
        recording({ targets: { camera: { type: "camera" } } })]) {
        const store = createBackendStore();
        globalThis.fetch = server();
        await store.refresh();
        const previous = store.getSnapshot().recording;
        globalThis.fetch = server({ "/api/recordings/status": () => Response.json(invalid) });
        await assert.rejects(store.refreshRecording(), /unsupported/);
        assert.equal(store.getSnapshot().recording, previous);
        assert.equal(store.getSnapshot().synchronization.status, "error");
        await assert.rejects(store.startSampling("camera"), /未確認/);
    }
});

test("記録開始・停止の成功応答が検証不能なら結果不明とし、個別GETでは解除しない", async () => {
    for (const operation of ["start", "stop"]) {
        const store = createBackendStore();
        const initial = recording(operation === "stop" ? { recording_id: "recording", measurement_id: "measurement", is_recording: true } : {});
        globalThis.fetch = server({
            "/api/measurements/status": () => Response.json(measurement({ sampling_device_ids: ["camera"] })),
            "/api/recordings/status": () => Response.json(initial),
        });
        await store.refresh();
        const previous = store.getSnapshot().recording;
        let sent = 0;
        globalThis.fetch = server({ [`/api/recordings/${operation}`]: () => {
            sent++;
            return Response.json({ is_recording: operation === "start" });
        } });
        const action = () => operation === "start" ? store.startRecording({ directory: "test" }) : store.stopRecording();
        await assert.rejects(action(), /unsupported/);
        assert.equal(sent, 1);
        assert.equal(store.getSnapshot().recording, previous);
        assert.equal(store.getSnapshot().requests[`recording/${operation}`].outcomeUnknown, true);
        await store.refreshRecording();
        assert.equal(store.getSnapshot().requests[`recording/${operation}`].outcomeUnknown, true);
        await assert.rejects(action(), /未確認|結果不明/);
        assert.equal(sent, 1);
        await store.refresh();
        assert.equal(store.getSnapshot().requests[`recording/${operation}`].outcomeUnknown, false);
        assert.equal(store.getSnapshot().synchronization.status, "ready");
    }
});

test("起動IDが変わった個別応答を拒否した後、全体再取得で新しいサーバーへ切り替える", async () => {
    const store = createBackendStore();
    globalThis.fetch = server();
    await store.refresh();
    globalThis.fetch = server({
        "/api/recordings/status": () => Response.json(recording({ server_instance_id: "restarted" })),
        "/api/measurements/status": () => Response.json(measurement({ server_instance_id: "restarted" })),
    });
    await assert.rejects(store.refreshRecording(), /起動ID/);
    await store.refresh();
    assert.equal(store.getSnapshot().measurement.server_instance_id, "restarted");
    assert.equal(store.getSnapshot().recording.server_instance_id, "restarted");
    assert.equal(store.getSnapshot().synchronization.status, "ready");
});
