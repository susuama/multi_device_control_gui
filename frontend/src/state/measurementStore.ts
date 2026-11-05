import type { CameraPreviewEvent, MeasurementEvent, ReceivedEvent } from "../type/event.ts";

export const MEASUREMENT_LIMIT = 1000;
export type StreamState = Readonly<{ connected: boolean; sampling: boolean; lastReceivedAt: number | null; stale: boolean }>;
export type DataSnapshot = Readonly<{
    measurements: Readonly<Record<string, readonly MeasurementEvent[]>>;
    previews: Readonly<Record<string, CameraPreviewEvent>>;
    session: { server_instance_id: string; measurement_id: string | null } | null;
    needsResync: boolean;
    streams: Readonly<Record<string, StreamState>>;
}>;

/** 確認済み測定期間の数値履歴と最新画像を共有する。 */
export function createMeasurementStore() {
    let state: DataSnapshot = { measurements: {}, previews: {}, session: null, needsResync: false, streams: {} };
    const listeners = new Set<() => void>();
    function update(changes: Partial<DataSnapshot>) {
        state = { ...state, ...changes };
        listeners.forEach((listener) => listener());
    }
    return {
        getSnapshot: () => state,
        subscribe(listener: () => void) {
            listeners.add(listener);
            return () => { listeners.delete(listener); };
        },
        setSession(serverInstanceId: string, measurementId: string | null) {
            const same = state.session?.server_instance_id === serverInstanceId && state.session?.measurement_id === measurementId;
            if (same && !state.needsResync) return;
            update({ session: { server_instance_id: serverInstanceId, measurement_id: measurementId }, needsResync: false,
                ...(!same ? { measurements: {}, previews: {}, streams: Object.fromEntries(Object.entries(state.streams)
                    .map(([id, stream]) => [id, { ...stream, lastReceivedAt: null, stale: true }])) } : {}) });
        },
        setStreamState(id: string, value: { connected: boolean; sampling: boolean }) {
            const previous = state.streams[id];
            if (previous?.connected === value.connected && previous.sampling === value.sampling) return;
            update({ streams: { ...state.streams, [id]: { ...value, lastReceivedAt: previous?.lastReceivedAt ?? null,
                stale: !value.connected || !value.sampling || (previous?.stale ?? true) } } });
        },
        refreshFreshness(maxAgeMs: number) {
            const now = Date.now();
            let changed = false;
            const streams = Object.fromEntries(Object.entries(state.streams).map(([id, stream]) => {
                const stale = !stream.connected || !stream.sampling || stream.lastReceivedAt === null || now - stream.lastReceivedAt > maxAgeMs;
                changed ||= stale !== stream.stale;
                return [id, stale === stream.stale ? stream : { ...stream, stale }];
            }));
            if (changed) update({ streams });
        },
        receive(event: ReceivedEvent): boolean {
            if (!state.session || event.server_instance_id !== state.session.server_instance_id
                || event.measurement_id !== state.session.measurement_id || !event.measurement_id) {
                if (!state.needsResync) update({ needsResync: true });
                return false;
            }
            if (event.type === "camera_preview") {
                const previous = state.previews[event.id];
                if (previous && BigInt(event.timestamp_ns) <= BigInt(previous.timestamp_ns)) return true;
            }
            const previous = state.streams[event.id] ?? { connected: false, sampling: false };
            const streams = { ...state.streams, [event.id]: { ...previous, lastReceivedAt: Date.now(), stale: !previous.connected || !previous.sampling } };
            if (event.type === "measurement") {
                update({ streams, measurements: { ...state.measurements, [event.id]: [...(state.measurements[event.id] ?? []), event].slice(-MEASUREMENT_LIMIT) } });
            } else update({ streams, previews: { ...state.previews, [event.id]: event } });
            return true;
        },
    };
}

export const measurementStore = createMeasurementStore();
