import { backendStore as defaultBackendStore } from "./backendStore.ts";
import { measurementStore as defaultMeasurementStore } from "./measurementStore.ts";
import { getWebSocketClient as defaultGetWebSocketClient } from "../api/websocketClient.ts";

type RuntimeSnapshot = Readonly<{ phase: "idle" | "initializing" | "ready" | "error" | "disposed"; error: string }>;
type Backend = {
    getSnapshot(): {
        devices: readonly { id: string; type: string }[] | null;
        measurement: { server_instance_id: string; measurement_id: string | null; sampling_device_ids: readonly string[] } | null;
        synchronization: { status: string; error: string };
    };
    subscribe(listener: () => void): () => void;
    refresh(): Promise<void>;
};
type Measurements = {
    getSnapshot(): { needsResync: boolean; streams: Readonly<Record<string, { sampling: boolean }>> };
    subscribe(listener: () => void): () => void;
    setSession(serverInstanceId: string, measurementId: string | null): void;
    setStreamState(deviceId: string, state: { connected: boolean; sampling: boolean }): void;
    refreshFreshness(maxAgeMs: number): void;
};
type Client = {
    getSnapshot(): { status: string };
    subscribe(listener: () => void): () => void;
    acquire(): () => void;
};
type RuntimeOptions = {
    backendStore?: Backend;
    measurementStore?: Measurements;
    getWebSocketClient?: (key: string | null, maxsize: number) => Client;
    pollIntervalMs?: number;
    staleAfterMs?: number;
};

/** 状態の読出しと受信購読を運転し、取得・記録の開始停止は明示操作に委ねる。 */
export function createApplicationRuntime(options: RuntimeOptions = {}) {
    const backend = options.backendStore ?? defaultBackendStore;
    const measurements = options.measurementStore ?? defaultMeasurementStore;
    const getClient = options.getWebSocketClient ?? defaultGetWebSocketClient;
    const pollIntervalMs = options.pollIntervalMs ?? 1000;
    const staleAfterMs = options.staleAfterMs ?? 5000;
    let state: RuntimeSnapshot = { phase: "idle", error: "" };
    let started = false;
    let reconciling = false;
    let pending: Promise<void> | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const listeners = new Set<() => void>();
    const subscriptions: (() => void)[] = [];
    const leases = new Map<string | null, { client: Client; ids: string[]; release: () => void; unsubscribe: () => void }>();

    function update(phase: RuntimeSnapshot["phase"], error = "") {
        if (state.phase === phase && state.error === error) return;
        state = { phase, error };
        listeners.forEach((listener) => listener());
    }

    function updateStreams() {
        if (state.phase === "disposed") return;
        const active = new Set<string>();
        for (const lease of leases.values()) {
            for (const id of lease.ids) {
                active.add(id);
                measurements.setStreamState(id, { connected: lease.client.getSnapshot().status === "connected", sampling: true });
            }
        }
        for (const id of Object.keys(measurements.getSnapshot().streams)) {
            if (!active.has(id)) measurements.setStreamState(id, { connected: false, sampling: false });
        }
        measurements.refreshFreshness(staleAfterMs);
    }

    function reconcile() {
        if (!started || state.phase === "disposed" || reconciling) return;
        const snapshot = backend.getSnapshot();
        if (snapshot.synchronization.status !== "ready" || !snapshot.measurement) return;
        reconciling = true;
        try {
            const measurement = snapshot.measurement;
            measurements.setSession(measurement.server_instance_id, measurement.measurement_id);
            const desired = new Map<string | null, string[]>();
            for (const id of measurement.sampling_device_ids) {
                const device = snapshot.devices?.find((item) => item.id === id);
                if (!device) continue;
                const key = device.type === "camera" ? id : null;
                desired.set(key, [...(desired.get(key) ?? []), id]);
            }
            for (const [key, lease] of leases) {
                if (desired.has(key)) continue;
                lease.unsubscribe();
                lease.release();
                leases.delete(key);
            }
            for (const [key, ids] of desired) {
                const existing = leases.get(key);
                if (existing) {
                    existing.ids = ids;
                    continue;
                }
                const client = getClient(key, key === null ? 100 : 1);
                let previousStatus = client.getSnapshot().status;
                const unsubscribe = client.subscribe(() => {
                    const status = client.getSnapshot().status;
                    const restored = previousStatus !== "connected" && status === "connected";
                    previousStatus = status;
                    updateStreams();
                    if (restored) requestRefresh();
                });
                const release = client.acquire();
                leases.set(key, { client, ids, release, unsubscribe });
            }
            updateStreams();
        } finally {
            reconciling = false;
        }
    }

    function requestRefresh() {
        if (started && state.phase !== "disposed") void refresh().catch(() => {});
    }

    function schedule() {
        if (timer !== undefined) clearTimeout(timer);
        if (!started || state.phase === "disposed") return;
        timer = setTimeout(() => {
            timer = undefined;
            measurements.refreshFreshness(staleAfterMs);
            requestRefresh();
        }, pollIntervalMs);
    }

    function refresh(): Promise<void> {
        if (state.phase === "disposed") return Promise.resolve();
        if (!started) return start();
        if (pending) return pending;
        if (timer !== undefined) clearTimeout(timer);
        timer = undefined;
        pending = Promise.resolve().then(async () => {
            try {
                await backend.refresh();
                if (state.phase === "disposed") return;
                const snapshot = backend.getSnapshot();
                if (snapshot.synchronization.status !== "ready" || !snapshot.measurement) {
                    throw new Error(snapshot.synchronization.error || "測定状態APIの契約を確認できませんでした。");
                }
                reconcile();
                update("ready");
            } catch (cause) {
                if (state.phase !== "disposed") update("error", cause instanceof Error ? cause.message : String(cause));
                throw cause;
            } finally {
                pending = null;
                schedule();
            }
        });
        return pending;
    }

    function start(): Promise<void> {
        if (state.phase === "disposed") return Promise.resolve();
        if (!started) {
            started = true;
            update("initializing");
            subscriptions.push(backend.subscribe(reconcile));
            subscriptions.push(measurements.subscribe(() => {
                if (!reconciling && measurements.getSnapshot().needsResync) requestRefresh();
            }));
        } else if (!pending && state.phase === "ready") return Promise.resolve();
        return refresh();
    }

    return {
        getSnapshot: () => state,
        subscribe(listener: () => void) {
            listeners.add(listener);
            return () => { listeners.delete(listener); };
        },
        start,
        refresh,
        dispose() {
            if (state.phase === "disposed") return;
            started = false;
            update("disposed");
            if (timer !== undefined) clearTimeout(timer);
            subscriptions.splice(0).forEach((unsubscribe) => unsubscribe());
            for (const lease of leases.values()) {
                lease.unsubscribe();
                lease.release();
            }
            leases.clear();
            for (const [id, stream] of Object.entries(measurements.getSnapshot().streams)) {
                measurements.setStreamState(id, { connected: false, sampling: stream.sampling });
            }
        },
    };
}

export const applicationRuntime = createApplicationRuntime();
