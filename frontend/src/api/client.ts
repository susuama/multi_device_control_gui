import { logger } from "../logging/logStore.ts";

// Vite の /api プロキシを使い、同じホストにリクエストする。
export async function apiRawRequest(
    path: string,
    options: RequestInit = {},
): Promise<Response> {
    const headers = new Headers(options.headers);
    if (options.body != null && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
    }

    const method = options.method ?? "GET";
    const startedAt = performance.now();
    logger.info("api", `${method} ${path} を送信`);
    try {
        const response = await fetch(path, { ...options, headers });
        const message = `${method} ${path} → HTTP ${response.status} (${Math.round(performance.now() - startedAt)} ms)`;
        if (response.ok) logger.info("api", message);
        else logger.error("api", message);
        // 応答本文は消費せず、そのまま呼び出し元へ返す。
        return response;
    } catch (cause) {
        logger.error("api", `${method} ${path} 通信失敗: ${cause instanceof Error ? cause.message : String(cause)}`);
        throw cause;
    }
}

export async function apiRequest<T>(
    path: string,
    options: RequestInit = {},
): Promise<T> {
    const response = await apiRawRequest(path, options);

    if (!response.ok) {
        const body: unknown = await response.json().catch(() => null);
        const detail = body !== null && typeof body === "object" && "detail" in body
            ? body.detail
            : undefined;
        const message = typeof detail === "string"
            ? detail
            : detail != null
                ? JSON.stringify(detail)
                : response.statusText;

        throw new Error(`HTTP ${response.status}: ${message}`);
    }

    try {
        return await response.json() as T;
    } catch (cause) {
        logger.error("api", `${path} の応答をJSONとして読み取れませんでした。`);
        throw cause;
    }
}
