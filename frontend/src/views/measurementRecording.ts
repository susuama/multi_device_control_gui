import type { DeviceInfo } from "../type/devices.ts";
import type { RecordingStatus, StartRecordingRequest, VideoRecordingOptions } from "../type/recordings.ts";

export const videoFormats = ["h264_lossless", "ffv1", "mpeg4"] as const;
export const targetStateLabels = { not_started: "未開始", recording: "記録中", stopping: "終了処理中", completed: "保存完了", empty: "データなしで終了", failed: "保存失敗" };

/** 開始時の取得対象だけに動画形式を割り当てる。空の記録名はバックエンドへ委ねる。 */
export function buildRecordingRequest(directory: string, name: string, devices: readonly DeviceInfo[], samplingIds: readonly string[], formats: Record<string, string>): StartRecordingRequest {
    if (!directory.trim()) throw new Error("保存先の親フォルダを入力してください。");
    if (name && (name === "." || name === ".." || /[<>:"/\\|?*]/.test(name) || [...name].some(char => char.charCodeAt(0) < 32) || /[. ]$/.test(name))) {
        throw new Error("記録名にはフォルダ名を入力してください。使用できない文字や末尾の空白・ピリオドがあります。");
    }
    const video_options: Record<string, VideoRecordingOptions> = {};
    for (const id of samplingIds) {
        const device = devices.find(item => item.id === id);
        if (!device) throw new Error("取得対象を再確認してください。");
        if (device.type === "camera") {
            const format = formats[id];
            if (!videoFormats.includes(format as VideoRecordingOptions["format"])) throw new Error(`${device.model}の動画形式を選択してください。`);
            video_options[id] = { format: format as VideoRecordingOptions["format"] };
        }
    }
    return { directory, ...(name ? { name } : {}), video_options };
}

export function recordingStateLabel(status: RecordingStatus | null): string {
    if (!status) return "未確認";
    if (status.is_stopping) return "終了処理中";
    if (status.is_recording) return "記録中";
    if (status.error) return "エラー（終了状態を確認）";
    return status.recording_id ? "停止確認済み" : "未開始";
}
