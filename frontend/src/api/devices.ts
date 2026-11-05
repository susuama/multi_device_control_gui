import { apiRequest } from "./client.ts";
import type { DeviceInfo, DeviceSettings, SamplingStatus } from "../type/devices.ts";

const devicePath = (deviceId: string) => `/api/devices/${encodeURIComponent(deviceId)}`;

export function getDevices(): Promise<DeviceInfo[]> {
    return apiRequest<DeviceInfo[]>("/api/devices");
}

export function scanDevices(): Promise<DeviceInfo[]> {
    return apiRequest<DeviceInfo[]>("/api/devices/scan", { method: "POST" });
}

export function getDevice(deviceId: string): Promise<DeviceInfo> {
    return apiRequest<DeviceInfo>(devicePath(deviceId));
}

export function connectDevice(deviceId: string): Promise<{ success: boolean }> {
    return apiRequest(`${devicePath(deviceId)}/connect`, { method: "POST" });
}

export function disconnectDevice(deviceId: string): Promise<{ success: boolean }> {
    return apiRequest(`${devicePath(deviceId)}/disconnect`, { method: "POST" });
}

export function getDeviceSettings(deviceId: string): Promise<DeviceSettings> {
    return apiRequest(`${devicePath(deviceId)}/settings`);
}

/** 設定一式を送り、バックエンドが返す適用済みの値を取得する。 */
export function updateDeviceSettings(deviceId: string, settings: DeviceSettings): Promise<DeviceSettings> {
    return apiRequest(`${devicePath(deviceId)}/settings`, { method: "PUT", body: JSON.stringify(settings) });
}

export function getSamplingStatus(deviceId: string): Promise<SamplingStatus> {
    return apiRequest(`${devicePath(deviceId)}/sampling`);
}

export function startSampling(deviceId: string): Promise<{ success: boolean }> {
    return apiRequest(`${devicePath(deviceId)}/sampling/start`, { method: "POST" });
}

export function stopSampling(deviceId: string): Promise<{ success: boolean }> {
    return apiRequest(`${devicePath(deviceId)}/sampling/stop`, { method: "POST" });
}

/** 設定応答の必須項目を確認し、不正な値を確定状態へ入れない。 */
export function validateDeviceSettings(value: DeviceSettings, type: string): void {
    const finite = (item: unknown) => typeof item === "number" && Number.isFinite(item);
    if (!value || typeof value !== "object") throw new Error("機器設定の応答が不正です。");
    if (type === "camera") {
        if (!("width" in value) || !Number.isInteger(value.width) || value.width <= 0 || !Number.isInteger(value.height) || value.height <= 0
            || !finite(value.fps) || value.fps <= 0 || !finite(value.exposure) || value.exposure <= 0 || !finite(value.gain) || value.gain < 0
            || typeof value.gain_boost !== "boolean" || !["mono8", "rgb24", "bgr24"].includes(value.pixel_format)
            || !(value.roi === null || value.roi && [value.roi.x, value.roi.y, value.roi.width, value.roi.height].every(Number.isInteger)
                && value.roi.x >= 0 && value.roi.y >= 0 && value.roi.width > 0 && value.roi.height > 0
                && value.roi.x + value.roi.width <= value.width && value.roi.y + value.roi.height <= value.height)) {
            throw new Error("カメラ設定の応答が不正です。");
        }
    } else if (!("wavelength" in value) || !finite(value.wavelength) || !finite(value.range)
        || !Number.isInteger(value.averaging) || typeof value.auto_range !== "boolean" || typeof value.bandwidth !== "string") {
        throw new Error("数値機器設定の応答が不正です。");
    }
}
