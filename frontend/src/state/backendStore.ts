import * as devicesApi from "../api/devices.ts";
import * as recordingsApi from "../api/recordings.ts";
import { getMeasurementStatus, UnsupportedApiContractError } from "../api/measurements.ts";
import { getOperationAvailability, type Operation } from "./operationPolicy.ts";
import type { MeasurementStatus } from "../type/measurement.ts";
import type { DeviceInfo, DeviceSettings } from "../type/devices.ts";
import type { RecordingStatus, StartRecordingRequest } from "../type/recordings.ts";

type RequestState = Readonly<{ pending: number; error: string; outcomeUnknown: boolean; writing: boolean }>;
export type BackendSnapshot = Readonly<{
    measurement: MeasurementStatus | null;
    synchronization: Readonly<{ status: "uninitialized" | "refreshing" | "ready" | "error"; lastUpdatedAt: number | null; error: string }>;
    devices: readonly DeviceInfo[] | null;
    settings: Readonly<Record<string, DeviceSettings>>;
    sampling: Readonly<Record<string, boolean>>;
    recording: RecordingStatus | null;
    requests: Readonly<Record<string, RequestState>>;
}>;

/** APIの確定値を保持する。購読解除では機器操作やデータの消去を行わない。 */
export function createBackendStore() {
    let state: BackendSnapshot = { devices: null, settings: {}, sampling: {}, recording: null, requests: {}, measurement: null, synchronization: { status: "uninitialized", lastUpdatedAt: null, error: "" } };
    let generation = 0;
    let refreshing: Promise<void> | null = null;
    const listeners = new Set<() => void>();
    const revisions = new Map<string, number>();
    const writes = new Set<string>();
    const reads = new Map<string, { revision: number; promise: Promise<unknown> }>();

    function update(changes: Partial<BackendSnapshot>) {
        state = { ...state, ...changes };
        listeners.forEach((listener) => listener());
    }

    function requestState(key: string, delta: number, error?: string, outcomeUnknown?: boolean, writing?: boolean) {
        const previous = state.requests[key] ?? { pending: 0, error: "", outcomeUnknown: false, writing: false };
        update({ requests: { ...state.requests, [key]: {
            pending: previous.pending + delta, error: error ?? previous.error, outcomeUnknown: outcomeUnknown ?? previous.outcomeUnknown, writing: writing ?? previous.writing,
        } } });
    }

    function invalidate(key: string) {
        revisions.set(key, (revisions.get(key) ?? 0) + 1);
    }

    function run<T>(key: string, operation: string, task: () => Promise<T>, apply: (value: T) => void, write = false, pendingSettings?: DeviceSettings): Promise<T> {
        if (write && writes.has(key)) return Promise.reject(new Error("同じ対象の操作が進行中です。完了を待ってください。"));
        if (write) {
            const names: Record<string, Operation> = { settings: "updateSettings", connect: "connect", disconnect: "disconnect", startSampling: "startSampling", stopSampling: "stopSampling", start: "startRecording", stop: "stopRecording" };
            const availability = getOperationAvailability(state, { operation: names[operation], deviceId: key.startsWith("device:") ? key.slice(7) : undefined,
                settings: pendingSettings });
            if (!availability.allowed) return Promise.reject(new Error(availability.reason ?? "操作できません。"));
            generation++;
            writes.add(key);
            invalidate(key);
            if (key.startsWith("device:")) invalidate("devices");
        }
        const revision = revisions.get(key) ?? 0;
        const readKey = `${key}/${operation}`;
        const existing = reads.get(readKey);
        if (!write && existing?.revision === revision) return existing.promise as Promise<T>;
        requestState(readKey, 1, "", write ? false : undefined, write ? true : undefined);
        const promise = Promise.resolve().then(async () => {
            let responseReceived = false;
            try {
                const value = await task();
                responseReceived = true;
                if (write) {
                    invalidate(key);
                    if (key.startsWith("device:")) invalidate("devices");
                }
                // 更新操作より前に始めたGETの結果で、適用後の値を上書きしない。
                if (write || (revisions.get(key) ?? 0) === revision) {
                    apply(value);
                    requestState(readKey, 0, "");
                }
                return value;
            } catch (cause) {
                if (write || (revisions.get(key) ?? 0) === revision) {
                    const outcomeUnknown = write && (responseReceived || !!cause && typeof cause === "object" && "outcomeUnknown" in cause && cause.outcomeUnknown === true);
                    requestState(readKey, 0, cause instanceof Error ? cause.message : String(cause), write ? outcomeUnknown : undefined);
                    if (write || cause instanceof UnsupportedApiContractError) {
                        update({ synchronization: { ...state.synchronization, status: "error", error: outcomeUnknown
                            ? "操作結果が不明です。状態を再取得してください。"
                            : cause instanceof Error ? cause.message : String(cause) } });
                    }
                }
                throw cause;
            } finally {
                if (write) {
                    generation++;
                    invalidate(key);
                    writes.delete(key);
                } else if (reads.get(readKey)?.promise === promise) {
                    reads.delete(readKey);
                }
                requestState(readKey, -1, undefined, undefined, write ? false : undefined);
            }
        });
        if (!write) reads.set(readKey, { revision, promise });
        return write ? promise.then(async (value) => { await store.refresh(); return value; }) : promise;
    }

    function putDevice(device: DeviceInfo) {
        const devices = state.devices ?? [];
        update({ devices: devices.some((item) => item.id === device.id)
            ? devices.map((item) => item.id === device.id ? device : item) : [...devices, device] });
    }

    function putDevices(devices: DeviceInfo[]) {
        const ids = new Set(devices.map((device) => device.id));
        update({ devices,
            settings: Object.fromEntries(Object.entries(state.settings).filter(([id]) => ids.has(id))),
            sampling: Object.fromEntries(Object.entries(state.sampling).filter(([id]) => ids.has(id))),
        });
    }

    function validateRecording(value: RecordingStatus, serverInstanceId: string | null) {
        recordingsApi.validateRecordingStatus(value);
        if (serverInstanceId !== null && value.server_instance_id !== serverInstanceId) {
            throw new UnsupportedApiContractError("測定状態と記録状態のサーバー起動IDが一致しません。全体の状態を再取得してください。");
        }
    }

    function putRecording(value: RecordingStatus) {
        validateRecording(value, state.measurement?.server_instance_id ?? null);
        update({ recording: value });
    }

    const store = {
        getSnapshot: () => state,
        subscribe(listener: () => void) {
            listeners.add(listener);
            return () => { listeners.delete(listener); };
        },
        refreshDevices: () => run("devices", "list", devicesApi.getDevices, putDevices),
        scanDevices() {
            invalidate("devices");
            return run("devices", "scan", devicesApi.scanDevices, (devices) => {
                invalidate("devices");
                putDevices(devices);
            });
        },
        refreshDevice: (id: string) => run(`device:${id}`, "info", () => devicesApi.getDevice(id), putDevice),
        refreshSettings: (id: string) => run(`device:${id}`, "settings", () => devicesApi.getDeviceSettings(id),
            (value) => update({ settings: { ...state.settings, [id]: value } })),
        updateSettings: (id: string, settings: DeviceSettings) => run(`device:${id}`, "settings", () => devicesApi.updateDeviceSettings(id, settings),
            (value) => update({ settings: { ...state.settings, [id]: value } }), true, settings),
        refreshSampling: (id: string) => run(`device:${id}`, "sampling", () => devicesApi.getSamplingStatus(id),
            (value) => update({ sampling: { ...state.sampling, [id]: value.is_sampling } })),
        connectDevice: (id: string) => run(`device:${id}`, "connect", async () => {
            await devicesApi.connectDevice(id);
            return devicesApi.getDevice(id);
        }, putDevice, true),
        async disconnectDevice(id: string) {
            const device = await run(`device:${id}`, "disconnect", async () => {
                await devicesApi.disconnectDevice(id);
                return devicesApi.getDevice(id);
            }, (value) => {
                putDevice(value);
                const settings = { ...state.settings };
                delete settings[id];
                update({ settings, sampling: { ...state.sampling, [id]: false } });
            }, true);
            await store.refreshRecording();
            return device;
        },
        startSampling: (id: string) => run(`device:${id}`, "startSampling", async () => {
            await devicesApi.startSampling(id);
            return devicesApi.getSamplingStatus(id);
        }, (value) => update({ sampling: { ...state.sampling, [id]: value.is_sampling } }), true),
        async stopSampling(id: string) {
            const result = await run(`device:${id}`, "stopSampling", async () => {
                await devicesApi.stopSampling(id);
                return devicesApi.getSamplingStatus(id);
            }, (value) => update({ sampling: { ...state.sampling, [id]: value.is_sampling } }), true);
            await store.refreshRecording();
            return result;
        },
        refreshRecording: () => run("recording", "status", recordingsApi.getRecordingStatus, putRecording),
        startRecording: (value: StartRecordingRequest) => run("recording", "start", () => recordingsApi.startRecording(value),
            putRecording, true),
        stopRecording: () => run("recording", "stop", recordingsApi.stopRecording, putRecording, true),
        /** 機器・測定・記録を検証してから共通の確定値として反映する。 */
        refresh(deviceId?: string): Promise<void> {
            // 旧呼び出しのID指定があっても、起動IDの検証は全体で行う。
            void deviceId;
            if (refreshing) return refreshing;
            const perform = async () => {
                update({ synchronization: { ...state.synchronization, status: "refreshing", error: "" } });
                try {
                    let revision: number;
                    do {
                        revision = generation;
                        invalidate("devices");
                        invalidate("recording");
                        for (const device of state.devices ?? []) invalidate(`device:${device.id}`);
                        const [devices, measurement, recording] = await Promise.all([
                            devicesApi.getDevices(), getMeasurementStatus(), recordingsApi.getRecordingStatus(),
                        ]);
                        validateRecording(recording, measurement.server_instance_id);
                        if (!Array.isArray(devices) || devices.some((device) => !device || [device.id, device.type, device.manufacturer, device.model, device.serial_number, device.address].some((value) => typeof value !== "string") || !device.id
                            || !["connected", "disconnected", "error"].includes(device.status))
                            || new Set(devices.map((device) => device.id)).size !== devices.length) {
                            throw new UnsupportedApiContractError("機器一覧が不正です。");
                        }
                        if (measurement.sampling_device_ids.some((id) => !devices.some((device) => device.id === id && device.status === "connected"))) {
                            throw new UnsupportedApiContractError("取得中の機器と機器一覧が一致しません。");
                        }
                        const entries = await Promise.all(devices.filter((device) => device.status === "connected").map(async (device) => {
                            const settings = await devicesApi.getDeviceSettings(device.id);
                            devicesApi.validateDeviceSettings(settings, device.type);
                            return [device.id, settings] as const;
                        }));
                        if (revision !== generation) continue;
                        invalidate("devices");
                        invalidate("recording");
                        for (const device of devices) invalidate(`device:${device.id}`);
                        const requests = Object.fromEntries(Object.entries(state.requests).map(([key, request]) =>
                            [key, { ...request, outcomeUnknown: false }]));
                        update({ devices, settings: Object.fromEntries(entries), measurement, recording, requests,
                            sampling: Object.fromEntries(devices.map((device) => [device.id, measurement.sampling_device_ids.includes(device.id)])),
                            synchronization: { status: "ready", lastUpdatedAt: Date.now(), error: "" } });
                    } while (revision !== generation);
                } catch (cause) {
                    update({ synchronization: { ...state.synchronization, status: "error", error: cause instanceof Error ? cause.message : String(cause) } });
                    throw cause;
                }
            };
            refreshing = perform().finally(() => { refreshing = null; });
            return refreshing;
        },
    };
    return store;
}

export const backendStore = createBackendStore();
