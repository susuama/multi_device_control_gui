import assert from "node:assert/strict";
import { test } from "node:test";
import { createSettingsUpdates } from "../src/state/settingsUpdates.ts";

const pause = (ms) => new Promise(resolve => setTimeout(resolve, ms));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
async function until(check) {
    const deadline = Date.now() + 2000;
    while (!check()) { assert.ok(Date.now() < deadline, "待機期限"); await pause(5); }
}
const camera = () => ({ width: 640, height: 480, fps: 10, exposure: 0.01, gain: 1, gain_boost: false, pixel_format: "mono8", roi: null });
const edit = (value, revision) => ({ value, revision });

function fixture(interval = 20) {
    let state = { measurement: { server_instance_id: "server" }, devices: ["c", "d"].map(id => ({ id, type: "camera", status: "connected" })),
        synchronization: { status: "ready" }, settings: { c: camera(), d: camera() }, requests: {}, recording: { targets: {} } };
    const listeners = new Set();
    const calls = [], errors = [], applied = [];
    let active = 0, maxActive = 0;
    const controls = [];
    function update(changes) { state = { ...state, ...changes }; for (const listener of listeners) listener(); }
    const store = {
        getSnapshot: () => state,
        subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
        async updateSettings(id, value) {
            calls.push({ id, value, at: Date.now() });
            active++; maxActive = Math.max(maxActive, active);
            update({ requests: { ...state.requests, [`device:${id}/settings`]: { pending: 1, writing: true } } });
            try {
                await (controls.shift()?.promise ?? Promise.resolve());
                update({ settings: { ...state.settings, [id]: value } });
            } finally {
                active--;
                update({ requests: { ...state.requests, [`device:${id}/settings`]: { pending: 0, writing: false } } });
            }
        },
    };
    const queue = createSettingsUpdates(store, interval);
    const client = (id = "c") => queue.createEditor({ deviceId: id, session: "server", type: "camera", blocked: false,
        onProgress() {}, onError: message => errors.push(message), onApplied: edits => applied.push(edits) });
    return { queue, client, calls, controls, errors, applied, update, state: () => state, maxActive: () => maxActive };
}

test("入力が続く間も間隔送信し、最後の操作値まで反映する", async () => {
    const f = fixture(40), editor = f.client();
    try {
        for (let i = 2; i <= 14; i++) {
            editor.replace({ gain: edit(String(i), i) }, "immediate");
            await pause(15);
        }
        assert.ok(f.calls.length >= 3, "入力停止まで送信を待ち直しています");
        editor.flush();
        await until(() => f.state().settings.c.gain === 14);
        assert.ok(f.calls.length < 13, "各入力をそのまま送信しています");
        assert.ok(f.calls.slice(1, -1).every((call, i) => call.at - f.calls[i].at >= 35));
        assert.equal(f.maxActive(), 1);
    } finally { f.queue.dispose(); }
});

test("遅い応答中は項目ごとの最新値へ集約し、複数パネルでも同一機器に1件ずつ送る", async () => {
    const f = fixture(), first = f.client(), second = f.client();
    const slow = deferred(); f.controls.push(slow);
    try {
        first.replace({ exposure: edit("12", 1) }, "immediate"); first.flush();
        await until(() => f.calls.length === 1);
        first.replace({ exposure: edit("12", 1), gain: edit("3", 2) }, "immediate");
        first.replace({ exposure: edit("18", 3), gain: edit("4", 4) }, "immediate");
        second.replace({ fps: edit("15", 5) }, "manual"); second.flush();
        await pause(50);
        assert.equal(f.calls.length, 1);
        slow.resolve();
        await until(() => f.calls.length === 3 && f.state().settings.c.fps === 15);
        assert.equal(f.maxActive(), 1);
        assert.equal(f.calls[1].value.exposure, 0.018);
        assert.equal(f.calls[1].value.gain, 4);
        assert.equal(f.calls[1].value.fps, 10);
        assert.equal(f.calls[2].value.exposure, 0.018);
        assert.deepEqual(f.applied[0], { exposure: edit("12", 1) });
    } finally { slow.resolve(); f.queue.dispose(); }
});

