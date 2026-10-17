import { apiRequest } from "./client";
import type { CreateWindowRequest, WindowInfo } from "../type/windows";

export function getWindows(): Promise<WindowInfo[]> {
    return apiRequest<WindowInfo[]>("/api/windows");
}

export function createWindow(value: CreateWindowRequest): Promise<{ success: boolean }> {
    return apiRequest<{ success: boolean }>("/api/windows", {
        method: "POST",
        body: JSON.stringify(value),
    });
}
