import type { DeviceSettings } from "./devices.ts";

export interface VideoRecordingOptions {
    format: "h264_lossless" | "ffv1" | "mpeg4";
}

export interface StartRecordingRequest {
    directory: string;
    name?: string | null;
    video_options?: Record<string, VideoRecordingOptions>;
}

export interface RecordingTarget {
    type: string;
    settings_at_start: DeviceSettings;
    video_options: VideoRecordingOptions | null;
    state: "not_started" | "recording" | "stopping" | "completed" | "empty" | "failed";
    error: string | null;
    locked_settings: string[];
}

export interface RecordingStatus {
    server_instance_id: string;
    recording_id: string | null;
    measurement_id: string | null;
    targets: Record<string, RecordingTarget>;
    is_recording: boolean;
    is_stopping: boolean;
    directory: string | null;
    name: string | null;
    error: string | null;
}
