import type { BackendSnapshot } from "./backendStore.ts";
import type { DeviceSettings } from "../type/devices.ts";

export type Operation = "connect" | "disconnect" | "startSampling" | "stopSampling" | "startRecording" | "stopRecording" | "updateSettings";
export type OperationRequest = { operation: Operation; deviceId?: string; settings?: DeviceSettings };

/** 表示と操作の両方で、最後に確認した状態による可否を使う。 */
export function getOperationAvailability(snapshot: BackendSnapshot, request: OperationRequest): { allowed: boolean; reason: string | null } {
    const deny = (reason: string) => ({ allowed: false, reason });
    if (snapshot.synchronization.status !== "ready") return deny("状態が未確認です。全体の再取得が完了するまで操作できません。");
    if (Object.values(snapshot.requests).some((item) => item.outcomeUnknown)) return deny("結果不明の操作があります。状態を再取得してください。");
    const { operation, deviceId } = request;
    const deviceOperation = !["startRecording", "stopRecording"].includes(operation);
    const key = deviceOperation ? `device:${deviceId}/` : "recording/";
    if (Object.entries(snapshot.requests).some(([name, item]) => item.pending > 0 && item.writing !== false
        && (name.startsWith(key) || name.startsWith("recording/") || !deviceOperation && name.startsWith("device:")))) {
        return deny("同じ対象に関係する操作が進行中です。完了を待ってください。");
    }
    const recording = snapshot.recording;
    if (!deviceOperation) {
        if (!recording || !snapshot.measurement) return deny("測定・記録状態が未確認です。");
        if (recording.is_stopping && operation === "startRecording") return deny("記録の終了処理中です。");
        if (operation === "startRecording" && recording.is_recording) return deny("記録中です。");
        if (operation === "startRecording" && snapshot.measurement.sampling_device_ids.length === 0) return deny("取得中の機器がありません。");
        if (operation === "stopRecording" && !recording.is_recording && !recording.is_stopping) return deny("記録を開始していません。");
        return { allowed: true, reason: null };
    }
    const device = snapshot.devices?.find((item) => item.id === deviceId);
    if (!device) return deny("機器が確認できません。");
    if (operation === "connect") return device.status === "connected" ? deny("接続済みです。") : { allowed: true, reason: null };
    if (device.status !== "connected") return deny("機器に接続していません。");
    if (operation === "startSampling" && snapshot.sampling[device.id]) return deny("取得中です。");
    if (operation === "stopSampling" && !snapshot.sampling[device.id]) return deny("取得を開始していません。");
    if (operation === "updateSettings") {
        const current = snapshot.settings[device.id];
        if (!current || !request.settings) return deny("適用済み設定と更新設定が必要です。");
        for (const field of recording?.targets[device.id]?.locked_settings ?? []) {
            if (JSON.stringify(current[field as keyof DeviceSettings]) !== JSON.stringify(request.settings[field as keyof DeviceSettings])) {
                return deny(`記録対象の設定 ${field} は現在変更できません。`);
            }
        }
    }
    return { allowed: true, reason: null };
}
