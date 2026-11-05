import type { CameraPreviewMetadata, MeasurementEvent, ReceivedEvent } from "../type/event.ts";

const isObject = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const isId = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const isIndex = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;

/** テキストJSONまたは長さ付きJSONとJPEGを読み取り、イベント型を確認する。 */
export function decodeEvent(message: string | ArrayBuffer): ReceivedEvent {
    if (typeof message === "string") {
        const event: unknown = JSON.parse(message);
        if (!isObject(event) || event.type !== "measurement" || typeof event.id !== "string" || !event.id
            || !isId(event.server_instance_id) || !isId(event.measurement_id)
            || typeof event.timestamp_ns !== "number" || !Number.isFinite(event.timestamp_ns)
            || typeof event.elapsed_s !== "number" || !Number.isFinite(event.elapsed_s)
            || typeof event.datetime !== "string" || !Number.isFinite(Date.parse(event.datetime)) || !isObject(event.data)) {
            throw new Error("数値イベントの形式が正しくありません。");
        }
        return event as unknown as MeasurementEvent;
    }
    if (message.byteLength < 4) throw new Error("画像イベントの長さ情報が不足しています。");
    const length = new DataView(message).getUint32(0, false);
    if (length === 0 || length >= message.byteLength - 4) throw new Error("画像イベントのJSON長またはJPEGの長さが正しくありません。");
    const metadata: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(message, 4, length)));
    if (!isObject(metadata) || metadata.type !== "camera_preview" || typeof metadata.id !== "string" || !metadata.id
        || !isId(metadata.server_instance_id) || !isId(metadata.measurement_id)
        || !isIndex(metadata.frame_index) || typeof metadata.timestamp_ns !== "string" || !/^\d+$/.test(metadata.timestamp_ns)
        || !isIndex(metadata.width) || metadata.width === 0 || !isIndex(metadata.height) || metadata.height === 0) {
        throw new Error("画像イベントのメタデータが正しくありません。");
    }
    return { ...(metadata as unknown as CameraPreviewMetadata), jpeg: new Blob([message.slice(4 + length)], { type: "image/jpeg" }) };
}

