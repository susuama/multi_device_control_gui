import assert from "node:assert/strict";
import { test } from "node:test";
import { graphMeasurements, orderedMeasurements, measurementFields, measurementLabel, formatMeasurementValue, formatMeasurementTime, measurementRowKey } from "../src/views/measurementDisplay.ts";
import { createMeasurementStore } from "../src/state/measurementStore.ts";
const event = (elapsed_s, data) => ({ type: "measurement", server_instance_id: "s", measurement_id: "m", id: "meter", elapsed_s,
    timestamp_ns: elapsed_s * 1e9, datetime: "2026-11-08T00:00:00+09:00", data });

test("同時刻の別サンプルを区別し、並び順と表示範囲が動いても行を識別する", () => {
    const first = event(1, { power_w: 1 }), second = event(1, { power_w: 2 });
    const key = measurementRowKey(first);
    assert.notEqual(key, measurementRowKey(second));
    assert.equal(measurementRowKey(orderedMeasurements([second, first])[1]), key);
    assert.equal(measurementRowKey([event(0, {}), first].slice(-1)[0]), key);
});

test("日時フォーマッターを再利用しても既存の日時表記を変えない", () => {
    for (const value of ["2026-11-08T00:00:00.123+09:00", "2026-01-01T13:04:05.001Z"]) {
        assert.equal(formatMeasurementTime(value), new Date(value).toLocaleString("ja-JP", { hour12: false, fractionalSecondDigits: 3 }));
    }
});
test("表示用に時刻順へ並べても共有履歴を変更せず、最後の受信時刻で範囲を絞る", () => {
    const source = [event(30, { power_w: -1 }), event(0, { power_w: 2 }), event(20, { power_w: 0 })];
    assert.deepEqual(orderedMeasurements(source).map(e => e.elapsed_s), [0, 20, 30]);
    assert.equal(source[0].elapsed_s, 30);
    assert.deepEqual(graphMeasurements(source, "power_w", 10).map(p => [p.elapsed_s, p.value]), [[20, 0], [30, -1]]);
    assert.equal(graphMeasurements(source, "power_w", 0).length, 3);
    assert.deepEqual(graphMeasurements([], "power_w", 10), []);
});
test("0・負値と欠損を区別し、非数値・非有限値をグラフへ入れず、未知の単位を補わない", () => {
    const source = [event(0, { power_w: 0, label: "x", unknown: 1 }), event(1, { power_w: null }), event(2, { power_w: -2 }),
        event(3, {}), event(4, { power_w: "4" }), event(5, { power_w: Infinity })];
    assert.deepEqual(measurementFields(source, true), ["power_w", "unknown"]);
    assert.deepEqual(graphMeasurements(source, "power_w", 0).map(p => p.value), [0, null, -2, null, null, null]);
    assert.equal(measurementLabel("power_w"), "パワー [W]");
    assert.equal(measurementLabel("unknown"), "unknown");
    assert.equal(formatMeasurementValue(0), "0");
    assert.equal(formatMeasurementValue(false), "false");
    assert.equal(formatMeasurementValue(null), "—");
    assert.equal(formatMeasurementValue({ a: 1 }), '{"a":1}');
});
test("全履歴から表示範囲を取り出し、測定切替後の履歴を混ぜない", () => {
    const store = createMeasurementStore(); store.setSession("s", "m");
    for (let index = 0; index < 1050; index++) store.receive(event(index, { power_w: index }));
    const history = store.getSnapshot().measurements.meter;
    const events = history.read();
    assert.equal(events.length, 1050);
    assert.equal(graphMeasurements(events, "power_w", 0)[0].elapsed_s, 0);
    assert.equal(graphMeasurements(events, "power_w", 30).length, 31);
    assert.equal(events.slice(-100).length, 100);
    store.setSession("s", "new");
    assert.equal(store.getSnapshot().measurements.meter, undefined);
    assert.equal(store.receive(event(1051, { power_w: 1 })), false);
});
