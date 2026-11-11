import assert from "node:assert/strict";
import { test } from "node:test";
import { formatLogEntry } from "../src/logging/logDisplay.ts";
import { logger, logStore } from "../src/logging/logStore.ts";

test("ログの日時・余白・本文・順序を維持し、同じ行を繰り返し表示できる", () => {
    const entries = [
        { id: 1, timestamp: "2026-11-08T00:00:00.123+09:00", level: "INFO", source: "api", message: "日本語\n複数行" },
        { id: 2, timestamp: "2026-01-01T13:04:05Z", level: "ERROR", source: "long-source-name", message: "失敗" },
    ];
    const expected = entries.map(entry => `${new Date(entry.timestamp).toLocaleTimeString("ja-JP", { hour12: false })} ${entry.level.padEnd(5)} ${entry.source.padEnd(10)} ${entry.message}`);
    assert.deepEqual(entries.map(formatLogEntry), expected);
    assert.deepEqual(entries.map(formatLogEntry), expected);
});

test("上限循環とクリア後も現在のログだけを表示する", () => {
    logStore.clear();
    for (let index = 0; index < 1010; index++) logger.info("test", String(index));
    const entries = logStore.getSnapshot();
    assert.equal(entries.length, 1000);
    assert.ok(formatLogEntry(entries[0]).endsWith(" 10"));
    assert.ok(formatLogEntry(entries.at(-1)).endsWith(" 1009"));
    logStore.clear();
    logger.info("test", "新規");
    assert.equal(logStore.getSnapshot().map(formatLogEntry).length, 1);
    assert.ok(formatLogEntry(logStore.getSnapshot()[0]).endsWith(" 新規"));
});