test("送信済みの変更を次の待機分へ混ぜず、送信直前の最新設定とROIを使う", async () => {
    const f = fixture(), editor = f.client(), slow = deferred(); f.controls.push(slow);
    try {
        editor.replace({ exposure: edit("12", 1) }, "immediate"); editor.flush();
        await until(() => f.calls.length === 1);
        editor.replace({ exposure: edit("12", 1), gain: edit("4", 2) }, "immediate");
        slow.resolve();
        await until(() => f.applied.length === 1);
        const roi = { x: 2, y: 3, width: 20, height: 30 };
        f.update({ settings: { ...f.state().settings, c: { ...f.state().settings.c, exposure: 0.025, roi } } });
        await until(() => f.calls.length === 2);
        assert.equal(f.calls[1].value.exposure, 0.025);
        assert.deepEqual(f.calls[1].value.roi, roi);
        assert.equal(f.calls[1].value.gain, 4);
    } finally { slow.resolve(); f.queue.dispose(); }
});

test("同値の変更ではHTTPを省略し、manualをimmediateへ混ぜる要求を拒否する", async () => {
    const f = fixture(), editor = f.client();
    try {
        editor.replace({ gain: edit("1.0", 1) }, "immediate"); editor.flush();
        await until(() => f.applied.length === 1);
        assert.equal(f.calls.length, 0);
        editor.replace({ fps: edit("20", 2) }, "immediate"); editor.flush();
        await until(() => f.errors.length === 1);
        assert.equal(f.calls.length, 0);
        assert.match(f.errors[0], /適用方法/);
    } finally { f.queue.dispose(); }
});

test("非表示・破棄、切断、起動ID変更、明示取消しでは待機分を送らない", async () => {
    for (const action of ["dispose", "disconnect", "session", "cancel"]) {
        const f = fixture(40), editor = f.client();
        try {
            editor.replace({ gain: edit("4", 1) }, "immediate");
            if (action === "dispose") editor.dispose();
            if (action === "cancel") editor.cancel();
            if (action === "disconnect") f.update({ devices: [{ id: "c", type: "camera", status: "disconnected" }] });
            if (action === "session") f.update({ measurement: { server_instance_id: "new-server" } });
            await pause(65);
            assert.equal(f.calls.length, 0, action);
            if (action === "disconnect") f.update({ devices: [{ id: "c", type: "camera", status: "connected" }] });
            if (action === "session") f.update({ measurement: { server_instance_id: "server" } });
            await pause(50);
            assert.equal(f.calls.length, 0, `${action}後の再表示・再接続`);
        } finally { f.queue.dispose(); }
    }
});

test("失敗後は待機分を止め、状態復旧や編集では再送せず明示再適用だけを通す", async () => {
    const f = fixture(), first = f.client(), second = f.client(), slow = deferred(); f.controls.push(slow);
    try {
        first.replace({ gain: edit("3", 1) }, "immediate"); first.flush();
        await until(() => f.calls.length === 1);
        first.replace({ gain: edit("4", 2) }, "immediate");
        second.replace({ exposure: edit("20", 3) }, "immediate");
        slow.reject(new Error("結果不明"));
        await until(() => f.errors.length >= 2);
        f.update({ synchronization: { status: "ready" } });
        first.replace({ gain: edit("5", 4) }, "immediate"); first.flush();
        await pause(60);
        assert.equal(f.calls.length, 1);
        first.retry(); first.replace({ gain: edit("5", 4) }, "immediate"); first.flush();
        await until(() => f.state().settings.c.gain === 5);
        assert.equal(f.calls.length, 2);
    } finally { f.queue.dispose(); }
});

test("待機中に記録ロックが加わった項目は送信せず、別機器の操作を巻き込まない", async () => {
    const f = fixture(40), first = f.client(), second = f.client("d");
    try {
        first.replace({ fps: edit("20", 1) }, "manual");
        second.replace({ gain: edit("4", 2) }, "immediate");
        f.update({ recording: { targets: { c: { locked_settings: ["fps"] } } } });
        await until(() => f.errors.length === 1 && f.state().settings.d.gain === 4);
        assert.equal(f.calls.length, 1);
        assert.equal(f.calls[0].id, "d");
        assert.match(f.errors[0], /fps/);
    } finally { f.queue.dispose(); }
});

test("送信済みの応答は新しいセッションの入力へ通知せず、旧対象の待機分も破棄する", async () => {
    const f = fixture(), editor = f.client(), slow = deferred(); f.controls.push(slow);
    try {
        editor.replace({ gain: edit("3", 1) }, "immediate"); editor.flush();
        await until(() => f.calls.length === 1);
        editor.replace({ gain: edit("4", 2) }, "immediate");
        f.update({ measurement: { server_instance_id: "new-server" } });
        slow.resolve();
        await pause(60);
        assert.equal(f.calls.length, 1);
        assert.equal(f.applied.length, 0);
    } finally { slow.resolve(); f.queue.dispose(); }
});
