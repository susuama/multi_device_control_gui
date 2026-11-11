import type { LogEntry } from "./logStore.ts";

const formatter = new Intl.DateTimeFormat("ja-JP", { hour: "numeric", minute: "numeric", second: "numeric", hour12: false });
const lines = new WeakMap<LogEntry, string>();

/** 保持中のログの整形結果を再利用する。破棄されたログはキャッシュも回収される。 */
export function formatLogEntry(entry: LogEntry): string {
    let line = lines.get(entry);
    if (line === undefined) {
        line = `${formatter.format(new Date(entry.timestamp))} ${entry.level.padEnd(5)} ${entry.source.padEnd(10)} ${entry.message}`;
        lines.set(entry, line);
    }
    return line;
}
