import assert from "node:assert/strict";
import { test } from "node:test";
import { decodeEvent } from "../src/api/eventDecoder.ts";
import { createMeasurementStore } from "../src/state/measurementStore.ts";

const sample = (id, index) => ({ server_instance_id: "server", measurement_id: "measurement", type: "measurement", id, timestamp_ns: 1000 + index, elapsed_s: index / 10,
    datetime: "2026-11-02T00:00:00+09:00", data: { power_w: index } });
const metadata = (overrides = {}) => ({ server_instance_id: "server", measurement_id: "measurement", type: "camera_preview", id: "カメラA", frame_index: 7,
    timestamp_ns: "1000000000000000007", width: 640, height: 480, ...overrides });
export function binaryFrame(overrides = {}) {
    const header = new TextEncoder().encode(JSON.stringify(metadata(overrides)));
    const bytes = new Uint8Array(4 + header.length + 4);
    new DataView(bytes.buffer).setUint32(0, header.length, false);
    bytes.set(header, 4);
    bytes.set([0xff, 0xd8, 0xff, 0xd9], 4 + header.length);
    return bytes.buffer;
}

test("数値履歴はデバイス別に全件保持し、複数購読者へ通知する。購読解除で消去しない", () => {
    const store = createMeasurementStore();
    store.setSession("server", "measurement");
    let first = 0, second = 0;
    const off = store.subscribe(() => first++);
    store.subscribe(() => second++);
    for (let i = 0; i < 72000; i++) store.receive(sample("meter", i));
    const before = store.getSnapshot();
    assert.equal(before.measurements.meter.length, 72000);
    assert.equal(before.measurements.meter.at(0).data.power_w, 0);
    assert.equal(before.measurements.meter.at(-1).data.power_w, 71999);
    assert.equal(first, second);
    off();
    store.receive(sample("other", 100));
    assert.equal(second, first + 1);
    assert.equal(before.measurements.other, undefined);
    assert.equal(store.getSnapshot().measurements.meter, before.measurements.meter);
    assert.equal(store.getSnapshot(), store.getSnapshot());
});

test("UTF-8のバイト長とビッグエンディアンを読み、取得時刻の桁とJPEGのバイト列を保持する", async () => {
    const frame = decodeEvent(binaryFrame());
    assert.equal(frame.id, "カメラA");
    assert.equal(frame.timestamp_ns, "1000000000000000007");
    assert.equal(frame.jpeg.type, "image/jpeg");
    assert.deepEqual([...new Uint8Array(await frame.jpeg.arrayBuffer())], [0xff, 0xd8, 0xff, 0xd9]);
});

test("画像はデバイス別に最新1枚を保持し、別の接続から遅れて届く古い画像へ戻らない", () => {
    const store = createMeasurementStore();
    store.setSession("server", "measurement");
    store.receive(decodeEvent(binaryFrame()));
    const first = store.getSnapshot();
    store.receive(decodeEvent(binaryFrame({ frame_index: 8, timestamp_ns: "1000000000000000008" })));
    store.receive(decodeEvent(binaryFrame({ frame_index: 6, timestamp_ns: "1000000000000000006" })));
    store.receive(decodeEvent(binaryFrame({ id: "cameraB" })));
    assert.equal(store.getSnapshot().previews["カメラA"].frame_index, 8);
    assert.equal(first.previews["カメラA"].frame_index, 7);
    assert.equal(Object.keys(store.getSnapshot().previews).length, 2);
});

test("不正な画像長、メタデータ、UTF-8、数値イベントを拒否する", () => {
    assert.throws(() => decodeEvent(new ArrayBuffer(3)), /長さ/);
    const short = binaryFrame();
    new DataView(short).setUint32(0, short.byteLength, false);
    assert.throws(() => decodeEvent(short), /JSON長/);
    const invalidUtf8 = new Uint8Array([0, 0, 0, 1, 0xff, 0xff]).buffer;
    assert.throws(() => decodeEvent(invalidUtf8));
    for (const invalid of [{ type: "measurement" }, { width: 0 }, { frame_index: -1 }, { timestamp_ns: 1 }]) {
        assert.throws(() => decodeEvent(binaryFrame(invalid)), /メタデータ/);
    }
    for (const invalid of [{ type: "settings" }, { data: [] }, { datetime: "invalid" }, { elapsed_s: "1" }]) {
        assert.throws(() => decodeEvent(JSON.stringify({ ...sample("meter", 1), ...invalid })), /数値イベント/);
    }
});


test("未確認の受信IDを採用せず、確認済み測定の変更時に履歴を破棄する", () => {
    const store = createMeasurementStore();
    assert.equal(store.receive(sample("meter", 1)), false);
    assert.equal(store.getSnapshot().session, null);
    assert.equal(store.getSnapshot().needsResync, true);
    store.setSession("server", "measurement");
    store.receive(sample("meter", 1));
    const previous = store.getSnapshot().measurements;
    store.setSession("server", "measurement");
    assert.equal(store.getSnapshot().measurements, previous);
    assert.equal(store.receive({ ...sample("meter", 2), measurement_id: "other" }), false);
    assert.equal(store.getSnapshot().measurements.meter.length, 1);
    store.setSession("server", "other");
    assert.deepEqual(store.getSnapshot().measurements, {});
    assert.equal(store.getSnapshot().needsResync, false);
});

test("通信断と鮮度低下では最後の値を保持し、同じ状態設定は通知しない", () => {
    const store = createMeasurementStore();
    store.setSession("server", "measurement");
    store.setStreamState("meter", { connected: true, sampling: true });
    store.receive(sample("meter", 1));
    assert.equal(store.getSnapshot().streams.meter.stale, false);
    const before = store.getSnapshot();
    store.setSession("server", "measurement");
    store.setStreamState("meter", { connected: true, sampling: true });
    assert.equal(store.getSnapshot(), before);
    store.refreshFreshness(-1);
    assert.equal(store.getSnapshot().streams.meter.stale, true);
    store.setStreamState("meter", { connected: false, sampling: true });
    assert.equal(store.getSnapshot().measurements.meter.length, 1);
    assert.equal(store.getSnapshot().streams.meter.sampling, true);
    store.setSession("new-server", "measurement");
    assert.equal(store.getSnapshot().streams.meter.lastReceivedAt, null);
});
