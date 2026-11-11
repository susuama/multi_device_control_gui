import assert from "node:assert/strict";
import { test } from "node:test";
import { createDisplayStore, createFrameDisplayStore } from "../src/state/displayStore.ts";
import { createMeasurementStore } from "../src/state/measurementStore.ts";
import { EMPTY_HISTORY } from "../src/state/measurementHistory.ts";

function source() {
    let value = [];
    const listeners = new Set();
    return { getSnapshot: () => value, subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
        send(next) { value = next; listeners.forEach(listener => listener()); }, count: () => listeners.size };
}

test("表示更新をまとめても全受信値を残し、最後の追加後に最終表示を通知する", context => {
    context.mock.timers.enable({ apis: ["setTimeout"] });
    const store = createMeasurementStore(); store.setSession("s", "m");
    const empty = EMPTY_HISTORY;
    const display = createDisplayStore({ subscribe: store.subscribe, getSnapshot: () => store.getSnapshot().measurements.meter ?? empty });
    let updates = 0; const off = display.subscribe(() => updates++);
    for (let index = 0; index < 1050; index++) store.receive({ type: "measurement", server_instance_id: "s", measurement_id: "m", id: "meter",
        elapsed_s: index, timestamp_ns: index, datetime: "2026-11-08T00:00:00Z", data: { power_w: index } });
    assert.equal(updates, 0);
    assert.equal(store.getSnapshot().measurements.meter.length, 1050);
    context.mock.timers.tick(200);
    assert.equal(updates, 1);
    assert.equal(display.getSnapshot().length, 1050);
    assert.equal(display.getSnapshot().at(0).data.power_w, 0);
    assert.equal(display.getSnapshot().at(-1).data.power_w, 1049);
    assert.equal(display.getSnapshot(), store.getSnapshot().measurements.meter);
    off();
});

test("通知を待ち続けて延期せず、途中でも表示し、測定切替では旧データを直ちに消す", context => {
    context.mock.timers.enable({ apis: ["setTimeout"] });
    const input = source(); const display = createDisplayStore(input);
    let updates = 0; const off = display.subscribe(() => updates++);
    input.send([1]); context.mock.timers.tick(100);
    input.send([1, 2]); context.mock.timers.tick(100);
    assert.deepEqual(display.getSnapshot(), [1, 2]);
    input.send([1, 2, 3]);
    input.send([]);
    assert.deepEqual(display.getSnapshot(), []);
    assert.equal(updates, 2);
    context.mock.timers.tick(200);
    assert.equal(updates, 2);
    off();
});

test("非表示で購読と待機を解放し、再表示では最新履歴から再開する", context => {
    context.mock.timers.enable({ apis: ["setTimeout"] });
    const input = source(); const display = createDisplayStore(input);
    let updates = 0;
    const off = display.subscribe(() => updates++);
    const offSecond = display.subscribe(() => updates++);
    assert.equal(input.count(), 1);
    input.send([1]); off(); offSecond();
    assert.equal(input.count(), 0);
    input.send([2]); context.mock.timers.tick(200);
    assert.equal(updates, 0);
    assert.deepEqual(display.getSnapshot(), [2]);
    const offAgain = display.subscribe(() => updates++);
    input.send([3]); context.mock.timers.tick(200);
    assert.deepEqual(display.getSnapshot(), [3]);
    assert.equal(updates, 1);
    offAgain();
});

test("グラフは次のフレームで最新値を通知し、入力のない間は予約しない", context => {
    const frames = new Map();
    let next = 0;
    globalThis.requestAnimationFrame = callback => { frames.set(++next, callback); return next; };
    globalThis.cancelAnimationFrame = id => frames.delete(id);
    context.after(() => { delete globalThis.requestAnimationFrame; delete globalThis.cancelAnimationFrame; });
    const input = source(); const display = createFrameDisplayStore(input);
    let updates = 0; const off = display.subscribe(() => updates++);
    input.send([1]); input.send([1, 2]);
    assert.equal(frames.size, 1);
    const frame = [...frames.values()][0]; frames.clear(); frame();
    assert.deepEqual(display.getSnapshot(), [1, 2]);
    assert.equal(updates, 1);
    assert.equal(frames.size, 0);
    input.send([3]); input.send([]);
    assert.deepEqual(display.getSnapshot(), []);
    assert.equal(frames.size, 0);
    input.send([4]); off();
    assert.equal(frames.size, 0);
    assert.equal(input.count(), 0);
    input.send([5]);
    const again = display.subscribe(() => updates++);
    assert.deepEqual(display.getSnapshot(), [5]);
    again();
});
