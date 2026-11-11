import assert from "node:assert/strict";
import { test } from "node:test";
import { Decimation } from "chart.js";
import { createChartData } from "../src/views/chartData.ts";
const sample = (elapsed_s, power_w) => ({ elapsed_s, data: { power_w } });

test("既存点を再利用して追加分を変換し、欠損・0・負値・同時刻を保持する", () => {
    const convert = createChartData();
    const events = [sample(0, 0), sample(1, -2), sample(1, 3), sample(2, null), sample(3, 4)];
    const first = convert(events, "power_w");
    const second = convert([...events, sample(4, 5)], "power_w");
    assert.deepEqual(first, [[{ x: 0, y: 0 }, { x: 1, y: -2 }, { x: 1, y: 3 }], [{ x: 3, y: 4 }]]);
    assert.equal(first[0][1], second[0][1]);
    assert.equal(first[1].length, 1);
    assert.deepEqual(convert(events, "missing"), []);
    assert.equal(events[3].data.power_w, null);
});

test("実際のmin-maxプラグインで72000点を縮小し、正負のピークと元配列を保持する", () => {
    const points = Array.from({ length: 72000 }, (_, i) => ({ x: i / 10, y: Math.sin(i / 30) }));
    points[10000].y = 50; points[20000].y = -40;
    const dataset = { data: points };
    const chart = { width: 500, data: { datasets: [dataset] }, options: { indexAxis: "x", parsing: false },
        scales: { x: { type: "linear" } }, getDatasetMeta: () => ({ controller: { supportsDecimation: true }, xAxisID: "x",
            iScale: { axis: "x", getUserBounds: () => ({ minDefined: false, maxDefined: false }) } }) };
    Decimation.beforeElementsUpdate(chart, {}, { enabled: true, algorithm: "min-max" });
    assert.equal(dataset._data, points);
    assert.equal(points.length, 72000);
    assert.ok(dataset.data.length < 2500);
    assert.ok(dataset.data.some(point => point.y === 50));
    assert.ok(dataset.data.some(point => point.y === -40));
    assert.equal(points[10000].x, 1000);
    Decimation.destroy(chart);
    assert.equal(dataset.data, points);
});
