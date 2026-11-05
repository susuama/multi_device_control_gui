import { apiRequest } from "./client.ts";
import { validateDeviceSettings } from "./devices.ts";
import type { RecordingStatus, StartRecordingRequest } from "../type/recordings.ts";

/** 開始時に取得中の機器すべてを対象に記録を開始する。 */
export function startRecording(value: StartRecordingRequest): Promise<RecordingStatus> {
    return apiRequest("/api/recordings/start", { method: "POST", body: JSON.stringify(value) });
}

/** 動画・CSV・メタデータの保存完了まで応答を待つ。 */
export function stopRecording(): Promise<RecordingStatus> {
    return apiRequest("/api/recordings/stop", { method: "POST" });
}

export function getRecordingStatus(): Promise<RecordingStatus> {
    return apiRequest("/api/recordings/status");
}
import { UnsupportedApiContractError } from "./measurements.ts";

/** 中核の初期化では、旧APIの不足項目を推測で補わない。 */
export function validateRecordingStatus(value: RecordingStatus): void {
    const nullableString = (item: unknown) => item === null || typeof item === "string";
    if (!value || typeof value.server_instance_id !== "string" || !value.server_instance_id
        || !nullableString(value.recording_id) || value.recording_id === ""
        || !nullableString(value.measurement_id) || value.measurement_id === ""
        || typeof value.is_recording !== "boolean" || typeof value.is_stopping !== "boolean"
        || !nullableString(value.directory) || !nullableString(value.name) || !nullableString(value.error)
        || !value.targets || typeof value.targets !== "object" || Array.isArray(value.targets)
        || (value.recording_id === null && (value.measurement_id !== null || Object.keys(value.targets).length > 0 || value.is_recording || value.is_stopping))) {
        throw new UnsupportedApiContractError("記録状態の識別ID・対象情報を確認できません。");
    }
    for (const target of Object.values(value.targets)) {
        if (!target || typeof target.type !== "string" || !target.settings_at_start || typeof target.settings_at_start !== "object"
            || !["not_started", "recording", "stopping", "completed", "empty", "failed"].includes(target.state)
            || !nullableString(target.error) || !Array.isArray(target.locked_settings)
            || target.locked_settings.some((key) => typeof key !== "string")
            || !(target.video_options === null || target.video_options && ["h264_lossless", "ffv1", "mpeg4"].includes(target.video_options.format))) {
            throw new UnsupportedApiContractError("記録対象の設定・保存状態・設定ロックを確認できません。");
        }
        validateDeviceSettings(target.settings_at_start, target.type);
    }
}
