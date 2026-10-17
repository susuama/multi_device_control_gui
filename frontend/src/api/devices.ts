import { apiRequest } from "./client";
import type { DeviceInfo } from "../type/devices";

export function getDevices(): Promise<DeviceInfo[]> {
    return apiRequest<DeviceInfo[]>("/api/devices");
}

export function scanDevices(): Promise<DeviceInfo[]> {
    return apiRequest<DeviceInfo[]>("/api/devices/scan", { method: "POST" });
}

export function getDevice(deviceId: string): Promise<DeviceInfo> {
    return apiRequest<DeviceInfo>(`/api/devices/${encodeURIComponent(deviceId)}`);
}
