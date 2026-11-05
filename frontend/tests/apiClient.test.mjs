import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { ApiError, apiRawRequest, apiRequest } from "../src/api/client.ts";
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

test("通信失敗を記録し、結果が不明かを付けて渡す", async () => {
    const failure = new TypeError("network failed");
    globalThis.fetch = async () => { throw failure; };
    await assert.rejects(apiRawRequest("/api/status"), (error) => error instanceof ApiError && error.cause === failure && error.status === null && !error.outcomeUnknown);
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


test("書き込みのタイムアウトは結果不明とし、自動再送も独自オプション転送もしない", async () => {
    let calls = 0;
    globalThis.fetch = (_path, options) => {
        calls++;
        assert.equal("timeoutMs" in options, false);
        return new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(options.signal.reason)));
    };
    await assert.rejects(apiRequest("/api/start", { method: "POST", timeoutMs: 5 }),
        (error) => error instanceof ApiError && error.status === null && error.outcomeUnknown);
    assert.equal(calls, 1);
});

test("読み取りの通信断とHTTP失敗を変更結果不明にしない", async () => {
    globalThis.fetch = async () => { throw new TypeError("offline"); };
    await assert.rejects(apiRequest("/api/status"), (error) => error instanceof ApiError && !error.outcomeUnknown);
    await assert.rejects(apiRequest("/api/start", { method: "POST" }), (error) => error instanceof ApiError && error.outcomeUnknown);
    globalThis.fetch = async () => Response.json({ detail: "rejected" }, { status: 409 });
    await assert.rejects(apiRequest("/api/start", { method: "POST" }),
        (error) => error instanceof ApiError && error.status === 409 && !error.outcomeUnknown);
});

test("外部AbortSignalを保持し、送信前キャンセルでは書き込みを送らない", async () => {
    const controller = new AbortController();
    controller.abort();
    let calls = 0;
    globalThis.fetch = async () => { calls++; return Response.json({}); };
    await assert.rejects(apiRequest("/api/start", { method: "POST", signal: controller.signal }),
        (error) => error instanceof ApiError && !error.outcomeUnknown);
    assert.equal(calls, 0);
    const active = new AbortController();
    globalThis.fetch = (_path, options) => new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(options.signal.reason)));
    const pending = apiRequest("/api/start", { method: "POST", signal: active.signal });
    active.abort();
    await assert.rejects(pending, (error) => error instanceof ApiError && error.outcomeUnknown);
});

test("JSON本文の待機にもtimeoutMsを適用する", async () => {
    globalThis.fetch = async () => ({ ok: true, status: 200, json: () => new Promise(() => {}) });
    await assert.rejects(apiRequest("/api/status", { timeoutMs: 5 }), (error) => error instanceof ApiError && !error.outcomeUnknown);
});

test("変更要求の成功HTTP応答が不正JSONなら適用結果を不明として扱う", async () => {
    globalThis.fetch = async () => new Response("invalid json", { status: 200 });
    await assert.rejects(apiRequest("/api/start", { method: "POST" }),
        (error) => error instanceof ApiError && error.status === 200 && error.outcomeUnknown && error.cause instanceof SyntaxError);
    await assert.rejects(apiRequest("/api/status"), SyntaxError);
});
