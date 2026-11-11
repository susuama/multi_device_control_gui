import assert from "node:assert/strict";
import { test } from "node:test";
import { createMeasurementHistory } from "../src/state/measurementHistory.ts";
const sample = (elapsed_s, data = { power_w: elapsed_s }) => ({ elapsed_s, data });

test("追記しても古いスナップショットの件数・読出し・項目・時刻範囲は変わらない", () => {
    const writer = createMeasurementHistory();
    const empty = writer.getSnapshot();
    const first = writer.append(sample(1));
    const shown = first.range(-Infinity);
    const second = writer.append(sample(2, { power_w: null, other: 0, label: "x" }));
    assert.equal(first.length, 1);
    assert.equal(first.at(1), undefined);
    assert.equal(first.at(-2), undefined);
    assert.deepEqual(first.read(0, 100), shown);
    assert.deepEqual(first.range(0), shown);
    assert.equal(first.end, 1);
    assert.deepEqual(first.fields, ["power_w"]);
    assert.deepEqual(second.fields, ["power_w", "other", "label"]);
    assert.deepEqual(second.numericFields, ["power_w", "other"]);
    assert.deepEqual(empty.read(), []);
    assert.deepEqual(second.latest(1), [second.at(-1)]);
    assert.equal(shown.length, 1);
});

test("逆順受信・同時刻・負値・欠損を保持し、表示時だけ時刻順にする", () => {
    const writer = createMeasurementHistory();
    const a = sample(3), b = sample(1, { power_w: -1 }), c = sample(3, { power_w: null });
    writer.append(a); writer.append(b); const snapshot = writer.append(c);
    assert.deepEqual(snapshot.read(), [a, b, c]);
    assert.deepEqual(snapshot.range(1), [b, a, c]);
    assert.deepEqual(snapshot.range(3), [a, c]);
    assert.deepEqual(snapshot.latest(2), [a, c]);
    assert.equal(snapshot.start, 1); assert.equal(snapshot.end, 3);
    writer.append(sample(0));
    assert.deepEqual(snapshot.range(-Infinity), [b, a, c]);
});
