import assert from "node:assert/strict";
import { test } from "node:test";
import { decodeEvent } from "../src/api/eventDecoder.ts";

const measurement = { type: "measurement", server_instance_id: "server-A", measurement_id: "measurement-A", id: "meter", timestamp_ns: 1000,
    elapsed_s: 0.1, datetime: "2026-11-02T00:00:00+09:00", data: { power_w: 1 } };
const metadata = { type: "camera_preview", server_instance_id: "server-A", measurement_id: "measurement-A", id: "camera-日本語",
    frame_index: 0, timestamp_ns: "1000000000000000000", width: 10, height: 10 };
function binary(meta, jpeg = [0xff, 0xd8]) {
    const header = new TextEncoder().encode(JSON.stringify(meta));
    const bytes = new Uint8Array(4 + header.length + jpeg.length);
    new DataView(bytes.buffer).setUint32(0, header.length, false);
    bytes.set(header, 4);
    bytes.set(jpeg, 4 + header.length);
    return bytes.buffer;
}

test("数値とUTF-8画像メタデータの識別IDを保持する", () => {
    assert.deepEqual(decodeEvent(JSON.stringify(measurement)), measurement);
    const preview = decodeEvent(binary(metadata));
    assert.equal(preview.id, metadata.id);
    assert.equal(preview.server_instance_id, metadata.server_instance_id);
    assert.equal(preview.measurement_id, metadata.measurement_id);
    assert.equal(preview.timestamp_ns, metadata.timestamp_ns);
    assert.equal(preview.jpeg.size, 2);
    assert.equal(preview.jpeg.type, "image/jpeg");
});

test("識別IDが未実装・null・空文字・数値ならデータを採用しない", () => {
    for (const field of ["server_instance_id", "measurement_id"]) {
        for (const value of [undefined, null, "", 1]) {
            assert.throws(() => decodeEvent(JSON.stringify({ ...measurement, [field]: value })), /形式/);
            assert.throws(() => decodeEvent(binary({ ...metadata, [field]: value })), /メタデータ/);
        }
    }
});

test("不正JSON、長さ不足、JPEGなし、不正メタデータを拒否する", () => {
    assert.throws(() => decodeEvent("{broken"));
    assert.throws(() => decodeEvent(new ArrayBuffer(3)), /長さ情報/);
    assert.throws(() => decodeEvent(binary(metadata, [])), /JSON長/);
    assert.throws(() => decodeEvent(binary({ ...metadata, timestamp_ns: 100 })), /メタデータ/);
    assert.throws(() => decodeEvent(binary({ ...metadata, width: 0 })), /メタデータ/);
    assert.throws(() => decodeEvent(JSON.stringify({ ...measurement, elapsed_s: "1" })), /形式/);
});
