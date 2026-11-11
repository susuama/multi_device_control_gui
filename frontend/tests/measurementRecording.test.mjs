import { test } from "node:test";
import assert from "node:assert/strict";
import { buildRecordingRequest, recordingStateLabel, targetStateLabels } from "../src/views/measurementRecording.ts";
const devices = [{ id: "meter", type: "power_meter" }, { id: "camera", type: "camera", model: "Camera" }];
test("開始時の取得対象だけへ形式を送り、記録名の省略を維持する", () => {
    for (const format of ["h264_lossless", "ffv1", "mpeg4"]) {
        assert.deepEqual(buildRecordingRequest("C:/data", "", devices, ["camera"], { camera: format, unused: "ffv1" }),
            { directory: "C:/data", video_options: { camera: { format } } });
    }
    assert.deepEqual(buildRecordingRequest("C:/data", "run", devices, ["meter"], {}), { directory: "C:/data", name: "run", video_options: {} });
    for (const name of ["..", "a/b", "a\\b", "a:", "run."]) assert.throws(() => buildRecordingRequest("d", name, devices, [], {}), /記録名/);
    assert.throws(() => buildRecordingRequest(" ", "", devices, [], {}), /親フォルダ/);
    assert.throws(() => buildRecordingRequest("d", "", devices, ["camera"], {}), /動画形式/);
});
test("全体の停止と対象の保存結果を区別し、エラーを保存成功にしない", () => {
    const status = { recording_id: "r", is_recording: false, is_stopping: false, error: null };
    assert.equal(recordingStateLabel(status), "停止確認済み");
    assert.match(recordingStateLabel({ ...status, error: "JSON failed" }), /エラー/);
    assert.equal(recordingStateLabel({ ...status, is_stopping: true }), "終了処理中");
    assert.notEqual(targetStateLabels.empty, targetStateLabels.completed);
    assert.notEqual(targetStateLabels.failed, targetStateLabels.completed);
});
