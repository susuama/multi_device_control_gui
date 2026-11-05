export interface MeasurementStatus {
    server_instance_id: string;
    measurement_id: string | null;
    started_at: string | null;
    sampling_device_ids: string[];
    error: string | null;
}
