type Source<T> = { getSnapshot: () => T; subscribe: (listener: () => void) => () => void };

/** 表示への通知だけをまとめる。受信元を変更せず、待機中も最新スナップショット1件だけを参照する。 */
export function createDisplayStore<T extends { readonly length: number }>(source: Source<T>, intervalMs = 200) {
    return createScheduledDisplayStore(source, publish => {
        const timer = setTimeout(publish, intervalMs);
        return () => clearTimeout(timer);
    });
}

/** 新しいデータが届いたときだけ、次の画面フレームへ通知をまとめる。 */
export function createFrameDisplayStore<T extends { readonly length: number }>(source: Source<T>) {
    return createScheduledDisplayStore(source, publish => {
        const frame = requestAnimationFrame(publish);
        return () => cancelAnimationFrame(frame);
    });
}

function createScheduledDisplayStore<T extends { readonly length: number }>(source: Source<T>, schedule: (publish: () => void) => () => void) {
    let snapshot = source.getSnapshot();
    let cancelPending: (() => void) | undefined;
    let unsubscribe: (() => void) | undefined;
    const listeners = new Set<() => void>();
    function publish() {
        cancelPending = undefined;
        const next = source.getSnapshot();
        if (next === snapshot) return;
        snapshot = next;
        listeners.forEach(listener => listener());
    }
    function changed() {
        const next = source.getSnapshot();
        if (next === snapshot) return;
        if (next.length === 0) {
            cancelPending?.();
            publish();
        } else if (!cancelPending) cancelPending = schedule(publish);
    }
    return {
        getSnapshot: () => listeners.size ? snapshot : source.getSnapshot(),
        subscribe(listener: () => void) {
            if (!listeners.size) {
                snapshot = source.getSnapshot();
                unsubscribe = source.subscribe(changed);
            }
            listeners.add(listener);
            return () => {
                listeners.delete(listener);
                if (!listeners.size) {
                    unsubscribe?.(); unsubscribe = undefined;
                    cancelPending?.(); cancelPending = undefined;
                }
            };
        },
    };
}
