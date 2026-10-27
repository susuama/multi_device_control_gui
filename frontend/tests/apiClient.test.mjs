import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { apiRawRequest, apiRequest } from "../src/api/client.ts";
import { logStore } from "../src/logging/logStore.ts";

const originalFetch = globalThis.fetch;
beforeEach(() => logStore.clear());
afterEach(() => { globalThis.fetch = originalFetch; });

test("成功した応答本文を消費せずに返し、送信と結果を記録する", async () => {
    const response = Response.json({ status: "ok" });
    globalThis.fetch = async () => response;
    const result = await apiRawRequest("/api/status");
    assert.equal(result, response);
    assert.equal(result.bodyUsed, false);
    assert.deepEqual(await result.json(), { status: "ok" });
    const entries = logStore.getSnapshot();
    assert.equal(entries.length, 2);
    assert.match(entries[0].message, /GET \/api\/status/);
    assert.match(entries[1].message, /HTTP 200/);
    assert.equal(entries[1].level, "INFO");
});

test("HTTPエラーを記録し、apiRawRequestでは応答をそのまま返す", async () => {
    globalThis.fetch = async () => Response.json({ detail: "not available" }, { status: 503 });
    const response = await apiRawRequest("/api/devices");
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { detail: "not available" });
    assert.equal(logStore.getSnapshot().at(-1).level, "ERROR");
});

test("通信失敗を記録し、元の例外を呼び出し元へ渡す", async () => {
    const failure = new TypeError("network failed");
    globalThis.fetch = async () => { throw failure; };
    await assert.rejects(apiRawRequest("/api/status"), (error) => error === failure);
    assert.match(logStore.getSnapshot().at(-1).message, /通信失敗: network failed/);
});

test("apiRequestはHTTPエラーのdetailを保持する", async () => {
    globalThis.fetch = async () => Response.json({ detail: "device unavailable" }, { status: 503 });
    await assert.rejects(apiRequest("/api/devices"), /HTTP 503: device unavailable/);
});

test("JSON解析失敗を記録し、解析例外を返す", async () => {
    globalThis.fetch = async () => new Response("invalid json");
    await assert.rejects(apiRequest("/api/status"), SyntaxError);
    assert.match(logStore.getSnapshot().at(-1).message, /JSONとして読み取れません/);
});

test("本文のContent-Type既定値と、指定済みヘッダーを維持する", async () => {
    const requests = [];
    globalThis.fetch = async (_path, options) => {
        requests.push(options);
        return Response.json({ ok: true });
    };
    await apiRawRequest("/api/test", { method: "POST", body: "{}" });
    await apiRawRequest("/api/test", { method: "POST", body: "text", headers: { "Content-Type": "text/plain" } });
    assert.equal(requests[0].headers.get("Content-Type"), "application/json");
    assert.equal(requests[1].headers.get("Content-Type"), "text/plain");
    assert.equal(requests[0].body, "{}");
});
