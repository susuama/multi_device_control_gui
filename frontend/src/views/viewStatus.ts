import type { BackendSnapshot } from "../state/backendStore.ts";
import type { DataSnapshot } from "../state/measurementStore.ts";

export type ViewStatus = Readonly<{
    tone: "info" | "warning" | "error";
    message: string;
    blocking: boolean;
    lastReceivedAt?: number | null;
}>;

/** 機器別データの空表示と、最後のデータに添える注意を選ぶ。 */
export function getDeviceViewStatus(backend: BackendSnapshot, data: DataSnapshot,
    deviceId: string | null, source: "measurement" | "preview"): ViewStatus | null {
    if (!deviceId) return { tone: "info", message: "表示する機器を選択してください。", blocking: true };
    const hasData = source === "preview" ? !!data.previews[deviceId] : !!data.measurements[deviceId]?.length;
    const notice = (tone: ViewStatus["tone"], message: string): ViewStatus => ({
        tone, message, blocking: !hasData, lastReceivedAt: data.streams[deviceId]?.lastReceivedAt,
    });
    if (backend.synchronization.status === "error") {
        return notice("error", `状態を確認できません。${backend.synchronization.error} 状態を再取得してください。`);
    }
    if (!backend.devices || !backend.measurement) return notice("info", "機器の状態を確認しています。");
    const requests = Object.entries(backend.requests).filter(([key]) => key.startsWith(`device:${deviceId}/`)).map(([, value]) => value);
    if (requests.some((request) => request.outcomeUnknown)) return notice("warning", "機器操作の結果が不明です。状態を再取得してください。");
    if (requests.some((request) => request.pending > 0 && request.writing)) return notice("info", "機器を操作しています。完了を待ってください。");
    const error = requests.find((request) => request.error)?.error;
    if (error) return notice("error", `機器の操作または状態取得に失敗しました。${error}`);
    const device = backend.devices.find((item) => item.id === deviceId);
    if (!device) return notice("warning", "選択した機器が一覧にありません。表示する機器を選び直してください。");
    if (device.status === "error") return notice("error", `機器でエラーが発生しています。${device.error ?? "接続状態を確認してください。"}`);
    if (device.status !== "connected") return notice("warning", "機器が接続されていません。デバイス管理で接続してください。");
    if (data.needsResync) return notice("warning", "受信したデータの測定期間を確認しています。");
    if (!backend.sampling[deviceId]) return notice("info", hasData
        ? "取得は停止しています。最後に受信したデータを表示しています。" : "測定・記録でデータ取得を開始してください。");
    const stream = data.streams[deviceId];
    if (stream && !stream.connected) return notice("warning", "データの受信接続が切れています。再接続を待っています。");
    if (!hasData) return notice("info", "データを待っています。");
    if (stream?.stale) return notice("warning", "データの更新が止まっています。取得・通信状態を確認してください。");
    return null;
}
