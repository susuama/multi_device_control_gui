import { apiRequest } from "./client.ts";
import type { DeviceInfo, DeviceSettings, DiscoveredDevice, SamplingStatus } from "../type/devices.ts";

const devicePath = (deviceId: string) => `/api/devices/${encodeURIComponent(deviceId)}`;

export function getDevices(): Promise<DeviceInfo[]> {
    return apiRequest<DeviceInfo[]>("/api/devices");
}

export function scanDevices(): Promise<DiscoveredDevice[]> {
    return apiRequest<DiscoveredDevice[]>("/api/devices/scan", { method: "POST" });
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

export { validateDeviceSettings } from "../devices/settingsContracts.ts";
