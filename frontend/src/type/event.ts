export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

/** backend/app/models/events.py の数値イベントに対応する。 */
export interface MeasurementEvent {
    type: "measurement";
    server_instance_id: string;
    measurement_id: string;
    id: string;
    timestamp_ns: number; // JSON数値。表示の時間軸にはelapsed_sを使う。
    elapsed_s: number;
    datetime: string;
    data: Record<string, JsonValue>;
}

export interface CameraPreviewMetadata {
    type: "camera_preview";
    server_instance_id: string;
    measurement_id: string;
    id: string;
    frame_index: number;
    timestamp_ns: string; // 桁落ちを避けるためバックエンドが10進文字列で送る。
    width: number;
    height: number;
}

export interface CameraPreviewEvent extends CameraPreviewMetadata {
    jpeg: Blob;
}

export type ReceivedEvent = MeasurementEvent | CameraPreviewEvent;

