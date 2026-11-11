import assert from "node:assert/strict";
import { test } from "node:test";
import { createGraphSelection as createSelection, createGraphSeries, graphValue } from "../src/views/graphSeries.ts";
import { createMeasurementHistory } from "../src/state/measurementHistory.ts";
const cache = new WeakMap();
function history(events) {
    if (!cache.has(events)) {
        const writer = createMeasurementHistory();
        for (const event of events) writer.append(event);
        cache.set(events, writer.getSnapshot());
    }
    return cache.get(events);
}
function createGraphSelection(ids) {
    const select = createSelection(ids);
    return histories => select(Object.fromEntries(Object.entries(histories).map(([id, events]) => [id, history(events)])));
}
const sample = (id, elapsed_s, value) => ({ id, elapsed_s, datetime: `t${elapsed_s}`, data: { power_w: value } });
function graphSeries(events, ids, field, seconds) {
    const histories = ids.map(id => ({ id, events: history(events.filter(event => event.id === id)) }));
    return createGraphSeries()(histories, ids, field, seconds).series;
}

test("取得時刻を丸めず、同時刻の別サンプル・欠損・0・負値を系列ごとに保持する", () => {
    const events = [sample("a", 1, 0), sample("b", 1.25, -2), sample("a", 1, 3), sample("b", 2, null), sample("b", 3, "4")];
    const lines = graphSeries(events, ["a", "b"], "power_w", 0);
    assert.deepEqual(lines[0].points.map(p => [p.elapsed_s, graphValue(p, "power_w")]), [[1, 0], [1, 3]]);
    assert.deepEqual(lines[1].points.map(p => [p.elapsed_s, graphValue(p, "power_w")]), [[1.25, -2], [2, null], [3, null]]);
    assert.equal(lines[0].points[0], events[0]);
    assert.equal(events[1].elapsed_s, 1.25);
});

test("全系列で共通の右端を使い、停止した系列を古い時間幅で別表示しない", () => {
    const events = [sample("a", 1, 1), sample("b", 95, 2), sample("a", 90, 3), sample("b", 100, 4)];
    assert.deepEqual(graphSeries(events, ["a", "b"], "power_w", 10).map(s => s.points.map(p => p.elapsed_s)), [[90], [95, 100]]);
    assert.deepEqual(graphSeries(events, ["a"], "power_w", 10)[0].points.map(p => p.elapsed_s), [90]);
});

test("選択外の機器の更新を無視し、機器別1000件と測定切替の空履歴を取り込む", () => {
    const select = createGraphSelection(["a", "b"]);
    const a = Array.from({ length: 1000 }, (_, i) => sample("a", i, i));
    const b = Array.from({ length: 1000 }, (_, i) => sample("b", i + .25, i));
    const result = select({ a, b });
    assert.equal(result.length, 2);
    assert.deepEqual(result[0].events.read(), a);
    assert.deepEqual(result[1].events.read(), b);
    assert.equal(select({ a, b, c: [sample("c", 1, 1)] }), result);
    assert.deepEqual(createGraphSeries()(result, ["a", "b"], "power_w", 0).series.map(s => s.points.length), [1000, 1000]);
    assert.deepEqual(select({}), []);
});

test("更新のない機器の系列を再利用し、共通窓の境界を越えたときだけ切り直す", () => {
    const select = createGraphSelection(["a", "b"]), prepare = createGraphSeries();
    const a = [sample("a", 90, 1), sample("a", 100, 2)];
    const b = [sample("b", 95, 3), sample("b", 100, 4)];
    const first = prepare(select({ a, b }), ["a", "b"], "power_w", 10);
    const second = prepare(select({ a: [...a, sample("a", 101, 5)], b }), ["a", "b"], "power_w", 10);
    assert.equal(second.series[1], first.series[1]);
    assert.deepEqual(second.series[0].points.map(p => p.elapsed_s), [100, 101]);
    const third = prepare(select({ a: [...a, sample("a", 110, 6)], b }), ["a", "b"], "power_w", 10);
    assert.notEqual(third.series[1], first.series[1]);
    assert.deepEqual(third.series[1].points, [b[1]]);
    assert.equal(third.retainedCount, 5);
    assert.equal(third.retainedStart, 90);
    assert.equal(third.displayStart, 100);
    assert.equal(third.displayEnd, 110);
    const hidden = prepare(select({ a, b }), ["b"], "power_w", 10);
    assert.deepEqual(hidden.series.map(line => line.id), ["b"]);
    assert.deepEqual(prepare(select({}), ["a", "b"], "power_w", 0).series, []);
});

test("末尾追加・先頭削除と時刻逆転を扱い、元の履歴を変更しない", () => {
    const select = createGraphSelection(["a"]), prepare = createGraphSeries();
    const a = [sample("a", 1, 1), sample("a", 2, 2), sample("a", 3, 3)];
    assert.deepEqual(prepare(select({ a }), ["a"], "power_w", 0).series[0].points, a);
    const appended = [...a.slice(1), sample("a", 4, 4)];
    assert.deepEqual(prepare(select({ a: appended }), ["a"], "power_w", 0).series[0].points, appended);
    const late = [...appended, sample("a", 2.5, -1), sample("a", 3, 0)];
    const result = prepare(select({ a: late }), ["a"], "power_w", 0);
    assert.deepEqual(result.series[0].points.map(p => p.elapsed_s), [2, 2.5, 3, 3, 4]);
    assert.equal(result.series[0].points[2], a[2]);
    assert.deepEqual(late.map(p => p.elapsed_s), [2, 3, 4, 2.5, 3]);
    const replaced = [sample("a", 10, Infinity), sample("a", 11, NaN), sample("a", 12, 0)];
    assert.equal(prepare(select({ a: replaced }), ["a"], "power_w", 0).series[0].numericCount, 1);
    assert.equal(prepare(select({ a: replaced }), ["a"], "other", 0).series[0].numericCount, 0);
});
