import { apiRequest } from "./client.ts";
import type { MeasurementStatus } from "../type/measurement.ts";

export class UnsupportedApiContractError extends Error {
    readonly code = "unsupported";
    constructor(detail: string) {
        super(`unsupported: 中核API契約に対応していません。${detail}`);
        this.name = "UnsupportedApiContractError";
    }
}

export function validateMeasurementStatus(value: MeasurementStatus): void {
    if (!value || typeof value.server_instance_id !== "string" || !value.server_instance_id
        || !(value.measurement_id === null || typeof value.measurement_id === "string" && value.measurement_id.length > 0)
        || !(value.started_at === null || typeof value.started_at === "string" && Number.isFinite(Date.parse(value.started_at)) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value.started_at))
        || (value.measurement_id === null) !== (value.started_at === null)
        || !Array.isArray(value.sampling_device_ids) || value.sampling_device_ids.some((id) => typeof id !== "string" || !id)
        || new Set(value.sampling_device_ids).size !== value.sampling_device_ids.length
        || value.measurement_id === null && value.sampling_device_ids.length > 0
        || !(value.error === null || typeof value.error === "string")) {
        throw new UnsupportedApiContractError("測定状態の識別ID・開始日時・取得対象を確認できません。");
    }
}

export async function getMeasurementStatus(): Promise<MeasurementStatus> {
    let value: MeasurementStatus;
    try {
        value = await apiRequest("/api/measurements/status");
    } catch (cause) {
        if (cause && typeof cause === "object" && "status" in cause && (cause.status === 404 || cause.status === 405)) {
            throw new UnsupportedApiContractError("GET /api/measurements/status がありません。");
        }
        throw cause;
    }
    validateMeasurementStatus(value);
    return value;
}
