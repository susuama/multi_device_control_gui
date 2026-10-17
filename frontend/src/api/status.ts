import { apiRequest } from "./client";

export interface ApiStatus {
    status: string;
}

export function getStatus(): Promise<ApiStatus> {
    return apiRequest<ApiStatus>("/api/status");
}
