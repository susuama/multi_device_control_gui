import assert from "node:assert/strict";
import { test } from "node:test";
import { createPreferencesStore, PREFERENCES_STORAGE_KEY } from "../src/state/preferencesStore.ts";

test("設定は既定値から部分更新し、版付きキーへ保存して復元する", () => {
    const values = new Map();
    const storage = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
    const store = createPreferencesStore(storage);
    assert.deepEqual(store.getSnapshot(), { theme: "dark", defaultDirectory: "", error: "" });
    let count = 0;
    const off = store.subscribe(() => count++);
    store.update({ theme: "light" });
    store.update({ defaultDirectory: "C:/実験データ" });
    store.update({ theme: "light" });
    assert.equal(count, 2);
    assert.ok(values.has(PREFERENCES_STORAGE_KEY));
    assert.match(PREFERENCES_STORAGE_KEY, /v1$/);
    assert.deepEqual(createPreferencesStore(storage).getSnapshot(), store.getSnapshot());
    off();
    store.update({ theme: "dark" });
    assert.equal(count, 2);
});

test("読出し・保存失敗でもメモリの設定を更新し、保存復旧でエラーを解除する", () => {
    let fail = true;
    const store = createPreferencesStore({
        getItem() { throw new Error("読出し禁止"); },
        setItem() { if (fail) throw new Error("容量不足"); },
    });
    assert.match(store.getSnapshot().error, /読出し禁止/);
    store.update({ defaultDirectory: "D:/run" });
    assert.equal(store.getSnapshot().defaultDirectory, "D:/run");
    assert.match(store.getSnapshot().error, /容量不足/);
    fail = false;
    store.update({ theme: "light" });
    assert.equal(store.getSnapshot().error, "");
});

test("保存値が不正なら既定値と読込みエラーを提供する", () => {
    for (const value of ["{", "null", '{"theme":"blue","defaultDirectory":""}', '{"theme":"dark"}']) {
        const store = createPreferencesStore({ getItem: () => value, setItem() {} });
        assert.equal(store.getSnapshot().theme, "dark");
        assert.equal(store.getSnapshot().defaultDirectory, "");
        assert.match(store.getSnapshot().error, /読み込めません/);
    }
});
