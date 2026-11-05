import assert from "node:assert/strict";
import { test } from "node:test";
import { getOperationAvailability } from "../src/state/operationPolicy.ts";
const snapshot = () => ({ synchronization: { status: "ready", lastUpdatedAt: Date.now(), error: "" }, requests: {}, devices: [{ id: "camera", status: "connected" }], settings: { camera: { fps: 10, gain: 1, roi: null } }, sampling: { camera: true }, measurement: { sampling_device_ids: ["camera"] }, recording: { is_recording: true, is_stopping: false, targets: { camera: { locked_settings: ["fps", "roi"] } } } });
test("取得・記録状態とバックエンドが返す設定ロックを共通判定する", () => {
    const state = snapshot();
    assert.equal(getOperationAvailability(state, { operation: "startSampling", deviceId: "camera" }).allowed, false);
    assert.equal(getOperationAvailability(state, { operation: "stopSampling", deviceId: "camera" }).allowed, true);
    assert.equal(getOperationAvailability(state, { operation: "startRecording" }).allowed, false);
    assert.equal(getOperationAvailability(state, { operation: "stopRecording" }).allowed, true);
    assert.equal(getOperationAvailability(state, { operation: "updateSettings", deviceId: "camera", settings: { fps: 10, gain: 2, roi: null } }).allowed, true);
    assert.match(getOperationAvailability(state, { operation: "updateSettings", deviceId: "camera", settings: { fps: 11, gain: 1, roi: null } }).reason, /fps/);
});
test("未確認・処理中・結果不明では操作を拒否する", () => {
    const state = snapshot();
    state.synchronization.status = "error";
    assert.equal(getOperationAvailability(state, { operation: "stopRecording" }).allowed, false);
    state.synchronization.status = "ready";
    state.requests["device:camera/settings"] = { pending: 1, outcomeUnknown: false };
    assert.equal(getOperationAvailability(state, { operation: "stopRecording" }).allowed, false);
    state.requests["device:camera/settings"] = { pending: 0, outcomeUnknown: true };
    assert.equal(getOperationAvailability(state, { operation: "stopSampling", deviceId: "camera" }).allowed, false);
});


test("GETで確認した終了処理中は停止を再試行でき、進行中の停止要求は重複しない", () => {
    const state = snapshot();
    state.recording.is_recording = false;
    state.recording.is_stopping = true;
    assert.equal(getOperationAvailability(state, { operation: "stopRecording" }).allowed, true);
    assert.equal(getOperationAvailability(state, { operation: "startRecording" }).allowed, false);
    state.requests["recording/stop"] = { pending: 1, writing: true, outcomeUnknown: false };
    assert.equal(getOperationAvailability(state, { operation: "stopRecording" }).allowed, false);
});
