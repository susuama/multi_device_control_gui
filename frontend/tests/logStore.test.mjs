import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { logger, logStore, LOG_LIMIT } from "../src/logging/logStore.ts";

beforeEach(() => logStore.clear());

test("複数の購読先へ通知し、購読解除後もログの保持を続ける", () => {
    let first = 0;
    let second = 0;
    const unsubscribeFirst = logStore.subscribe(() => first++);
    const unsubscribeSecond = logStore.subscribe(() => second++);
    logger.info("frontend", "first");
    unsubscribeFirst();
    logger.warn("frontend", "second");
    unsubscribeSecond();
    logger.error("frontend", "third");
    assert.equal(first, 1);
    assert.equal(second, 2);
    assert.deepEqual(logStore.getSnapshot().map((entry) => entry.message), ["first", "second", "third"]);
});

test("保持上限を超えた古いログを破棄し、最新のログを残す", () => {
    for (let index = 0; index < LOG_LIMIT + 5; index++) logger.info("test", String(index));
    const entries = logStore.getSnapshot();
    assert.equal(entries.length, LOG_LIMIT);
    assert.equal(entries[0].message, "5");
    assert.equal(entries.at(-1).message, String(LOG_LIMIT + 4));
});

test("更新がない間は同じスナップショットを返し、クリアを全購読先へ通知する", () => {
    logger.info("test", "kept");
    const snapshot = logStore.getSnapshot();
    assert.equal(snapshot, logStore.getSnapshot());
    let updates = 0;
    const unsubscribe = logStore.subscribe(() => updates++);
    logStore.clear();
    logStore.clear();
    unsubscribe();
    assert.equal(updates, 1);
    assert.notEqual(snapshot, logStore.getSnapshot());
    assert.deepEqual(logStore.getSnapshot(), []);
    assert.equal(snapshot[0].message, "kept");
});
