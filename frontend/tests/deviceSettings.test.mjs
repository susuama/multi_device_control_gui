import assert from "node:assert/strict";
import { test } from "node:test";
import { settingsContracts, getSettingsContract, validateDeviceSettings } from "../src/devices/settingsContracts.ts";
import { settingsEqual } from "../src/devices/settingsValues.ts";
import { validateRecordingStatus } from "../src/api/recordings.ts";

const cameraContract = settingsContracts.camera;
const meterContract = settingsContracts.power_meter;

const camera = () => ({ width: 640, height: 480, fps: 10, exposure: 0.01, gain: 1, gain_boost: false,
    pixel_format: "mono8", roi: { x: 1, y: 2, width: 20, height: 30 } });
const meter = () => ({ wavelength: 532, range: 0.01, auto_range: true, bandwidth: "low", averaging: 1 });

test("露光時間をmsで編集し、送信時は秒へ戻す。未編集の値は変換しない", () => {
    const original = camera();
    assert.equal(cameraContract.getInputs(original).exposure, "10");
    const updated = cameraContract.merge(original, { exposure: "12.5", gain_boost: true });
    assert.equal(updated.exposure, 0.0125);
    assert.equal(updated.gain_boost, true);
    assert.equal(original.exposure, 0.01);
    const precise = { ...original, exposure: 0.000000123456789 };
    assert.equal(cameraContract.merge(precise, { gain: "2" }).exposure, precise.exposure);
});

test("他パネルで変更された未編集の設定・画像情報・保存ROIを最新値のまま送る", () => {
    const latest = { ...camera(), width: 800, fps: 30, gain: 4, roi: { x: 10, y: 20, width: 100, height: 40 } };
    const merged = cameraContract.merge(latest, { exposure: "20" });
    assert.deepEqual(merged, { ...latest, exposure: 0.02 });
    assert.deepEqual(latest.roi, { x: 10, y: 20, width: 100, height: 40 });
    const current = { ...meter(), range: 2, averaging: 8 };
    assert.deepEqual(meterContract.merge(current, { wavelength: "635" }), { ...current, wavelength: 635 });
});

test("空欄・非数値・無限大・負値・非整数を拒否し、機器固有の上限は追加しない", () => {
    for (const exposure of ["", " ", "abc", "Infinity", "-1", "0"]) {
        assert.throws(() => cameraContract.merge(camera(), { exposure }), /露光時間/);
    }
    for (const averaging of ["0", "-1", "1.5", "9007199254740992"]) {
        assert.throws(() => meterContract.merge(meter(), { averaging }), /内部平均化/);
    }
    assert.throws(() => meterContract.merge(meter(), { range: "0" }), /測定レンジ/);
    assert.throws(() => meterContract.merge(meter(), { bandwidth: "" }), /帯域幅/);
    assert.throws(() => cameraContract.merge(camera(), { gain_boost: "false" }), /チェック/);
    assert.equal(cameraContract.merge(camera(), { gain: "0" }).gain, 0);
    assert.equal(meterContract.merge(meter(), { wavelength: "2500", averaging: "10000" }).wavelength, 2500);
});

test("数値の表記だけの違いを変更扱いにせず、適用済み値の変化を検出する", () => {
    assert.equal(settingsEqual(meter(), meterContract.merge(meter(), { wavelength: "532.0" })), true);
    assert.equal(settingsEqual(camera(), { ...camera(), gain: 2 }), false);
    assert.equal(settingsEqual(camera(), { ...camera(), roi: null }), false);
    assert.equal(settingsEqual(meter(), camera()), false);
});

test("機器種類の登録から応答を検証し、未登録種類を他機器へ流さない", () => {
    validateDeviceSettings(camera(), "camera");
    validateDeviceSettings(meter(), "power_meter");
    assert.throws(() => validateDeviceSettings(meter(), "camera"), /カメラ/);
    assert.throws(() => validateDeviceSettings(camera(), "power_meter"), /パワーメーター/);
    assert.equal(getSettingsContract("unknown"), undefined);
    assert.equal(getSettingsContract("toString"), undefined);
    assert.throws(() => validateDeviceSettings(meter(), "unknown"), /未対応/);
    assert.throws(() => cameraContract.merge(camera(), { wavelength: "635" }), /未対応の設定項目/);
    assert.deepEqual(cameraContract.modes, { exposure: "immediate", gain: "immediate", gain_boost: "manual", fps: "manual" });
    assert.ok(Object.values(meterContract.modes).every(mode => mode === "manual"));
});

test("記録開始時設定も同じ登録から検証する", () => {
    const status = { server_instance_id: "s", recording_id: "r", measurement_id: "m", directory: "d", name: "n",
        is_recording: true, is_stopping: false, error: null, targets: {
            camera: { type: "camera", settings_at_start: camera(), state: "recording", error: null, locked_settings: ["fps"], video_options: { format: "ffv1" } },
        } };
    validateRecordingStatus(status);
    status.targets.camera.type = "unknown";
    assert.throws(() => validateRecordingStatus(status), /未対応/);
});
