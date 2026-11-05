import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import * as devices from "../src/api/devices.ts";
import * as recordings from "../src/api/recordings.ts";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test("機器の設定・取得・接続APIはIDをエンコードし、必要なJSONだけを送る", async () => {
    const requests = [];
    globalThis.fetch = async (path, options) => {
        requests.push({ path, ...options });
        return Response.json(options.method === "PUT" ? JSON.parse(options.body) : { success: true });
    };
    const id = "カメラ /1";
    const root = `/api/devices/${encodeURIComponent(id)}`;
    const settings = { width: 640, height: 480, fps: 10, exposure: 0.01, gain: 1, gain_boost: false, pixel_format: "mono8", roi: null };
    await devices.connectDevice(id);
    await devices.disconnectDevice(id);
    await devices.getDeviceSettings(id);
    assert.deepEqual(await devices.updateDeviceSettings(id, settings), settings);
    await devices.getSamplingStatus(id);
    await devices.startSampling(id);
    await devices.stopSampling(id);
    assert.deepEqual(requests.map(({ path, method = "GET" }) => [path, method]), [
        [`${root}/connect`, "POST"], [`${root}/disconnect`, "POST"], [`${root}/settings`, "GET"],
        [`${root}/settings`, "PUT"], [`${root}/sampling`, "GET"], [`${root}/sampling/start`, "POST"], [`${root}/sampling/stop`, "POST"],
    ]);
    assert.deepEqual(JSON.parse(requests[3].body), settings);
    assert.equal(requests[3].headers.get("Content-Type"), "application/json");
    assert.equal(requests[5].body, undefined);
});

test("記録APIは機器別の保存形式を送り、全体状態とエラーをそのまま返す", async () => {
    const requests = [];
    const status = { is_recording: true, is_stopping: false, directory: "C:/data/run1", name: "run1", error: "camera failed" };
    globalThis.fetch = async (path, options) => { requests.push({ path, ...options }); return Response.json(status); };
    const body = { directory: "C:/data", name: "run1", video_options: { camera: { format: "ffv1" } } };
    assert.deepEqual(await recordings.startRecording(body), status);
    await recordings.stopRecording();
    await recordings.getRecordingStatus();
    assert.deepEqual(requests.map(({ path, method = "GET" }) => [path, method]), [
        ["/api/recordings/start", "POST"], ["/api/recordings/stop", "POST"], ["/api/recordings/status", "GET"],
    ]);
    assert.deepEqual(JSON.parse(requests[0].body), body);
});
