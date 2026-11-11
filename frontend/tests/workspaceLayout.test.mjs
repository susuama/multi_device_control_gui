import assert from "node:assert/strict";
import { test } from "node:test";
import { insertPanel, panelIds, panelPath, validateLayouts } from "../src/workspace/layout.ts";
import { layouts } from "../src/workspace/layouts.ts";

const views = ["devices", "image", "camera-settings", "measurement-recording", "graph", "power-settings"];
const panel = (id, viewId = "devices") => ({ id, viewId });
const definition = (layout, panels = [panel("a"), panel("b")]) => ({ id: "test", title: "検証", panels, layout });
const split = () => ({ type: "split", direction: "row", children: ["a", "b"] });

test("標準配置から共通パネルをまとめ、2行3列を列順で読み出す", () => {
    assert.equal(validateLayouts(layouts, views, "grid").length, 6);
    assert.deepEqual(panelIds(layouts.find(item => item.id === "grid").layout), ["P1", "P4", "P2", "P5", "P3", "P6"]);
    assert.deepEqual(panelIds(null), []);
});

test("参照不整合、重複ID、初期配置の欠落を原因付きで報告する", () => {
    assert.throws(() => validateLayouts([definition(split())], [], "test"), /ビューが存在/);
    assert.throws(() => validateLayouts([definition(split())], views, "missing"), /初期レイアウト/);
    assert.throws(() => validateLayouts([definition(split()), definition(split())], views, "test"), /重複/);
    assert.throws(() => validateLayouts([definition(split(), [panel("a"), panel("a")])], views, "test"), /重複/);
    assert.throws(() => validateLayouts([definition({ ...split(), children: ["a", "a"] })], views, "test"), /重複/);
    assert.throws(() => validateLayouts([definition(split(), [panel("a"), panel("c")])], views, "test"), /一致しません/);
    assert.throws(() => validateLayouts([
        definition("a", [panel("a")]),
        { ...definition("a", [panel("a", "image")]), id: "other" },
    ], views, "test"), /初期定義が異なり/);
    try { validateLayouts([definition("unknown", [])], views, "test"); }
    catch (error) {
        assert.match(error.message, /レイアウト「test」/);
        assert.ok(error.cause instanceof Error);
    }
});

test("不正な分割率・方向と未対応のタブを拒否する", () => {
    for (const sizes of [[50], [40, 40], [0, 100], [-10, 110], [NaN, 50]]) {
        assert.throws(() => validateLayouts([definition({ ...split(), splitPercentages: sizes })], views, "test"), /分割率/);
    }
    assert.throws(() => validateLayouts([definition({ ...split(), children: ["a"] })], views, "test"), /2つ以上/);
    assert.throws(() => validateLayouts([definition({ ...split(), direction: "diagonal" })], views, "test"), /方向/);
    assert.throws(() => validateLayouts([definition({ type: "tabs", tabs: ["a", "b"], activeTabIndex: 0 })], views, "test"), /タブ/);
});

test("選択パネルだけを分割して追加し、元の配置と既存比率を変更しない", () => {
    const original = { ...split(), splitPercentages: [30, 70] };
    const next = insertPanel(original, "b", "c", "column");
    assert.deepEqual(next, {
        ...original, children: ["a", { type: "split", direction: "column", children: ["b", "c"] }],
    });
    assert.deepEqual(original.children, ["a", "b"]);
    assert.deepEqual(insertPanel(null, "", "new", "row"), "new");
    assert.deepEqual(panelIds(next), ["a", "b", "c"]);
});

test("最大化表示とは独立に、元の配置内のパネル位置を取得する", () => {
    const original = insertPanel(split(), "b", "c", "column");
    assert.deepEqual(panelPath(original, "a"), [0]);
    assert.deepEqual(panelPath(original, "b"), [1, 0]);
    assert.deepEqual(panelPath(original, "c"), [1, 1]);
    assert.deepEqual(panelPath("a", "a"), []);
    assert.equal(panelPath(null, "a"), null);
    assert.equal(panelPath(original, "unknown"), null);
    assert.deepEqual(panelPath(insertPanel("a", "a", "b", "row"), "b"), [1]);
});
