/** 表示部品から独立してログを保持し、更新を購読者へ通知する。共有範囲は現在のウィンドウ。 */
export type LogLevel = "INFO" | "WARN" | "ERROR";
export type LogEntry = Readonly<{
    id: number;
    timestamp: string;
    level: LogLevel;
    source: string;
    message: string;
}>;

export const LOG_LIMIT = 1000;

let entries: readonly LogEntry[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function append(level: LogLevel, source: string, message: string): void {
    const entry = { id: nextId++, timestamp: new Date().toISOString(), level, source, message };
    entries = [...entries, entry].slice(-LOG_LIMIT);
    listeners.forEach((listener) => listener());
}

export const logStore = {
    // 更新がない間は同じ配列を返し、購読先で不要な再描画を起こさない。
    getSnapshot: () => entries,
    subscribe(listener: () => void) {
        listeners.add(listener);
        return () => { listeners.delete(listener); };
    },
    clear() {
        if (entries.length === 0) return;
        entries = [];
        listeners.forEach((listener) => listener());
    },
};

export const logger = {
    info: (source: string, message: string) => append("INFO", source, message),
    warn: (source: string, message: string) => append("WARN", source, message),
    error: (source: string, message: string) => append("ERROR", source, message),
};
