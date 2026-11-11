import assert from "node:assert/strict";
import { test } from "node:test";
import { getDeviceViewStatus } from "../src/views/viewStatus.ts";
import { createBackendStore } from "../src/state/backendStore.ts";
import { createMeasurementStore } from "../src/state/measurementStore.ts";

function snapshots() {
    return {
        backend: { ...createBackendStore().getSnapshot(), devices: [{ id: "meter", status: "connected" }],
            measurement: { sampling_device_ids: ["meter"] }, sampling: { meter: true },
            synchronization: { status: "ready", lastUpdatedAt: 100, error: "" } },
        data: { ...createMeasurementStore().getSnapshot(), streams: {
            meter: { connected: true, sampling: true, lastReceivedAt: 100, stale: false },
        } },
    };
}

test("機器の取得エラー理由を表示し、受信済みの値を残す", () => {
    const { backend, data } = snapshots();
    backend.devices[0] = { id: "meter", status: "error", error: "USB read failed" };
    data.measurements = { meter: [{ data: { power_w: 1 } }] };
    const status = getDeviceViewStatus(backend, data, "meter", "measurement");
    assert.equal(status.tone, "error");
    assert.match(status.message, /USB read failed/);
    assert.equal(status.blocking, false);
});

test("対象未選択・初期化・未接続・未取得・初回受信待ちを区別する", () => {
    const { backend, data } = snapshots();
    const status = () => getDeviceViewStatus(backend, data, "meter", "measurement");
    assert.match(getDeviceViewStatus(backend, data, null, "measurement").message, /選択/);
    assert.match(status().message, /待っています/);
    backend.sampling.meter = false;
    assert.match(status().message, /取得を開始/);
    backend.devices[0].status = "disconnected";
    assert.match(status().message, /接続されていません/);
    backend.devices = null;
    assert.match(status().message, /確認しています/);
    backend.synchronization = { status: "error", error: "HTTP 503" };
    assert.equal(status().tone, "error");
    assert.equal(status().blocking, true);
    assert.match(status().message, /503/);
});

test("周期更新中に表示を消さず、受信断・鮮度低下・取得停止でも最後の値を残す", () => {
    const { backend, data } = snapshots();
    data.measurements = { meter: [{ data: { power_w: 1 } }] };
    const status = () => getDeviceViewStatus(backend, data, "meter", "measurement");
    backend.synchronization.status = "refreshing";
    assert.equal(status(), null);
    data.streams.meter.stale = true;
    assert.equal(status().blocking, false);
    assert.equal(status().lastReceivedAt, 100);
    assert.match(status().message, /更新が止まって/);
    data.streams.meter.connected = false;
    assert.match(status().message, /受信接続が切れ/);
    backend.synchronization = { status: "error", error: "offline" };
    assert.equal(status().blocking, false);
    backend.synchronization.status = "ready";
    backend.sampling.meter = false;
    assert.match(status().message, /取得は停止/);
    assert.equal(data.measurements.meter[0].data.power_w, 1);
});

test("画像と数値の有無を独立して判定し、機器消失・測定期間の確認を通知する", () => {
    const { backend, data } = snapshots();
    data.previews = { meter: { jpeg: new Blob(["jpeg"]) } };
    assert.equal(getDeviceViewStatus(backend, data, "meter", "preview"), null);
    assert.equal(getDeviceViewStatus(backend, data, "meter", "measurement").blocking, true);
    data.needsResync = true;
    assert.match(getDeviceViewStatus(backend, data, "meter", "preview").message, /測定期間/);
    backend.devices = [];
    const status = getDeviceViewStatus(backend, data, "meter", "preview");
    assert.match(status.message, /一覧にありません/);
    assert.equal(status.blocking, false);
});

test("処理中・結果不明・機器別エラーを表示し、他機器の操作を混ぜない", () => {
    const { backend, data } = snapshots();
    backend.requests = { "device:other/settings": { pending: 1, writing: true, error: "other error", outcomeUnknown: true } };
    const status = () => getDeviceViewStatus(backend, data, "meter", "measurement");
    assert.match(status().message, /待っています/);
    backend.requests["device:meter/settings"] = { pending: 1, writing: true, error: "", outcomeUnknown: false };
    assert.match(status().message, /機器を操作/);
    backend.requests["device:meter/settings"] = { pending: 0, writing: true, error: "timeout", outcomeUnknown: true };
    assert.match(status().message, /結果が不明/);
    backend.requests["device:meter/settings"] = { pending: 0, writing: false, error: "setting failed", outcomeUnknown: false };
    assert.equal(status().tone, "error");
    assert.match(status().message, /setting failed/);
});
